from pathlib import Path

def test_checked_in_service_templates_and_installers():
    root = Path(__file__).parents[1]
    launchd = (root / "services/macos/com.codex-goal-monitor.plist.in").read_text()
    systemd = (root / "services/systemd/codex-goal-monitor.service.in").read_text()
    timer = (root / "services/systemd/codex-goal-monitor.timer").read_text()
    macos_installer = root / "scripts/install-macos.sh"
    linux_installer = root / "scripts/install-systemd-user.sh"
    macos_uninstaller = root / "scripts/uninstall-macos.sh"
    linux_uninstaller = root / "scripts/uninstall-systemd-user.sh"

    assert "<integer>300</integer>" in launchd
    assert "@EXECUTABLE@" in launchd and "@CONFIG@" in launchd
    assert 'ExecStart="@EXECUTABLE@" --config "@CONFIG@" reconcile' in systemd
    assert "OnUnitActiveSec=5min" in timer and "Persistent=true" in timer
    assert macos_installer.stat().st_mode & 0o111
    assert linux_installer.stat().st_mode & 0o111
    assert macos_uninstaller.stat().st_mode & 0o111
    assert linux_uninstaller.stat().st_mode & 0o111
