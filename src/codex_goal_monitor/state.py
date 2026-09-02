from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import fcntl


class AlreadyRunning(RuntimeError):
    pass


class StateStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.runtime_path = self.directory / "runtime.json"
        self.audit_path = self.directory / "audit.jsonl"

    @contextmanager
    def lock(self) -> Iterator[None]:
        lock_path = self.directory / "reconcile.lock"
        with lock_path.open("a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AlreadyRunning("another reconciliation is running") from exc
            yield

    def load(self) -> dict[str, Any]:
        try:
            return json.loads(self.runtime_path.read_text())
        except FileNotFoundError:
            return {"threads": {}}

    def save(self, value: dict[str, Any]) -> None:
        temporary = self.runtime_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(self.runtime_path)

    def audit(self, event: str, **fields: Any) -> None:
        record = {"time": int(time.time()), "event": event, **fields}
        with self.audit_path.open("a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        os.chmod(self.audit_path, 0o600)
