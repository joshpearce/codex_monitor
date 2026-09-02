from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
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
CLAUDE_OUTSIDE_SANDBOX = (
    "Run the already-authorized Claude Opus review outside the Codex command sandbox, where Claude "
    "can access the user's stored credentials. Do not ask the user to log in based on a sandboxed "
    "`claude auth status` result. Continue working toward the full Goal."
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
    def __init__(
        self, config: Config, client: ProtocolClient, store: StateStore, run_id: str | None = None
    ):
        self.config = config
        self.client = client
        self.store = store
        self.run_id = run_id or str(uuid.uuid4())
        self.runtime = store.load()
        self.runtime.setdefault("threads", {})

    async def reconcile_project(self, project: Project) -> dict[str, Any]:
        started = time.monotonic()
        report: dict[str, Any] = {
            "runId": self.run_id,
            "project": project.name,
            "threadId": project.thread_id,
            "actions": [],
        }
        # ProtocolClient intentionally has one reader; requests are serialized so
        # notifications and server requests can be dispatched deterministically.
        read_result = await self.client.call(
            "thread/read", {"threadId": project.thread_id, "includeTurns": False}
        )
        goal_result = await self.client.call("thread/goal/get", {"threadId": project.thread_id})
        loaded_result = await self.client.call("thread/loaded/list", {})
        thread = unwrap(read_result, "thread") or {}
        goal = unwrap(goal_result, "goal")
        report["beforeGoalStatus"] = goal.get("status") if goal else None
        report["beforeThreadStatus"] = status_type(thread)
        report["loaded"] = project.thread_id in loaded_ids(loaded_result)

        if not project.ensure_goal_running or not goal:
            report["result"] = "disabled-or-no-goal"
            return await self._finish_report(report, started)
        if project.goal_objective and goal.get("objective") != project.goal_objective:
            raise RuntimeError(
                f"{project.name}: objective mismatch: expected {project.goal_objective!r}, "
                f"found {goal.get('objective')!r}"
            )
        goal_status = goal.get("status")
        if goal_status == "complete":
            report["result"] = "complete"
            return await self._finish_report(report, started)

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
        needs_authorization = goal_status in {"paused", "blocked"}
        # turn/start also steers an already-active turn. This matters because
        # goal/set(active) can synchronously start an automatic continuation;
        # the authorization must be injected into that turn rather than lost.
        should_send = thread_state != "active" or needs_authorization
        if should_send and self._cooldown_elapsed(project.thread_id):
            last_text = await self._last_assistant_text(project.thread_id)
            fingerprint = blocker_fingerprint(last_text) if last_text else None
            claude_host_recovery = looks_like_claude_sandbox_auth_failure(last_text)
            if claude_host_recovery:
                message = CLAUDE_OUTSIDE_SANDBOX
                recovery = "claude_host_recovery"
                blocker_kind = "claude_sandbox_auth"
            elif needs_authorization or looks_like_approval_question(last_text):
                message = self.config.affirmative_answer
                recovery = "generic_approval"
                blocker_kind = "approval_question"
            else:
                message = CONTINUATION
                recovery = "continuation"
                blocker_kind = "idle_or_unknown"
            thread_runtime = self.runtime["threads"].setdefault(project.thread_id, {})
            previous = thread_runtime.get("lastBlockerFingerprint")
            repeats = int(thread_runtime.get("sameBlockerCount", 0)) + 1 if previous == fingerprint else 1
            thread_runtime["lastBlockerFingerprint"] = fingerprint
            thread_runtime["sameBlockerCount"] = repeats
            report.update({
                "blockerKind": blocker_kind,
                "blockerFingerprint": fingerprint,
                "sameBlockerCount": repeats,
                "recoveryStrategy": recovery,
            })
            params = {
                "threadId": project.thread_id,
                "input": [{"type": "text", "text": message}],
                "cwd": str(project.path),
                "turnTrigger": "codex-goal-monitor",
                **overrides(self.config),
            }
            if claude_host_recovery:
                params["sandbox"] = "danger-full-access"
            await self.client.call("turn/start", params)
            thread_runtime["lastContinuationAt"] = int(time.time())
            report["actions"].append("turn/start")
            report["result"] = "recovery-submitted"
        elif thread_state == "active":
            report["result"] = "already-active"
        else:
            report["result"] = "continuation-cooldown"
        return await self._finish_report(report, started)

    async def _finish_report(self, report: dict[str, Any], started: float) -> dict[str, Any]:
        thread_id = report["threadId"]
        try:
            thread = unwrap(await self.client.call(
                "thread/read", {"threadId": thread_id, "includeTurns": False}
            ), "thread") or {}
            goal = unwrap(await self.client.call("thread/goal/get", {"threadId": thread_id}), "goal")
            report["afterThreadStatus"] = status_type(thread)
            report["afterGoalStatus"] = goal.get("status") if goal else None
        except ProtocolError as exc:
            report["postCheckError"] = str(exc)
        report.setdefault("outcome", report.get("result", "observed"))
        report["elapsedMs"] = round((time.monotonic() - started) * 1000)
        return report

    async def _last_assistant_text(self, thread_id: str) -> str:
        try:
            result = await self.client.call("thread/items/list", {
                "threadId": thread_id, "limit": 20, "sortDirection": "desc"
            })
        except ProtocolError:
            return ""
        for entry in unwrap(result, "data") or []:
            item = entry.get("item", entry) if isinstance(entry, dict) else entry
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            kind = str(item.get("type", "")).lower()
            if role != "assistant" and "agentmessage" not in kind and "agent_message" not in kind:
                continue
            if isinstance(item.get("text"), str):
                return item["text"]
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
                report = {
                    "runId": self.run_id,
                    "project": project.name,
                    "threadId": project.thread_id,
                    "outcome": "error",
                    "error": str(exc),
                }
                self.store.audit("error", **report)
                reports.append(report)
        self.store.save(self.runtime)
        return reports


def looks_like_approval_question(text: str) -> bool:
    lowered = text.lower()
    decision_words = ("authorize", "approval", "approve", "permission", "may i", "do you want")
    return bool(text) and ("?" in text or "pending" in lowered) and any(word in lowered for word in decision_words)


def blocker_fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def looks_like_claude_sandbox_auth_failure(text: str) -> bool:
    lowered = text.lower()
    return "claude" in lowered and (
        "unauthenticated" in lowered
        or "claude /login" in lowered
        or ("loggedin" in lowered and "false" in lowered)
        or "host-side command runner" in lowered
        or "host-side execution capability" in lowered
    )


async def run_once(config: Config, store: StateStore) -> list[dict[str, Any]]:
    run_id = str(uuid.uuid4())
    started = time.monotonic()
    store.audit("run-started", runId=run_id, projectCount=len(config.projects))
    try:
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
            await client.initialize()
            reconciler = Reconciler(config, client, store, run_id)
            reports = await reconciler.run()
            await client.drain(config.drain_seconds)
            for event in handler.events:
                store.audit("auto-approved", runId=run_id, **event)
        errors = sum("error" in report for report in reports)
        store.audit(
            "run-finished", runId=run_id, outcome="error" if errors else "success",
            projectCount=len(reports), errorCount=errors,
            elapsedMs=round((time.monotonic() - started) * 1000),
        )
        return reports
    except Exception as exc:
        store.audit(
            "run-failed", runId=run_id, errorType=type(exc).__name__, error=str(exc),
            elapsedMs=round((time.monotonic() - started) * 1000),
        )
        raise


async def inspect_once(config: Config) -> list[dict[str, Any]]:
    """Read live state without starting the daemon or changing a thread."""
    async def reject_server_request(request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"inspect mode cannot answer {request.get('method')}")

    async with websockets.unix_connect(
        str(config.socket_path), uri="ws://localhost/", compression=None,
        open_timeout=config.connect_timeout_seconds, close_timeout=3,
        max_size=32 * 1024 * 1024,
    ) as ws:
        client = ProtocolClient(ws, reject_server_request)
        await client.initialize()
        loaded = loaded_ids(await client.call("thread/loaded/list", {}))
        reports = []
        for project in config.projects:
            try:
                thread = unwrap(await client.call(
                    "thread/read", {"threadId": project.thread_id, "includeTurns": False}
                ), "thread")
                goal = unwrap(await client.call(
                    "thread/goal/get", {"threadId": project.thread_id}
                ), "goal")
                reports.append({
                    "project": project.name,
                    "threadId": project.thread_id,
                    "loaded": project.thread_id in loaded,
                    "thread": thread,
                    "goal": goal,
                })
            except Exception as exc:
                reports.append({"project": project.name, "threadId": project.thread_id, "error": str(exc)})
        return reports
