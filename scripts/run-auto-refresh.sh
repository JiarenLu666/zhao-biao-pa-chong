#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
PYTHON="$PROJECT_DIR/.venv/bin/python"

"$PYTHON" -m tender_monitor.cli auto-refresh \
  --db "$PROJECT_DIR/data/tenders.sqlite3" \
  --scope jiangsu \
  --snapshot-dir "$PROJECT_DIR/data/raw/okcis-auto" \
  --report-output "$PROJECT_DIR/data/report.csv" \
  --digest-output "$PROJECT_DIR/data/digest.txt" \
  --review-output "$PROJECT_DIR/data/review.csv"

if [ -n "${SERVERCHAN_SENDKEY:-}" ]; then
  exec "$PYTHON" -m tender_monitor.cli notify-serverchan \
    --db "$PROJECT_DIR/data/tenders.sqlite3"
fi

if [ -n "${PUSHPLUS_TOKEN:-}" ]; then
  exec "$PYTHON" -m tender_monitor.cli notify-pushplus \
    --db "$PROJECT_DIR/data/tenders.sqlite3"
fi
