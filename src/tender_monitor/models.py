"""领域数据结构。

这里只定义跨来源都需要的字段；来源特有字段应保留在 raw_payload 中，
避免把某一个网站的页面结构固化成全局模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TenderRecord:
    """一条公告的规范化记录。金额统一使用人民币元。"""

    title: str
    source: str
    url: str
    announcement_type: str | None = None
    procurement_method: str | None = None
    project_id: str | None = None
    province: str = "江苏"
    city: str | None = None
    district: str | None = None
    published_at: str | None = None
    deadline: str | None = None
    budget_yuan: float | None = None
    budget_raw: str | None = None
    budget_status: str = "UNKNOWN"
    content: str | None = None
    raw_hash: str | None = None
    detail_hash: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
