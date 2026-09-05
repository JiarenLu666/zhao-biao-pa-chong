"""江苏省级 OKCIS 聚合入口配置。

该入口和地市入口使用同一套静态列表结构。它只负责发现公开列表元数据，
不替代江苏政府采购网的官方核验，也不访问需要验证码的详情页。
"""

from __future__ import annotations

from dataclasses import dataclass

BASE_URL = "https://jiangsu.okcis.cn"
SOURCE_NAME = "okcis_jiangsu"
CITY: str | None = None
SNAPSHOT_PREFIX = "okcis-jiangsu"


@dataclass(frozen=True)
class OkcisSourceConfig:
    """一个 OKCIS 入口的采集元数据。"""

    name: str
    base_url: str
    province: str = "江苏"
    city: str | None = None
    snapshot_prefix: str = "okcis-jiangsu"


JIANGSU_SOURCE = OkcisSourceConfig(
    name=SOURCE_NAME,
    base_url=BASE_URL,
    province="江苏",
    city=CITY,
    snapshot_prefix=SNAPSHOT_PREFIX,
)

