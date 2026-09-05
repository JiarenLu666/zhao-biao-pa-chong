from tender_monitor.models import TenderRecord
from tender_monitor.normalization import budget_status, parse_budget_from_text
from tender_monitor.rate_limit import (
    BudgetExceededError,
    CircuitOpenError,
    RateLimitPolicy,
    RequestGuard,
    is_rate_limited,
)
from tender_monitor.sources.ccgp_jiangsu import (
    SearchQuery,
    build_detail_url,
    build_list_api_url,
    build_search_url,
)
from tender_monitor.sources.ccgp_jiangsu_detail import parse_detail_payload
from tender_monitor.sources.ccgp_jiangsu_list import (
    ListApiError,
    parse_list_payload,
    parse_list_response,
)


def test_build_search_url_is_page_entrypoint():
    assert build_search_url().endswith("/jiangsu/cggg_search.html")


def test_build_list_api_url_uses_real_script_parameters():
    url = build_list_api_url(page=2, announcement_type="cggg", procurement_method="cgfs008")
    assert "cglx=cggg" in url
    assert "cgfs=cgfs008" in url
    assert "page=2" in url


def test_search_query_requires_explicit_incremental_window_and_captcha():
    query = SearchQuery(
        start_ms=1_756_790_400_000,
        end_ms=1_756_876_799_000,
        announcement_type="cggg",
        parent_region_code="320100",
        captcha_code="human-entered",
    )
    params = query.as_params(page=2)
    assert params["sd"] == 1_756_790_400_000
    assert params["ed"] == 1_756_876_799_000
    assert params["cglx"] == "cggg"
    assert params["pqy"] == "320100"
    assert params["code"] == "human-entered"
    assert params["page"] == 2


def test_build_detail_url_uses_gglb_and_ggid():
    url = build_detail_url("cggg", "abc123")
    assert url.endswith("gglb=cggg&ggid=abc123")


def test_tender_record_defaults_to_unknown_budget():
    record = TenderRecord(
        title="示例公告",
        source="ccgp_jiangsu",
        url="https://example.invalid/tender/1",
    )
    assert record.budget_status == "UNKNOWN"


def test_budget_parser_only_uses_labeled_amount():
    value, raw = parse_budget_from_text(
        "项目编号：JSZC-320000-ABC-G2026-0001；发布日期：2026-09-02；"
        "预算金额：20.50万元。"
    )
    assert value == 205000
    assert raw == "20.50万元"


def test_budget_parser_does_not_guess_unlabeled_numbers():
    assert parse_budget_from_text("项目编号 JSZC-320000-ABC-G2026-0001") == (None, None)
    assert budget_status(None) == "UNKNOWN"


def test_detail_payload_maps_public_fields():
    payload = {
        "msg": "OK",
        "data": {
            "title": "江苏文化宣传服务采购公告",
            "ggCode": "cggg",
            "publishDate": "2026-09-02 09:00:00",
            "pZoneName": "江苏省",
            "zoneName": "南京市",
            "summary": "项目编号：JSZC-320100-TEST-G2026-0001",
            "projId": "proj-1",
            "files": [{"name": "采购文件.pdf", "url": "https://example.invalid/file.pdf"}],
            "content": (
                "<p>项目编号：JSZC-320100-TEST-G2026-0001</p>"
                "<p>预算金额：18万元</p><p>采购方式：竞争性磋商</p>"
            ),
        },
    }
    record = parse_detail_payload(payload, "https://example.invalid/detail")
    assert record.project_id == "JSZC-320100-TEST-G2026-0001"
    assert record.budget_yuan == 180000
    assert record.budget_status == "NORMAL"
    assert record.procurement_method == "竞争性磋商"
    assert len(record.raw_payload["files"]) == 1


def test_over_limit_page_is_detected_without_relying_on_status_code():
    body = '<html><body>访问过于频繁，请稍后再试</body></html>'
    assert is_rate_limited(200, body)
    assert is_rate_limited(401, "")
    assert is_rate_limited(200, "", "http://example.invalid/pss/error/overLimitIP.html")
    assert not is_rate_limited(200, "查询成功，共 10 条")


def test_request_guard_opens_circuit_after_over_limit_response():
    now = [100.0]
    guard = RequestGuard(
        RateLimitPolicy(
            min_request_interval_seconds=5,
            request_jitter_seconds=0,
            cooldown_seconds=60,
        ),
        clock=lambda: now[0],
    )

    assert guard.before_request(page=1) == 0
    guard.after_response(200, "访问过于频繁，请稍后再试")

    try:
        guard.before_request(page=2)
    except CircuitOpenError as exc:
        assert "冷却" in str(exc)
    else:
        raise AssertionError("限流响应后必须立即熔断")

    now[0] += 61
    assert guard.before_request(page=2) == 0


def test_request_guard_returns_pacing_delay_with_jitter():
    now = [100.0]
    guard = RequestGuard(
        RateLimitPolicy(
            min_request_interval_seconds=10,
            request_jitter_seconds=4,
        ),
        clock=lambda: now[0],
        random_source=lambda: 0.5,
    )
    assert guard.before_request(page=1) == 0
    now[0] = 101.0
    assert guard.before_request(page=2) == 11.0


def test_request_guard_enforces_run_budget():
    guard = RequestGuard(
        RateLimitPolicy(
            min_request_interval_seconds=0,
            request_jitter_seconds=0,
            max_pages_per_run=2,
            max_records_per_run=3,
        ),
        clock=lambda: 100.0,
    )
    guard.before_request(page=1)
    guard.after_response(200, "", records_count=2)
    guard.before_request(page=2)
    guard.after_response(200, "", records_count=1)

    try:
        guard.before_request(page=3)
    except BudgetExceededError:
        pass
    else:
        raise AssertionError("超过单次运行页数预算时必须停止")


def test_list_payload_maps_script_confirmed_result_shape():
    page = parse_list_payload(
        {
            "code": 200,
            "result": {
                "count": 1,
                "pageNo": 1,
                "list": [
                    {
                        "id": "abc123",
                        "type": 1,
                        "ggCode": "cggg",
                        "title": "摄影服务采购公告",
                        "summary": "预算金额：18万元",
                        "zoneName": "南京市",
                        "pZoneName": "江苏省",
                        "publishDate": "2026-09-02 09:00:00",
                    }
                ],
            },
        }
    )
    assert page.total == 1
    assert page.items[0].title == "摄影服务采购公告"
    assert page.items[0].detail_url().endswith("gglb=cggg&ggid=abc123")


def test_list_response_does_not_turn_captcha_error_into_empty_page():
    try:
        parse_list_response(200, '{"msg":"ERROR","message":"验证码不能为空。"}')
    except ListApiError as exc:
        assert "验证码" in str(exc)
    else:
        raise AssertionError("验证码错误必须保留为失败，不得伪装成空列表")
