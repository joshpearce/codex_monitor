from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import default_config_path, load_config
from .reconcile import inspect_once, run_once
from .service import install, systemd_units, launchd_plist, uninstall
from .state import AlreadyRunning, StateStore


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="codex-goal-monitor")
    result.add_argument("--config", type=Path, default=default_config_path())
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("reconcile", help="run one bounded reconciliation")
    commands.add_parser("inspect", help="read configured live thread and Goal state without mutation")
    commands.add_parser("status", help="print the most recently persisted state")
    commands.add_parser("install", help="install and enable the current user's timer")
    commands.add_parser("uninstall", help="disable and remove the current user's timer")
    commands.add_parser("print-service", help="print the service definitions for this OS")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "uninstall":
        print("\n".join(map(str, uninstall())))
        return 0
    if args.command == "print-service":
        import platform
        if platform.system() == "Darwin":
            sys.stdout.buffer.write(launchd_plist(args.config.expanduser()))
        else:
            service, timer = systemd_units(args.config.expanduser())
            print(service)
            print(timer)
        return 0

    config = load_config(args.config)
    if args.command == "inspect":
        print(json.dumps(asyncio.run(inspect_once(config)), indent=2, sort_keys=True))
        return 0
    store = StateStore(config.state_dir)
    if args.command == "status":
        print(json.dumps(store.load(), indent=2, sort_keys=True))
        return 0
    if args.command == "install":
        print("\n".join(map(str, install(args.config.expanduser().resolve()))))
        return 0
    try:
        with store.lock():
            reports = asyncio.run(run_once(config, store))
    except AlreadyRunning as exc:
        print(str(exc))
        return 0
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 1 if any("error" in report for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
