"""公告标题和项目编号的稳定去重键。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher

from .models import TenderRecord


def normalize_title(value: str | None) -> str:
    """去除空白、标点和常见金额单位，保留中文/字母/数字。"""

    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = re.sub(r"(?:万元|万|元)", "", text)
    return "".join(char for char in text if char.isalnum())


def normalize_project_id(value: str | None) -> str | None:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = re.sub(r"[^0-9a-z]+", "", text)
    return text or None


_FUZZY_SUFFIXES = ("更正公告", "变更公告", "采购公告", "招标公告", "中标公告", "成交公告", "合同公告")


def _fuzzy_title_key(value: str | None) -> str:
    text = normalize_title(value)
    suffixes = tuple(normalize_title(suffix) for suffix in _FUZZY_SUFFIXES)
    changed = True
    while changed and text:
        changed = False
        for suffix in suffixes:
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text


def titles_similar(first: str | None, second: str | None, *, threshold: float = 0.95) -> bool:
    """判断两个标题是否足够相似；只供同日候选记录做保守兜底。"""

    if not 0 < threshold <= 1:
        raise ValueError("threshold 必须在 (0, 1] 范围内")
    left = _fuzzy_title_key(first)
    right = _fuzzy_title_key(second)
    if not left or not right:
        return False
    return SequenceMatcher(None, left, right).ratio() >= threshold


def _published_day(value: str | None) -> str:
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value or "")
    return match.group(1) if match else "unknown-day"


def duplicate_key(record: TenderRecord) -> str:
    """优先按项目编号去重，无编号时按标题和发布日期兜底。"""

    project_id = normalize_project_id(record.project_id)
    if project_id:
        return f"project:{project_id}"

    title = normalize_title(record.title)
    if title:
        return f"title:{title}|day:{_published_day(record.published_at)}"

    digest = hashlib.sha256(record.url.encode("utf-8")).hexdigest()
    return f"url:{digest}"
