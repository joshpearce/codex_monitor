from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import pytest
import websockets

from codex_goal_monitor.approvals import AggressiveApprovalHandler
from codex_goal_monitor.config import Config, Project
from codex_goal_monitor.protocol import ProtocolClient
from codex_goal_monitor.reconcile import ensure_daemon, run_once, unwrap
from codex_goal_monitor.state import StateStore


pytestmark = pytest.mark.integration

GENERIC_APPROVAL = "Yes. I approve and authorize the requested action. Continue working toward the full goal."
REQUIRED_ENV = ("RUN_CODEX_GOAL_INTEGRATION", "CODEX_INTEGRATION_AUTHORIZE_EXTERNAL_REVIEW")


def require_opt_in() -> None:
    missing = [name for name in REQUIRED_ENV if os.environ.get(name) != "1"]
    if missing:
        pytest.skip("live Codex/Claude integration requires explicit opt-in: " + ", ".join(missing))


async def connect(config: Config, thread_ids: set[str] | None = None) -> ProtocolClient:
    handler = AggressiveApprovalHandler(thread_ids or set(), GENERIC_APPROVAL)
    ws = await websockets.unix_connect(
        str(config.socket_path), uri="ws://localhost/", compression=None,
        open_timeout=10, close_timeout=3, max_size=32 * 1024 * 1024,
    )
    client = ProtocolClient(ws, handler)
    await client.initialize()
    return client


async def wait_for_goal(client: ProtocolClient, thread_id: str, wanted: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        goal = unwrap(await client.call("thread/goal/get", {"threadId": thread_id}), "goal")
        if goal and goal.get("status") == wanted:
            return goal
        await asyncio.sleep(10)
    raise AssertionError(f"Goal {thread_id} did not reach {wanted!r} within {timeout}s")


async def latest_items(client: ProtocolClient, thread_id: str) -> list[dict]:
    result = await client.call("thread/items/list", {
        "threadId": thread_id, "limit": 100, "sortDirection": "desc"
    })
    return unwrap(result, "data") or []


@pytest.mark.asyncio
async def test_generic_canned_answer_resumes_goal_into_claude_review(tmp_path: Path):
    require_opt_in()
    template = Path(__file__).parents[2] / "integration/canned_approval"
    project_path = tmp_path / "image-converter-project"
    shutil.copytree(template, project_path)
    objective = str((project_path / "GOAL.md").resolve())
    prompt = (project_path / "INITIAL_PROMPT.md").read_text().replace("`GOAL.md`", objective)

    provisional = Config(
        projects=(Project("fixture", project_path, "pending", objective),),
        socket_path=Path.home() / ".codex/app-server-control/app-server-control.sock",
        state_dir=tmp_path / "state",
        drain_seconds=5,
    )
    await ensure_daemon(provisional)
    creator = await connect(provisional)
    try:
        started = await creator.call("thread/start", {
            "cwd": str(project_path),
            "runtimeWorkspaceRoots": [str(project_path)],
            "approvalPolicy": "never",
            "approvalsReviewer": "auto_review",
            "historyMode": "paginated",
        })
        thread = unwrap(started, "thread") or started
        thread_id = thread.get("id") or thread.get("threadId")
        assert thread_id, started
        await creator.call("thread/goal/set", {
            "threadId": thread_id, "objective": objective, "status": "active"
        })
        await creator.call("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": str(project_path),
            "approvalPolicy": "never",
            "approvalsReviewer": "auto_review",
            "turnTrigger": "codex-goal-monitor-integration",
        })
        blocked = await wait_for_goal(creator, thread_id, "blocked", timeout=1800)
        items_before = await latest_items(creator, thread_id)
        transcript_before = json.dumps(items_before).lower()
        assert "claude" in transcript_before
        assert "authoriz" in transcript_before or "approval" in transcript_before
    finally:
        await creator.ws.close()

    config = Config(
        projects=(Project("fixture", project_path, thread_id, objective),),
        socket_path=provisional.socket_path,
        state_dir=tmp_path / "state",
        drain_seconds=20,
        continuation_cooldown_seconds=0,
        approval_policy="never",
        approvals_reviewer="auto_review",
        affirmative_answer=GENERIC_APPROVAL,
    )
    reports = await run_once(config, StateStore(config.state_dir))
    assert reports[0]["actions"] == ["goal/blocked->active", "turn/start"]

    verifier = await connect(config, {thread_id})
    try:
        active = await wait_for_goal(verifier, thread_id, "active", timeout=120)
        assert active["objective"] == blocked["objective"]
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            transcript_after = json.dumps(await latest_items(verifier, thread_id)).lower()
            if "authorization received" in transcript_after or "claude -p" in transcript_after:
                break
            await asyncio.sleep(5)
        else:
            raise AssertionError("Goal resumed but did not acknowledge authorization or enter Claude review")
    finally:
        await verifier.ws.close()
