#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_DIR=${JIANGSU_TENDER_MONITOR_RUNTIME_DIR:-"$HOME/Library/Application Support/zhao-biao-pa-chong"}

exec "$PROJECT_DIR/.venv/bin/python" -m tender_monitor.cli dashboard \
  --db "$RUNTIME_DIR/data/tenders.sqlite3" \
  --allow-write \
  --open \
  "$@"
