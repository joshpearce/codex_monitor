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


def test_checked_in_service_templates_and_installers():
    root = Path(__file__).parents[1]
    launchd = (root / "services/macos/com.codex-goal-monitor.plist.in").read_text()
    systemd = (root / "services/systemd/codex-goal-monitor.service.in").read_text()
    timer = (root / "services/systemd/codex-goal-monitor.timer").read_text()
    macos_installer = root / "scripts/install-macos.sh"
    linux_installer = root / "scripts/install-systemd-user.sh"

    assert "<integer>300</integer>" in launchd
    assert "@EXECUTABLE@" in launchd and "@CONFIG@" in launchd
    assert 'ExecStart="@EXECUTABLE@" --config "@CONFIG@" reconcile' in systemd
    assert "OnUnitActiveSec=5min" in timer and "Persistent=true" in timer
    assert macos_installer.stat().st_mode & 0o111
    assert linux_installer.stat().st_mode & 0o111
