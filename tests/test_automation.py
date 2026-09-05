import csv

import pytest

from tender_monitor.automation import CycleAlreadyRunningError, run_automatic_cycle
from tender_monitor.rate_limit import RateLimitPolicy, RequestGuard
from tender_monitor.sources.okcis_taixing import build_list_url
from tender_monitor.storage import TenderDatabase


def _page_html(title: str, href: str, page: int) -> str:
    return f"""
    <li name="all_num">最新招标公告数据共:2条</li>
    <ul class="dqz_body_all_styleh">
      <li class="dbas_biaoti"><a name="result-list-title"
        href="{href}" title="{title}">{title}</a></li>
      <li class="dbas_or"><a>招标公告</a><t>泰兴 2026-09-03</t></li>
    </ul>
    <span>共2页</span><span>共2条</span>
    <input id="countnum_page" value="{page}" />
    <input id="countnum_pagesize" value="2" />
    <input id="countnum_size" value="50" />
    """


def test_automatic_cycle_is_idempotent_and_refreshes_outputs(tmp_path):
    responses = {
        build_list_url(page=1, page_size=50, time_type=1): (
            200,
            _page_html("视频拍摄服务公告", "/dnww1.html", 1),
        ),
        build_list_url(page=2, page_size=50, time_type=1): (
            200,
            _page_html("文化节活动策划公告", "/dnww2.html", 2),
        ),
    }
    db_path = tmp_path / "data" / "tenders.sqlite3"
    report_path = tmp_path / "out" / "report.csv"
    digest_path = tmp_path / "out" / "digest.txt"
    review_path = tmp_path / "out" / "review.csv"
    guard = RequestGuard(
        RateLimitPolicy(
            min_request_interval_seconds=0,
            request_jitter_seconds=0,
            max_pages_per_run=2,
            max_records_per_run=100,
        )
    )

    first = run_automatic_cycle(
        lambda url: responses[url],
        db_path=db_path,
        pages=2,
        guard=guard,
        sleep=lambda _: None,
        report_output=report_path,
        digest_output=digest_path,
        review_output=review_path,
        snapshot_dir=tmp_path / "raw",
    )
    second = run_automatic_cycle(
        lambda url: responses[url],
        db_path=db_path,
        pages=2,
        guard=RequestGuard(
            RateLimitPolicy(
                min_request_interval_seconds=0,
                request_jitter_seconds=0,
                max_pages_per_run=2,
                max_records_per_run=100,
            )
        ),
        sleep=lambda _: None,
        report_output=report_path,
        digest_output=digest_path,
        review_output=review_path,
        snapshot_dir=tmp_path / "raw",
    )

    assert first.collection.records_seen == 2
    assert second.collection.saved_count == 2
    assert first.review_queue_count == 2
    assert report_path.exists()
    assert "视频拍摄服务公告" in digest_path.read_text(encoding="utf-8")
    with report_path.open(newline="", encoding="utf-8-sig") as handle:
        assert len(list(csv.DictReader(handle))) == 2
    with TenderDatabase(db_path) as db:
        assert len(db.list_tenders()) == 2


def test_automatic_cycle_refuses_an_existing_live_lock(tmp_path):
    lock_path = tmp_path / "run.lock"
    lock_path.write_text(str(__import__("os").getpid()), encoding="utf-8")

    with pytest.raises(CycleAlreadyRunningError):
        run_automatic_cycle(
            lambda _: (200, ""),
            db_path=tmp_path / "data" / "tenders.sqlite3",
            pages=1,
            sleep=lambda _: None,
            lock_path=lock_path,
        )


def test_automatic_cycle_stops_at_source_reported_last_page(tmp_path):
    url = build_list_url(page=1, page_size=50, time_type=1)
    requests: list[str] = []

    def fetch_page(request_url: str) -> tuple[int, str]:
        requests.append(request_url)
        return 200, _page_html("视频拍摄服务公告", "/dnww1.html", 1).replace(
            "<span>共2页</span>",
            "<span>共1页</span>",
        ).replace(
            '<input id="countnum_pagesize" value="2" />',
            '<input id="countnum_pagesize" value="1" />',
        )

    result = run_automatic_cycle(
        fetch_page,
        db_path=tmp_path / "data" / "tenders.sqlite3",
        pages=2,
        sleep=lambda _: None,
        snapshot_dir=None,
    )

    assert requests == [url]
    assert result.collection.pages_fetched == 1
    assert result.collection.stopped_reason == "已到列表末页"


def test_automatic_cycle_reports_when_page_budget_is_reached(tmp_path):
    responses = {
        build_list_url(page=1, page_size=50, time_type=1): (
            200,
            _page_html("视频拍摄服务公告 1", "/dnww1.html", 1).replace(
                "<span>共2页</span>",
                "<span>共3页</span>",
            ).replace(
                '<input id="countnum_pagesize" value="2" />',
                '<input id="countnum_pagesize" value="3" />',
            ),
        ),
        build_list_url(page=2, page_size=50, time_type=1): (
            200,
            _page_html("视频拍摄服务公告 2", "/dnww2.html", 2).replace(
                "<span>共2页</span>",
                "<span>共3页</span>",
            ).replace(
                '<input id="countnum_pagesize" value="2" />',
                '<input id="countnum_pagesize" value="3" />',
            ),
        ),
    }
    result = run_automatic_cycle(
        lambda url: responses[url],
        db_path=tmp_path / "data" / "tenders.sqlite3",
        pages=2,
        sleep=lambda _: None,
        snapshot_dir=None,
    )

    assert result.collection.pages_fetched == 2
    assert result.collection.stopped_reason == "达到本次页数上限"
