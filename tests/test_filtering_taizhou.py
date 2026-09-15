"""泰州地区扩词筛选的离线测试。"""

from tender_monitor.filtering import TAIZHOU_EXTRA_INCLUDE_TERMS, evaluate_record
from tender_monitor.models import TenderRecord


def _record(**changes) -> TenderRecord:
    values = {
        "title": "测试公告",
        "source": "okcis_taizhou",
        "url": "https://example.invalid/detail/taizhou",
        "province": "江苏",
        "city": "泰州市",
        "published_at": "2026-09-15 09:00:00",
        "budget_status": "NORMAL",
        "content": "",
    }
    values.update(changes)
    return TenderRecord(**values)


def test_taizhou_photography_printing_terms_match_for_taizhou_region():
    decision = evaluate_record(
        _record(
            title="拍照冲印服务比选公告",
            budget_status="UNKNOWN",
        )
    )

    assert decision.status == "REVIEW"
    assert "泰州扩词:拍照" in decision.include_matches
    assert "泰州扩词:冲印" in decision.include_matches
    assert decision.score > 0


def test_same_terms_do_not_match_outside_taizhou_region():
    decision = evaluate_record(
        _record(
            title="拍照冲印服务比选公告",
            city="南京市",
            budget_status="UNKNOWN",
        )
    )

    assert decision.status == "NO_MATCH"
    assert decision.score == 0
    assert not decision.include_matches


def test_electronic_info_capture_target_is_not_filtered():
    """实证目标标题含“信息采集”，必须命中而不是被排除词误杀。"""

    decision = evaluate_record(
        _record(
            title="电子信息采集拍照及冲印一寸照片比选公告",
            city="泰兴市",
            budget_status="UNKNOWN",
        )
    )

    assert decision.status == "REVIEW"
    assert "泰州扩词:拍照" in decision.include_matches
    assert "泰州扩词:照片" in decision.include_matches
    assert "泰州扩词:冲印" in decision.include_matches
    assert not decision.exclude_matches


def test_taizhou_region_detection_covers_all_markers():
    for city in (
        "泰州市",
        "海陵区",
        "高港区",
        "泰兴市",
        "靖江市",
        "兴化市",
        "姜堰区",
    ):
        decision = evaluate_record(_record(title="拍照服务公告", city=city))
        assert decision.score > 0, city
    # province 含泰州标记同样生效（city 为空时）。
    decision = evaluate_record(
        _record(title="拍照服务公告", city=None, province="江苏泰州")
    )
    assert decision.score > 0


def test_extra_terms_share_the_same_scoring_as_normal_hits():
    # “图书馆”属公共文化组、无标题加分，标题命中即 3 分。
    plain = evaluate_record(
        _record(title="图书馆服务公告", url="https://example.invalid/detail/a")
    )
    extra = evaluate_record(_record(title="拍照服务公告", city="泰州市"))

    # 标题命中均为 3 分，泰州扩词与普通命中同分值。
    assert plain.score == 3
    assert extra.score == 3


def test_extra_terms_constant_is_declared():
    assert TAIZHOU_EXTRA_INCLUDE_TERMS == ("拍照", "照片", "冲印")
