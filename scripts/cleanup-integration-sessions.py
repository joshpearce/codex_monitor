#!/usr/bin/env python3
"""Delete only disposable canned-approval Codex threads and their pytest projects."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

import websockets

from codex_goal_monitor.protocol import ProtocolClient


PROJECT_PATTERN = re.compile(
    r"^/private/var/folders/[^/]+/[^/]+/T/pytest-of-[^/]+/pytest-\d+/"
    r"test_generic_canned_answer_res\d+/(?:image-converter-project|json-formatter-project)$"
)


def discover() -> dict[str, Path]:
    sessions = Path.home() / ".codex/sessions"
    targets: dict[str, Path] = {}
    for rollout in sessions.rglob("*.jsonl"):
        try:
            first = json.loads(rollout.open().readline())
        except (OSError, ValueError):
            continue
        if first.get("type") != "session_meta":
            continue
        payload = first.get("payload", {})
        cwd = str(payload.get("cwd", ""))
        if PROJECT_PATTERN.fullmatch(cwd):
            targets[str(payload["session_id"])] = Path(cwd)
    return targets


async def main() -> None:
    targets = discover()
    if not targets:
        print("No disposable integration sessions found.")
        return

    async def reject(request):
        raise RuntimeError(f"cleanup cannot answer {request.get('method')}")

    socket = Path.home() / ".codex/app-server-control/app-server-control.sock"
    async with websockets.unix_connect(
        str(socket), uri="ws://localhost/", compression=None,
        open_timeout=10, close_timeout=3, max_size=32 * 1024 * 1024,
    ) as ws:
        client = ProtocolClient(ws, reject)
        await client.initialize()
        for thread_id, project in sorted(targets.items()):
            await client.call("thread/delete", {"threadId": thread_id})
            print(f"Deleted Codex thread {thread_id}")
            if project.exists() and PROJECT_PATTERN.fullmatch(str(project)):
                shutil.rmtree(project)
                print(f"Removed disposable project {project}")


if __name__ == "__main__":
    asyncio.run(main())
