#!/bin/bash
# macOS: double-click this file in Finder. It opens Terminal and runs
# "claude_switch.py menu" from the folder it lives in.
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3 is required: https://www.python.org/downloads/  (or run: xcode-select --install)"
else
  "$PY" ./claude_switch.py menu
fi
echo
read -r -p "Press Enter to close / 按 Enter 關閉 " _
