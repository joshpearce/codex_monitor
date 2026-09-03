from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NOTICE_DEFAULTS = {
    "daemon_unavailable": True,
    "overlapping_run": True,
    "unloaded_thread": True,
    "paused_goal": True,
    "blocked_goal": True,
    "usage_limited_goal": True,
    "budget_limited_goal": True,
    "idle_active_goal": True,
    "active_thread": True,
    "complete_goal": True,
    "missing_or_disabled_goal": True,
    "objective_mismatch": True,
    "natural_language_approval": True,
    "claude_sandbox_auth": True,
    "command_approval": True,
    "file_change_approval": True,
    "permissions_approval": True,
    "user_input": True,
    "mcp_elicitation": True,
    "legacy_exec_approval": True,
    "legacy_patch_approval": True,
    "repeated_blocker": True,
    "orphaned_approval": True,
}


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    thread_id: str | None = None
    goal_objective: str | None = None
    ensure_goal_running: bool = True


@dataclass(frozen=True)
class Config:
    projects: tuple[Project, ...]
    socket_path: Path
    state_dir: Path
    codex_command: str = "codex"
    drain_seconds: float = 15.0
    active_turn_watch_seconds: float = 21600.0
    connect_timeout_seconds: float = 10.0
    transport_reconnect_attempts: int = 3
    transport_reconnect_delay_seconds: float = 1.0
    command_timeout_seconds: float = 30.0
    continuation_cooldown_seconds: int = 240
    orphaned_approval_seconds: int = 300
    approval_policy: str | None = None
    approvals_reviewer: str | None = None
    affirmative_answer: str = (
        "Yes. I authorize the requested action and any necessary follow-up actions "
        "needed to continue this goal. Continue working toward the full goal."
    )
    notices: dict[str, bool] | None = None

    def notice_enabled(self, name: str) -> bool:
        return (self.notices or NOTICE_DEFAULTS).get(name, True)


def default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "codex-monitor/projects.toml"


def migrate_legacy_thread_ids(
    path: Path, config: Config, runtime: dict[str, Any]
) -> bool:
    """Move legacy per-project thread IDs into mutable runtime state.

    Runtime is updated in memory first; the caller must persist it before this
    function rewrites the configuration file.
    """
    legacy = [project for project in config.projects if project.thread_id]
    if not legacy:
        return False
    projects_state = runtime.setdefault("projects", {})
    for project in legacy:
        projects_state.setdefault(str(project.path), {}).setdefault(
            "threadId", project.thread_id
        )

    source = path.expanduser().read_text()
    lines = source.splitlines(keepends=True)
    project_index = -1
    removed = 0
    migrated: list[str] = []
    thread_line = re.compile(r"^\s*thread_id\s*=")
    for line in lines:
        if re.match(r"^\s*\[\[project\]\]\s*(?:#.*)?$", line.rstrip("\r\n")):
            project_index += 1
        if (
            project_index >= 0
            and project_index < len(config.projects)
            and config.projects[project_index].thread_id
            and thread_line.match(line)
        ):
            removed += 1
            continue
        migrated.append(line)
    if removed != len(legacy):
        raise ValueError(
            f"{path}: found {removed} legacy thread_id lines for {len(legacy)} configured projects"
        )
    temporary = path.expanduser().with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(migrated))
    os.chmod(temporary, path.expanduser().stat().st_mode & 0o777)
    temporary.replace(path.expanduser())
    return True


def load_config(path: Path | None = None) -> Config:
    path = (path or default_config_path()).expanduser()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    if raw.get("version") != 1:
        raise ValueError(f"{path}: version must be 1")

    defaults = raw.get("defaults", {})
    configured_notices = raw.get("notices", {})
    unknown_notices = set(configured_notices) - set(NOTICE_DEFAULTS)
    if unknown_notices:
        raise ValueError(f"{path}: unknown [notices] keys: {', '.join(sorted(unknown_notices))}")
    non_boolean_notices = [
        name for name, value in configured_notices.items() if not isinstance(value, bool)
    ]
    if non_boolean_notices:
        raise ValueError(
            f"{path}: [notices] values must be booleans: "
            f"{', '.join(sorted(non_boolean_notices))}"
        )
    notices = {**NOTICE_DEFAULTS, **{
        name: bool(value) for name, value in configured_notices.items()
    }}
    projects = []
    for entry in raw.get("project", []):
        project_path = Path(entry["path"]).expanduser().resolve()
        raw_thread_id = entry.get("thread_id")
        thread_id = str(raw_thread_id).strip() if raw_thread_id is not None else None
        if raw_thread_id is not None and not thread_id:
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
        active_turn_watch_seconds=float(raw.get("active_turn_watch_seconds", 21600)),
        connect_timeout_seconds=float(raw.get("connect_timeout_seconds", 10)),
        transport_reconnect_attempts=int(raw.get("transport_reconnect_attempts", 3)),
        transport_reconnect_delay_seconds=float(
            raw.get("transport_reconnect_delay_seconds", 1)
        ),
        command_timeout_seconds=float(raw.get("command_timeout_seconds", 30)),
        continuation_cooldown_seconds=int(raw.get("continuation_cooldown_seconds", 240)),
        orphaned_approval_seconds=int(raw.get("orphaned_approval_seconds", 300)),
        # Omitted by default: project-local Codex config remains authoritative.
        approval_policy=defaults.get("approval_policy"),
        approvals_reviewer=defaults.get("approvals_reviewer"),
        affirmative_answer=str(defaults.get("affirmative_answer", Config.__dataclass_fields__["affirmative_answer"].default)),
        notices=notices,
    )
