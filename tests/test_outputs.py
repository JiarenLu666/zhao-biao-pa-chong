import csv
import json
import sys

from test_pipeline import make_record

from tender_monitor.cli import main
from tender_monitor.ingest import ingest_record
from tender_monitor.pdf import extract_pdf_text
from tender_monitor.report import export_csv, export_json, export_review_queue
from tender_monitor.storage import TenderDatabase


def test_pdf_extractor_marks_missing_file_without_crashing(tmp_path):
    result = extract_pdf_text(tmp_path / "missing.pdf")
    assert result.status == "ERROR"
    assert result.text == ""


def test_reports_export_database_rows(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    with db:
        db.upsert(make_record())
        csv_path = tmp_path / "report.csv"
        json_path = tmp_path / "report.json"
        export_csv(db, csv_path)
        export_json(db, json_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["title"] == "摄影设备采购项目"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload[0]["project_id"] == "JSZC-320100-TEST-G2026-0001"


def test_review_queue_exports_only_unverified_actionable_rows(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    with db:
        ingest_record(
            db,
            make_record(
                url="https://example.invalid/review",
                project_id="JSZC-320100-REVIEW",
            )
        )
        ingest_record(
            db,
            make_record(
                title="建筑施工项目",
                url="https://example.invalid/filtered",
                project_id="JSZC-320100-FILTERED",
                budget_yuan=100000,
                budget_raw="10万元",
                budget_status="NORMAL",
                content="建筑施工服务。",
            )
        )
        ingest_record(
            db,
            make_record(
                title="暂不跟进的摄影服务项目",
                url="https://example.invalid/review-snoozed",
                project_id="JSZC-320100-REVIEW-SNOOZED",
            ),
        )
        db.update_follow_up(
            "https://example.invalid/review-snoozed",
            status="NOT_REQUIRED",
            notes="暂时不跟进",
        )
        verified_url = "https://example.invalid/verified"
        ingest_record(
            db,
            make_record(
                url=verified_url,
                project_id="JSZC-320100-VERIFIED-OLD",
            )
        )
        db.verify_tender(
            verified_url,
            project_id="JSZC-320100-VERIFIED",
            budget_yuan=180000,
            budget_raw="18万元",
            verification_source="test",
        )
        path = tmp_path / "review.csv"
        export_review_queue(db, path)

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["url"] for row in rows] == ["https://example.invalid/review"]
    assert rows[0]["verification_status"] == "UNVERIFIED"


def test_refresh_command_generates_all_local_outputs(tmp_path, monkeypatch):
    db_path = tmp_path / "tenders.sqlite3"
    with TenderDatabase(db_path) as db:
        ingest_record(
            db,
            make_record(
                project_id="JSZC-320100-REFRESH",
                url="https://example.invalid/refresh",
            ),
        )
    report_path = tmp_path / "report.csv"
    digest_path = tmp_path / "digest.txt"
    review_path = tmp_path / "review.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tender-monitor",
            "refresh",
            "--db",
            str(db_path),
            "--report-output",
            str(report_path),
            "--digest-output",
            str(digest_path),
            "--review-output",
            str(review_path),
        ],
    )

    main()

    assert report_path.exists()
    assert "摄影设备采购项目" in report_path.read_text(encoding="utf-8-sig")
    assert "摄影设备采购项目" in digest_path.read_text(encoding="utf-8")
    assert "摄影设备采购项目" in review_path.read_text(encoding="utf-8-sig")


def test_notify_pushplus_command_uses_environment_token(tmp_path, monkeypatch):
    db_path = tmp_path / "tenders.sqlite3"
    with TenderDatabase(db_path) as db:
        ingest_record(db, make_record(url="https://example.invalid/notify"))

    sent = {}

    def fake_send(text, token, *, title, channel, option=None):
        sent.update(text=text, token=token, title=title, channel=channel, option=option)

    monkeypatch.setenv("PUSHPLUS_TOKEN", "test-token")
    monkeypatch.setattr("tender_monitor.cli.send_pushplus_message", fake_send)
    monkeypatch.setattr(
        sys,
        "argv",
        ["tender-monitor", "notify-pushplus", "--db", str(db_path)],
    )

    main()

    assert sent["token"] == "test-token"
    assert sent["channel"] == "wechat"
    assert "摄影设备采购项目" in sent["text"]


def test_notify_pushplus_command_skips_when_no_actionable_rows(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "tenders.sqlite3"
    with TenderDatabase(db_path) as db:
        ingest_record(
            db,
            make_record(
                title="建筑施工噪声项目",
                url="https://example.invalid/no-hit",
                content="建筑施工服务。",
                project_id="JSZC-320100-NO-HIT",
            ),
        )

    monkeypatch.setenv("PUSHPLUS_TOKEN", "test-token")

    def fail_send(*args, **kwargs):
        raise AssertionError("没有命中时不应发送通知")

    monkeypatch.setattr("tender_monitor.cli.send_pushplus_message", fail_send)
    monkeypatch.setattr(
        sys,
        "argv",
        ["tender-monitor", "notify-pushplus", "--db", str(db_path)],
    )

    main()

    assert "没有命中目标，未发送通知" in capsys.readouterr().out


def test_notification_delivery_deduplicates_by_tender_and_channel(tmp_path):
    db_path = tmp_path / "tenders.sqlite3"
    with TenderDatabase(db_path) as db:
        result = ingest_record(db, make_record(url="https://example.invalid/serverchan"))
        first = db.list_unnotified_actionable(channel="serverchan")
        assert [row["id"] for row in first] == [result.tender_id]

        assert db.mark_notifications_sent([result.tender_id], channel="serverchan") == 1
        assert db.list_unnotified_actionable(channel="serverchan") == []
        assert db.mark_notifications_sent([result.tender_id], channel="serverchan") == 0

        # 另一个通知渠道有独立的发送账本，不会被 Server酱记录吞掉。
        assert [row["id"] for row in db.list_unnotified_actionable(channel="pushplus")] == [
            result.tender_id
        ]


def test_notify_serverchan_marks_rows_only_after_success(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "tenders.sqlite3"
    with TenderDatabase(db_path) as db:
        ingest_record(db, make_record(url="https://example.invalid/serverchan-cli"))

    sent = {}

    def fake_send(text, sendkey, *, title):
        sent.update(text=text, sendkey=sendkey, title=title)

    monkeypatch.setenv("SERVERCHAN_SENDKEY", "SCT-test-key")
    monkeypatch.setattr("tender_monitor.cli.send_serverchan_message", fake_send)
    monkeypatch.setattr(
        sys,
        "argv",
        ["tender-monitor", "notify-serverchan", "--db", str(db_path)],
    )

    main()
    assert sent["sendkey"] == "SCT-test-key"
    assert "摄影设备采购项目" in sent["text"]
    assert "已记录 1 条新命中" in capsys.readouterr().out

    with TenderDatabase(db_path) as db:
        assert db.list_unnotified_actionable(channel="serverchan") == []
