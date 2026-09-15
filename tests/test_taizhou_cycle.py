"""泰州全域多源自动循环的离线测试。"""

import pytest

from tender_monitor.automation import run_taizhou_cycle
from tender_monitor.rate_limit import CircuitOpenError, RateLimitPolicy, RequestGuard
from tender_monitor.sources.okcis_taixing import build_list_url
from tender_monitor.sources.okcis_taizhou import TAIZHOU_SOURCES
from tender_monitor.storage import TenderDatabase


def _page_html(title: str, href: str, page: int) -> str:
    return f"""
    <li name="all_num">最新招标公告数据共:2条</li>
    <ul class="dqz_body_all_styleh">
      <li class="dbas_biaoti"><a name="result-list-title"
        href="{href}" title="{title}">{title}</a></li>
      <li class="dbas_or"><a>招标公告</a><t>泰兴 2026-09-15</t></li>
    </ul>
    <span>共2页</span><span>共2条</span>
    <input id="countnum_page" value="{page}" />
    <input id="countnum_pagesize" value="2" />
    <input id="countnum_size" value="50" />
    """


def _build_responses(titles_by_source: dict[str, str]) -> dict[str, tuple[int, str]]:
    """按来源构造 build_list_url 维度的离线响应字典（每站 1 页）。"""

    responses: dict[str, tuple[int, str]] = {}
    for source in TAIZHOU_SOURCES:
        responses[
            build_list_url(page=1, page_size=50, time_type=1, base_url=source.base_url)
        ] = (
            200,
            _page_html(titles_by_source[source.name], "/dnww1.html", 1),
        )
    return responses


def test_taizhou_cycle_ingests_all_seven_sources(tmp_path):
    responses = _build_responses(
        {
            "okcis_taizhou": "泰州市文化馆摄影服务公告",
            "okcis_hailing": "海陵区视频拍摄服务公告",
            "okcis_gaogang": "高港区文化节活动策划公告",
            "okcis_taixing": "泰兴市图片采风服务公告",
            "okcis_jingjiang": "靖江市短视频推广服务公告",
            "okcis_xinghua": "兴化市影像记录服务公告",
            "okcis_jiangyan": "姜堰区文旅宣传活动公告",
        }
    )
    db_path = tmp_path / "data" / "tenders.sqlite3"

    result = run_taizhou_cycle(
        lambda url: responses[url],
        db_path=db_path,
        pages=1,
        sleep=lambda _: None,
        snapshot_dir=None,
        report_output=tmp_path / "out" / "report.csv",
        digest_output=tmp_path / "out" / "digest.txt",
        review_output=tmp_path / "out" / "review.csv",
    )

    # 汇总数字：七站各 1 页、各 1 条（每页样本含 1 条公告）。
    assert result.collection.pages_fetched == len(TAIZHOU_SOURCES)
    assert result.collection.records_seen == len(TAIZHOU_SOURCES)
    assert result.collection.saved_count == len(TAIZHOU_SOURCES)
    assert len(result.source_reports) == len(TAIZHOU_SOURCES)
    assert [name for name, _ in result.source_reports] == [
        source.name for source in TAIZHOU_SOURCES
    ]
    # 每个来源的独立明细正确。
    for _, report in result.source_reports:
        assert report.records_seen == 1
        assert report.saved_count == 1
        assert report.pages_fetched == 1

    with TenderDatabase(db_path) as db:
        rows = db.list_tenders()
    assert {row["source"] for row in rows} == {source.name for source in TAIZHOU_SOURCES}
    assert {row["city"] for row in rows} == {source.city for source in TAIZHOU_SOURCES}
    # 循环结束后只刷新一次本地输出。
    assert (tmp_path / "out" / "report.csv").exists()


def test_taizhou_cycle_shared_guard_throttles_across_sources(tmp_path):
    responses = _build_responses(
        {source.name: f"{source.city}摄影服务公告" for source in TAIZHOU_SOURCES}
    )
    # 共享 guard：15 秒最小间隔、0 抖动、假时钟不走，等待只能逐次累加。
    clock = {"now": 100.0}
    guard = RequestGuard(
        RateLimitPolicy(
            min_request_interval_seconds=15.0,
            request_jitter_seconds=0.0,
            max_pages_per_run=len(TAIZHOU_SOURCES),
            max_records_per_run=len(TAIZHOU_SOURCES) * 50,
        ),
        clock=lambda: clock["now"],
        random_source=lambda: 0.0,
    )
    delays: list[float] = []

    result = run_taizhou_cycle(
        lambda url: responses[url],
        db_path=tmp_path / "data" / "tenders.sqlite3",
        pages=1,
        guard=guard,
        sleep=lambda seconds: delays.append(seconds),
        snapshot_dir=None,
        report_output=tmp_path / "out" / "report.csv",
        digest_output=tmp_path / "out" / "digest.txt",
        review_output=tmp_path / "out" / "review.csv",
    )

    # 全部请求经过同一个 guard：首个立即发出，后续每次至少等 15 秒。
    assert result.collection.pages_fetched == len(TAIZHOU_SOURCES)
    assert delays == [15.0 * index for index in range(len(TAIZHOU_SOURCES))]
    assert guard.pages_used == len(TAIZHOU_SOURCES)


def test_taizhou_cycle_stops_entire_run_when_one_source_is_rate_limited(tmp_path):
    rate_limited = build_list_url(
        page=1, page_size=50, time_type=1, base_url="https://taixingshi.okcis.cn"
    )
    requests: list[str] = []

    def fetch_page(url: str) -> tuple[int, str]:
        requests.append(url)
        if url == rate_limited:
            return 429, ""
        return 200, _page_html("泰州市摄影服务公告", "/dnww1.html", 1)

    with pytest.raises(CircuitOpenError):
        run_taizhou_cycle(
            fetch_page,
            db_path=tmp_path / "data" / "tenders.sqlite3",
            pages=1,
            sleep=lambda _: None,
            snapshot_dir=None,
            report_output=tmp_path / "out" / "report.csv",
            digest_output=tmp_path / "out" / "digest.txt",
            review_output=tmp_path / "out" / "review.csv",
        )

    # 泰兴站之前的站（市级、海陵、高港）正常完成后，泰兴站限流立即熔断，
    # 其后的靖江、兴化、姜堰不再请求。
    limited_index = next(
        index
        for index, source in enumerate(TAIZHOU_SOURCES)
        if source.base_url == "https://taixingshi.okcis.cn"
    )
    assert len(requests) == limited_index + 1
    blocked_hosts = (
        "jingjiangshi",
        "xinghuashi",
        "jiangyanshi",
    )
    assert all(host not in url for url in requests for host in blocked_hosts)


def test_taizhou_cycle_default_guard_relaxes_budget_by_source_count(tmp_path):
    """默认 guard 的预算按站数放宽，但节流策略保持不变。"""

    responses = _build_responses(
        {source.name: f"{source.city}摄影服务公告" for source in TAIZHOU_SOURCES}
    )
    result = run_taizhou_cycle(
        lambda url: responses[url],
        db_path=tmp_path / "data" / "tenders.sqlite3",
        pages=1,
        sleep=lambda _: None,
        snapshot_dir=None,
        report_output=tmp_path / "out" / "report.csv",
        digest_output=tmp_path / "out" / "digest.txt",
        review_output=tmp_path / "out" / "review.csv",
    )

    # 七站 × 1 页全部在放宽后的预算内完成，未触发 BudgetExceededError。
    assert result.collection.pages_fetched == len(TAIZHOU_SOURCES)
    assert result.collection.saved_count == len(TAIZHOU_SOURCES)
