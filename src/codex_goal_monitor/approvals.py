from __future__ import annotations

from typing import Any


class AggressiveApprovalHandler:
    """Approve requests for threads the reconciler explicitly manages."""

    def __init__(self, managed_thread_ids: set[str], affirmative_answer: str):
        self.managed_thread_ids = managed_thread_ids
        self.affirmative_answer = affirmative_answer
        self.events: list[dict[str, Any]] = []

    def _managed(self, params: dict[str, Any]) -> bool:
        thread_id = params.get("threadId") or params.get("thread_id") or params.get("conversationId")
        return str(thread_id) in self.managed_thread_ids

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request["method"]
        params = request.get("params", {})
        if not self._managed(params):
            raise ValueError(f"refusing approval for unmanaged thread: {params.get('threadId')}")

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
        self.events.append({"method": method, "threadId": params.get("threadId"), "result": result})
        return result
