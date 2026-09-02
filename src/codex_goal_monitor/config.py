from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    thread_id: str
    goal_objective: str | None = None
    ensure_goal_running: bool = True


@dataclass(frozen=True)
class Config:
    projects: tuple[Project, ...]
    socket_path: Path
    state_dir: Path
    codex_command: str = "codex"
    drain_seconds: float = 15.0
    connect_timeout_seconds: float = 10.0
    command_timeout_seconds: float = 30.0
    continuation_cooldown_seconds: int = 240
    approval_policy: str | None = None
    approvals_reviewer: str | None = None
    affirmative_answer: str = (
        "Yes. I authorize the requested action and any necessary follow-up actions "
        "needed to continue this goal. Continue working toward the full goal."
    )


def default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "codex-monitor/projects.toml"


def load_config(path: Path | None = None) -> Config:
    path = (path or default_config_path()).expanduser()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    if raw.get("version") != 1:
        raise ValueError(f"{path}: version must be 1")

    defaults = raw.get("defaults", {})
    projects = []
    for entry in raw.get("project", []):
        project_path = Path(entry["path"]).expanduser().resolve()
        thread_id = str(entry["thread_id"]).strip()
        if not thread_id:
            raise ValueError(f"{path}: project {entry.get('name', project_path)} has an empty thread_id")
        projects.append(Project(
            name=str(entry.get("name", project_path.name)),
            path=project_path,
            thread_id=thread_id,
            goal_objective=entry.get("goal_objective"),
            ensure_goal_running=bool(entry.get("ensure_goal_running", True)),
        ))
    if not projects:
        raise ValueError(f"{path}: configure at least one [[project]]")

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")).expanduser()
    return Config(
        projects=tuple(projects),
        socket_path=Path(raw.get("socket_path", codex_home / "app-server-control/app-server-control.sock")).expanduser(),
        state_dir=Path(raw.get("state_dir", state_home / "codex-monitor")).expanduser(),
        codex_command=str(raw.get("codex_command", "codex")),
        drain_seconds=float(raw.get("drain_seconds", 15)),
        connect_timeout_seconds=float(raw.get("connect_timeout_seconds", 10)),
        command_timeout_seconds=float(raw.get("command_timeout_seconds", 30)),
        continuation_cooldown_seconds=int(raw.get("continuation_cooldown_seconds", 240)),
        # Omitted by default: project-local Codex config remains authoritative.
        approval_policy=defaults.get("approval_policy"),
        approvals_reviewer=defaults.get("approvals_reviewer"),
        affirmative_answer=str(defaults.get("affirmative_answer", Config.__dataclass_fields__["affirmative_answer"].default)),
    )
