import json

from tender_monitor.dedupe import duplicate_key, normalize_title, titles_similar
from tender_monitor.filtering import FilterDecision, evaluate_record
from tender_monitor.ingest import ingest_record
from tender_monitor.models import TenderRecord
from tender_monitor.storage import TenderDatabase


def make_record(**changes):
    values = {
        "title": "摄影设备采购项目",
        "source": "ccgp_jiangsu",
        "url": "https://example.invalid/detail/1",
        "project_id": "JSZC-320100-TEST-G2026-0001",
        "published_at": "2026-09-02 09:00:00",
        "budget_yuan": 180000,
        "budget_raw": "18万元",
        "budget_status": "NORMAL",
        "content": "为公共文化活动提供摄影、图片和影像服务。",
    }
    values.update(changes)
    return TenderRecord(**values)


def test_photography_equipment_is_not_rejected_by_broad_equipment_word():
    decision = evaluate_record(make_record())
    assert decision.status == "MATCH"
    assert "摄影" in decision.include_matches
    assert not decision.exclude_matches


def test_video_recording_is_a_target_service():
    decision = evaluate_record(make_record(title="班主任基本功展示视频录制服务比选公告"))
    assert decision.status == "MATCH"
    assert "视频" in decision.include_matches


def test_video_equipment_result_is_filtered_but_photography_equipment_stays():
    decision = evaluate_record(
        make_record(
            title="视频会议系统设备采购公开招标中标结果公告",
            announcement_type="zbgg",
        )
    )
    assert decision.status == "FILTERED"
    assert "公告类型:zbgg" in decision.exclude_matches
    photography = evaluate_record(make_record(title="摄影设备采购项目"))
    assert photography.status == "MATCH"


def test_contract_and_maintenance_announcements_are_not_actionable():
    decision = evaluate_record(
        make_record(
            title="会场音视频设备维保服务合同",
            announcement_type="htgg",
        )
    )
    assert decision.status == "FILTERED"
    assert "设备维保" in decision.exclude_matches
    assert "公告类型:htgg" in decision.exclude_matches


def test_medical_imaging_maintenance_is_not_photography_service():
    decision = evaluate_record(
        make_record(title="X射线计算机断层摄影系统维保服务采购公告")
    )
    assert decision.status == "FILTERED"
    assert "维保" in decision.exclude_matches


def test_terminated_target_announcement_is_not_actionable():
    decision = evaluate_record(
        make_record(
            title="视频拍摄服务终止公告",
            budget_yuan=None,
            budget_raw=None,
            budget_status="UNKNOWN",
        )
    )
    assert decision.status == "FILTERED"
    assert "终止公告" in decision.exclude_matches


def test_agency_name_containing_engineering_does_not_filter_target_project():
    decision = evaluate_record(
        make_record(content="摄影服务采购。代理机构：某某工程技术有限公司。")
    )
    assert decision.status == "MATCH"
    assert "工程" not in decision.exclude_matches


def test_promotion_only_in_body_requires_more_signal():
    decision = evaluate_record(
        make_record(
            title="普法宣传资料印刷项目",
            content="宣传单页和宣传册印刷服务。",
        )
    )
    assert decision.status == "NO_MATCH"


def test_unknown_budget_is_review_and_over_budget_is_marked():
    unknown = evaluate_record(make_record(budget_yuan=None, budget_raw=None, budget_status="UNKNOWN"))
    over = evaluate_record(
        make_record(budget_yuan=250000, budget_raw="25万元", budget_status="OVER_BUDGET")
    )
    assert unknown.status == "REVIEW"
    assert over.status == "OVER_BUDGET"


def test_title_normalization_and_identity_prefer_project_id():
    assert normalize_title("  摄影：服务（项目）  ") == "摄影服务项目"
    first = make_record(title="摄影服务采购公告")
    second = make_record(title="摄影服务采购公告（更正）", url="https://example.invalid/other")
    assert duplicate_key(first) == duplicate_key(second)
    assert titles_similar("摄影服务采购公告", "摄影服务采购公告（更正公告）")


