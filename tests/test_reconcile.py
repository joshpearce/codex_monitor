from dataclasses import replace
from pathlib import Path

import pytest

from codex_goal_monitor.config import Config, Project
from codex_goal_monitor.reconcile import (
    Reconciler,
    looks_like_approval_question,
    looks_like_claude_sandbox_auth_failure,
)
from codex_goal_monitor.state import StateStore


class FakeClient:
    def __init__(self, *, thread_status="idle", goal_status="active", loaded=True):
        self.thread_status = thread_status
        self.goal_status = goal_status
        self.loaded = loaded
        self.calls = []

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": "thread-1", "status": {"type": self.thread_status}}}
        if method == "thread/goal/get":
            return {"goal": {"threadId": "thread-1", "objective": "goal.md", "status": self.goal_status}}
        if method == "thread/loaded/list":
            return {"data": ["thread-1"] if self.loaded else []}
        if method == "thread/items/list":
            return {"data": []}
        if method == "thread/resume":
            self.loaded = True
            self.thread_status = "active"
            return {"thread": {}}
        if method == "thread/goal/set":
            self.goal_status = params["status"]
            return {"goal": {}}
        if method == "turn/start":
            self.thread_status = "active"
            return {"turn": {"status": "inProgress"}}
        raise AssertionError(method)


def make_config(tmp_path):
    return Config(
        projects=(Project("demo", Path("/tmp/demo"), "thread-1", "goal.md"),),
        socket_path=tmp_path / "socket",
        state_dir=tmp_path / "state",
        drain_seconds=0,
    )


@pytest.mark.asyncio
async def test_idle_active_goal_starts_continuation(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient()
    report = await Reconciler(config, client, StateStore(config.state_dir)).reconcile_project(config.projects[0])
    assert "turn/start" in report["actions"]


@pytest.mark.asyncio
async def test_unloaded_active_goal_resumes_without_duplicate_turn(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(thread_status="notLoaded", loaded=False)
    report = await Reconciler(config, client, StateStore(config.state_dir)).reconcile_project(config.projects[0])
    assert report["actions"] == ["thread/resume"]


@pytest.mark.asyncio
async def test_blocked_goal_is_reactivated_and_answered(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(goal_status="blocked")
    report = await Reconciler(config, client, StateStore(config.state_dir)).reconcile_project(config.projects[0])
    assert report["actions"] == ["goal/blocked->active", "turn/start"]
    turn = next(params for method, params in client.calls if method == "turn/start")
    assert turn["input"][0]["text"].startswith("Yes.")


@pytest.mark.asyncio
async def test_disabled_blocked_notice_leaves_goal_untouched(tmp_path):
    config = replace(make_config(tmp_path), notices={"blocked_goal": False})
    client = FakeClient(goal_status="blocked")
    report = await Reconciler(config, client, StateStore(config.state_dir)).reconcile_project(
        config.projects[0]
    )
    assert report["result"] == "notice-disabled"
    assert not any(method == "thread/goal/set" for method, _ in client.calls)
    assert not any(method == "turn/start" for method, _ in client.calls)


@pytest.mark.asyncio
async def test_blocked_goal_steers_authorization_when_reactivation_started_turn(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(thread_status="active", goal_status="blocked")
    report = await Reconciler(config, client, StateStore(config.state_dir)).reconcile_project(config.projects[0])
    assert report["actions"] == ["goal/blocked->active", "turn/start"]
    turn = next(params for method, params in client.calls if method == "turn/start")
    assert turn["input"][0]["text"].startswith("Yes.")


def test_detects_natural_language_authorization_question():
    assert looks_like_approval_question("Do you authorize sending the repository to Claude?")
    assert not looks_like_approval_question("The build completed successfully.")


def test_detects_claude_sandbox_auth_failure():
    assert looks_like_claude_sandbox_auth_failure(
        "Goal blocked: Claude CLI remains unauthenticated. Complete `claude /login`."
    )
    assert not looks_like_claude_sandbox_auth_failure("Claude review completed successfully.")


@pytest.mark.asyncio
async def test_claude_auth_blocker_requests_outside_sandbox(tmp_path):
    config = replace(make_config(tmp_path), continuation_cooldown_seconds=0)
    client = FakeClient(goal_status="blocked")

    original_call = client.call

    async def call(method, params):
        if method == "thread/items/list":
            return {"data": [{"item": {
                "type": "agentMessage",
                "text": "Claude CLI remains unauthenticated. Complete `claude /login`.",
            }}]}
        return await original_call(method, params)

    client.call = call
    first_reconciler = Reconciler(config, client, StateStore(config.state_dir), "run-123")
    report = await first_reconciler.reconcile_project(config.projects[0])
    first_reconciler.store.save(first_reconciler.runtime)
    turn = next(params for method, params in client.calls if method == "turn/start")
    assert "outside the Codex command sandbox" in turn["input"][0]["text"]
    assert turn["sandbox"] == "danger-full-access"
    assert report["runId"] == "run-123"
    assert report["blockerKind"] == "claude_sandbox_auth"
    assert report["recoveryStrategy"] == "claude_host_recovery"
    assert len(report["blockerFingerprint"]) == 16
    assert report["sameBlockerCount"] == 1
    assert report["result"] == "recovery-submitted"
    assert report["afterGoalStatus"] == "active"
    assert report["afterThreadStatus"] == "active"
    assert isinstance(report["elapsedMs"], int)

    client.goal_status = "blocked"
    client.thread_status = "idle"
    repeated = await Reconciler(
        config, client, StateStore(config.state_dir), "run-456"
    ).reconcile_project(config.projects[0])
    assert repeated["sameBlockerCount"] == 2


@pytest.mark.asyncio
async def test_last_assistant_text_unwraps_thread_item_entry(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient()

    async def call(method, params):
        assert method == "thread/items/list"
        return {"data": [{
            "turnId": "turn-1",
            "item": {
                "type": "agentMessage",
                "text": "Do you authorize the review?",
            },
        }]}

    client.call = call
    text = await Reconciler(config, client, StateStore(config.state_dir))._last_assistant_text("thread-1")
    assert text == "Do you authorize the review?"
