import pytest

from tender_monitor.rate_limit import CircuitOpenError, RateLimitPolicy, RequestGuard
from tender_monitor.sources.okcis_taixing import (
    build_list_url,
    collect_list_pages,
    is_detail_captcha_gate,
    parse_list_page,
)
from tender_monitor.storage import TenderDatabase


def test_okcis_static_list_parser_reads_title_budget_hint_and_pagination():
    html = """
    <li name="all_num">最新招标公告数据共:<samp>4895</samp>条</li>
    <ul class="dqz_body_all_styleh">
      <li class="dbas_biaoti"><a name="result-list-title"
        href="/dnww20260902131145324786.html" title="视频拍摄服务比选公告">视频拍摄服务比选公告</a></li>
      <li class="dbas_or"><a>招标公告</a><t>泰州 2026-09-02</t></li>
    </ul>
    <ul class="dqz_body_all_styleh">
      <li class="dbas_biaoti"><a name="result-list-title"
        href="/dnww20260902152340239549.html" title="道路建设比选公告">道路建设比选公告</a></li>
      <li class="dbas_or"><a>: 88.73万</a><t>泰州 2026-09-02</t></li>
    </ul>
    <span>共98页</span><span>共4895条</span>
    <input id="countnum_page" value="1" />
    <input id="countnum_pagesize" value="98" />
    <input id="countnum_size" value="50" />
    """
    page = parse_list_page(html)
    assert page.total == 4895
    assert page.pages == 98
    assert len(page.items) == 2
    assert page.items[0].title == "视频拍摄服务比选公告"
    assert page.items[0].published_at == "2026-09-02"
    assert page.items[1].budget_raw == "88.73万"
    assert page.items[1].listed_area == "泰州"
    assert page.items[1].to_tender_record().budget_yuan is None
    assert page.items[0].url.endswith("dnww20260902131145324786.html")


def test_okcis_detail_gate_is_recorded_not_solved():
    html = '<form action="/php/checkUser/doVerify.php"><title>请输入验证码</title></form>'
    assert is_detail_captcha_gate(html)
    assert not is_detail_captcha_gate("<html><title>公告正文</title></html>")


def test_okcis_list_url_keeps_time_window_and_page_parameters():
    assert build_list_url(page=2, page_size=50, time_type=1) == (
        "https://taixingshi.okcis.cn/sww/bn/2-50-1"
    )


def test_collect_list_pages_is_low_frequency_and_ingests_records(tmp_path):
    def page_html(title: str, page: int) -> str:
        return f"""
        <li name="all_num">最新招标公告数据共:<samp>2</samp>条</li>
        <ul class="dqz_body_all_styleh">
          <li class="dbas_biaoti"><a name="result-list-title"
            href="/dnww{page}.html" title="{title}">{title}</a></li>
          <li class="dbas_or"><a>招标公告</a><t>泰兴 2026-09-02</t></li>
        </ul>
        <span>共2页</span><span>共2条</span>
        <input id="countnum_page" value="{page}" />
        <input id="countnum_pagesize" value="2" />
        <input id="countnum_size" value="50" />
        """

    responses = {
        build_list_url(page=1, page_size=50, time_type=1): (200, page_html("视频拍摄服务公告", 1)),
        build_list_url(page=2, page_size=50, time_type=1): (200, page_html("文化节活动策划公告", 2)),
    }
    delays: list[float] = []
    guard = RequestGuard(
        RateLimitPolicy(
            min_request_interval_seconds=10,
            request_jitter_seconds=0,
            max_pages_per_run=2,
            max_records_per_run=100,
        )
    )
    with TenderDatabase(tmp_path / "tenders.sqlite3") as db:
        result = collect_list_pages(
            lambda url: responses[url],
            db,
            pages=2,
            guard=guard,
            sleep=delays.append,
            snapshot_dir=tmp_path / "raw",
        )
        rows = db.list_tenders()

    assert result.pages_fetched == 2
    assert result.records_seen == 2
    assert result.saved_count == 2
    assert result.status_counts["REVIEW"] == 2
    assert delays[0] == 0.0
    assert delays[1] == pytest.approx(10.0, abs=0.01)
    assert len(rows) == 2
    assert (tmp_path / "raw" / "okcis-taixing-001.html").exists()


def test_collect_list_pages_fails_closed_on_limit_response(tmp_path):
    responses = {
        build_list_url(page=1, page_size=50, time_type=1): (
            200,
            """
            <li name="all_num">数据共:1条</li>
            <ul class="dqz_body_all_styleh">
              <li class="dbas_biaoti"><a name="result-list-title"
                href="/dnww1.html" title="视频拍摄服务公告">视频拍摄服务公告</a></li>
              <li class="dbas_or"><a>招标公告</a><t>泰兴 2026-09-02</t></li>
            </ul>
            <span>共2页</span><span>共1条</span>
            <input id="countnum_page" value="1" />
            <input id="countnum_pagesize" value="2" />
            <input id="countnum_size" value="50" />
            """,
        ),
        build_list_url(page=2, page_size=50, time_type=1): (
            429,
            "访问过于频繁，请稍后再试",
        ),
    }
    guard = RequestGuard(
        RateLimitPolicy(
            min_request_interval_seconds=0,
            request_jitter_seconds=0,
            max_pages_per_run=2,
        )
    )
    with TenderDatabase(tmp_path / "tenders.sqlite3") as db:
        try:
            collect_list_pages(
                lambda url: responses[url],
                db,
                pages=2,
                guard=guard,
                sleep=lambda _: None,
            )
        except CircuitOpenError as exc:
            assert "限流" in str(exc)
        else:
            raise AssertionError("限流响应必须立即停止，不能继续抓下一页")


def test_collect_list_pages_supports_province_entry_and_uses_listed_city(tmp_path):
    html = """
    <li name="all_num">最新招标公告数据共:1条</li>
    <ul class="dqz_body_all_styleh">
      <li class="dbas_biaoti"><a name="result-list-title"
        href="/dnww-province.html" title="南京市文化活动策划公告">南京市文化活动策划公告</a></li>
      <li class="dbas_or"><a>招标公告</a><t>南京市 2026-09-03</t></li>
    </ul>
    <span>共1页</span><span>共1条</span>
    <input id="countnum_page" value="1" />
    <input id="countnum_pagesize" value="1" />
    <input id="countnum_size" value="50" />
    """
    province_url = "https://jiangsu.okcis.cn/sww/bn/1-50-1"
    with TenderDatabase(tmp_path / "tenders.sqlite3") as db:
        collect_list_pages(
            lambda url: (200, html) if url == province_url else (404, ""),
            db,
            pages=1,
            base_url="https://jiangsu.okcis.cn",
            source_name="okcis_jiangsu",
            city=None,
            snapshot_prefix="okcis-jiangsu",
            sleep=lambda _: None,
            snapshot_dir=tmp_path / "raw",
        )
        row = db.list_tenders()[0]

    assert row["source"] == "okcis_jiangsu"
    assert row["city"] == "南京市"
    assert (tmp_path / "raw" / "okcis-jiangsu-001.html").exists()
