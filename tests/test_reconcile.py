from dataclasses import replace
from pathlib import Path

import pytest

from codex_goal_monitor.config import Config, Project
from codex_goal_monitor.reconcile import (
    Reconciler,
    drain_recovery_turns,
    looks_like_approval_question,
    looks_like_claude_host_runner_unavailable,
    looks_like_claude_sandbox_auth_failure,
    looks_like_repository_external_authorization_accepted,
    looks_like_repository_external_authorization_request,
    is_recoverable_transport_error,
    run_once,
)
from codex_goal_monitor import reconcile
from codex_goal_monitor.state import StateStore


class FakeClient:
    def __init__(self, *, thread_status="idle", goal_status="active", loaded=True, active_flags=None):
        self.thread_status = thread_status
        self.goal_status = goal_status
        self.loaded = loaded
        self.active_flags = active_flags or []
        self.calls = []

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "status": {
                "type": self.thread_status, "activeFlags": self.active_flags,
            }}}
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
        if method == "thread/start":
            self.loaded = True
            self.thread_status = "idle"
            self.active_flags = []
            return {"thread": {"id": "thread-2"}}
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


class WatchClient:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.drains = []
        self.reads = 0

    async def drain(self, seconds):
        self.drains.append(seconds)

    async def call(self, method, params):
        assert method == "thread/read"
        self.reads += 1
        return {"thread": {"status": {"type": next(self.statuses)}}}


def test_unclean_websocket_reset_is_recoverable():
    assert is_recoverable_transport_error(RuntimeError(
        "remote app server transport failed: WebSocket protocol error: "
        "Connection reset without closing handshake"
    ))
    assert not is_recoverable_transport_error(RuntimeError("objective mismatch"))


