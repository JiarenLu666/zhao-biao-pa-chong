"""保留浏览器会话的人工验证码采集入口。

验证码必须由用户在可见浏览器中手工输入；本模块不识别、破解或转交验证码。
默认只查当天、低频翻页，并在限流页出现时立即终止。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from ..rate_limit import (
    CircuitOpenError,
    RateLimitPolicy,
    RequestGuard,
    is_rate_limited,
)
from .ccgp_jiangsu import HTTP_BASE_URL, SearchQuery, build_search_url
from .ccgp_jiangsu_list import AnnouncementListItem


class ManualQueryError(RuntimeError):
    """浏览器查询失败，需要人工检查页面状态。"""


@dataclass(frozen=True)
class ManualQueryResult:
    total: int
    pages: int
    items: tuple[AnnouncementListItem, ...]


def _wait_for_manual_captcha_entry() -> str:
    """等待用户直接在可见浏览器的验证码框中输入。"""

    input("请直接在浏览器验证码框中手工输入，完成后回终端按回车继续：")
    return ""


def parse_rendered_rows(
    html: str,
    *,
    base_url: str = HTTP_BASE_URL,
) -> tuple[AnnouncementListItem, ...]:
    """解析页面脚本渲染出的表格行，不发起任何请求。"""

    soup = BeautifulSoup(html, "html.parser")
    items: list[AnnouncementListItem] = []
    for row in soup.select("tbody.conn_list_items tr"):
        cells = row.select("td")
        if len(cells) < 4:
            continue
        href = row.get("href") or ""
        absolute_url = urljoin(base_url, href)
        query = parse_qs(urlparse(absolute_url).query)
        announcement_type = (query.get("gglb") or [None])[0]
        item_id = (query.get("ggid") or [None])[0]
        area = cells[2].get_text(" ", strip=True)
        province, _, city = area.partition(" - ")
        items.append(
            AnnouncementListItem(
                title=cells[1].get_text(" ", strip=True),
                summary=None,
                announcement_type=announcement_type,
                item_id=item_id,
                item_type=1 if item_id else None,
                source_url=absolute_url if href else None,
                province=province or None,
                city=city or None,
                published_at=cells[3].get_text(" ", strip=True) or None,
                raw={"href": href, "cells": [cell.get_text(" ", strip=True) for cell in cells]},
                base_url=base_url,
            )
        )
    return tuple(items)


def parse_rendered_total(html: str) -> int | None:
    """读取页面可选的总条数；当前页面没有总数时返回 ``None``。"""

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    match = re.search(r"(?:共|总计)\s*([\d,]+)\s*条", text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _date_text(timestamp_ms: int) -> str:
    china_timezone = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=china_timezone).strftime("%Y-%m-%d")


def collect_manual_query(
    query: SearchQuery,
    *,
    max_pages: int = 10,
    headless: bool = False,
    wait_timeout_ms: int = 15_000,
    captcha_reader: Callable[[], str] | None = None,
    snapshot_dir: str | Path | None = None,
) -> ManualQueryResult:
    """启动可见浏览器，人工输入一次验证码后低频采集列表页。

    该函数需要安装可选的 ``browser`` 依赖，并且不会在无人值守模式下运行。
    默认提示用户直接在浏览器输入框填写；传入 ``captcha_reader`` 仅用于测试或
    已由用户手工提供的字符串，不接入任何识别服务。
    """

    if headless:
        raise ValueError("人工验证码模式必须使用可见浏览器（headless=False）")
    if max_pages <= 0:
        raise ValueError("max_pages 必须大于 0")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ManualQueryError("请安装 browser 可选依赖：pip install -e '.[browser]'") from exc

    reader = captcha_reader or _wait_for_manual_captcha_entry
    guard = RequestGuard(RateLimitPolicy(max_pages_per_run=max_pages))
    all_items: list[AnnouncementListItem] = []
    snapshot_path = Path(snapshot_dir) if snapshot_dir is not None else None
    if snapshot_path is not None:
        snapshot_path.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            page.goto(build_search_url(), wait_until="domcontentloaded", timeout=wait_timeout_ms)
            if query.start_ms and query.end_ms:
                page.locator('#date a[data="-1"]').click()
                page.locator("#inpstart").evaluate(
                    "(el, value) => { el.value = value; }", _date_text(query.start_ms)
                )
                page.locator("#inpend").evaluate(
                    "(el, value) => { el.value = value; }", _date_text(query.end_ms)
                )
            else:
                page.locator('#date a[data="0"]').click()
            if query.parent_region_code:
                page.locator("#zone").select_option(query.parent_region_code)
                page.wait_for_timeout(300)
            if query.region_code:
                page.locator(f'#zone_c a[data="{query.region_code}"]').click()
            if query.announcement_type:
                page.locator(f'#cgtype a[data="{query.announcement_type}"]').click()
            if query.procurement_method:
                page.locator(f'#cgfs a[data="{query.procurement_method}"]').click()
            if query.title:
                page.locator("#ipt-keyword").fill(query.title)
            captcha = query.captcha_code or reader()
            if captcha.strip():
                # 仅兼容显式传入的人工字符串；默认流程要求用户直接填写浏览器输入框。
                page.locator("#validateCode").fill(captcha.strip())
            elif not page.locator("#validateCode").input_value().strip():
                raise ManualQueryError("验证码输入框为空，已停止查询")

            total = 0
            for page_number in range(1, max_pages + 1):
                delay = guard.before_request(page=page_number)
                if delay:
                    time.sleep(delay)
                if page_number == 1:
                    page.locator(".q_submit").click()
                else:
                    page_link = page.locator(f'.page li[data-page="{page_number}"]')
                    if page_link.count() == 0:
                        break
                    page_link.click()
                page.wait_for_function(
                    """() => location.href.includes('overLimitIP') ||
                        document.querySelector('.empty_data')?.textContent.trim() ||
                        document.querySelector('#errmsg')?.textContent.trim() ||
                        (document.querySelector('.conn_list_items') &&
                         document.querySelectorAll('.conn_list_items tr').length > 0)""",
                    timeout=wait_timeout_ms,
                )
                rendered = page.content()
                if snapshot_path is not None:
                    (snapshot_path / f"ccgp-jiangsu-{page_number:03d}.html").write_text(
                        rendered,
                        encoding="utf-8",
                    )
                if is_rate_limited(200, rendered, page.url):
                    guard.after_response(200, rendered, records_count=0)
                    raise CircuitOpenError("列表页返回限流信号，已停止自动重试")
                error_text = page.locator("#errmsg").inner_text().strip()
                if error_text:
                    raise ManualQueryError(error_text)
                current_items = parse_rendered_rows(rendered)
                parsed_total = parse_rendered_total(rendered)
                if parsed_total is not None:
                    total = parsed_total
                else:
                    total = max(total, len(all_items) + len(current_items))
                all_items.extend(current_items)
                guard.after_response(200, rendered, records_count=len(current_items))
                if not current_items or (parsed_total is not None and len(all_items) >= total):
                    break
            return ManualQueryResult(total=total, pages=guard.pages_used, items=tuple(all_items))
        finally:
            try:
                browser.close()
            except PlaywrightError:
                # 用户按 Ctrl-C 中断等待时，Playwright driver 可能已先退出。
                pass
