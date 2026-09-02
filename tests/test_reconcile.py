from pathlib import Path

import pytest

from codex_goal_monitor.config import Config, Project
from codex_goal_monitor.reconcile import Reconciler, looks_like_approval_question
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
