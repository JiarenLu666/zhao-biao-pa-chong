"""analyze-log 观察子命令的离线测试。"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest

from tender_monitor.cli import main as cli_main
from tender_monitor.observation import (
    CycleLogEntry,
    export_hourly_csv,
    export_scope_csv,
    filter_recent_entries,
    parse_log_entries,
    summarize_by_day,
    summarize_by_hour,
    summarize_by_scope,
    summarize_scope_by_day,
)


def _line(
    *,
    finished_at: str = "2026-09-15T03:00:05+00:00",
    source: str = "okcis_jiangsu",
    scope: str = "jiangsu",
    pages_fetched: int = 1,
    records_seen: int = 50,
    saved_count: int = 3,
    stopped_reason: str | None = "达到本次页数上限",
) -> str:
    return json.dumps(
        {
            "source": source,
            "scope": scope,
            "pages_fetched": pages_fetched,
            "records_seen": records_seen,
            "saved_count": saved_count,
            "stopped_reason": stopped_reason,
            "finished_at": finished_at,
        },
        ensure_ascii=False,
    )


def test_parse_log_entries_skips_bad_lines_and_counts_them():
    text = "\n".join(
        [
            "这不是 JSON",
            _line(),
            "[1, 2, 3]",
            _line(finished_at="2026-09-15T04:00:05+00:00", source="okcis_taizhou", scope="taizhou"),
            json.dumps({"source": "okcis_jiangsu"}),  # 缺 finished_at
            _line(finished_at="not-a-time"),  # finished_at 不可解析
        ]
    )

    report = parse_log_entries(text)

    assert len(report.entries) == 2
    assert report.invalid_lines == 2
    assert report.missing_finished_at == 2
    first = report.entries[0]
    assert isinstance(first, CycleLogEntry)
    assert first.source == "okcis_jiangsu"
    assert first.scope == "jiangsu"
    assert first.pages_fetched == 1
    assert first.records_seen == 50
    assert first.saved_count == 3
    assert first.stopped_reason == "达到本次页数上限"
    assert first.timestamp is not None
    # UTC+00:00 转成中国时区 UTC+8 后是 11 点。
    assert first.timestamp.hour == 11


def test_parse_log_entries_tolerates_missing_optional_fields():
    text = json.dumps(
        {
            "finished_at": "2026-09-15T04:00:05+00:00",
            "records_seen": "70",  # 数字写成字符串也能宽松解析
            "saved_count": -5,  # 负数记 0
        }
    )

    report = parse_log_entries(text)

    assert len(report.entries) == 1
    entry = report.entries[0]
    assert entry.source is None
    assert entry.scope is None
    assert entry.records_seen == 70
    assert entry.saved_count == 0
    assert entry.stopped_reason is None


def test_summaries_bucket_in_china_timezone():
    report = parse_log_entries(
        "\n".join(
            [
                _line(finished_at="2026-09-14T16:30:00+00:00"),  # 北京 15 日 00:30
                _line(finished_at="2026-09-15T03:00:05+00:00"),  # 北京 15 日 11:00
                _line(finished_at="2026-09-15T03:30:05+00:00"),  # 北京 15 日 11:30
            ]
        )
    )

    hourly = summarize_by_hour(report.entries)
    daily = summarize_by_day(report.entries)

    assert list(hourly) == ["2026-09-15 00:00", "2026-09-15 11:00"]
    assert hourly["2026-09-15 00:00"].cycles == 1
    assert hourly["2026-09-15 11:00"].cycles == 2
    assert hourly["2026-09-15 11:00"].records_seen == 100
    assert hourly["2026-09-15 11:00"].saved_count == 6
    assert list(daily) == ["2026-09-15"]
    assert daily["2026-09-15"].cycles == 3
    assert daily["2026-09-15"].saved_count == 9


def test_filter_recent_entries_keeps_only_recent_days():
    report = parse_log_entries(
        "\n".join(
            [
                _line(finished_at="2026-09-01T00:00:00+00:00"),
                _line(finished_at="2026-09-13T00:00:00+00:00"),
                _line(finished_at="2026-09-15T00:00:00+00:00"),
            ]
        )
    )
    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

    recent = filter_recent_entries(report.entries, 3, now=now)

    assert len(recent) == 2
    with pytest.raises(ValueError):
        filter_recent_entries(report.entries, 0, now=now)


def test_export_hourly_csv_writes_bucket_rows(tmp_path):
    entries = (
        CycleLogEntry(
            timestamp=datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc),
            source="okcis_jiangsu",
            scope="jiangsu",
            pages_fetched=1,
            records_seen=50,
            saved_count=3,
            stopped_reason=None,
            finished_at="2026-09-15T03:00:05+00:00",
        ),
    )
    hourly = summarize_by_hour(entries)
    output = tmp_path / "out" / "observe-report.csv"

    written = export_hourly_csv(hourly, output)

    assert written == output
    with output.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["hour"] == "2026-09-15 19:00"
    assert rows[0]["cycles"] == "1"
    assert rows[0]["records_seen"] == "50"
    assert rows[0]["saved_count"] == "3"


def test_analyze_log_cli_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / "data" / "auto-refresh.stdout.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join(
            [
                _line(finished_at="2026-09-15T03:00:05+00:00"),
                "坏行",
                json.dumps({"source": "okcis_jiangsu"}),
            ]
        ),
        encoding="utf-8",
    )

    cli_main(
        [
            "analyze-log",
            "--log",
            str(log_path),
            "--output",
            str(tmp_path / "out" / "observe-report.csv"),
            "--days",
            "7",
        ]
    )

    captured = capsys.readouterr().out
    assert "按小时汇总" in captured
    assert "2026-09-15 11:00" in captured
    assert "共解析 1 条有效记录" in captured
    assert (tmp_path / "out" / "observe-report.csv").exists()


def test_analyze_log_cli_reports_missing_log_file(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        cli_main(["analyze-log", "--log", str(tmp_path / "nope.log")])

    assert "日志文件不存在" in capsys.readouterr().err


def test_summarize_by_scope_and_scope_by_day_separate_sources():
    """分源汇总：省站轮与泰州轮不能混在一起，否则无法判断来源价值。"""

    report = parse_log_entries(
        "\n".join(
            [
                _line(
                    finished_at="2026-09-15T03:00:05+00:00",
                    source="okcis_jiangsu",
                    scope="jiangsu",
                    records_seen=50,
                    saved_count=3,
                ),
                _line(
                    finished_at="2026-09-15T03:10:05+00:00",
                    source="okcis_taizhou",
                    scope="taizhou",
                    records_seen=30,
                    saved_count=5,
                ),
                _line(
                    finished_at="2026-09-16T03:00:05+00:00",
                    source="okcis_taizhou",
                    scope="taizhou",
                    records_seen=20,
                    saved_count=1,
                ),
            ]
        )
    )

    scope_totals = summarize_by_scope(report.entries)
    assert set(scope_totals) == {"jiangsu", "taizhou"}
    assert scope_totals["jiangsu"].records_seen == 50
    assert scope_totals["jiangsu"].cycles == 1
    assert scope_totals["taizhou"].records_seen == 50
    assert scope_totals["taizhou"].saved_count == 6
    assert scope_totals["taizhou"].cycles == 2

    rows = summarize_scope_by_day(report.entries)
    assert [(row.scope, row.day) for row in rows] == [
        ("jiangsu", "2026-09-15"),
        ("taizhou", "2026-09-15"),
        ("taizhou", "2026-09-16"),
    ]
    assert rows[1].records_seen == 30
    assert rows[2].records_seen == 20


def test_export_scope_csv_writes_scope_day_rows(tmp_path):
    report = parse_log_entries(
        "\n".join(
            [
                _line(finished_at="2026-09-15T03:00:05+00:00"),
                _line(
                    finished_at="2026-09-15T03:10:05+00:00",
                    source="okcis_taizhou",
                    scope="taizhou",
                    records_seen=30,
                    saved_count=5,
                ),
            ]
        )
    )
    output = tmp_path / "out" / "observe-by-scope.csv"

    written = export_scope_csv(summarize_scope_by_day(report.entries), output)

    assert written == output
    with output.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert [(row["scope"], row["day"]) for row in rows] == [
        ("jiangsu", "2026-09-15"),
        ("taizhou", "2026-09-15"),
    ]
    assert rows[1]["records_seen"] == "30"


def test_analyze_log_cli_exports_by_scope_csv(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / "data" / "auto-refresh.stdout.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join(
            [
                _line(finished_at="2026-09-15T03:00:05+00:00"),
                _line(
                    finished_at="2026-09-15T03:10:05+00:00",
                    source="okcis_taizhou",
                    scope="taizhou",
                    records_seen=30,
                    saved_count=5,
                ),
            ]
        ),
        encoding="utf-8",
    )

    cli_main(
        [
            "analyze-log",
            "--log",
            str(log_path),
            "--output",
            str(tmp_path / "out" / "observe-report.csv"),
            "--by-scope-output",
            str(tmp_path / "out" / "observe-by-scope.csv"),
            "--days",
            "7",
        ]
    )

    captured = capsys.readouterr().out
    assert "按来源汇总" in captured
    assert "taizhou  轮数 1  看到 30 条  入库 5 条" in captured
    assert "分来源日汇总已写入" in captured
    with (tmp_path / "out" / "observe-by-scope.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
