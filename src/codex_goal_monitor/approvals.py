from __future__ import annotations

from collections.abc import Callable
from typing import Any


class AggressiveApprovalHandler:
    """Approve requests for threads the reconciler explicitly manages."""

    def __init__(
        self, managed_thread_ids: set[str], affirmative_answer: str,
        notices: dict[str, bool] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.managed_thread_ids = managed_thread_ids
        self.affirmative_answer = affirmative_answer
        self.notices = notices or {}
        self.event_callback = event_callback
        self.events: list[dict[str, Any]] = []

    def _managed(self, params: dict[str, Any]) -> bool:
        thread_id = params.get("threadId") or params.get("thread_id") or params.get("conversationId")
        return str(thread_id) in self.managed_thread_ids

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request["method"]
        params = request.get("params", {})
        if not self._managed(params):
            raise ValueError(f"refusing approval for unmanaged thread: {params.get('threadId')}")

        notice_by_method = {
            "item/commandExecution/requestApproval": "command_approval",
            "item/fileChange/requestApproval": "file_change_approval",
            "item/permissions/requestApproval": "permissions_approval",
            "item/tool/requestUserInput": "user_input",
            "mcpServer/elicitation/request": "mcp_elicitation",
            "execCommandApproval": "legacy_exec_approval",
            "applyPatchApproval": "legacy_patch_approval",
        }
        notice = notice_by_method.get(method)
        if notice and not self.notices.get(notice, True):
            raise ValueError(f"automatic response disabled by notices.{notice}")

        if method == "item/commandExecution/requestApproval":
            available = params.get("availableDecisions") or []
            decision = "acceptForSession" if not available or "acceptForSession" in available else "accept"
            result: Any = {"decision": decision}
        elif method == "item/fileChange/requestApproval":
            result = {"decision": "accept"}
        elif method == "item/permissions/requestApproval":
            result = {
                "permissions": params.get("permissions", {}),
                "scope": "session",
                "strictAutoReview": False,
            }
        elif method == "item/tool/requestUserInput":
            answers = {}
            for question in params.get("questions", []):
                options = question.get("options") or []
                answers[question["id"]] = {"answers": [options[0]["label"] if options else self.affirmative_answer]}
            result = {"answers": answers}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "accept", "content": {}}
        elif method == "execCommandApproval":
            result = {"decision": "approved_for_session"}
        elif method == "applyPatchApproval":
            result = {"decision": "approved"}
        else:
            raise ValueError(f"unknown server request method {method!r}")
        event = {"method": method, "threadId": params.get("threadId"), "result": result}
        self.events.append(event)
        if self.event_callback:
            self.event_callback(event)
        return result
