#!/usr/bin/env bash
# Start ICO in DEMO mode (2 demo vCenters + 1 demo NetBackup, throwaway SQLite) for UI checks.
# usage: ICO_PASS='<admin password>' bash .claude/skills/web-layout/scripts/run_demo.sh [port]
# Needs the backend requirements installed (pip install -r backend/requirements.txt).
set -euo pipefail
PORT="${1:-8765}"
: "${ICO_PASS:?set ICO_PASS to the admin password the demo should use (min 10 chars)}"
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
WORK="$(mktemp -d -t ico-demo-XXXXXX)"
mkdir -p "$WORK/files"
cd "$ROOT/backend"
export DATABASE_URL="sqlite:///$WORK/ico.db" SPEND_FILES_DIR="$WORK/files" ADMIN_PASSWORD="$ICO_PASS"
JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
SECRETS_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export JWT_SECRET SECRETS_KEY
for i in 1 2; do export "VCENTER_${i}_HOST=demo$i" "VCENTER_${i}_DEMO=true" "VCENTER_${i}_NAME=DC$i"; done
export NETBACKUP_1_HOST=nbu.demo NETBACKUP_1_DEMO=true NETBACKUP_1_KEY=demo
echo "ICO demo: http://localhost:$PORT  (data: $WORK)" >&2
exec python3 -m uvicorn app.main:app --port "$PORT"
