"""公开附件下载；不处理登录、CA 或验证码。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .pdf import PdfExtractionResult, extract_pdf_text
from .rate_limit import CircuitOpenError, RequestGuard, is_rate_limited
from .storage import TenderDatabase


@dataclass(frozen=True)
class DownloadResult:
    status: str
    path: Path | None
    bytes_written: int = 0
    content_type: str | None = None
    error: str | None = None


def _safe_filename(url: str, filename: str | None) -> str:
    candidate = Path(filename or Path(urlparse(url).path).name).name
    if not candidate or candidate in {".", ".."}:
        candidate = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return candidate.replace("\x00", "_")


def download_public_attachment(
    url: str,
    destination_dir: str | Path,
    *,
    filename: str | None = None,
    client: httpx.Client | None = None,
    guard: RequestGuard | None = None,
    timeout_seconds: float = 30,
    max_bytes: int = 20 * 1024 * 1024,
) -> DownloadResult:
    """下载一个公开可见附件，超过大小上限或遇到限流则停止。"""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return DownloadResult("ERROR", None, error="仅允许 http/https 公共 URL")
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须大于 0")

    output_dir = Path(destination_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_filename(url, filename)
    owned_client = client is None
    request_client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=True)
    try:
        if guard is not None:
            delay = guard.before_request(page=guard.pages_used + 1)
            if delay:
                time.sleep(delay)
        response = request_client.get(url)
        body_sample = response.content[:4096].decode("utf-8", errors="ignore")
        if is_rate_limited(response.status_code, body_sample, str(response.url)):
            if guard is not None:
                guard.after_response(response.status_code, body_sample, url=str(response.url))
            raise CircuitOpenError("附件来源返回限流信号，已停止自动重试")
        response.raise_for_status()
        if len(response.content) > max_bytes:
            return DownloadResult("ERROR", None, error="附件超过大小上限")
        output_path.write_bytes(response.content)
        if guard is not None:
            guard.after_response(response.status_code, body_sample, url=str(response.url))
        content_type = response.headers.get("content-type", "").split(";", 1)[0] or None
        return DownloadResult("DOWNLOADED", output_path, len(response.content), content_type)
    except CircuitOpenError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        return DownloadResult("ERROR", None, error=f"{type(exc).__name__}: {exc}")
    finally:
        if owned_client:
            request_client.close()


def process_downloaded_attachment(
    db: TenderDatabase,
    attachment_id: int,
    path: str | Path,
) -> PdfExtractionResult:
    """提取已下载附件并把结果回写到附件表。"""

    result = extract_pdf_text(path)
    db.update_attachment_extraction(
        attachment_id,
        file_path=str(path),
        status=result.status,
        text=result.text or None,
    )
    return result
