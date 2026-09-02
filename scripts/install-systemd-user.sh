#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "error: this installer is for Linux" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
config_path="${1:-${XDG_CONFIG_HOME:-$HOME/.config}/codex-monitor/projects.toml}"
monitor_bin="${CODEX_GOAL_MONITOR_BIN:-$(command -v codex-goal-monitor || true)}"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ -z "$monitor_bin" || ! -x "$monitor_bin" ]]; then
  echo "error: codex-goal-monitor is not executable; install it or set CODEX_GOAL_MONITOR_BIN" >&2
  exit 2
fi
if [[ ! -f "$config_path" ]]; then
  mkdir -p "$(dirname "$config_path")"
  cp "$repo_dir/config/projects.example.toml" "$config_path"
  chmod 600 "$config_path"
  echo "Created starter configuration $config_path"
  echo "Edit its [[project]] entry, then run this installer again." >&2
  exit 2
fi

mkdir -p "$unit_dir"
python3 -c '
import pathlib, sys
template, destination, executable, config = sys.argv[1:]
escape = lambda value: value.replace("\\", "\\\\").replace("\"", "\\\"")
text = pathlib.Path(template).read_text()
text = text.replace("@EXECUTABLE@", escape(executable)).replace("@CONFIG@", escape(config))
pathlib.Path(destination).write_text(text)
' "$repo_dir/services/systemd/codex-goal-monitor.service.in" \
  "$unit_dir/codex-goal-monitor.service" "$monitor_bin" "$config_path"
cp "$repo_dir/services/systemd/codex-goal-monitor.timer" "$unit_dir/codex-goal-monitor.timer"

systemctl --user daemon-reload
systemctl --user enable --now codex-goal-monitor.timer
systemctl --user start codex-goal-monitor.service
echo "Installed and started $unit_dir/codex-goal-monitor.timer"
