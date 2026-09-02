from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import default_config_path, load_config
from .reconcile import inspect_once, run_once
from .state import AlreadyRunning, StateStore


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="codex-goal-monitor")
    result.add_argument("--config", type=Path, default=default_config_path())
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("reconcile", help="run one bounded reconciliation")
    commands.add_parser("inspect", help="read configured live thread and Goal state without mutation")
    commands.add_parser("status", help="print the most recently persisted state")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "inspect":
        print(json.dumps(asyncio.run(inspect_once(config)), indent=2, sort_keys=True))
        return 0
    store = StateStore(config.state_dir)
    if args.command == "status":
        print(json.dumps(store.load(), indent=2, sort_keys=True))
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
