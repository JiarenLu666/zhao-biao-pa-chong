"""7 天数据保留清理的离线测试：数据库级联删 + 快照清理 + 各调用入口。"""

import json
import os

import pytest

from tender_monitor.automation import run_okcis_cycle
from tender_monitor.cleanup import purge_snapshot_files
from tender_monitor.cli import main
from tender_monitor.models import TenderRecord
from tender_monitor.sources.okcis_jiangsu import OkcisSourceConfig
from tender_monitor.sources.okcis_taixing import build_list_url
from tender_monitor.storage import TenderDatabase


def _record(title: str, url: str, published_at: str) -> TenderRecord:
    return TenderRecord(
        title=title,
        source="okcis_test",
        url=url,
        province="江苏",
        city="泰兴市",
        published_at=published_at,
    )


def _seed_tenders(db_path):
    """入库一老一新两条公告，并给老公告挂附件和通知账本。"""

    with TenderDatabase(db_path) as db:
        old_id = db.upsert(
            _record("旧公告-文化馆设备采购", "https://a.example/old", "2020-01-01")
        )
        db.upsert(_record("新公告-摄影服务采购", "https://a.example/new", "2099-01-01"))
        connection = db.connect()
        connection.execute(
            "INSERT INTO attachments (tender_id, file_name, file_url) VALUES (?, ?, ?)",
            (old_id, "old.pdf", "https://a.example/old.pdf"),
        )
        connection.execute(
            "INSERT INTO notification_deliveries (tender_id, channel) VALUES (?, ?)",
            (old_id, "serverchan"),
        )
        connection.commit()


def test_purge_tenders_older_than_cascades_related_rows(tmp_path):
    db_path = tmp_path / "data" / "tenders.sqlite3"
    _seed_tenders(db_path)

    with TenderDatabase(db_path) as db:
        purged = db.purge_tenders_older_than(7)
        rows = db.list_tenders()
        connection = db.connect()
        attachments = connection.execute("SELECT * FROM attachments").fetchall()
        deliveries = connection.execute(
            "SELECT * FROM notification_deliveries"
        ).fetchall()

    assert purged == 1
    assert [row["title"] for row in rows] == ["新公告-摄影服务采购"]
    # 级联删除：老公告的附件与通知账本一并清空。
    assert attachments == []
    assert deliveries == []


def test_purge_tenders_older_than_also_deletes_verified_records(tmp_path):
    """指挥官拍板的行为：超过保留期的记录一律删除，人工已核验（VERIFIED）也不例外。"""

    db_path = tmp_path / "data" / "tenders.sqlite3"
    with TenderDatabase(db_path) as db:
        old_id = db.upsert(
            _record("已核验老公告", "https://a.example/verified-old", "2020-01-01")
        )
        db.verify_tender(
            "https://a.example/verified-old",
            project_id="JSZC-TEST-VERIFIED-OLD",
            verification_source="manual",
        )
        connection = db.connect()
        connection.execute(
            "INSERT INTO attachments (tender_id, file_name, file_url) VALUES (?, ?, ?)",
            (old_id, "verified-old.pdf", "https://a.example/verified-old.pdf"),
        )
        connection.commit()
        rows = db.list_tenders()
        assert rows[0]["verification_status"] == "VERIFIED"

        assert db.purge_tenders_older_than(7) == 1
        assert db.list_tenders() == []
        # 级联清理对已核验记录同样生效。
        assert connection.execute("SELECT * FROM attachments").fetchall() == []


def test_purge_tenders_older_than_falls_back_to_created_at(tmp_path):
    db_path = tmp_path / "data" / "tenders.sqlite3"
    with TenderDatabase(db_path) as db:
        db.upsert(_record("无发布日的老公告", "https://a.example/none", ""))
        # 直接把 published_at 置空，模拟缺失发布时间的存量数据。
        db.connect().execute("UPDATE tenders SET published_at = NULL")
        db.connect().commit()

        # 入库日是今天，7 天保留期内不应被删。
        assert db.purge_tenders_older_than(7) == 0
        assert len(db.list_tenders()) == 1


def test_purge_tenders_older_than_rejects_non_positive_retention(tmp_path):
    with TenderDatabase(tmp_path / "data" / "tenders.sqlite3") as db:
        with pytest.raises(ValueError):
            db.purge_tenders_older_than(0)
        with pytest.raises(ValueError):
            db.purge_tenders_older_than(-1)


def test_purge_snapshot_files_removes_only_expired_files(tmp_path):
    snapshot_dir = tmp_path / "raw"
    snapshot_dir.mkdir()
    old_file = snapshot_dir / "okcis-taixing-001.html"
    new_file = snapshot_dir / "okcis-taixing-002.html"
    old_file.write_text("<html>old</html>", encoding="utf-8")
    new_file.write_text("<html>new</html>", encoding="utf-8")
    sub_dir = snapshot_dir / "nested"
    sub_dir.mkdir()
    # 用 2000-01-01 的时间戳把第一个文件人为变老（本地时区无关紧要，
    # 只要明显早于 now - 7 天）。
    os.utime(old_file, (946684800, 946684800))

    removed = purge_snapshot_files(snapshot_dir, 7)

    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()
    assert sub_dir.is_dir()  # 子目录不递归处理


def test_purge_snapshot_files_handles_missing_dir_and_bad_args(tmp_path):
    assert purge_snapshot_files(tmp_path / "missing", 7) == 0
    with pytest.raises(ValueError):
        purge_snapshot_files(tmp_path, 0)


