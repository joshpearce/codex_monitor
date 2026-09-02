import plistlib
from pathlib import Path

from codex_goal_monitor.service import launchd_plist, systemd_units


def test_launchd_runs_every_five_minutes():
    plist = plistlib.loads(launchd_plist(Path("/tmp/projects.toml")))
    assert plist["StartInterval"] == 300
    assert plist["RunAtLoad"] is True


def test_systemd_timer_runs_every_five_minutes():
    service, timer = systemd_units(Path("/tmp/projects.toml"))
    assert "Type=oneshot" in service
    assert "OnUnitActiveSec=5min" in timer
    assert "Persistent=true" in timer
