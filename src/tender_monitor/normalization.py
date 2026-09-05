"""公告文本和预算金额的保守解析。"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import ClassVar


class _TextExtractor(HTMLParser):
    """将公告 HTML 转成带段落换行的纯文本。"""

    _BLOCK_TAGS: ClassVar[set[str]] = {
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def html_to_text(value: str | None) -> str:
    """提取公告正文文本；输入本身也可以是纯文本。"""

    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()


_AMOUNT_RE = re.compile(
    r"(?P<amount>\d[\d,，]*(?:\.\d+)?)\s*(?P<unit>万元|万|元)"
)
_BUDGET_LABELS = ("预算金额", "采购预算", "项目预算", "最高限价")


def parse_budget_from_text(value: str | None) -> tuple[float | None, str | None]:
    """从明确的预算标签后提取金额，返回 (人民币元, 原始金额片段)。

    标签后找不到带单位的阿拉伯数字时返回 ``(None, None)``，宁可进入
    UNKNOWN，也不把年份、项目编号或日期误判为预算。
    """

    text = html_to_text(value)
    for label in _BUDGET_LABELS:
        for match in re.finditer(re.escape(label), text):
            window = text[match.end() : match.end() + 100]
            amount_match = _AMOUNT_RE.search(window)
            if not amount_match:
                continue
            raw = amount_match.group(0).replace("，", ",")
            number = float(amount_match.group("amount").replace(",", ""))
            unit = amount_match.group("unit")
            if unit in ("万", "万元"):
                number *= 10000
            return number, raw
    return None, None


def budget_status(
    budget_yuan: float | None,
    *,
    normal_limit_yuan: float = 200_000,
    review_limit_yuan: float = 300_000,
) -> str:
    """按业务目标标记预算，不代表法定采购方式。"""

    if budget_yuan is None:
        return "UNKNOWN"
    if budget_yuan <= normal_limit_yuan:
        return "NORMAL"
    if budget_yuan <= review_limit_yuan:
        return "OVER_BUDGET"
    return "FILTERED"


def labeled_value(value: str | None, label: str) -> str | None:
    """提取一个简单的“标签：值”字段；复杂字段交给专用解析器。"""

    text = html_to_text(value)
    match = re.search(re.escape(label) + r"\s*[：:]\s*([^\n]{1,200})", text)
    return match.group(1).strip() if match else None
