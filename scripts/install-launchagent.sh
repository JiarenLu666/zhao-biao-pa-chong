#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_DIR=${JIANGSU_TENDER_MONITOR_RUNTIME_DIR:-"$HOME/Library/Application Support/zhao-biao-pa-chong"}
TEMPLATE="$PROJECT_DIR/scripts/com.jiangsu.tender-monitor.auto-refresh.plist"
TARGET="$HOME/Library/LaunchAgents/com.jiangsu.tender-monitor.auto-refresh.plist"

mkdir -p "$(dirname -- "$TARGET")"
sed "s|__RUNTIME_DIR__|$RUNTIME_DIR|g" "$TEMPLATE" > "$TARGET"
plutil -lint "$TARGET" >/dev/null
launchctl bootout "gui/$(id -u)/com.jiangsu.tender-monitor.auto-refresh" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "LaunchAgent 已安装：$TARGET"
echo "运行副本：$RUNTIME_DIR"
