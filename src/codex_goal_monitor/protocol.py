from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class ProtocolError(RuntimeError):
    pass


ServerRequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class ProtocolClient:
    ws: Any
    server_request_handler: ServerRequestHandler
    next_id: int = 1
    notifications: list[dict[str, Any]] = field(default_factory=list)

    async def send(self, message: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(message, separators=(",", ":")))

    async def call(self, method: str, params: dict[str, Any], timeout: float = 30) -> Any:
        request_id = self.next_id
        self.next_id += 1
        await self.send({"method": method, "id": request_id, "params": params})
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            message = json.loads(raw)
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise ProtocolError(f"{method}: {json.dumps(message['error'], sort_keys=True)}")
                return message.get("result")
            await self.dispatch(message)

    async def dispatch(self, message: dict[str, Any]) -> None:
        if "method" in message and "id" in message:
            try:
                result = await self.server_request_handler(message)
                await self.send({"id": message["id"], "result": result})
            except Exception as exc:
                await self.send({"id": message["id"], "error": {"code": -32000, "message": str(exc)}})
            return
        self.notifications.append(message)

    async def initialize(self) -> Any:
        result = await self.call("initialize", {
            "clientInfo": {"name": "codex-goal-monitor", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True, "requestAttestation": False},
        })
        await self.send({"method": "initialized"})
        return result

    async def drain(self, seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + seconds
        while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
            try:
                message = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=remaining))
            except asyncio.TimeoutError:
                return
            await self.dispatch(message)