def _single_source_page(page: int) -> str:
    return f"""
    <li name="all_num">最新招标公告数据共:1条</li>
    <ul class="dqz_body_all_styleh">
      <li class="dbas_biaoti"><a name="result-list-title"
        href="/dnww1.html" title="测试市摄影服务公告">测试市摄影服务公告</a></li>
      <li class="dbas_or"><a>招标公告</a><t>测试 2099-01-01</t></li>
    </ul>
    <span>共1页</span><span>共1条</span>
    <input id="countnum_page" value="{page}" />
    <input id="countnum_pagesize" value="1" />
    <input id="countnum_size" value="50" />
    """


_SINGLE_SOURCE = (
    OkcisSourceConfig(
        name="okcis_test",
        base_url="https://test.okcis.cn",
        province="江苏",
        city="测试市",
        snapshot_prefix="okcis-test",
    ),
)


def test_okcis_cycle_purges_expired_data_at_cycle_end(tmp_path):
    db_path = tmp_path / "data" / "tenders.sqlite3"
    snapshot_dir = tmp_path / "raw"
    snapshot_dir.mkdir()
    old_snapshot = snapshot_dir / "okcis-test-000.html"
    old_snapshot.write_text("<html>old</html>", encoding="utf-8")
    os.utime(old_snapshot, (946684800, 946684800))
    _seed_tenders(db_path)
    url = build_list_url(
        page=1, page_size=50, time_type=1, base_url="https://test.okcis.cn"
    )

    result = run_okcis_cycle(
        lambda request_url: (
            (200, _single_source_page(1))
            if request_url == url
            else (_ for _ in ()).throw(AssertionError(request_url))
        ),
        sources=_SINGLE_SOURCE,
        db_path=db_path,
        pages=1,
        sleep=lambda _: None,
        snapshot_dir=snapshot_dir,
        report_output=tmp_path / "out" / "report.csv",
        digest_output=tmp_path / "out" / "digest.txt",
        review_output=tmp_path / "out" / "review.csv",
    )

    # 轮末清理：老公告 + 老快照被删，新采集的公告与快照保留。
    assert result.purged_tenders == 1
    assert result.purged_snapshots == 1
    assert not old_snapshot.exists()
    with TenderDatabase(db_path) as db:
        titles = {row["title"] for row in db.list_tenders()}
    assert titles == {"测试市摄影服务公告", "新公告-摄影服务采购"}


def test_okcis_cycle_purges_tenders_before_ingest(tmp_path):
    """钉死清理时序：公告清理发生在采集入库之前，本轮新采的老公告不会被误删。"""

    stale_page = _single_source_page(1).replace("2099-01-01", "2020-01-01")
    url = build_list_url(
        page=1, page_size=50, time_type=1, base_url="https://test.okcis.cn"
    )

    result = run_okcis_cycle(
        lambda request_url: (
            (200, stale_page)
            if request_url == url
            else (_ for _ in ()).throw(AssertionError(request_url))
        ),
        sources=_SINGLE_SOURCE,
        db_path=tmp_path / "data" / "tenders.sqlite3",
        pages=1,
        sleep=lambda _: None,
        snapshot_dir=None,
        report_output=tmp_path / "out" / "report.csv",
        digest_output=tmp_path / "out" / "digest.txt",
        review_output=tmp_path / "out" / "review.csv",
    )

    # 若清理发生在采集之后，这条 2020 年的公告会在入库后被删掉；
    # 正确时序（先清理后采集）下它应保留，且清理计数只含本轮之前的旧数据。
    assert result.purged_tenders == 0
    with TenderDatabase(tmp_path / "data" / "tenders.sqlite3") as db:
        titles = {row["title"] for row in db.list_tenders()}
    assert titles == {"测试市摄影服务公告"}


def test_okcis_cycle_skips_cleanup_when_retention_disabled(tmp_path):
    db_path = tmp_path / "data" / "tenders.sqlite3"
    _seed_tenders(db_path)

    result = run_okcis_cycle(
        lambda request_url: (200, _single_source_page(1)),
        sources=_SINGLE_SOURCE,
        db_path=db_path,
        pages=1,
        sleep=lambda _: None,
        snapshot_dir=None,
        report_output=tmp_path / "out" / "report.csv",
        digest_output=tmp_path / "out" / "digest.txt",
        review_output=tmp_path / "out" / "review.csv",
        retention_days=None,
    )

    assert result.purged_tenders == 0
    assert result.purged_snapshots == 0
    with TenderDatabase(db_path) as db:
        titles = {row["title"] for row in db.list_tenders()}
    assert "旧公告-文化馆设备采购" in titles


def test_cleanup_cli_purges_database_and_snapshots(tmp_path, capsys):
    db_path = tmp_path / "data" / "tenders.sqlite3"
    snapshot_dir = tmp_path / "raw"
    snapshot_dir.mkdir()
    old_snapshot = snapshot_dir / "okcis-taixing-001.html"
    old_snapshot.write_text("<html>old</html>", encoding="utf-8")
    os.utime(old_snapshot, (946684800, 946684800))
    _seed_tenders(db_path)

    main(
        [
            "cleanup",
            "--db",
            str(db_path),
            "--retention-days",
            "7",
            "--snapshot-dir",
            str(snapshot_dir),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "retention_days": 7,
        "purged_tenders": 1,
        "purged_snapshots": 1,
        "snapshot_dir": str(snapshot_dir),
    }
    assert not old_snapshot.exists()
    with TenderDatabase(db_path) as db:
        titles = {row["title"] for row in db.list_tenders()}
    assert titles == {"新公告-摄影服务采购"}


def test_cleanup_cli_rejects_non_positive_retention(tmp_path):
    with pytest.raises(SystemExit):
        main(
            [
                "cleanup",
                "--db",
                str(tmp_path / "data" / "tenders.sqlite3"),
                "--retention-days",
                "0",
            ]
        )