@pytest.mark.asyncio
async def test_run_reconnects_after_unclean_websocket_reset(monkeypatch, tmp_path):
    config = replace(
        make_config(tmp_path), transport_reconnect_attempts=2,
        transport_reconnect_delay_seconds=0,
    )
    store = StateStore(config.state_dir)
    attempts = 0

    async def fake_ensure_daemon(config):
        return None

    async def fake_run_connected(config, store, run_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError(
                "WebSocket protocol error: Connection reset without closing handshake"
            )
        return [{"threadId": "thread-1", "result": "already-active"}]

    monkeypatch.setattr(reconcile, "ensure_daemon", fake_ensure_daemon)
    monkeypatch.setattr(reconcile, "_run_connected", fake_run_connected)

    reports = await run_once(config, store)

    assert reports[0]["result"] == "already-active"
    assert attempts == 2
    assert '"event": "transport-reconnecting"' in store.audit_path.read_text()


@pytest.mark.asyncio
async def test_recovery_watch_stays_attached_until_started_turn_is_idle(tmp_path):
    config = replace(
        make_config(tmp_path), drain_seconds=0, active_turn_watch_seconds=60,
        approvals_reviewer="user",
    )
    client = WatchClient(["active", "idle"])

    await drain_recovery_turns(client, config, {"thread-1"})

    assert client.reads == 2
    assert client.drains == [0, 1.0]


@pytest.mark.asyncio
async def test_recovery_watch_is_not_extended_without_user_reviewer(tmp_path):
    config = replace(make_config(tmp_path), drain_seconds=0, approvals_reviewer="auto_review")
    client = WatchClient([])

    await drain_recovery_turns(client, config, {"thread-1"})

    assert client.reads == 0
    assert client.drains == [0]


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
    assert ("thread/resume", {
        "threadId": "thread-1",
        "excludeTurns": True,
    }) in client.calls


@pytest.mark.asyncio
async def test_crashed_idle_session_resumes_goal_with_session_id(tmp_path):
    """An unloaded session is the app-server equivalent of `codex resume ID`.

    If resume doesn't start the active Goal automatically, the fresh idle state
    is handled by the ordinary continuation path in the same reconciliation.
    """
    config = make_config(tmp_path)
    client = FakeClient(thread_status="notLoaded", goal_status="active", loaded=False)
    original_call = client.call

    async def call(method, params):
        if method == "thread/resume":
            client.calls.append((method, params))
            client.loaded = True
            client.thread_status = "idle"
            return {"thread": {}}
        return await original_call(method, params)

    client.call = call

    report = await Reconciler(
        config, client, StateStore(config.state_dir)
    ).reconcile_project(config.projects[0])

    resume = next(params for method, params in client.calls if method == "thread/resume")
    assert resume["threadId"] == config.projects[0].thread_id
    assert report["actions"] == ["thread/resume", "turn/start"]


@pytest.mark.asyncio
async def test_orphaned_approval_replaces_thread_and_adopts_new_id(tmp_path):
    config = replace(make_config(tmp_path), orphaned_approval_seconds=300)
    store = StateStore(config.state_dir)
    store.save({
        "threads": {},
        "projects": {"/tmp/demo": {
            "threadId": "thread-1", "waitingOnApprovalSince": 1,
        }},
    })
    client = FakeClient(thread_status="active", active_flags=["waitingOnApproval"])
    reconciler = Reconciler(config, client, store, managed_thread_ids={"thread-1"})

    report = await reconciler.reconcile_project(config.projects[0])

    assert report["result"] == "replacement-started"
    assert report["previousThreadId"] == "thread-1"
    assert report["threadId"] == "thread-2"
    assert reconciler.runtime["projects"]["/tmp/demo"]["threadId"] == "thread-2"
    assert "thread-2" in reconciler.managed_thread_ids
    methods = [method for method, _ in client.calls]
    assert methods.index("thread/start") < methods.index("thread/goal/set") < methods.index("turn/start")


@pytest.mark.asyncio
async def test_fresh_approval_wait_is_observed_without_replacement(tmp_path):
    config = replace(make_config(tmp_path), orphaned_approval_seconds=300)
    client = FakeClient(thread_status="active", active_flags=["waitingOnApproval"])
    reconciler = Reconciler(config, client, StateStore(config.state_dir))

    report = await reconciler.reconcile_project(config.projects[0])

    assert report["result"] == "already-active"
    assert not any(method == "thread/start" for method, _ in client.calls)


@pytest.mark.asyncio
async def test_blocked_goal_is_reactivated_and_answered(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(goal_status="blocked")
    report = await Reconciler(config, client, StateStore(config.state_dir)).reconcile_project(config.projects[0])
    assert report["actions"] == ["goal/blocked->active", "turn/start"]
    turn = next(params for method, params in client.calls if method == "turn/start")
    assert turn["input"][0]["text"].startswith("Yes.")


@pytest.mark.asyncio
async def test_blocked_goal_authorization_bypasses_continuation_cooldown(tmp_path):
    config = make_config(tmp_path)
    store = StateStore(config.state_dir)
    store.save({"threads": {"thread-1": {"lastContinuationAt": 4102444800}}})
    client = FakeClient(goal_status="blocked")

    report = await Reconciler(config, client, store).reconcile_project(config.projects[0])

    assert report["actions"] == ["goal/blocked->active", "turn/start"]


@pytest.mark.asyncio
async def test_idle_goal_continuation_still_honors_cooldown(tmp_path):
    config = make_config(tmp_path)
    store = StateStore(config.state_dir)
    store.save({"threads": {"thread-1": {"lastContinuationAt": 4102444800}}})
    client = FakeClient(goal_status="active")

    report = await Reconciler(config, client, store).reconcile_project(config.projects[0])

    assert report["actions"] == []
    assert report["result"] == "continuation-cooldown"


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
    assert looks_like_claude_host_runner_unavailable(
        "This thread has no host-side command runner for Claude."
    )


def test_detects_explicit_repository_external_authorization_request():
    assert looks_like_repository_external_authorization_request(
        "Blocked: explicit repository-to-Anthropic authorization is required. "
        "Generic authorization is not accepted."
    )
    assert looks_like_repository_external_authorization_request(
        "Authorization is pending to send repository materials to Claude."
    )
    assert looks_like_repository_external_authorization_request(
        "The goal remains blocked pending custom explicit repository-to-Anthropic consent."
    )
    assert looks_like_repository_external_authorization_request(
        "The goal is blocked by the environment's egress approval configuration."
    )
    assert not looks_like_repository_external_authorization_request(
        "Claude CLI remains unauthenticated."
    )
    assert looks_like_repository_external_authorization_accepted(
        "Explicit authorization accepted. I will resume the Claude review."
    )


@pytest.mark.asyncio
async def test_repository_external_authorization_names_materials_and_recipient(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(goal_status="blocked")
    original_call = client.call

    async def call(method, params):
        if method == "thread/items/list":
            return {"data": [{"item": {
                "type": "agentMessage",
                "text": "Blocked: explicit repository-to-Anthropic authorization is required. "
                        "Generic authorization is not accepted.",
            }}]}
        return await original_call(method, params)

    client.call = call
    report = await Reconciler(
        config, client, StateStore(config.state_dir)
    ).reconcile_project(config.projects[0])
    turn = next(params for method, params in client.calls if method == "turn/start")
    message = turn["input"][0]["text"]
    assert "repository-derived materials" in message
    assert "Anthropic Claude Opus" in message
    assert "subscription usage" in message
    assert "goal/blocked->active" not in report["actions"]
    assert report["actions"] == ["turn/start/standalone-authorization"]
    assert report["afterGoalStatus"] == "blocked"
    assert report["recoveryStrategy"] == "standalone_repository_external_authorization"


@pytest.mark.asyncio
async def test_accepted_standalone_authorization_reactivates_and_continues_goal(tmp_path):
    config = make_config(tmp_path)
    store = StateStore(config.state_dir)
    store.save({"threads": {"thread-1": {"repositoryAuthorizationPending": True}}})
    client = FakeClient(goal_status="blocked")
    original_call = client.call

    async def call(method, params):
        if method == "thread/items/list":
            return {"data": [{"item": {
                "type": "agentMessage",
                "text": "Explicit authorization accepted. The Claude review may proceed.",
            }}]}
        return await original_call(method, params)

    client.call = call
    reconciler = Reconciler(config, client, store)
    report = await reconciler.reconcile_project(config.projects[0])

    assert report["actions"] == ["goal/blocked->active", "turn/start"]
    turn = next(params for method, params in client.calls if method == "turn/start")
    assert turn["input"][0]["text"].startswith("Continue working")
    assert "sandbox" not in turn
    assert "repositoryAuthorizationPending" not in reconciler.runtime["threads"]["thread-1"]


@pytest.mark.asyncio
async def test_pending_standalone_authorization_is_not_steered_while_active(tmp_path):
    config = make_config(tmp_path)
    store = StateStore(config.state_dir)
    store.save({"threads": {"thread-1": {"repositoryAuthorizationPending": True}}})
    client = FakeClient(thread_status="active", goal_status="blocked")

    report = await Reconciler(config, client, store).reconcile_project(config.projects[0])

    assert report["actions"] == []
    assert report["result"] == "authorization-in-progress"
    assert not any(method == "turn/start" for method, _ in client.calls)


@pytest.mark.asyncio
async def test_rejected_standalone_authorization_is_not_retried_or_made_generic(tmp_path):
    config = make_config(tmp_path)
    store = StateStore(config.state_dir)
    store.save({"threads": {"thread-1": {"repositoryAuthorizationPending": True}}})
    client = FakeClient(thread_status="idle", goal_status="blocked")
    original_call = client.call

    async def call(method, params):
        if method == "thread/items/list":
            return {"data": [{"item": {
                "type": "agentMessage",
                "text": "The goal is blocked by the environment's egress approval configuration.",
            }}]}
        return await original_call(method, params)

    client.call = call
    report = await Reconciler(config, client, store).reconcile_project(config.projects[0])

    assert report["actions"] == []
    assert report["result"] == "authorization-not-accepted"
    assert report["recoveryStrategy"] == "manual_intervention_required"
    assert not any(method == "turn/start" for method, _ in client.calls)


@pytest.mark.asyncio
async def test_user_routed_approvals_clear_stale_authorization_rejection(tmp_path):
    config = replace(
        make_config(tmp_path),
        approval_policy="on-request",
        approvals_reviewer="user",
    )
    store = StateStore(config.state_dir)
    store.save({"threads": {"thread-1": {"repositoryAuthorizationPending": True}}})
    client = FakeClient(thread_status="idle", goal_status="blocked")
    original_call = client.call

    async def call(method, params):
        if method == "thread/items/list":
            return {"data": [{"item": {
                "type": "agentMessage",
                "text": "The environment rejected the review through its egress approval policy.",
            }}]}
        return await original_call(method, params)

    client.call = call
    reconciler = Reconciler(config, client, store)
    report = await reconciler.reconcile_project(config.projects[0])

    assert report["actions"] == ["goal/blocked->active", "turn/start"]
    turn = next(params for method, params in client.calls if method == "turn/start")
    assert turn["approvalPolicy"] == "on-request"
    assert turn["approvalsReviewer"] == "user"
    assert turn["input"][0]["text"].startswith("Continue working")
    assert "repositoryAuthorizationPending" not in reconciler.runtime["threads"]["thread-1"]


@pytest.mark.asyncio
async def test_missing_claude_host_runner_requires_manual_intervention(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(goal_status="blocked")
    original_call = client.call

    async def call(method, params):
        if method == "thread/items/list":
            return {"data": [{"item": {
                "type": "agentMessage",
                "text": "No host-side execution capability is available for Claude.",
            }}]}
        return await original_call(method, params)

    client.call = call
    report = await Reconciler(
        config, client, StateStore(config.state_dir)
    ).reconcile_project(config.projects[0])

    assert report["actions"] == []
    assert report["result"] == "manual-intervention-required"
    assert report["blockerKind"] == "claude_host_runner_unavailable"


@pytest.mark.asyncio
async def test_post_drain_verification_detects_immediate_reblock(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(goal_status="blocked")
    reconciler = Reconciler(config, client, StateStore(config.state_dir))
    reports = [{
        "threadId": "thread-1",
        "result": "recovery-submitted",
        "outcome": "recovery-submitted",
    }]

    await reconciler.verify_recoveries(reports)

    assert reports[0]["result"] == "recovery-reblocked"
    assert reports[0]["outcome"] == "recovery-reblocked"
    assert reports[0]["verifiedGoalStatus"] == "blocked"


@pytest.mark.asyncio
async def test_post_drain_verification_detects_standalone_authorization_acceptance(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient(thread_status="idle", goal_status="blocked")

    async def call(method, params):
        if method == "thread/read":
            return {"thread": {"status": {"type": "idle"}}}
        if method == "thread/items/list":
            return {"data": [{"item": {
                "type": "agentMessage",
                "text": "Explicit authorization accepted. The Claude review may proceed.",
            }}]}
        raise AssertionError(method)

    client.call = call
    reports = [{
        "threadId": "thread-1",
        "result": "authorization-submitted",
        "outcome": "authorization-submitted",
    }]

    await Reconciler(config, client, StateStore(config.state_dir)).verify_recoveries(reports)

    assert reports[0]["result"] == "authorization-accepted"
    assert reports[0]["outcome"] == "authorization-accepted"


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
