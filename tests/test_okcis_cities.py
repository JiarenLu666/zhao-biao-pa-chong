"""江苏 13 市 OKCIS 市级站配置的静态校验与离线端到端循环。"""

from pathlib import Path

from tender_monitor.automation import run_okcis_cycle
from tender_monitor.sources.okcis_cities import CITY_SOURCES
from tender_monitor.sources.okcis_taixing import build_list_url
from tender_monitor.sources.okcis_taizhou import okcis_taizhou
from tender_monitor.storage import TenderDatabase

CITY_NAMES = {
    "南京市",
    "无锡市",
    "徐州市",
    "常州市",
    "苏州市",
    "南通市",
    "连云港市",
    "淮安市",
    "盐城市",
    "扬州市",
    "镇江市",
    "泰州市",
    "宿迁市",
}


def test_city_sources_cover_all_thirteen_cities():
    assert len(CITY_SOURCES) == 13
    assert {source.city for source in CITY_SOURCES} == CITY_NAMES


def test_city_source_names_and_urls_are_unique():
    names = [source.name for source in CITY_SOURCES]
    urls = [source.base_url for source in CITY_SOURCES]
    assert len(set(names)) == len(names)
    assert len(set(urls)) == len(urls)
    assert all(url.startswith("https://") for url in urls)
    assert all(url.endswith(".okcis.cn") for url in urls)
    assert all(source.province == "江苏" for source in CITY_SOURCES)
    assert all(
        source.snapshot_prefix == source.name.replace("okcis_", "okcis-")
        for source in CITY_SOURCES
    )


def test_taizhou_reuses_verified_config():
    """泰州站必须复用泰州全域轮里已实测的同一份配置，不允许另起炉灶。"""

    taizhou = next(source for source in CITY_SOURCES if source.city == "泰州市")
    assert taizhou == okcis_taizhou


_PAGE_HTML = """
<li name="all_num">最新招标公告数据共:1条</li>
<ul class="dqz_body_all_styleh">
  <li class="dbas_biaoti"><a name="result-list-title"
    href="/dnww1.html" title="{title}">{title}</a></li>
  <li class="dbas_or"><a>招标公告</a><t>测试 2099-01-01</t></li>
</ul>
<span>共1页</span><span>共1条</span>
<input id="countnum_page" value="1" />
<input id="countnum_pagesize" value="1" />
<input id="countnum_size" value="50" />
"""


def test_okcis_cycle_covers_all_thirteen_cities_end_to_end(tmp_path):
    """13 市离线端到端：默认 guard 预算按 13 站放宽、按序逐站采集、轮末清理字段就位。"""

    responses = {
        build_list_url(
            page=1, page_size=50, time_type=1, base_url=source.base_url
        ): (200, _PAGE_HTML.format(title=f"{source.city}摄影服务公告"))
        for source in CITY_SOURCES
    }
    requested_urls: list[str] = []

    def fetch_page(url: str) -> tuple[int, str]:
        requested_urls.append(url)
        return responses[url]

    result = run_okcis_cycle(
        fetch_page,
        sources=CITY_SOURCES,
        db_path=tmp_path / "data" / "tenders.sqlite3",
        pages=1,
        sleep=lambda _: None,
        snapshot_dir=None,
        report_output=tmp_path / "out" / "report.csv",
        digest_output=tmp_path / "out" / "digest.txt",
        review_output=tmp_path / "out" / "review.csv",
    )

    # 13 站全部请求且顺序与 CITY_SOURCES 一致（未传 guard，走默认放宽预算）。
    assert result.collection.pages_fetched == 13
    assert result.collection.records_seen == 13
    assert result.collection.saved_count == 13
    assert [name for name, _ in result.source_reports] == [
        source.name for source in CITY_SOURCES
    ]
    assert {row["city"] for row in _list_cities(tmp_path)} == {
        source.city for source in CITY_SOURCES
    }
    # 轮末清理默认开启：本轮无过期数据，purged 字段应为 0 且出现在结果中。
    assert result.purged_tenders == 0
    assert result.purged_snapshots == 0


def _list_cities(tmp_path: Path) -> list:
    with TenderDatabase(tmp_path / "data" / "tenders.sqlite3") as db:
        return db.list_tenders()
