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

export SMS_PROVIDER="${SMS_PROVIDER:-mock}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///./hangout.db}"
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:9000}"

exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload
