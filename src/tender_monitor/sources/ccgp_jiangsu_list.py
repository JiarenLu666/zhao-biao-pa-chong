"""江苏政府采购网公告列表接口的响应模型和离线解析器。

列表接口的参数和响应外壳来自 ``cggg_search.js`` 的实测代码：成功响应
使用 ``result.count``、``result.pageNo`` 和 ``result.list``。本模块只做
结构化解析，不负责获取验证码，也不会在错误响应上自动重试。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..models import TenderRecord
from ..normalization import budget_status, parse_budget_from_text
from ..rate_limit import CircuitOpenError, is_rate_limited
from .ccgp_jiangsu import HTTP_BASE_URL, build_detail_url


class ListApiError(ValueError):
    """列表接口返回业务错误或无法识别的响应。"""


@dataclass(frozen=True)
class AnnouncementListItem:
    """列表页提供的一条公告摘要。"""

    title: str
    summary: str | None
    announcement_type: str | None
    item_id: str | None
    item_type: int | None
    source_url: str | None
    province: str | None
    city: str | None
    published_at: str | None
    raw: Mapping[str, Any]
    base_url: str = HTTP_BASE_URL

    def detail_url(self) -> str | None:
        """按页面脚本规则构造详情 URL；非省网详情项返回原始 URL。"""

        if self.item_type == 1 and self.announcement_type and self.item_id:
            return build_detail_url(
                self.announcement_type,
                self.item_id,
                base_url=self.base_url,
            )
        return self.source_url

    def to_tender_record(self) -> TenderRecord:
        """将官方列表摘要转成统一记录；详情字段留待人工核验。"""

        url = self.detail_url()
        if not url:
            raise ValueError("列表项缺少详情 URL")
        budget_yuan, budget_raw = parse_budget_from_text(self.summary)
        return TenderRecord(
            title=self.title,
            source="ccgp_jiangsu",
            url=url,
            announcement_type=self.announcement_type,
            province=self.province or "江苏",
            city=self.city,
            published_at=self.published_at,
            budget_yuan=budget_yuan,
            budget_raw=budget_raw,
            budget_status=budget_status(budget_yuan),
            content=self.summary,
            raw_payload={
                "summary": self.summary,
                "item_id": self.item_id,
                "item_type": self.item_type,
                "detail_access": "public_detail_pending",
                "raw": dict(self.raw),
            },
        )


@dataclass(frozen=True)
class AnnouncementPage:
    """一次列表查询的分页结果。"""

    page_number: int
    total: int
    items: tuple[AnnouncementListItem, ...]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_list_payload(
    payload: Mapping[str, Any],
    *,
    base_url: str = HTTP_BASE_URL,
) -> AnnouncementPage:
    """解析接口 JSON；验证码错误等业务失败会抛出 ``ListApiError``。"""

    if payload.get("code") != 200:
        message = payload.get("message") or payload.get("msg") or "列表接口返回失败"
        raise ListApiError(str(message))

    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ListApiError("列表接口缺少 result 对象")

    try:
        total = int(result.get("count") or 0)
        page_number = int(result.get("pageNo") or 1)
    except (TypeError, ValueError) as exc:
        raise ListApiError("列表接口分页字段格式错误") from exc

    raw_items = result.get("list")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise ListApiError("列表接口 list 字段格式错误")

    items: list[AnnouncementListItem] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise ListApiError("列表接口包含无法解析的列表项")
        items.append(
            AnnouncementListItem(
                title=str(raw_item.get("title") or "").strip(),
                summary=_optional_text(raw_item.get("summary")),
                announcement_type=_optional_text(raw_item.get("ggCode")),
                item_id=_optional_text(raw_item.get("id")),
                item_type=_optional_int(raw_item.get("type")),
                source_url=_optional_text(raw_item.get("url")),
                province=_optional_text(raw_item.get("pZoneName")),
                city=_optional_text(raw_item.get("zoneName")),
                published_at=_optional_text(raw_item.get("publishDate")),
                raw=raw_item,
                base_url=base_url,
            )
        )

    return AnnouncementPage(page_number=page_number, total=total, items=tuple(items))


def parse_list_response(
    status_code: int | None,
    body: str,
    *,
    url: str | None = None,
    base_url: str = HTTP_BASE_URL,
) -> AnnouncementPage:
    """先识别限流，再解析 JSON，避免把错误页/验证码错误当成空结果。"""

    if is_rate_limited(status_code, body, url):
        raise CircuitOpenError("列表接口返回限流信号，已停止自动重试")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ListApiError("列表接口未返回 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ListApiError("列表接口 JSON 顶层格式错误")
    return parse_list_payload(payload, base_url=base_url)
