#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: this installer is for macOS" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
config_path="${1:-${XDG_CONFIG_HOME:-$HOME/.config}/codex-monitor/projects.toml}"
monitor_bin="${CODEX_GOAL_MONITOR_BIN:-$(command -v codex-goal-monitor || true)}"

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
  exit 0
fi

template="$repo_dir/services/macos/com.codex-goal-monitor.plist.in"
destination="$HOME/Library/LaunchAgents/com.codex-goal-monitor.plist"
log_dir="$HOME/Library/Logs"
mkdir -p "$(dirname "$destination")" "$log_dir"

python3 -c '
import html, pathlib, sys
template, destination, executable, config, log_dir = sys.argv[1:]
text = pathlib.Path(template).read_text()
for key, value in {
    "@EXECUTABLE@": executable,
    "@CONFIG@": config,
    "@LOG_DIR@": log_dir,
}.items():
    text = text.replace(key, html.escape(value, quote=True))
pathlib.Path(destination).write_text(text)
' "$template" "$destination" "$monitor_bin" "$config_path" "$log_dir"

domain="gui/$(id -u)"
launchctl bootout "$domain" "$destination" 2>/dev/null || true
launchctl bootstrap "$domain" "$destination"
launchctl kickstart -k "$domain/com.codex-goal-monitor"
echo "Installed and started $destination"
