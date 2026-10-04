#!/usr/bin/env sh
# Start JARVIS on macOS or Linux (first run installs everything).
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Setting up JARVIS for the first time..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m jarvis
