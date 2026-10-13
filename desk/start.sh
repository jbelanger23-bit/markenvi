#!/usr/bin/env bash
# desk launcher for mac / linux. ./start.sh
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: creating a virtual environment and installing dependencies..."
  python3 -m venv .venv && .venv/bin/python -m pip install --quiet -r requirements.txt || exit 1
fi
exec .venv/bin/python server.py "$@"
