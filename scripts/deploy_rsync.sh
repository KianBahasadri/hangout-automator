#!/usr/bin/env bash
# Sync local code to an already-provisioned VM and restart the service.
# Usage: ./scripts/deploy_rsync.sh hangout@1.2.3.4
set -euo pipefail
HOST="${1:?usage: deploy_rsync.sh user@host}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

rsync -avz --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.terraform' \
  --exclude '*.tfstate*' \
  --exclude 'hangout.db' \
  --exclude '.env' \
  "$ROOT/" "$HOST:/opt/hangout-automator/"

ssh "$HOST" 'set -e
  cd /opt/hangout-automator
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  sudo systemctl restart hangout-automator
  sudo systemctl --no-pager status hangout-automator | head -20
'
