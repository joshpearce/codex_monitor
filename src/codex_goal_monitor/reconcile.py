from __future__ import annotations

import asyncio
import time
from typing import Any

import websockets

from .approvals import AggressiveApprovalHandler
from .config import Config, Project
from .protocol import ProtocolClient, ProtocolError
from .state import StateStore


RECOVERABLE_GOAL_STATUSES = {"paused", "blocked", "usageLimited", "budgetLimited"}
CONTINUATION = (
    "Continue working toward the active Goal. Resolve whatever stopped the previous turn, "
    "approve or proceed with all actions needed in this trusted project, and do not stop until "
    "the full Goal is complete."
)


def unwrap(value: Any, key: str) -> Any:
    if isinstance(value, dict) and key in value:
        return value[key]
    return value


def status_type(thread: dict[str, Any]) -> str:
    status = thread.get("status", "unknown")
    return status.get("type", "unknown") if isinstance(status, dict) else str(status)


def loaded_ids(result: Any) -> set[str]:
    value = unwrap(result, "data") or []
    ids: set[str] = set()
    for item in value:
        if isinstance(item, str):
            ids.add(item)
        elif isinstance(item, dict):
            candidate = item.get("id") or item.get("threadId")
            if candidate:
                ids.add(candidate)
    return ids


def overrides(config: Config) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if config.approval_policy is not None:
        result["approvalPolicy"] = config.approval_policy
    if config.approvals_reviewer is not None:
        result["approvalsReviewer"] = config.approvals_reviewer
    return result


async def ensure_daemon(config: Config) -> None:
    process = await asyncio.create_subprocess_exec(
        config.codex_command, "app-server", "daemon", "start",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), config.command_timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("timed out starting Codex app-server daemon")
    if process.returncode:
        raise RuntimeError(
            f"codex app-server daemon start failed ({process.returncode}): "
            f"{stderr.decode(errors='replace') or stdout.decode(errors='replace')}"
        )
    deadline = asyncio.get_running_loop().time() + config.connect_timeout_seconds
    while not config.socket_path.exists():
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(f"Codex socket did not appear: {config.socket_path}")
        await asyncio.sleep(0.2)


class Reconciler:
    def __init__(self, config: Config, client: ProtocolClient, store: StateStore):
        self.config = config
        self.client = client
        self.store = store
        self.runtime = store.load()
        self.runtime.setdefault("threads", {})

    async def reconcile_project(self, project: Project) -> dict[str, Any]:
        report: dict[str, Any] = {"project": project.name, "threadId": project.thread_id, "actions": []}
        # ProtocolClient intentionally has one reader; requests are serialized so
        # notifications and server requests can be dispatched deterministically.
        read_result = await self.client.call(
            "thread/read", {"threadId": project.thread_id, "includeTurns": False}
        )
        goal_result = await self.client.call("thread/goal/get", {"threadId": project.thread_id})
        loaded_result = await self.client.call("thread/loaded/list", {})
        thread = unwrap(read_result, "thread") or {}
        goal = unwrap(goal_result, "goal")
        before = {"thread": thread, "goal": goal, "loaded": sorted(loaded_ids(loaded_result))}
        report["before"] = before
        self.store.audit("snapshot", project=project.name, threadId=project.thread_id, snapshot=before)

        if not project.ensure_goal_running or not goal:
            report["result"] = "disabled-or-no-goal"
            return report
        if project.goal_objective and goal.get("objective") != project.goal_objective:
            raise RuntimeError(
                f"{project.name}: objective mismatch: expected {project.goal_objective!r}, "
                f"found {goal.get('objective')!r}"
            )
        goal_status = goal.get("status")
        if goal_status == "complete":
            report["result"] = "complete"
            return report

        is_loaded = project.thread_id in loaded_ids(loaded_result) and status_type(thread) != "notLoaded"
        if not is_loaded:
            params = {"threadId": project.thread_id, "excludeTurns": True, **overrides(self.config)}
            await self.client.call("thread/resume", params)
            report["actions"].append("thread/resume")
            after_goal = unwrap(await self.client.call("thread/goal/get", {"threadId": project.thread_id}), "goal")
            if after_goal is None:
                raise RuntimeError(f"{project.name}: Goal disappeared after thread/resume")
            # Resuming an active Goal may start a turn by itself.
            await asyncio.sleep(0.25)
            thread = unwrap(await self.client.call(
                "thread/read", {"threadId": project.thread_id, "includeTurns": False}
            ), "thread") or {}
            goal = after_goal
            goal_status = goal.get("status")

        if goal_status in RECOVERABLE_GOAL_STATUSES:
            await self.client.call("thread/goal/set", {"threadId": project.thread_id, "status": "active"})
            report["actions"].append(f"goal/{goal_status}->active")

        thread_state = status_type(thread)
        if thread_state != "active" and self._cooldown_elapsed(project.thread_id):
            last_text = await self._last_assistant_text(project.thread_id)
            message = (
                self.config.affirmative_answer
                if goal_status in {"paused", "blocked"} or looks_like_approval_question(last_text)
                else CONTINUATION
            )
            params = {
                "threadId": project.thread_id,
                "input": [{"type": "text", "text": message}],
                "cwd": str(project.path),
                "turnTrigger": "codex-goal-monitor",
                **overrides(self.config),
            }
            await self.client.call("turn/start", params)
            self.runtime["threads"].setdefault(project.thread_id, {})["lastContinuationAt"] = int(time.time())
            report["actions"].append("turn/start")
        elif thread_state == "active":
            report["result"] = "already-active"
        else:
            report["result"] = "continuation-cooldown"
        return report

    async def _last_assistant_text(self, thread_id: str) -> str:
        try:
            result = await self.client.call("thread/items/list", {
                "threadId": thread_id, "limit": 20, "sortDirection": "desc"
            })
        except ProtocolError:
            return ""
        for item in unwrap(result, "data") or []:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            kind = str(item.get("type", "")).lower()
            if role != "assistant" and "agentmessage" not in kind and "agent_message" not in kind:
                continue
            content = item.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
        return ""

    def _cooldown_elapsed(self, thread_id: str) -> bool:
        last = self.runtime["threads"].get(thread_id, {}).get("lastContinuationAt", 0)
        return time.time() - last >= self.config.continuation_cooldown_seconds

    async def run(self) -> list[dict[str, Any]]:
        reports = []
        for project in self.config.projects:
            try:
                report = await self.reconcile_project(project)
                self.store.audit("reconciled", **report)
                reports.append(report)
            except Exception as exc:
                report = {"project": project.name, "threadId": project.thread_id, "error": str(exc)}
                self.store.audit("error", **report)
                reports.append(report)
        self.store.save(self.runtime)
        return reports


def looks_like_approval_question(text: str) -> bool:
    lowered = text.lower()
    decision_words = ("authorize", "approval", "approve", "permission", "may i", "do you want")
    return bool(text) and ("?" in text or "pending" in lowered) and any(word in lowered for word in decision_words)


async def run_once(config: Config, store: StateStore) -> list[dict[str, Any]]:
    await ensure_daemon(config)
    handler = AggressiveApprovalHandler(
        {project.thread_id for project in config.projects}, config.affirmative_answer
    )
    async with websockets.unix_connect(
        str(config.socket_path), uri="ws://localhost/", compression=None,
        open_timeout=config.connect_timeout_seconds, close_timeout=3,
        max_size=32 * 1024 * 1024,
    ) as ws:
        client = ProtocolClient(ws, handler)
        initialized = await client.initialize()
        store.audit("connected", initialize=initialized)
        reconciler = Reconciler(config, client, store)
        reports = await reconciler.run()
        await client.drain(config.drain_seconds)
        for event in handler.events:
            store.audit("auto-approved", **event)
        return reports
