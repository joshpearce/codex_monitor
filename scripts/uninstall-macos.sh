#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: this uninstaller is for macOS" >&2
  exit 2
fi

destination="$HOME/Library/LaunchAgents/com.codex-goal-monitor.plist"
domain="gui/$(id -u)"

if [[ -f "$destination" ]]; then
  launchctl bootout "$domain" "$destination" 2>/dev/null || true
  rm -f "$destination"
  echo "Removed $destination"
else
  echo "Not installed: $destination"
fi
