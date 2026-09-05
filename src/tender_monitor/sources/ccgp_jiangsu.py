"""江苏政府采购网来源适配器的第一阶段基础定义。

页面选择器和分页协议暂不在这里猜测；先通过侦察记录确认，再补解析逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

HTTP_BASE_URL = "http://www.ccgp-jiangsu.gov.cn"
SEARCH_PATH = "/jiangsu/cggg_search.html"
LIST_API_PATH = "/pss/jsp/search_cggg.jsp"


@dataclass(frozen=True)
class SearchQuery:
    """与官网脚本参数一一对应的检索条件。

    日期使用毫秒时间戳；正式任务应显式传入最近 24 小时窗口，避免页面默认
    的“近一个月”造成过量翻页。
    """

    purchaser: str = ""
    project_number: str = ""
    region_code: str = ""
    parent_region_code: str = ""
    start_ms: int = 0
    end_ms: int = 0
    agency: str = ""
    announcement_type: str = ""
    title: str = ""
    captcha_code: str = ""
    content: str = ""
    procurement_method: str = ""

    def as_params(self, *, page: int = 1) -> dict[str, object]:
        if page < 1:
            raise ValueError("page 必须从 1 开始")
        return {
            "cgr": self.purchaser,
            "xmbh": self.project_number,
            "qy": self.region_code,
            "pqy": self.parent_region_code,
            "sd": self.start_ms,
            "ed": self.end_ms,
            "dljg": self.agency,
            "cglx": self.announcement_type,
            "bt": self.title,
            "code": self.captcha_code,
            "nr": self.content,
            "cgfs": self.procurement_method,
            "page": page,
        }


def build_search_url(*, base_url: str = HTTP_BASE_URL) -> str:
    """构造公告检索页面入口 URL。

    页面入口本身不按 ``gglb`` 过滤；公告类型和采购方式由页面脚本
    传给 ``LIST_API_PATH`` 的 ``cglx``、``cgfs`` 参数控制。
    """

    return f"{base_url.rstrip('/')}{SEARCH_PATH}"


def build_list_api_url(
    *,
    page: int = 1,
    announcement_type: str = "",
    procurement_method: str = "",
    captcha_code: str = "",
    project_number: str = "",
    start_ms: int = 0,
    end_ms: int = 0,
    region_code: str = "",
    parent_region_code: str = "",
    title: str = "",
    content: str = "",
    purchaser: str = "",
    agency: str = "",
    base_url: str = HTTP_BASE_URL,
) -> str:
    """构造公开列表接口 URL（接口当前要求验证码）。"""

    if page < 1:
        raise ValueError("page 必须从 1 开始")
    query = urlencode(
        SearchQuery(
            purchaser=purchaser,
            project_number=project_number,
            region_code=region_code,
            parent_region_code=parent_region_code,
            start_ms=start_ms,
            end_ms=end_ms,
            agency=agency,
            announcement_type=announcement_type,
            title=title,
            captcha_code=captcha_code,
            content=content,
            procurement_method=procurement_method,
        ).as_params(page=page)
    )
    return f"{base_url.rstrip('/')}{LIST_API_PATH}?{query}"


def build_detail_url(gglb: str, ggid: str, *, base_url: str = HTTP_BASE_URL) -> str:
    """构造省网详情 URL；``gglb`` 在这里才是详情路由参数。"""

    if not gglb or not ggid:
        raise ValueError("gglb 和 ggid 不能为空")
    query = urlencode({"gglb": gglb, "ggid": ggid})
    return f"{base_url.rstrip('/')}/jiangsu/js_cggg/details.html?{query}"