def test_database_upsert_is_idempotent_and_keeps_raw_payload(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    record = make_record(raw_payload={"files": [{"name": "采购文件.pdf"}]})
    with db:
        first_id = db.upsert(record)
        second_id = db.upsert(record)
        rows = db.list_tenders()
    assert first_id == second_id
    assert len(rows) == 1
    assert json.loads(rows[0]["raw_payload_json"])["files"][0]["name"] == "采购文件.pdf"


def test_ingest_persists_filter_decision_and_attachments(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    record = make_record(
        raw_payload={
            "files": [
                {
                    "name": "采购文件.pdf",
                    "url": "https://example.invalid/file.pdf",
                    "contentType": "application/pdf",
                }
            ]
        }
    )
    with db:
        result = ingest_record(db, record)
        row = db.list_tenders()[0]
        attachments = db.list_attachments(result.tender_id)
    assert result.decision.status == "MATCH"
    assert row["filter_status"] == "MATCH"
    assert "摄影" in json.loads(row["filter_include_matches_json"])
    assert attachments[0]["file_name"] == "采购文件.pdf"


def test_database_fuzzy_dedupes_same_day_title_without_project_id(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    first = make_record(
        project_id=None,
        title="摄影服务采购公告",
        url="https://example.invalid/detail/first",
    )
    second = make_record(
        project_id=None,
        title="摄影服务采购公告（更正公告）",
        url="https://example.invalid/detail/second",
    )
    with db:
        first_id = db.upsert(first)
        second_id = db.upsert(second)
    assert first_id == second_id


def test_database_reclassifies_existing_rows_after_filter_rule_change(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    record = make_record(
        title="视频拍摄服务终止公告",
        budget_yuan=None,
        budget_raw=None,
        budget_status="UNKNOWN",
    )
    stale_decision = FilterDecision(
        "REVIEW", 7, ("拍摄",), (), ("预算无法从锚定字段确认，需人工复核",)
    )
    with db:
        db.upsert(record, decision=stale_decision)
        assert db.reclassify_filters() == 1
        row = db.list_tenders()[0]
    assert row["filter_status"] == "FILTERED"
    assert "终止公告" in json.loads(row["filter_exclude_matches_json"])


def test_manual_verification_updates_core_fields_and_filter_status(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    record = make_record(
        source="okcis_taixing",
        city="泰兴市",
        title="班主任基本功展示视频录制比选公告",
        project_id=None,
        budget_yuan=None,
        budget_raw="9000元",
        budget_status="UNKNOWN",
        content=None,
        raw_payload={"detail_access": "captcha_required"},
    )
    with db:
        tender_id = db.upsert(record, decision=evaluate_record(record))
        verified_id = db.verify_tender(
            record.url,
            project_id="JSZC-321283-FW2026-15690",
            budget_yuan=9000,
            budget_raw="¥9,000",
            deadline="2026-09-07 13:00",
            content="服务品目：商务服务/摄影服务；采购方式：公开比选。",
            verification_source="用户截图人工核验",
            notes="项目预算 ¥9,000；响应截止 2026-09-07 13:00。",
            evidence_path="data/evidence/okcis-taixing-2026-09-02-video-recording.png",
        )
        row = db.list_tenders()[0]

    assert verified_id == tender_id
    assert row["project_id"] == "JSZC-321283-FW2026-15690"
    assert row["budget_yuan"] == 9000
    assert row["budget_status"] == "NORMAL"
    assert row["deadline"] == "2026-09-07 13:00"
    assert row["verification_status"] == "VERIFIED"
    assert row["verification_source"] == "用户截图人工核验"
    assert row["filter_status"] == "MATCH"
    payload = json.loads(row["raw_payload_json"])
    assert payload["manual_verification"]["evidence_path"].endswith("video-recording.png")


def test_manual_detail_edit_preserves_follow_up_decision(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    record = make_record(title="摄影服务项目", project_id="JSZC-DETAIL-EDIT")
    with db:
        db.upsert(record, decision=evaluate_record(record))
        db.verify_tender(
            record.url,
            project_id=record.project_id,
            budget_yuan=180000,
            budget_raw="18万元",
            verification_source="test",
        )
        db.update_follow_up(record.url, status="NOT_REQUIRED", notes="不跟进")
        db.verify_tender(
            record.url,
            content="补充后的人工核验内容",
            verification_source="dashboard_manual",
        )
        row = db.list_tenders()[0]

    assert row["verification_status"] == "VERIFIED"
    assert row["follow_up_status"] == "NOT_REQUIRED"
    assert row["content"] == "补充后的人工核验内容"


def test_sparse_list_refresh_does_not_overwrite_verified_fields(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    record = make_record(
        source="okcis_taixing",
        title="班主任基本功展示视频录制比选公告",
        project_id=None,
        budget_yuan=None,
        budget_raw="9000元",
        budget_status="UNKNOWN",
        content=None,
    )
    with db:
        db.upsert(record, decision=evaluate_record(record))
        db.verify_tender(
            record.url,
            project_id="JSZC-321283-FW2026-15690",
            budget_yuan=9000,
            budget_raw="¥9,000",
            deadline="2026-09-07 13:00",
            content="服务品目：商务服务/摄影服务；采购方式：公开比选。",
            verification_source="用户截图人工核验",
        )
        db.upsert(record, decision=evaluate_record(record))
        row = db.list_tenders()[0]

    assert row["project_id"] == "JSZC-321283-FW2026-15690"
    assert row["budget_yuan"] == 9000
    assert row["deadline"] == "2026-09-07 13:00"
    assert row["verification_status"] == "VERIFIED"
