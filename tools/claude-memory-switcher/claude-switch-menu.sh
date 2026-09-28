#!/bin/sh
# Linux: run (or double-click in a file manager that runs scripts in a terminal).
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 ./claude_switch.py menu "$@"
  status=$?
  # Double-clicked in a file manager: keep the window open to read the result.
  if [ -t 0 ]; then printf '\nPress Enter to close / 按 Enter 關閉 '; read -r _; fi
  exit $status
else
  echo "Python 3 is required (e.g. sudo apt install python3)."
  exit 1
fi
