#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "error: this uninstaller is for Linux" >&2
  exit 2
fi

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
service_path="$unit_dir/codex-goal-monitor.service"
timer_path="$unit_dir/codex-goal-monitor.timer"

systemctl --user disable --now codex-goal-monitor.timer 2>/dev/null || true
systemctl --user stop codex-goal-monitor.service 2>/dev/null || true
rm -f "$service_path" "$timer_path"
systemctl --user daemon-reload
systemctl --user reset-failed codex-goal-monitor.service 2>/dev/null || true
echo "Removed $service_path and $timer_path"
