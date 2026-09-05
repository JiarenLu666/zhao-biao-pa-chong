"""公开 PDF 附件的保守文本提取。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


@dataclass(frozen=True)
class PdfExtractionResult:
    status: str
    text: str
    pages: int = 0
    error: str | None = None


def extract_pdf_text(path: str | Path) -> PdfExtractionResult:
    """提取文字型 PDF；扫描件返回 EMPTY，损坏/不可读文件返回 ERROR。"""

    pdf_path = Path(path)
    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            return PdfExtractionResult("ENCRYPTED", "", len(reader.pages), "PDF 需要密码或登录")
        chunks: list[str] = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
    except (OSError, PdfReadError, TypeError, ValueError) as exc:
        return PdfExtractionResult("ERROR", "", 0, f"{type(exc).__name__}: {exc}")

    text = "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()
    if not text:
        return PdfExtractionResult("EMPTY", "", len(reader.pages), "未提取到文字，可能是扫描件")
    return PdfExtractionResult("EXTRACTED", text, len(reader.pages))
