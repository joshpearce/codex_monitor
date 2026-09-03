from pathlib import Path

import pytest

from codex_goal_monitor.config import load_config, migrate_legacy_thread_ids


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


def test_project_thread_id_is_optional_for_project_keyed_startup(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
orphaned_approval_seconds = 600
[[project]]
name = "demo"
path = "/tmp/demo"
goal_objective = "goal.md"
''')
    config = load_config(config_path)
    assert config.projects[0].thread_id is None
    assert config.orphaned_approval_seconds == 600


def test_migrates_legacy_thread_ids_to_runtime_and_preserves_config(tmp_path: Path):
    config_path = tmp_path / "projects.toml"
    config_path.write_text('''
version = 1
# Keep this comment.
[[project]]
name = "one"
path = "/tmp/one"
thread_id = "thread-1"
goal_objective = "one.md"

[[project]]
name = "two"
path = "/tmp/two"
goal_objective = "two.md"
''')
    config = load_config(config_path)
    runtime = {"threads": {}}

    assert migrate_legacy_thread_ids(config_path, config, runtime) is True

    rewritten = config_path.read_text()
    assert "thread_id" not in rewritten
    assert "# Keep this comment." in rewritten
    assert 'goal_objective = "one.md"' in rewritten
    assert runtime["projects"][str(config.projects[0].path)]["threadId"] == "thread-1"
    assert load_config(config_path).projects[0].thread_id is None


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
