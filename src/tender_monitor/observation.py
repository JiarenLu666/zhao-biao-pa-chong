"""从 auto-refresh 的 stdout 日志做纯本地观察分析。

调度器每轮向 ``data/auto-refresh.stdout.log`` 追加一行 JSON
（见 scripts/com.jiangsu.tender-monitor.auto-refresh.plist 的
StandardOutPath）。本模块只读本地文件、不发任何网络请求，用于
回答“最近每天/每小时各采了多少条”这类观察性问题。
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CHINA_TIMEZONE = timezone(timedelta(hours=8))

#: 与 automation.AutomaticCycleResult.finished_at 相同的 ISO 8601 格式。
FINISHED_AT_FORMAT_HINT = "finished_at 需为 ISO 8601 时间戳"


@dataclass(frozen=True)
class CycleLogEntry:
    """一行 auto-refresh JSON 日志的宽松解析结果。"""

    timestamp: datetime | None
    source: str | None
    scope: str | None
    pages_fetched: int
    records_seen: int
    saved_count: int
    stopped_reason: str | None
    finished_at: str | None


@dataclass(frozen=True)
class LogParseReport:
    """解析统计：有效条目 + 被静默跳过的行数。"""

    entries: tuple[CycleLogEntry, ...]
    invalid_lines: int = 0
    missing_finished_at: int = 0


@dataclass(frozen=True)
class BucketSummary:
    """一个时间桶（小时/天）内的聚合计数。"""

    cycles: int = 0
    records_seen: int = 0
    saved_count: int = 0


@dataclass(frozen=True)
class ScopeDayRow:
    """一个“来源 × 自然日”的聚合计数行。"""

    scope: str
    day: str
    cycles: int
    records_seen: int
    saved_count: int


def _parse_int(value: Any) -> int:
    """宽松取整：非数字或缺字段一律记 0。"""

    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return 0
    return 0


def _parse_finished_at(value: Any) -> datetime | None:
    """把 finished_at 解析成带时区的 datetime；解析失败按缺失处理。"""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # 兼容不带时区的历史日志：按中国时区理解。
        parsed = parsed.replace(tzinfo=CHINA_TIMEZONE)
    return parsed.astimezone(CHINA_TIMEZONE)


def parse_log_entries(text: str) -> LogParseReport:
    """逐行解析 auto-refresh stdout 日志。

    非 JSON 行静默跳过并计数；缺少 finished_at 或其不可解析的行
    同样跳过并单独计数——没有时间戳就无法分桶。
    """

    entries: list[CycleLogEntry] = []
    invalid_lines = 0
    missing_finished_at = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if not isinstance(payload, dict):
            invalid_lines += 1
            continue
        timestamp = _parse_finished_at(payload.get("finished_at"))
        if timestamp is None:
            missing_finished_at += 1
            continue
        stopped_reason = payload.get("stopped_reason")
        entries.append(
            CycleLogEntry(
                timestamp=timestamp,
                source=payload.get("source") if isinstance(payload.get("source"), str) else None,
                scope=payload.get("scope") if isinstance(payload.get("scope"), str) else None,
                pages_fetched=_parse_int(payload.get("pages_fetched")),
                records_seen=_parse_int(payload.get("records_seen")),
                saved_count=_parse_int(payload.get("saved_count")),
                stopped_reason=(
                    stopped_reason if isinstance(stopped_reason, str) else None
                ),
                finished_at=payload.get("finished_at")
                if isinstance(payload.get("finished_at"), str)
                else None,
            )
        )
    return LogParseReport(
        entries=tuple(entries),
        invalid_lines=invalid_lines,
        missing_finished_at=missing_finished_at,
    )


def filter_recent_entries(
    entries: tuple[CycleLogEntry, ...],
    days: int,
    *,
    now: datetime | None = None,
) -> tuple[CycleLogEntry, ...]:
    """只保留最近 ``days`` 天（按中国时区自然日）内的条目。"""

    if days < 1:
        raise ValueError("days 必须大于 0")
    reference = now or datetime.now(timezone.utc)
    cutoff = (
        reference.astimezone(CHINA_TIMEZONE) - timedelta(days=days - 1)
    ).replace(hour=0, minute=0, second=0, microsecond=0)
    return tuple(entry for entry in entries if entry.timestamp is not None and entry.timestamp >= cutoff)


def summarize_by_hour(
    entries: tuple[CycleLogEntry, ...],
) -> dict[str, BucketSummary]:
    """按中国时区小时分桶，桶键为 ``YYYY-MM-DD HH:00``。"""

    buckets: dict[str, BucketSummary] = {}
    for entry in entries:
        if entry.timestamp is None:
            continue
        local = entry.timestamp.astimezone(CHINA_TIMEZONE)
        key = local.strftime("%Y-%m-%d %H:00")
        previous = buckets.get(key, BucketSummary())
        buckets[key] = BucketSummary(
            cycles=previous.cycles + 1,
            records_seen=previous.records_seen + entry.records_seen,
            saved_count=previous.saved_count + entry.saved_count,
        )
    return dict(sorted(buckets.items()))


def summarize_by_day(
    entries: tuple[CycleLogEntry, ...],
) -> dict[str, BucketSummary]:
    """按中国时区自然日分桶，桶键为 ``YYYY-MM-DD``。"""

    buckets: dict[str, BucketSummary] = {}
    for entry in entries:
        if entry.timestamp is None:
            continue
        key = entry.timestamp.astimezone(CHINA_TIMEZONE).strftime("%Y-%m-%d")
        previous = buckets.get(key, BucketSummary())
        buckets[key] = BucketSummary(
            cycles=previous.cycles + 1,
            records_seen=previous.records_seen + entry.records_seen,
            saved_count=previous.saved_count + entry.saved_count,
        )
    return dict(sorted(buckets.items()))


def summarize_by_scope(
    entries: tuple[CycleLogEntry, ...],
) -> dict[str, BucketSummary]:
    """按调度范围（scope）聚合全部条目，回答“每个来源合计采了多少”。

    每小时省站轮与泰州轮各写一行日志，仅按时间桶相加分不清来源；
    分源合计是判断“泰州站聚合是否已覆盖县级站”的前提。
    """

    buckets: dict[str, BucketSummary] = {}
    for entry in entries:
        key = entry.scope or entry.source or "unknown"
        previous = buckets.get(key, BucketSummary())
        buckets[key] = BucketSummary(
            cycles=previous.cycles + 1,
            records_seen=previous.records_seen + entry.records_seen,
            saved_count=previous.saved_count + entry.saved_count,
        )
    return dict(sorted(buckets.items()))


def summarize_scope_by_day(
    entries: tuple[CycleLogEntry, ...],
) -> tuple[ScopeDayRow, ...]:
    """按“scope × 自然日”聚合，观察各来源的每日增量趋势。"""

    buckets: dict[tuple[str, str], BucketSummary] = {}
    for entry in entries:
        if entry.timestamp is None:
            continue
        scope = entry.scope or entry.source or "unknown"
        day = entry.timestamp.astimezone(CHINA_TIMEZONE).strftime("%Y-%m-%d")
        key = (scope, day)
        previous = buckets.get(key, BucketSummary())
        buckets[key] = BucketSummary(
            cycles=previous.cycles + 1,
            records_seen=previous.records_seen + entry.records_seen,
            saved_count=previous.saved_count + entry.saved_count,
        )
    rows = [
        ScopeDayRow(
            scope=scope,
            day=day,
            cycles=summary.cycles,
            records_seen=summary.records_seen,
            saved_count=summary.saved_count,
        )
        for (scope, day), summary in sorted(buckets.items())
    ]
    return tuple(rows)


def export_hourly_csv(rows: Mapping[str, BucketSummary], path: str | Path) -> Path:
    """把小时分桶汇总写入 CSV；返回写入路径便于 CLI 展示。"""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(("hour", "cycles", "records_seen", "saved_count"))
        for key, summary in rows.items():
            writer.writerow((key, summary.cycles, summary.records_seen, summary.saved_count))
    return output


def export_scope_csv(rows: tuple[ScopeDayRow, ...], path: str | Path) -> Path:
    """把“来源 × 自然日”汇总写入 CSV；返回写入路径便于 CLI 展示。"""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(("scope", "day", "cycles", "records_seen", "saved_count"))
        for row in rows:
            writer.writerow(
                (row.scope, row.day, row.cycles, row.records_seen, row.saved_count)
            )
    return output


def format_summary_text(
    hourly: Mapping[str, BucketSummary],
    daily: Mapping[str, BucketSummary],
    scope_totals: Mapping[str, BucketSummary] | None = None,
) -> str:
    """生成人类可读的“分天 × 小时”汇总表文本。"""

    lines: list[str] = ["按小时汇总（中国时区 UTC+8）："]
    if not hourly:
        lines.append("  （最近范围内没有可统计的采集轮次）")
    for key, summary in hourly.items():
        lines.append(
            f"  {key}  轮数 {summary.cycles}  看到 {summary.records_seen} 条"
            f"  入库 {summary.saved_count} 条"
        )
    lines.append("按天汇总（中国时区 UTC+8）：")
    for key, summary in daily.items():
        lines.append(
            f"  {key}  轮数 {summary.cycles}  看到 {summary.records_seen} 条"
            f"  入库 {summary.saved_count} 条"
        )
    if scope_totals is not None:
        lines.append("按来源汇总（全部天数合计）：")
        for key, summary in scope_totals.items():
            lines.append(
                f"  {key}  轮数 {summary.cycles}  看到 {summary.records_seen} 条"
                f"  入库 {summary.saved_count} 条"
            )
    return "\n".join(lines)
