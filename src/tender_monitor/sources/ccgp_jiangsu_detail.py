"""江苏政府采购网公开详情接口。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from ..models import TenderRecord
from ..normalization import (
    budget_status,
    html_to_text,
    labeled_value,
    parse_budget_from_text,
)

_PROJECT_ID_RE = re.compile(
    r"(?:项目编号|招标编号|采购计划备案号)\s*[：:]?\s*([A-Z0-9][A-Z0-9-]{6,})",
    re.IGNORECASE,
)


def parse_detail_payload(payload: Mapping[str, Any], url: str) -> TenderRecord:
    """将详情接口 JSON 转为跨来源统一记录。"""

    if payload.get("msg") != "OK" or not isinstance(payload.get("data"), Mapping):
        raise ValueError("详情接口未返回可解析数据")

    data = payload["data"]
    raw_content = str(data.get("content") or "")
    content = html_to_text(raw_content)
    budget_yuan, budget_raw = parse_budget_from_text(content)
    project_match = _PROJECT_ID_RE.search(content) or _PROJECT_ID_RE.search(
        str(data.get("summary") or "")
    )
    project_id = project_match.group(1) if project_match else None
    procurement_method = labeled_value(content, "采购方式")
    detail_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

    return TenderRecord(
        title=str(data.get("title") or "").strip(),
        source="ccgp_jiangsu",
        url=url,
        announcement_type=data.get("ggCode"),
        procurement_method=procurement_method,
        project_id=project_id,
        province=str(data.get("pZoneName") or "江苏").strip() or "江苏",
        city=str(data.get("zoneName") or "").strip() or None,
        published_at=data.get("publishDate"),
        budget_yuan=budget_yuan,
        budget_raw=budget_raw,
        budget_status=budget_status(budget_yuan),
        content=content,
        detail_hash=detail_hash,
        raw_payload={
            "summary": data.get("summary"),
            "projId": data.get("projId"),
            "ggCode": data.get("ggCode"),
            "files": data.get("files") or [],
            "raw_content": raw_content,
        },
    )
