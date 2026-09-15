"""泰州全域 OKCIS 子站配置。

泰州站页面"所属地区"导航列出 7 个子站：泰州 | 海陵 | 高港 | 兴化 |
靖江 | 泰兴 | 姜堰。2026-09-15 两次实测确认全部子站的静态列表结构
与泰兴一致（列表路径均为 ``/sww/bn/{page}-{page_size}-{time_type}``，
均返回 HTTP 200），因此直接复用 okcis_taixing 的解析器与采集器。

实测补充：泰州市级站本身即为泰州全域聚合站（首页可见泰兴/海陵/姜堰
等区县公告），而高港、姜堰等区县子站体量极小（"最新"窗口仅 1 条）。
7 站全采存在冗余，但由去重机制消化，换取零盲区；后续可用
``analyze-log`` 的分源命中数据决定是否裁撤重复来源。
"""

from __future__ import annotations

from .okcis_jiangsu import OkcisSourceConfig
from .okcis_taixing import SOURCE_NAME as TAIXING_SOURCE_NAME

TAIZHOU_BASE_URL = "https://taizhou.okcis.cn"
HAILING_BASE_URL = "https://hailingqu.okcis.cn"
GAOGANG_BASE_URL = "https://gaogangqu.okcis.cn"
TAIXING_BASE_URL = "https://taixingshi.okcis.cn"
JINGJIANG_BASE_URL = "https://jingjiangshi.okcis.cn"
XINGHUA_BASE_URL = "https://xinghuashi.okcis.cn"
JIANGYAN_BASE_URL = "https://jiangyanshi.okcis.cn"

okcis_taizhou = OkcisSourceConfig(
    name="okcis_taizhou",
    base_url=TAIZHOU_BASE_URL,
    province="江苏",
    city="泰州市",
    snapshot_prefix="okcis-taizhou",
)

okcis_hailing = OkcisSourceConfig(
    name="okcis_hailing",
    base_url=HAILING_BASE_URL,
    province="江苏",
    city="海陵区",
    snapshot_prefix="okcis-hailing",
)

okcis_gaogang = OkcisSourceConfig(
    name="okcis_gaogang",
    base_url=GAOGANG_BASE_URL,
    province="江苏",
    city="高港区",
    snapshot_prefix="okcis-gaogang",
)

okcis_taixing = OkcisSourceConfig(
    name=TAIXING_SOURCE_NAME,
    base_url=TAIXING_BASE_URL,
    province="江苏",
    city="泰兴市",
    snapshot_prefix="okcis-taixing",
)

okcis_jingjiang = OkcisSourceConfig(
    name="okcis_jingjiang",
    base_url=JINGJIANG_BASE_URL,
    province="江苏",
    city="靖江市",
    snapshot_prefix="okcis-jingjiang",
)

okcis_xinghua = OkcisSourceConfig(
    name="okcis_xinghua",
    base_url=XINGHUA_BASE_URL,
    province="江苏",
    city="兴化市",
    snapshot_prefix="okcis-xinghua",
)

okcis_jiangyan = OkcisSourceConfig(
    name="okcis_jiangyan",
    base_url=JIANGYAN_BASE_URL,
    province="江苏",
    city="姜堰区",
    snapshot_prefix="okcis-jiangyan",
)

#: 泰州全域采集顺序：市级站优先，随后按页面地区导航顺序排列各区县站
#: （海陵、高港、泰兴、靖江、兴化、姜堰）。
TAIZHOU_SOURCES: tuple[OkcisSourceConfig, ...] = (
    okcis_taizhou,
    okcis_hailing,
    okcis_gaogang,
    okcis_taixing,
    okcis_jingjiang,
    okcis_xinghua,
    okcis_jiangyan,
)
