#!/usr/bin/env bash
# Run JSpeak in the foreground (logs to terminal). Ctrl+C to stop.
set -euo pipefail
cd "$(dirname "$0")"
export YDOTOOL_SOCKET="${YDOTOOL_SOCKET:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/.ydotool_socket}"
exec python3 app.py
