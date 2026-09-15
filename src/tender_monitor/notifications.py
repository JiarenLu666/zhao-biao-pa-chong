"""推送前的短摘要格式化与通知渠道发送。

凭据只由调用方传入，库层不读取、不保存任何本地配置或密钥。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from .follow_up import ACTIONABLE_FILTER_STATUSES, COMPLETED_FOLLOW_UP_STATUSES


class NotificationError(RuntimeError):
    """通知渠道请求失败或渠道返回业务错误。"""


def _field(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        return default


def actionable_rows(rows: Iterable[Any]) -> list[Any]:
    """筛出需要提醒的目标候选，排除已完成或明确不跟进的项目。"""

    return [
        row
        for row in rows
        if _field(row, "filter_status") in ACTIONABLE_FILTER_STATUSES
        and _field(row, "follow_up_status") not in COMPLETED_FOLLOW_UP_STATUSES
    ]


def format_digest(
    rows: Iterable[Any],
    *,
    max_items: int = 20,
    now: datetime | None = None,
) -> str:
    """生成只含关键字段和原文链接的通知文本。"""

    if max_items <= 0:
        raise ValueError("max_items 必须大于 0")
    reviewable = actionable_rows(rows)
    selected = reviewable[:max_items]
    verified_count = sum(
        _field(row, "verification_status") == "VERIFIED" for row in reviewable
    )
    if verified_count and verified_count == len(reviewable):
        count_label = "已核验待跟进"
    elif verified_count:
        count_label = "待复核/跟进"
    else:
        count_label = "待复核"
    china_timezone = timezone(timedelta(hours=8))
    timestamp = (now or datetime.now(tz=china_timezone)).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"江苏文化采购公告（{timestamp}）",
        f"{count_label} {len(reviewable)} 条，展示前 {len(selected)} 条",
    ]
    for index, row in enumerate(selected, start=1):
        title = str(_field(row, "title") or "未命名公告").strip()
        city = str(_field(row, "city") or _field(row, "province") or "江苏").strip()
        budget = str(_field(row, "budget_raw") or "预算未知").strip()
        status = str(_field(row, "filter_status") or "REVIEW")
        url = str(_field(row, "url") or "").strip()
        lines.append(f"{index}. [{status}] {title}｜{city}｜{budget}")
        if url:
            lines.append(f"   {url}")
    if not selected:
        lines.append("暂无需要人工复核的新增公告。")
    return "\n".join(lines)


PUSHPLUS_API_URL = "https://www.pushplus.plus/send"

SERVERCHAN_API_BASE = "https://sctapi.ftqq.com"

_SERVERCHAN3_UID_RE = re.compile(r"^sctp(\d+)t", re.IGNORECASE)


def _serverchan_endpoint(sendkey: str) -> str:
    """按 SendKey 前缀构造 Server酱 的发送端点。

    - Turbo：``SCT`` 开头（``SCTP`` 除外），端点为 ``sctapi.ftqq.com``。
    - Server酱³：``sctp{uid}t...`` 开头，端点为 ``https://{uid}.push.ft07.com``。
      两者都接受 POST form 的 ``title``/``desp`` 且以 ``code=0`` 表示成功，
      因此发送函数无需区分版本。
    """

    normalized = sendkey.strip()
    if not normalized:
        raise ValueError("Server酱 SendKey 不能为空")
    upper = normalized.upper()
    if upper.startswith("SCTP"):
        uid_match = _SERVERCHAN3_UID_RE.match(normalized)
        if uid_match is None:
            raise ValueError("Server酱³ SendKey 格式无法识别（应为 sctp{uid}t... 开头）")
        return f"https://{uid_match.group(1)}.push.ft07.com/send/{normalized}.send"
    if not upper.startswith("SCT"):
        raise ValueError(
            "当前仅支持 Server酱 Turbo（SCT 开头）或 Server酱³（sctp 开头）SendKey"
        )
    return f"{SERVERCHAN_API_BASE}/{normalized}.send"


def send_serverchan_message(
    text: str,
    sendkey: str,
    *,
    title: str = "江苏文化采购公告",
    client: httpx.Client | None = None,
    timeout_seconds: float = 15,
    endpoint: str | None = None,
) -> None:
    """通过 Server酱 Turbo 或 Server酱³ 发送一条文本消息。

    SendKey 前缀决定端点：``SCT`` 开头走 Turbo（微信），
    ``sctp`` 开头走 Server酱³（App）。
    ``client`` 和 ``endpoint`` 只用于测试或调用方复用 HTTP 会话；生产调用
    默认按前缀自动选择官方端点。SendKey 不会出现在异常信息中。
    """

    message = text.strip()
    if not message:
        raise ValueError("通知内容不能为空")
    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("通知标题不能为空")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    normalized_endpoint = (endpoint or _serverchan_endpoint(sendkey)).strip()
    parts = urlsplit(normalized_endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Server酱 API 地址必须是 http(s) 地址")

    owned_client = client is None
    request_client = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = request_client.post(
            normalized_endpoint,
            data={"title": normalized_title, "desp": message},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NotificationError(f"Server酱请求失败：{exc.__class__.__name__}") from exc
    finally:
        if owned_client:
            request_client.close()

    try:
        result = response.json()
    except ValueError as exc:
        raise NotificationError("Server酱返回了无法解析的响应") from exc
    if not isinstance(result, Mapping):
        raise NotificationError("Server酱返回格式无法识别")
    error_code = result.get("code")
    if error_code not in (0, "0"):
        error_message = str(result.get("message") or result.get("msg") or "未提供错误信息")
        raise NotificationError(f"Server酱返回错误 {error_code}：{error_message}")


def send_pushplus_message(
    text: str,
    token: str,
    *,
    title: str = "江苏文化采购公告",
    channel: str = "wechat",
    option: str | None = None,
    client: httpx.Client | None = None,
    timeout_seconds: float = 15,
    endpoint: str = PUSHPLUS_API_URL,
) -> None:
    """通过 PushPlus 发送一条文本消息到微信或 QQ。

    ``client`` 仅用于测试或调用方复用 HTTP 会话；生产调用默认创建短生命周期
    客户端。Token 不会出现在异常信息中。
    """

    message = text.strip()
    if not message:
        raise ValueError("通知内容不能为空")
    normalized_token = token.strip()
    if not normalized_token:
        raise ValueError("PushPlus Token 不能为空")
    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("通知标题不能为空")
    normalized_channel = channel.strip().lower()
    if normalized_channel not in {"wechat", "qq"}:
        raise ValueError("PushPlus channel 只支持 wechat 或 qq")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    normalized_endpoint = endpoint.strip()
    parts = urlsplit(normalized_endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("PushPlus API 地址必须是 http(s) 地址")
    payload = {
        "token": normalized_token,
        "title": normalized_title,
        "content": message,
        "template": "txt",
        "channel": normalized_channel,
    }
    if option and option.strip():
        payload["option"] = option.strip()
    owned_client = client is None
    request_client = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = request_client.post(
            normalized_endpoint,
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NotificationError(f"PushPlus 请求失败：{exc.__class__.__name__}") from exc
    finally:
        if owned_client:
            request_client.close()

    try:
        result = response.json()
    except ValueError as exc:
        raise NotificationError("PushPlus 返回了无法解析的响应") from exc
    if not isinstance(result, Mapping):
        raise NotificationError("PushPlus 返回格式无法识别")
    error_code = result.get("code")
    if error_code not in (200, "200"):
        error_message = str(result.get("errmsg") or "未提供错误信息")
        if error_message == "未提供错误信息":
            error_message = str(result.get("msg") or error_message)
        raise NotificationError(f"PushPlus 返回错误 {error_code}：{error_message}")
