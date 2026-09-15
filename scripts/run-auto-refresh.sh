#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
PYTHON="$PROJECT_DIR/.venv/bin/python"

# 每小时一轮：采集江苏 13 个地级市 OKCIS 市级站（省站已退役，不再调度；
# 每个市级站均聚合下属区县内容）。轮末自动清理超过 7 天的旧公告与快照
# （retention_days=7）。

"$PYTHON" -m tender_monitor.cli auto-refresh \
  --db "$PROJECT_DIR/data/tenders.sqlite3" \
  --scope cities13 \
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
