import json

import pytest

from codex_goal_monitor.protocol import ProtocolClient


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = iter(incoming)
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        return json.dumps(next(self.incoming))


@pytest.mark.asyncio
async def test_call_dispatches_server_request_before_correlated_response():
    socket = FakeSocket([
        {"id": 99, "method": "approval", "params": {"threadId": "t"}},
        {"id": 1, "result": {"ok": True}},
    ])

    async def approve(request):
        return {"decision": "accept"}

    client = ProtocolClient(socket, approve)
    result = await client.call("thread/read", {"threadId": "t"})
    assert result == {"ok": True}
    assert socket.sent == [
        {"method": "thread/read", "id": 1, "params": {"threadId": "t"}},
        {"id": 99, "result": {"decision": "accept"}},
    ]
