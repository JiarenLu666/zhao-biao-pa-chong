import httpx

from tender_monitor.attachments import (
    download_public_attachment,
    process_downloaded_attachment,
)
from tender_monitor.models import TenderRecord
from tender_monitor.rate_limit import CircuitOpenError
from tender_monitor.storage import TenderDatabase


def test_download_public_attachment_writes_content_with_safe_filename(tmp_path):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-public-sample",
            )
        )
    )
    try:
        result = download_public_attachment(
            "https://example.invalid/files/../../采购文件.pdf",
            tmp_path,
            client=client,
        )
    finally:
        client.close()
    assert result.status == "DOWNLOADED"
    assert result.path is not None
    assert result.path.parent == tmp_path
    assert result.path.read_bytes() == b"%PDF-public-sample"


def test_download_public_attachment_fails_closed_on_rate_limit(tmp_path):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content="访问过于频繁，请稍后再试".encode(),
            )
        )
    )
    try:
        try:
            download_public_attachment("https://example.invalid/file.pdf", tmp_path, client=client)
        except CircuitOpenError:
            pass
        else:
            raise AssertionError("附件限流响应不能继续下载")
    finally:
        client.close()


def test_process_downloaded_attachment_records_extraction_status(tmp_path):
    db = TenderDatabase(tmp_path / "tenders.sqlite3")
    with db:
        tender_id = db.upsert(
            TenderRecord(
                title="附件测试",
                source="test",
                url="https://example.invalid/tender",
            )
        )
        db.upsert_attachments(
            tender_id,
            [{"name": "采购文件.pdf", "url": "https://example.invalid/file.pdf"}],
        )
        attachment_id = db.list_attachments(tender_id)[0]["id"]
        result = process_downloaded_attachment(db, attachment_id, tmp_path / "missing.pdf")
        row = db.list_attachments(tender_id)[0]
    assert result.status == "ERROR"
    assert row["extraction_status"] == "ERROR"
