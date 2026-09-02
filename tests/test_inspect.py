import pytest

from codex_goal_monitor import reconcile
from codex_goal_monitor.config import Config, Project


class FakeConnection:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return None


class FakeProtocolClient:
    def __init__(self, ws, handler):
        pass

    async def initialize(self):
        return {}

    async def call(self, method, params):
        if method == "thread/loaded/list":
            return {"data": ["thread-1"]}
        if method == "thread/read":
            return {"thread": {"status": {"type": "idle"}}}
        if method == "thread/goal/get":
            return {"goal": {"objective": "goal.md", "status": "blocked"}}
        raise AssertionError(method)


@pytest.mark.asyncio
async def test_inspect_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(reconcile.websockets, "unix_connect", lambda *a, **k: FakeConnection())
    monkeypatch.setattr(reconcile, "ProtocolClient", FakeProtocolClient)
    config = Config(
        projects=(Project("demo", tmp_path, "thread-1", "goal.md"),),
        socket_path=tmp_path / "socket",
        state_dir=tmp_path / "state",
    )
    result = await reconcile.inspect_once(config)
    assert result[0]["loaded"] is True
    assert result[0]["goal"]["status"] == "blocked"
