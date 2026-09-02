from pathlib import Path

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
