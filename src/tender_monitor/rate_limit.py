"""对公开来源请求做低频、预算和限流熔断保护。

这个模块不尝试识别或绕过验证码。它只负责在调用列表接口之前控制请求
节奏，并在站点明确返回限流信号后 fail closed，避免自动重试继续放大封禁。
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass


class CircuitOpenError(RuntimeError):
    """来源已返回限流信号，当前请求通道处于冷却状态。"""


class BudgetExceededError(RuntimeError):
    """单次运行已达到预设的页数或记录数上限。"""


@dataclass(frozen=True)
class RateLimitPolicy:
    """单来源运行的保守请求策略。

    ``min_request_interval_seconds`` 是两次请求之间的最低间隔，
    ``request_jitter_seconds`` 会为每次间隔增加 0 到该值之间的随机抖动。
    """

    min_request_interval_seconds: float = 10.0
    request_jitter_seconds: float = 5.0
    cooldown_seconds: float = 3600.0
    max_pages_per_run: int = 10
    max_records_per_run: int = 100

    def __post_init__(self) -> None:
        if self.min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds 不能为负数")
        if self.request_jitter_seconds < 0:
            raise ValueError("request_jitter_seconds 不能为负数")
        if self.cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds 必须大于 0")
        if self.max_pages_per_run <= 0:
            raise ValueError("max_pages_per_run 必须大于 0")
        if self.max_records_per_run <= 0:
            raise ValueError("max_records_per_run 必须大于 0")


def is_rate_limited(
    status_code: int | None,
    body: str | None,
    url: str | None = None,
) -> bool:
    """判断 HTTP 响应是否表现为站点限流。

    江苏站点可能用 429/401，也可能返回 HTTP 200 后展示
    ``overLimitIP.html``。因此不能只依赖状态码。
    """

    if status_code in {401, 429}:
        return True
    normalized = f"{body or ''} {url or ''}".lower()
    markers = (
        "访问过于频繁",
        "频繁访问",
        "overlimitip",
        "over limit",
    )
    return any(marker in normalized for marker in markers)


class RequestGuard:
    """在来源请求边界实施节流、运行预算和熔断。

    ``before_request`` 返回调用方需要等待的秒数；它不会在库内部阻塞，
    便于 CLI、异步任务和测试分别决定如何等待。收到响应后必须调用
    ``after_response``，限流响应会打开冷却闸门且不会自动重试。
    """

    def __init__(
        self,
        policy: RateLimitPolicy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self.policy = policy or RateLimitPolicy()
        self._clock = clock
        self._random_source = random_source
        self._next_request_at: float | None = None
        self._blocked_until = 0.0
        self._pages_used = 0
        self._records_seen = 0

    @property
    def pages_used(self) -> int:
        return self._pages_used

    @property
    def records_seen(self) -> int:
        return self._records_seen

    @property
    def blocked_until(self) -> float:
        return self._blocked_until

    def before_request(self, *, page: int) -> float:
        """预留一个请求槽位并返回所需等待时间。"""

        if page < 1:
            raise ValueError("page 必须从 1 开始")

        now = self._clock()
        if now < self._blocked_until:
            remaining = math.ceil(self._blocked_until - now)
            raise CircuitOpenError(f"来源处于冷却期，约 {remaining} 秒后再试")

        if self._pages_used >= self.policy.max_pages_per_run:
            raise BudgetExceededError("已达到单次运行最大请求页数，主动停止")
        if self._records_seen >= self.policy.max_records_per_run:
            raise BudgetExceededError("已达到单次运行最大记录数，主动停止")

        interval = self.policy.min_request_interval_seconds + (
            self._random_source() * self.policy.request_jitter_seconds
        )
        next_request_at = max(now, self._next_request_at or now)
        delay = max(0.0, next_request_at - now)
        self._next_request_at = next_request_at + interval
        self._pages_used += 1
        return delay

    def after_response(
        self,
        status_code: int | None,
        body: str | None,
        *,
        url: str | None = None,
        records_count: int = 0,
    ) -> None:
        """记录响应；限流时打开熔断，记录数超预算时停止。"""

        if records_count < 0:
            raise ValueError("records_count 不能为负数")

        if is_rate_limited(status_code, body, url):
            self._blocked_until = max(
                self._blocked_until,
                self._clock() + self.policy.cooldown_seconds,
            )
            return

        self._records_seen += records_count
        if self._records_seen > self.policy.max_records_per_run:
            raise BudgetExceededError("响应记录数超过单次运行预算，主动停止")
