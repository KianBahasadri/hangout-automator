#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/uvicorn ]; then
  if command -v uv >/dev/null 2>&1; then
    # Prefer 3.12 — system 3.14 may lack pydantic wheels
    uv python install 3.12 >/dev/null 2>&1 || true
    uv venv --clear --python 3.12 .venv
    uv pip install -r requirements.txt
  else
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
  fi
fi

exec .venv/bin/python -m app.dev
