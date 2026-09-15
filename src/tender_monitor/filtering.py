"""面向摄影、文旅宣传和公共文化项目的保守筛选。

泰州地区扩词决策（2026-09-15 指挥官拍板）：泰州地区记录额外追加
（“拍照”、“照片”、“冲印”）三个包含词；**明确不加排除词**——
实证目标“电子信息采集拍照及冲印一寸照片比选”含“信息采集”字样，
若配“信息采集”排除词会误杀该目标。泰州扩词带来的噪声由
预算 UNKNOWN → REVIEW 的人工复核环节消化。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import TenderRecord

INCLUDE_GROUPS: dict[str, tuple[str, ...]] = {
    "摄影": ("摄影", "拍摄", "视频", "图片", "视觉", "影像", "采风"),
    "文旅宣传": (
        "文旅",
        "文化旅游",
        "推广",
        "新媒体",
        "短视频",
        "达人",
        "网红",
        "自媒体",
        "宣传",
    ),
    "公共文化": (
        "文化",
        "文艺演出",
        "剧目",
        "非遗",
        "文化遗产",
        "博物馆",
        "图书馆",
        "文化节",
        "文化服务",
        "活动策划",
    ),
}

#: 泰州地区专属扩词：仅在记录归属泰州地区时参与包含匹配。
TAIZHOU_EXTRA_INCLUDE_TERMS = ("拍照", "照片", "冲印")

#: 判定“泰州地区”的地名标记：city 或 province 含任一即视为泰州地区。
#: 覆盖泰州市级站及全部 6 个区县子站（海陵、高港、泰兴、靖江、兴化、姜堰）。
TAIZHOU_REGION_MARKERS = ("泰州", "海陵", "高港", "泰兴", "靖江", "兴化", "姜堰")

#: 泰州扩词命中写入 include_matches 时使用的前缀标注。
TAIZHOU_MATCH_PREFIX = "泰州扩词"

EXCLUDE_TERMS = (
    "工程",
    "施工",
    "建筑",
    "医疗设备",
    "信息化硬件",
    "监理",
    "检测",
    "实验室",
    "维保",
    "X射线",
)
LIFECYCLE_EXCLUDE_TERMS = ("终止公告", "废标公告", "流标公告", "取消公告")
EQUIPMENT_EXCLUDE_TERMS = ("设备采购", "视频会议", "会议系统", "设备维保")
PHOTOGRAPHY_EQUIPMENT_EXCEPTIONS = ("摄影", "拍摄", "图片", "影像")
NON_ACTIONABLE_ANNOUNCEMENT_TYPES = {
    "zbgg",  # 中标公告
    "cjgg",  # 成交公告
    "rwjg",  # 入围结果公告
    "htgg",  # 合同公告
    "zzgg",  # 终止公告
    "fbgg",  # 废标/流标公告
    "ysgg",  # 公共服务验收公告
}
GENERIC_PROMOTION = "宣传"
PROMOTION_SECONDARY = (
    "文旅",
    "文化旅游",
    "推广",
    "新媒体",
    "短视频",
    "达人",
    "网红",
    "自媒体",
)


@dataclass(frozen=True)
class FilterDecision:
    """筛选结论及可解释的命中理由。"""

    status: str
    score: int
    include_matches: tuple[str, ...]
    exclude_matches: tuple[str, ...]
    reasons: tuple[str, ...]


def _contains_generic_promotion(title: str, content: str) -> bool:
    """“宣传”单独出现时不算有效命中，避免普法/党建等噪音。"""

    if GENERIC_PROMOTION not in title and GENERIC_PROMOTION not in content:
        return False
    return any(term in title or term in content for term in PROMOTION_SECONDARY)


def _is_taizhou_region(record: TenderRecord) -> bool:
    """判断记录是否归属泰州地区（city 或 province 含泰州相关地名）。"""

    fields = (record.city or "", record.province or "")
    return any(marker in field for marker in TAIZHOU_REGION_MARKERS for field in fields)


def evaluate_record(record: TenderRecord) -> FilterDecision:
    """按标题+正文评分，并保留 UNKNOWN/超预算状态供人工筛选。"""

    title = (record.title or "").casefold()
    content = (record.content or "").casefold()
    include_matches: list[str] = []
    score = 0

    for group, terms in INCLUDE_GROUPS.items():
        for term in terms:
            if term == GENERIC_PROMOTION and not _contains_generic_promotion(title, content):
                continue
            in_title = term in title
            in_content = term in content
            if in_title or in_content:
                include_matches.append(term)
                score += 3 if in_title else 1
        if group == "摄影" and any(term in title for term in terms):
            score += 1

    # 泰州地区扩词：与普通命中同分值（标题 3 分、正文 1 分），
    # 命中词加“泰州扩词:”前缀标注，便于在报告里区分来源。
    if _is_taizhou_region(record):
        for term in TAIZHOU_EXTRA_INCLUDE_TERMS:
            in_title = term in title
            in_content = term in content
            if in_title or in_content:
                include_matches.append(f"{TAIZHOU_MATCH_PREFIX}:{term}")
                score += 3 if in_title else 1

    # “工程”在代理机构名称中极常见，正文命中不能单独作为排除依据；
    # 其他排除词仍可在正文中保守识别。
    exclude_matches = tuple(
        term
        for term in EXCLUDE_TERMS
        if term in title or (term != "工程" and term in content)
    )
    lifecycle_matches = tuple(term for term in LIFECYCLE_EXCLUDE_TERMS if term in title)
    equipment_matches = tuple(
        term
        for term in EQUIPMENT_EXCLUDE_TERMS
        if term in title
        and not any(exception in title for exception in PHOTOGRAPHY_EQUIPMENT_EXCEPTIONS)
    )
    announcement_type = (record.announcement_type or "").casefold()
    type_matches = (
        (f"公告类型:{announcement_type}",)
        if announcement_type in NON_ACTIONABLE_ANNOUNCEMENT_TYPES
        else ()
    )
    exclude_matches = tuple(
        dict.fromkeys((*exclude_matches, *lifecycle_matches, *equipment_matches, *type_matches))
    )
    reasons: list[str] = []
    if include_matches:
        reasons.append("命中目标关键词：" + "、".join(dict.fromkeys(include_matches)))
    if exclude_matches:
        reasons.append("命中排除词：" + "、".join(exclude_matches))
        return FilterDecision("FILTERED", score, tuple(include_matches), exclude_matches, tuple(reasons))
    if score == 0:
        reasons.append("未命中有效目标关键词")
        return FilterDecision("NO_MATCH", 0, (), exclude_matches, tuple(reasons))
    if record.budget_status == "FILTERED":
        reasons.append("预算超过 30 万元")
        return FilterDecision("FILTERED", score, tuple(include_matches), (), tuple(reasons))
    if record.budget_status == "UNKNOWN":
        reasons.append("预算无法从锚定字段确认，需人工复核")
        return FilterDecision("REVIEW", score, tuple(include_matches), (), tuple(reasons))
    if record.budget_status == "OVER_BUDGET":
        reasons.append("预算处于 20–30 万元区间，需人工确认")
        return FilterDecision("OVER_BUDGET", score, tuple(include_matches), (), tuple(reasons))
    return FilterDecision("MATCH", score, tuple(include_matches), (), tuple(reasons))
