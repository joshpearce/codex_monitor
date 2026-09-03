from pathlib import Path

import pytest

from codex_goal_monitor.config import load_config


def test_load_config_preserves_project_approval_settings_by_default(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
[[project]]
name = "demo"
path = "/tmp/demo"
thread_id = "thread-1"
goal_objective = "goal.md"
''')
    config = load_config(config_path)
    assert config.approval_policy is None
    assert config.approvals_reviewer is None
    assert config.active_turn_watch_seconds == 21600
    assert config.transport_reconnect_attempts == 3
    assert config.projects[0].thread_id == "thread-1"


def test_load_explicit_aggressive_overrides(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
[defaults]
approval_policy = "never"
approvals_reviewer = "auto_review"
[[project]]
path = "/tmp/demo"
thread_id = "thread-1"
''')
    config = load_config(config_path)
    assert config.approval_policy == "never"
    assert config.approvals_reviewer == "auto_review"


def test_load_active_turn_watch_seconds(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
active_turn_watch_seconds = 900
[[project]]
path = "/tmp/demo"
thread_id = "thread-1"
''')
    assert load_config(config_path).active_turn_watch_seconds == 900


def test_load_transport_reconnect_settings(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
transport_reconnect_attempts = 5
transport_reconnect_delay_seconds = 0.25
[[project]]
path = "/tmp/demo"
thread_id = "thread-1"
''')
    config = load_config(config_path)
    assert config.transport_reconnect_attempts == 5
    assert config.transport_reconnect_delay_seconds == 0.25


def test_load_notice_overrides(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
[notices]
blocked_goal = false
command_approval = false
[[project]]
path = "/tmp/demo"
thread_id = "thread-1"
''')
    config = load_config(config_path)
    assert config.notice_enabled("blocked_goal") is False
    assert config.notice_enabled("command_approval") is False
    assert config.notice_enabled("paused_goal") is True


def test_rejects_unknown_notice(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
[notices]
surprise = false
[[project]]
path = "/tmp/demo"
thread_id = "thread-1"
''')
    with pytest.raises(ValueError, match="unknown.*surprise"):
        load_config(config_path)


def test_rejects_non_boolean_notice(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
[notices]
blocked_goal = "false"
[[project]]
path = "/tmp/demo"
thread_id = "thread-1"
''')
    with pytest.raises(ValueError, match="must be booleans.*blocked_goal"):
        load_config(config_path)
