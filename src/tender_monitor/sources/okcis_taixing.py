"""OKCIS 聚合站静态列表适配器。

默认配置仍然指向泰兴站；通过参数可以复用同一解析器采集江苏省级聚合
入口或其他已核验的地市入口。
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..ingest import ingest_record
from ..models import TenderRecord
from ..rate_limit import (
    CircuitOpenError,
    RateLimitPolicy,
    RequestGuard,
    is_rate_limited,
)
from ..storage import TenderDatabase

BASE_URL = "https://taixingshi.okcis.cn"
LIST_PATH = "/sww/bn/"
SOURCE_NAME = "okcis_taixing"

OKCIS_RATE_LIMIT_POLICY = RateLimitPolicy(
    min_request_interval_seconds=15,
    request_jitter_seconds=5,
    cooldown_seconds=3600,
    max_pages_per_run=2,
    max_records_per_run=100,
)

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_BUDGET_RE = re.compile(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)\s*(万元|万|元)")
_COUNT_RE = re.compile(r"数据共\s*[:：]?\s*([\d,]+)\s*条")
_PAGES_RE = re.compile(r"共\s*([\d,]+)\s*页")


class OkcisListError(ValueError):
    """聚合站列表页结构异常或被验证页替代。"""


@dataclass(frozen=True)
class OkcisListItem:
    title: str
    url: str
    category: str | None
    published_at: str | None
    listed_area: str | None
    budget_raw: str | None
    raw: Mapping[str, object]

    def to_tender_record(
        self,
        *,
        source: str = SOURCE_NAME,
        province: str = "江苏",
        city: str | None = "泰兴市",
    ) -> TenderRecord:
        """把列表摘要转成可入库记录；未标注预算故意保持 UNKNOWN。"""

        resolved_city = city or self.listed_area
        return TenderRecord(
            title=self.title,
            source=source,
            url=self.url,
            announcement_type=self.category,
            province=province,
            city=resolved_city,
            published_at=self.published_at,
            budget_raw=self.budget_raw,
            budget_status="UNKNOWN",
            content=None,
            raw_payload={
                "listed_area": self.listed_area,
                "budget_source": "unlabeled_list_hint" if self.budget_raw else None,
                "detail_access": "captcha_required",
                "raw": dict(self.raw),
            },
        )


@dataclass(frozen=True)
class OkcisListPage:
    current_page: int
    pages: int
    page_size: int
    total: int
    items: tuple[OkcisListItem, ...]


@dataclass(frozen=True)
class OkcisCollectionResult:
    """一次列表采集的可审计结果。"""

    pages_fetched: int
    records_seen: int
    saved_count: int
    status_counts: dict[str, int]
    stopped_reason: str | None = None


def build_list_url(
    *,
    page: int = 1,
    page_size: int = 50,
    time_type: int = 1,
    base_url: str = BASE_URL,
) -> str:
    """构造 OKCIS 列表页 URL。

    ``time_type`` 是站点自身的时间范围参数：1=最新、8=近三天、
    2=近一周、4=近三个月、5=近半年。默认只取“最新”，避免误扫全量历史。
    """

    if page < 1:
        raise ValueError("page 必须从 1 开始")
    if page_size not in {10, 20, 30, 40, 50}:
        raise ValueError("page_size 必须是站点支持的 10/20/30/40/50")
    if time_type not in {1, 8, 2, 4, 5}:
        raise ValueError("time_type 必须是站点支持的 1/8/2/4/5")
    return f"{base_url.rstrip('/')}{LIST_PATH}{page}-{page_size}-{time_type}"


FetchPage = Callable[[str], tuple[int, str]]


def collect_list_pages(
    fetch_page: FetchPage,
    db: TenderDatabase,
    *,
    pages: int = 1,
    page_size: int = 50,
    time_type: int = 1,
    base_url: str = BASE_URL,
    source_name: str = SOURCE_NAME,
    province: str = "江苏",
    city: str | None = "泰兴市",
    snapshot_prefix: str = "okcis-taixing",
    guard: RequestGuard | None = None,
    sleep: Callable[[float], None] = time.sleep,
    snapshot_dir: str | Path | None = None,
) -> OkcisCollectionResult:
    """低频抓取公开列表并写入统一数据库。

    ``fetch_page`` 是网络边界，返回 ``(status_code, decoded_html)``；将它作为
    参数保留后，离线快照可以完整测试，不需要在测试中触碰真实站点。此函数
    绝不请求详情页，因此不会触发 OKCIS 详情验证码。
    """

    if pages < 1:
        raise ValueError("pages 必须大于 0")
    request_guard = guard or RequestGuard(OKCIS_RATE_LIMIT_POLICY)
    snapshot_path = Path(snapshot_dir) if snapshot_dir is not None else None
    if snapshot_path is not None:
        snapshot_path.mkdir(parents=True, exist_ok=True)

    pages_fetched = 0
    records_seen = 0
    saved_count = 0
    status_counts: Counter[str] = Counter()
    stopped_reason: str | None = None

    for page_number in range(1, pages + 1):
        url = build_list_url(
            page=page_number,
            page_size=page_size,
            time_type=time_type,
            base_url=base_url,
        )
        delay = request_guard.before_request(page=page_number)
        sleep(delay)
        status_code, body = fetch_page(url)
        if is_rate_limited(status_code, body, url):
            request_guard.after_response(status_code, body, url=url)
            raise CircuitOpenError("OKCIS 返回限流信号，已停止本次采集并进入冷却")

        parsed = parse_list_page(body, base_url=base_url)
        request_guard.after_response(
            status_code,
            body,
            url=url,
            records_count=len(parsed.items),
        )
        pages_fetched += 1
        if snapshot_path is not None:
            (snapshot_path / f"{snapshot_prefix}-{page_number:03d}.html").write_text(
                body,
                encoding="utf-8",
            )
        if not parsed.items:
            stopped_reason = "列表页为空"
            break

        for item in parsed.items:
            record = item.to_tender_record(
                source=source_name,
                province=province,
                city=city,
            )
            record.raw_payload.update(
                {
                    "list_page": page_number,
                    "list_page_size": page_size,
                    "time_type": time_type,
                }
            )
            result = ingest_record(db, record)
            records_seen += 1
            saved_count += 1
            status_counts[result.decision.status] += 1

        if page_number >= parsed.pages:
            stopped_reason = "已到列表末页"
            break

    if stopped_reason is None and pages_fetched >= pages:
        stopped_reason = "达到本次页数上限"

    return OkcisCollectionResult(
        pages_fetched=pages_fetched,
        records_seen=records_seen,
        saved_count=saved_count,
        status_counts=dict(status_counts),
        stopped_reason=stopped_reason,
    )


def is_detail_captcha_gate(html: str) -> bool:
    """识别详情页验证码门槛，不尝试提交或计算答案。"""

    normalized = html.lower()
    return any(
        marker in normalized
        for marker in (
            "请输入验证码",
            "/php/checkuser/doverify.php",
            "/php/checkuser/yanzhengcode.php",
        )
    )


def _int_input(value: str | None, default: int) -> int:
    if not value:
        return default
    match = re.search(r"\d[\d,]*", value)
    return int(match.group(0).replace(",", "")) if match else default


def parse_list_page(html: str, *, base_url: str = BASE_URL) -> OkcisListPage:
    """解析公开静态列表；预算只保存为提示，不从无标签数字猜金额。"""

    if is_detail_captcha_gate(html):
        raise OkcisListError("列表页被验证码页替代")
    soup = BeautifulSoup(html, "html.parser")
    count_node = soup.select_one('li[name="all_num"]')
    count_text = count_node.get_text(" ", strip=True) if count_node else ""
    total_match = _COUNT_RE.search(count_text)
    if not total_match:
        total_match = _COUNT_RE.search(soup.get_text(" ", strip=True))
    total = _int_input(total_match.group(1) if total_match else None, 0)

    pages_node = soup.select_one("#countnum_pagesize")
    page_size_node = soup.select_one("#countnum_size")
    current_node = soup.select_one("#countnum_page")
    pages = _int_input(pages_node.get("value") if pages_node else None, 1)
    page_size = _int_input(page_size_node.get("value") if page_size_node else None, 50)
    current_page = _int_input(current_node.get("value") if current_node else None, 1)
    if pages == 1:
        pages_match = _PAGES_RE.search(soup.get_text(" ", strip=True))
        pages = _int_input(pages_match.group(1) if pages_match else None, pages)

    items: list[OkcisListItem] = []
    for anchor in soup.select('a[name="result-list-title"]'):
        title = str(anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
        href = str(anchor.get("href") or "").strip()
        if not title or not href:
            continue
        row = anchor.find_parent("ul", class_="dqz_body_all_styleh")
        meta = row.select_one("li.dbas_or") if row else None
        meta_text = meta.get_text(" ", strip=True) if meta else ""
        date_match = _DATE_RE.search(meta_text)
        listed_area = meta_text[: date_match.start()].strip(" |\t") if date_match else None
        budget_match = _BUDGET_RE.search(meta_text)
        category = None
        if meta:
            for category_anchor in meta.select("a"):
                value = category_anchor.get_text(" ", strip=True)
                if value and not _BUDGET_RE.search(value):
                    category = value
                    break
        if listed_area and category:
            listed_area = re.sub(
                rf"^{re.escape(category)}\s*",
                "",
                listed_area,
                count=1,
            ).strip(" |\t")
        if listed_area and budget_match:
            listed_area = listed_area.replace(budget_match.group(0), "", 1)
            listed_area = listed_area.strip(" |\t:：")
        if listed_area:
            listed_area = re.sub(r"\s+", " ", listed_area).strip(" |:：")
        budget_raw = budget_match.group(0).replace(" ", "") if budget_match else None
        items.append(
            OkcisListItem(
                title=title,
                url=urljoin(base_url, href),
                category=category or "bn",
                published_at=date_match.group(1) if date_match else None,
                listed_area=listed_area or None,
                budget_raw=budget_raw,
                raw={
                    "href": href,
                    "rec": anchor.get("rec"),
                    "rec_uniseq": anchor.get("rec_uniseq"),
                    "meta": meta_text,
                },
            )
        )
    return OkcisListPage(
        current_page=current_page,
        pages=pages,
        page_size=page_size,
        total=total,
        items=tuple(items),
    )
