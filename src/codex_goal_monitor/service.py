from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


def executable() -> str:
    return shutil.which("codex-goal-monitor") or str(Path(sys.argv[0]).resolve())


def launchd_plist(config_path: Path) -> bytes:
    home = Path.home()
    return plistlib.dumps({
        "Label": "com.codex-goal-monitor",
        "ProgramArguments": [executable(), "--config", str(config_path), "reconcile"],
        "RunAtLoad": True,
        "StartInterval": 300,
        "ProcessType": "Background",
        "StandardOutPath": str(home / "Library/Logs/codex-goal-monitor.log"),
        "StandardErrorPath": str(home / "Library/Logs/codex-goal-monitor.error.log"),
    }, sort_keys=True)


def systemd_units(config_path: Path) -> tuple[str, str]:
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    service = f"""[Unit]
Description=Reconcile configured Codex Goals

[Service]
Type=oneshot
ExecStart={quote(executable())} --config {quote(str(config_path))} reconcile
TimeoutStartSec=2min
"""
    timer = """[Unit]
Description=Reconcile Codex Goals every five minutes

[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
Persistent=true
RandomizedDelaySec=10s

[Install]
WantedBy=timers.target
"""
    return service, timer


def install(config_path: Path) -> list[Path]:
    if platform.system() == "Darwin":
        destination = Path.home() / "Library/LaunchAgents/com.codex-goal-monitor.plist"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(launchd_plist(config_path))
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(destination)], check=False)
        subprocess.run(["launchctl", "bootstrap", domain, str(destination)], check=True)
        return [destination]
    destination = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
    destination.mkdir(parents=True, exist_ok=True)
    service_path = destination / "codex-goal-monitor.service"
    timer_path = destination / "codex-goal-monitor.timer"
    service, timer = systemd_units(config_path)
    service_path.write_text(service)
    timer_path.write_text(timer)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", timer_path.name], check=True)
    return [service_path, timer_path]


def uninstall() -> list[Path]:
    if platform.system() == "Darwin":
        destination = Path.home() / "Library/LaunchAgents/com.codex-goal-monitor.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(destination)], check=False)
        if destination.exists():
            destination.unlink()
        return [destination]
    destination = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
    paths = [destination / "codex-goal-monitor.service", destination / "codex-goal-monitor.timer"]
    subprocess.run(["systemctl", "--user", "disable", "--now", paths[1].name], check=False)
    for path in paths:
        if path.exists():
            path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return paths
