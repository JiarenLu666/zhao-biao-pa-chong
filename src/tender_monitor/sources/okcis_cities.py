"""江苏 13 个地级市 OKCIS 市级站配置。

13 个市级站的静态列表结构已于 2026-09-15 低频逐站实测（列表路径
``/sww/bn/{page}-{page_size}-{time_type}`` 均返回 HTTP 200，解析器兼容；
南京站 total=221、5 页），与已实测的泰州全域子站共用同一套结构，
因此直接复用 okcis_taixing 的解析器与采集器。

13 个市级站按江苏省行政区划惯用顺序排列：南京、无锡、徐州、常州、
苏州、南通、连云港、淮安、盐城、扬州、镇江、泰州、宿迁。泰州站沿用
okcis_taizhou 中已实测的配置；宿迁为实测确认的特殊拼写
``suqianshi.okcis.cn``，淮安为 ``huaian.okcis.cn``（无 huaianshi）。
每个市级站聚合下属区县内容（泰州、南京均已实证）。若某站结构异常
会在采集时抛出 ``OkcisListError``，可用 ``analyze-log`` 的分源命中
数据决定裁撤个别来源。
"""

from __future__ import annotations

from .okcis_jiangsu import OkcisSourceConfig
from .okcis_taizhou import okcis_taizhou

NANJING_BASE_URL = "https://nanjing.okcis.cn"
WUXI_BASE_URL = "https://wuxi.okcis.cn"
XUZHOU_BASE_URL = "https://xuzhou.okcis.cn"
CHANGZHOU_BASE_URL = "https://changzhou.okcis.cn"
SUZHOU_BASE_URL = "https://suzhou.okcis.cn"
NANTONG_BASE_URL = "https://nantong.okcis.cn"
LIANYUNGANG_BASE_URL = "https://lianyungang.okcis.cn"
HUAIAN_BASE_URL = "https://huaian.okcis.cn"
YANCHENG_BASE_URL = "https://yancheng.okcis.cn"
YANGZHOU_BASE_URL = "https://yangzhou.okcis.cn"
ZHENJIANG_BASE_URL = "https://zhenjiang.okcis.cn"
SUQIAN_BASE_URL = "https://suqianshi.okcis.cn"  # 实测确认的特殊拼写

okcis_nanjing = OkcisSourceConfig(
    name="okcis_nanjing",
    base_url=NANJING_BASE_URL,
    province="江苏",
    city="南京市",
    snapshot_prefix="okcis-nanjing",
)

okcis_wuxi = OkcisSourceConfig(
    name="okcis_wuxi",
    base_url=WUXI_BASE_URL,
    province="江苏",
    city="无锡市",
    snapshot_prefix="okcis-wuxi",
)

okcis_xuzhou = OkcisSourceConfig(
    name="okcis_xuzhou",
    base_url=XUZHOU_BASE_URL,
    province="江苏",
    city="徐州市",
    snapshot_prefix="okcis-xuzhou",
)

okcis_changzhou = OkcisSourceConfig(
    name="okcis_changzhou",
    base_url=CHANGZHOU_BASE_URL,
    province="江苏",
    city="常州市",
    snapshot_prefix="okcis-changzhou",
)

okcis_suzhou = OkcisSourceConfig(
    name="okcis_suzhou",
    base_url=SUZHOU_BASE_URL,
    province="江苏",
    city="苏州市",
    snapshot_prefix="okcis-suzhou",
)

okcis_nantong = OkcisSourceConfig(
    name="okcis_nantong",
    base_url=NANTONG_BASE_URL,
    province="江苏",
    city="南通市",
    snapshot_prefix="okcis-nantong",
)

okcis_lianyungang = OkcisSourceConfig(
    name="okcis_lianyungang",
    base_url=LIANYUNGANG_BASE_URL,
    province="江苏",
    city="连云港市",
    snapshot_prefix="okcis-lianyungang",
)

okcis_huaian = OkcisSourceConfig(
    name="okcis_huaian",
    base_url=HUAIAN_BASE_URL,
    province="江苏",
    city="淮安市",
    snapshot_prefix="okcis-huaian",
)

okcis_yancheng = OkcisSourceConfig(
    name="okcis_yancheng",
    base_url=YANCHENG_BASE_URL,
    province="江苏",
    city="盐城市",
    snapshot_prefix="okcis-yancheng",
)

okcis_yangzhou = OkcisSourceConfig(
    name="okcis_yangzhou",
    base_url=YANGZHOU_BASE_URL,
    province="江苏",
    city="扬州市",
    snapshot_prefix="okcis-yangzhou",
)

okcis_zhenjiang = OkcisSourceConfig(
    name="okcis_zhenjiang",
    base_url=ZHENJIANG_BASE_URL,
    province="江苏",
    city="镇江市",
    snapshot_prefix="okcis-zhenjiang",
)

okcis_suqian = OkcisSourceConfig(
    name="okcis_suqian",
    base_url=SUQIAN_BASE_URL,
    province="江苏",
    city="宿迁市",
    snapshot_prefix="okcis-suqian",
)

#: 江苏 13 个地级市 OKCIS 市级站，按行政区划惯用顺序排列
#: （南京 → 宿迁，泰州复用 okcis_taizhou 中已实测的配置）。
CITY_SOURCES: tuple[OkcisSourceConfig, ...] = (
    okcis_nanjing,
    okcis_wuxi,
    okcis_xuzhou,
    okcis_changzhou,
    okcis_suzhou,
    okcis_nantong,
    okcis_lianyungang,
    okcis_huaian,
    okcis_yancheng,
    okcis_yangzhou,
    okcis_zhenjiang,
    okcis_taizhou,
    okcis_suqian,
)
