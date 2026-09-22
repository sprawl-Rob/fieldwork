#!/bin/zsh
set -e
cd "$(dirname "$0")"
if curl --silent --fail http://127.0.0.1:8765/api/state >/dev/null; then
  open http://127.0.0.1:8765
  exit 0
fi
if [[ -x .venv/bin/python ]]; then
  JOB_PYTHON=".venv/bin/python"
else
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  JOB_PYTHON=".venv/bin/python"
fi
(sleep 2; open http://127.0.0.1:8765) &
exec "$JOB_PYTHON" app.py
