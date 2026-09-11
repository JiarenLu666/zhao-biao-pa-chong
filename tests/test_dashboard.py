import json
import plistlib
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from test_pipeline import make_record

from tender_monitor.dashboard import (
    build_dashboard_payload,
    initialize_project,
    make_dashboard_handler,
    render_dashboard_html,
)
from tender_monitor.ingest import ingest_record
from tender_monitor.storage import TenderDatabase


def test_dashboard_payload_exposes_counts_and_safe_tender_fields(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    with db:
        ingest_record(
            db,
            make_record(
                project_id="JSZC-320100-DASH-MATCH",
                url="https://example.invalid/dashboard-match",
            ),
        )
        ingest_record(
            db,
            make_record(
                project_id="JSZC-320100-DASH-FILTERED",
                title="建筑施工项目",
                url="https://example.invalid/dashboard-filtered",
                content="建筑施工服务。",
            ),
        )

        payload = build_dashboard_payload(db)

    assert payload["summary"]["total"] == 2
    assert payload["summary"]["status_counts"] == {
        "MATCH": 1,
        "OVER_BUDGET": 0,
        "REVIEW": 0,
        "NO_MATCH": 0,
        "FILTERED": 1,
    }
    assert payload["summary"]["verification_counts"] == {
        "UNVERIFIED": 2,
        "VERIFIED": 0,
    }
    assert payload["summary"]["actionable_unverified"] == 1
    assert payload["tenders"][0]["title"] == "建筑施工项目"
    assert payload["tenders"][0]["filter_exclude_matches"] == ["施工", "建筑"]
    assert "raw_payload" not in payload["tenders"][0]


def test_dashboard_today_count_uses_calendar_day_not_latest_published_day(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    with db:
        ingest_record(
            db,
            make_record(
                project_id="JSZC-320100-DASH-YESTERDAY",
                url="https://example.invalid/dashboard-yesterday",
                published_at="2026-09-03 09:00:00",
            ),
        )
        ingest_record(
            db,
            make_record(
                project_id="JSZC-320100-DASH-TODAY",
                url="https://example.invalid/dashboard-today",
                published_at="2026-09-04 09:00:00",
            ),
        )
        payload = build_dashboard_payload(
            db,
            now=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc),
        )

    assert payload["summary"]["today_date"] == "2026-09-04"
    assert payload["summary"]["today_count"] == 1


def test_initialize_project_creates_dirs_and_database(tmp_path):
    db_path = tmp_path / "data" / "tenders.sqlite3"

    first = initialize_project(db_path)
    assert db_path.exists()
    assert first.db_path == db_path
    assert (tmp_path / "data" / "raw").is_dir()
    assert (tmp_path / "data" / "attachments").is_dir()
    assert (tmp_path / "data" / "evidence").is_dir()


def test_dashboard_html_is_self_contained_and_reads_local_api():
    html = render_dashboard_html()

    assert "江苏文化采购公告" in html
    assert "/api/dashboard" in html
    assert "/api/follow-up" in html
    assert "自动同步" in html
    assert "需要人工处理" in html
    assert "/api/attention" in html
    assert "setInterval" in html
    assert "visibilitychange" in html
    assert "UNMATCHED" in html
    assert "已过滤" not in html
    # 漏斗卡片：公告总数 / 今日新增 / 待核验 / 已核验 / 已完成
    assert "公告总数" in html
    assert "今日新增" in html
    assert "待核验" in html
    assert "已核验" in html
    assert "已完成" in html
    assert "预算待确认" in html
    assert "待复核" not in html
    assert "快速视图" in html
    assert "viewbar" in html
    # 行结构：机器状态列 + 跟进独立列（fucell）
    assert "fucell" in html
    assert "暂不跟进" in html
    assert "跟进中" in html
    # 详情抽屉：首次提示可编辑，保存后走本地 /api/verify
    assert "首次核验请先核对" in html
    assert "保存公告详情" in html
    assert "/api/verify" in html
    # 自包含单页：不依赖外部 CDN
    assert "https://" not in html


def test_dashboard_save_action_is_named_and_styled_as_save():
    html = render_dashboard_html()

    assert 'id="saveFu">保存</button>' in html
    assert ".d-acts .primary" in html
    assert "保存跟进" not in html


def test_detail_drawer_layers_above_sticky_header():
    """打开详情时，抽屉和遮罩不能被全局 sticky 顶栏盖住。"""

    html = render_dashboard_html()
    style = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert style is not None

    def z_index(selector):
        match = re.search(rf"{re.escape(selector)}\{{[^}}]*z-index:(\d+)", style.group(1))
        assert match is not None, f"缺少 {selector} 的 z-index"
        return int(match.group(1))

    header_z = z_index("header")
    backdrop_z = z_index(".backdrop")
    drawer_z = z_index(".drawer")
    assert backdrop_z > header_z
    assert drawer_z > backdrop_z


def test_auto_refresh_launch_agent_invokes_shell_explicitly():
    plist_path = Path(__file__).parents[1] / "scripts" / "com.jiangsu.tender-monitor.auto-refresh.plist"
    payload = plistlib.loads(plist_path.read_bytes())

    assert payload["ProgramArguments"] == [
        "/bin/sh",
        "__RUNTIME_DIR__/scripts/run-auto-refresh.sh",
    ]


def test_dashboard_summary_separates_pending_and_verified_follow_up(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    with db:
        record = make_record(
            project_id="JSZC-320100-DASH-VERIFIED",
            url="https://example.invalid/dashboard-verified",
        )
        ingest_record(db, record)
        db.verify_tender(
            record.url,
            project_id=record.project_id,
            budget_yuan=180000,
            budget_raw="18万元",
            verification_source="test",
        )
        payload = build_dashboard_payload(db)

    assert payload["summary"]["actionable_unverified"] == 0
    assert payload["summary"]["verified_actionable"] == 1


def test_dashboard_summary_hides_unverified_not_required_items(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    with db:
        record = make_record(
            project_id="JSZC-320100-DASH-SNOOZED",
            url="https://example.invalid/dashboard-snoozed",
        )
        ingest_record(db, record)
        db.update_follow_up(record.url, status="NOT_REQUIRED", notes="暂时不跟进")
        payload = build_dashboard_payload(db)

    assert payload["summary"]["actionable_unverified"] == 0


def test_dashboard_verify_endpoint_saves_details_and_preserves_follow_up(tmp_path):
    db_path = tmp_path / "tenders.sqlite3"
    record = make_record(
        url="https://example.invalid/dashboard-edit",
        title="摄影服务项目",
        project_id=None,
    )
    with TenderDatabase(db_path) as db:
        ingest_record(db, record)

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_dashboard_handler(db_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/api/verify"

    def post(payload):
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        result = post(
            {
                "url": record.url,
                "project_id": "JSZC-DASH-EDIT",
                "budget_yuan": 9000,
                "budget_raw": "¥9,000",
                "deadline": "2026-09-07 13:00",
                "content": "补充后的公告摘要",
                "notes": "来自 Dashboard 的人工核验",
            }
        )
        assert result["status"] == "VERIFIED"
        with TenderDatabase(db_path) as db:
            db.update_follow_up(record.url, status="NOT_REQUIRED", notes="不跟进")

        post(
            {
                "url": record.url,
                "project_id": "JSZC-DASH-EDIT",
                "budget_yuan": 9000,
                "budget_raw": "¥9,000",
                "content": "再次编辑后的摘要",
                "notes": "二次修改",
            }
        )
        with TenderDatabase(db_path) as db:
            row = db.list_tenders()[0]
        assert row["verification_status"] == "VERIFIED"
        assert row["follow_up_status"] == "NOT_REQUIRED"
        assert row["content"] == "再次编辑后的摘要"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_verified_tender_follow_up_can_move_from_pending_to_done(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    with db:
        record = make_record(
            project_id="JSZC-320100-DASH-FOLLOW",
            url="https://example.invalid/dashboard-follow",
        )
        ingest_record(db, record)
        db.verify_tender(
            record.url,
            project_id=record.project_id,
            budget_yuan=180000,
            budget_raw="18万元",
            verification_source="test",
        )
        row = db.list_tenders()[0]
        assert row["follow_up_status"] == "PENDING"
        payload = build_dashboard_payload(db)
        assert payload["summary"]["verified_actionable"] == 1
        assert payload["summary"]["funnel_counts"] == {
            "pending_verification": 0,
            "verified": 1,
            "completed": 0,
        }

        db.update_follow_up(
            record.url,
            status="DONE",
            notes="已联系采购单位，后续留意结果。",
        )
        row = db.list_tenders()[0]
        payload = build_dashboard_payload(db)

    assert row["follow_up_status"] == "DONE"
    assert row["follow_up_notes"] == "已联系采购单位，后续留意结果。"
    assert payload["summary"]["verified_actionable"] == 0
    assert payload["summary"]["funnel_counts"]["completed"] == 1


def test_dashboard_javascript_is_syntax_valid():
    node = shutil.which("node")
    if node is None:
        pytest.skip("需要 Node.js 才能校验内嵌 Dashboard 脚本")
    script = re.search(r"<script>(.*?)</script>", render_dashboard_html(), re.DOTALL)
    assert script is not None
    result = subprocess.run(
        [node, "--check", "-"],
        input=script.group(1),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
