"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import __version__
from .attention import write_attention
from .automation import CycleAlreadyRunningError, run_automatic_cycle
from .dashboard import initialize_project, run_dashboard
from .follow_up import ACTIONABLE_FILTER_STATUSES
from .ingest import ingest_record
from .notifications import (
    NotificationError,
    actionable_rows,
    format_digest,
    send_pushplus_message,
    send_serverchan_message,
)
from .rate_limit import BudgetExceededError, CircuitOpenError, RequestGuard
from .report import (
    export_csv,
    export_json,
    export_review_queue,
    refresh_outputs,
)
from .sources.browser_manual import ManualQueryError, collect_manual_query
from .sources.ccgp_jiangsu import SearchQuery
from .sources.ccgp_jiangsu_detail import parse_detail_payload
from .sources.okcis_jiangsu import JIANGSU_SOURCE
from .sources.okcis_taixing import OkcisListError, collect_list_pages
from .storage import TenderDatabase

_FETCH_RETRY_DELAYS = (15.0, 30.0)
_TRANSIENT_FETCH_ERRORS = (
    httpx.NetworkError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)


def _fetch_page_with_retries(
    fetch_page: Callable[[str], tuple[int, str]],
    url: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    retry_delays: tuple[float, ...] = _FETCH_RETRY_DELAYS,
) -> tuple[int, str]:
    """对瞬时传输错误做有限退避重试，429/验证码仍由上层立即熔断。"""

    for retry_delay in (*retry_delays, None):
        try:
            return fetch_page(url)
        except _TRANSIENT_FETCH_ERRORS:
            if retry_delay is None:
                raise
            # 默认退避不低于 OKCIS 的最小请求间隔，避免重试放大限流。
            sleep(retry_delay)
    raise AssertionError("unreachable")


def _date_range_ms(start_date: str | None, end_date: str | None) -> tuple[int, int]:
    """将中国时区日期边界转为官网所需的毫秒时间戳。"""

    if not start_date and not end_date:
        return 0, 0
    if not start_date or not end_date:
        raise ValueError("--start-date 和 --end-date 必须同时提供")
    china_timezone = timezone(timedelta(hours=8))
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=china_timezone)
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=china_timezone)
    except ValueError as exc:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式") from exc
    if end < start:
        raise ValueError("--end-date 不能早于 --start-date")
    end = end + timedelta(days=1) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="江苏文化招标信息采集器")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("db-init", help="初始化 SQLite 数据库")
    init_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")

    setup_parser = subparsers.add_parser(
        "setup",
        help="首次使用时创建本地目录和数据库",
    )
    setup_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="启动本地 Dashboard",
    )
    dashboard_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    dashboard_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址，默认仅本机访问",
    )
    dashboard_parser.add_argument("--port", type=int, default=8765, help="监听端口")
    dashboard_parser.add_argument(
        "--open",
        action="store_true",
        help="启动后自动打开浏览器",
    )
    dashboard_parser.add_argument(
        "--allow-write",
        action="store_true",
        default=None,
        help="允许网页修改跟进状态；局域网模式默认只读",
    )

    reclassify_parser = subparsers.add_parser(
        "reclassify",
        help="按当前关键词规则重新评估已有公告",
    )
    reclassify_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")

    import_parser = subparsers.add_parser("import-detail", help="从详情 JSON 快照导入一条公告")
    import_parser.add_argument("payload", type=Path, help="详情接口 JSON 文件")
    import_parser.add_argument("--url", required=True, help="公告详情原文 URL")
    import_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")

    verify_parser = subparsers.add_parser(
        "verify",
        help="将人工核验结果合并回已有公告",
    )
    verify_parser.add_argument("--url", required=True, help="数据库中已有的公告 URL")
    verify_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    verify_parser.add_argument("--project-id", help="人工确认的项目编号")
    verify_parser.add_argument("--budget-yuan", type=float, help="人工确认的预算（人民币元）")
    verify_parser.add_argument("--budget-raw", help="原文预算写法，例如 ¥9,000")
    verify_parser.add_argument("--deadline", help="响应/投标截止时间")
    verify_parser.add_argument("--content", help="人工确认的服务内容摘要")
    verify_parser.add_argument(
        "--source",
        default="manual",
        dest="verification_source",
        help="核验来源说明",
    )
    verify_parser.add_argument("--notes", help="核验备注")
    verify_parser.add_argument("--evidence-path", help="截图或其他证据文件路径")

    report_parser = subparsers.add_parser("report", help="从 SQLite 导出 CSV/JSON 报告")
    report_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    report_parser.add_argument("--format", choices=("csv", "json"), default="csv")
    report_parser.add_argument("--output", type=Path, required=True, help="输出文件路径")
    report_parser.add_argument("--limit", type=int, help="最多导出条数")

    review_parser = subparsers.add_parser(
        "review",
        help="导出尚未核验、但值得跟进的人工复核队列",
    )
    review_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    review_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="复核队列 CSV 输出路径",
    )
    review_parser.add_argument("--limit", type=int, help="最多导出条数")

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="重新分类并生成完整报告、摘要和人工复核队列",
    )
    refresh_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    refresh_parser.add_argument(
        "--report-output",
        type=Path,
        default=Path("data/report.csv"),
        help="完整 CSV 报告路径",
    )
    refresh_parser.add_argument(
        "--digest-output",
        type=Path,
        default=Path("data/digest.txt"),
        help="跟进摘要路径",
    )
    refresh_parser.add_argument(
        "--review-output",
        type=Path,
        default=Path("data/review.csv"),
        help="人工复核队列路径",
    )
    refresh_parser.add_argument("--limit", type=int, help="摘要和复核队列最多展示条数")

    digest_parser = subparsers.add_parser("digest", help="生成待复核公告摘要文本")
    digest_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    digest_parser.add_argument("--output", type=Path, required=True, help="输出文件路径")
    digest_parser.add_argument("--limit", type=int, default=20, help="最多展示条数")

    serverchan_parser = subparsers.add_parser(
        "notify-serverchan",
        aliases=["notify-wechat"],
        help="将新的命中目标通过免费 Server酱 Turbo 发送到微信",
    )
    serverchan_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    serverchan_parser.add_argument(
        "--sendkey",
        help="Server酱 Turbo SendKey；未提供时读取 SERVERCHAN_SENDKEY",
    )
    serverchan_parser.add_argument(
        "--title",
        default="江苏文化采购公告",
        help="通知标题",
    )
    serverchan_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="摘要最多展示条数",
    )

    notify_parser = subparsers.add_parser(
        "notify-pushplus",
        help="将有命中目标的摘要通过 PushPlus 发送到微信或 QQ",
    )
    notify_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    notify_parser.add_argument(
        "--token",
        help="PushPlus Token；未提供时读取 PUSHPLUS_TOKEN",
    )
    notify_parser.add_argument(
        "--channel",
        choices=("wechat", "qq"),
        help="推送渠道；未提供时读取 PUSHPLUS_CHANNEL，默认 wechat",
    )
    notify_parser.add_argument(
        "--option",
        help="渠道配置编码；未提供时读取 PUSHPLUS_OPTION",
    )
    notify_parser.add_argument(
        "--title",
        default="江苏文化采购公告",
        help="通知标题",
    )
    notify_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="摘要最多展示条数",
    )

    okcis_parser = subparsers.add_parser(
        "collect-okcis",
        help="低频采集泰兴 OKCIS 公开列表（不访问验证码详情）",
    )
    okcis_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    okcis_parser.add_argument("--pages", type=int, default=1, help="本次最多采集页数（默认 1）")
    okcis_parser.add_argument(
        "--page-size",
        type=int,
        choices=(10, 20, 30, 40, 50),
        default=50,
        help="每页条数（默认 50）",
    )
    okcis_parser.add_argument(
        "--time-type",
        type=int,
        choices=(1, 8, 2, 4, 5),
        default=1,
        help="站点时间范围：1最新、8近三天、2近一周、4近三个月、5近半年",
    )
    okcis_parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/raw/okcis-taixing"),
        help="保存原始列表 HTML 的目录",
    )

    auto_parser = subparsers.add_parser(
        "auto-refresh",
        help="低频自动采集 OKCIS 列表并刷新本地输出",
    )
    auto_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    auto_parser.add_argument(
        "--pages",
        type=int,
        default=2,
        help="本次最多采集页数（默认 2，按源站实际页数提前停止）",
    )
    auto_parser.add_argument(
        "--page-size",
        type=int,
        choices=(10, 20, 30, 40, 50),
        default=50,
        help="每页条数（默认 50）",
    )
    auto_parser.add_argument(
        "--time-type",
        type=int,
        choices=(1, 8, 2, 4, 5),
        default=1,
        help="站点时间范围：1最新、8近三天、2近一周、4近三个月、5近半年",
    )
    auto_parser.add_argument(
        "--scope",
        choices=("jiangsu", "taixing"),
        default="jiangsu",
        help="自动采集范围：jiangsu=江苏省级聚合入口（默认），taixing=仅泰兴",
    )
    auto_parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/raw/okcis-auto"),
        help="保存原始列表 HTML 的目录",
    )
    auto_parser.add_argument(
        "--report-output",
        type=Path,
        default=Path("data/report.csv"),
        help="完整 CSV 报告路径",
    )
    auto_parser.add_argument(
        "--digest-output",
        type=Path,
        default=Path("data/digest.txt"),
        help="跟进摘要路径",
    )
    auto_parser.add_argument(
        "--review-output",
        type=Path,
        default=Path("data/review.csv"),
        help="人工复核队列路径",
    )
    auto_parser.add_argument("--limit", type=int, default=20, help="摘要和复核队列最多展示条数")
    auto_parser.add_argument("--lock-path", type=Path, help="自动采集锁文件路径")

    ccgp_parser = subparsers.add_parser(
        "collect-ccgp-manual",
        help="人工输入验证码后低频采集江苏政府采购网列表",
    )
    ccgp_parser.add_argument("--db", default="data/tenders.sqlite3", help="数据库路径")
    ccgp_parser.add_argument("--pages", type=int, default=1, help="本次最多采集页数（默认 1）")
    ccgp_parser.add_argument("--start-date", help="起始日期（YYYY-MM-DD），默认当天")
    ccgp_parser.add_argument("--end-date", help="结束日期（YYYY-MM-DD），默认当天")
    ccgp_parser.add_argument("--parent-region-code", help="地市代码，例如 320100")
    ccgp_parser.add_argument("--region-code", help="区县代码")
    ccgp_parser.add_argument("--announcement-type", help="公告类型，例如 cggg、zbgg、cjgg")
    ccgp_parser.add_argument("--procurement-method", help="采购方式，例如 cgfs008")
    ccgp_parser.add_argument("--title", help="标题关键词")
    ccgp_parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=Path("data/raw/ccgp-manual"),
        help="保存浏览器渲染列表 HTML 的目录",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "db-init":
        with TenderDatabase(args.db):
            pass
        print(f"数据库已初始化：{args.db}")
        return
    if args.command == "setup":
        result = initialize_project(args.db)
        print(
            json.dumps(
                {
                    "db": str(result.db_path),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "dashboard":
        run_dashboard(
            args.db,
            host=args.host,
            port=args.port,
            open_browser=args.open,
            allow_write=args.allow_write,
        )
        return
    if args.command == "reclassify":
        with TenderDatabase(args.db) as db:
            outputs = refresh_outputs(db)
        print(
            f"已重新评估 {outputs.reclassified} 条公告并刷新本地输出："
            f"{outputs.report_path}、{outputs.digest_path}、{outputs.review_path}"
        )
        return
    if args.command == "import-detail":
        payload = json.loads(args.payload.read_text(encoding="utf-8"))
        with TenderDatabase(args.db) as db:
            record = parse_detail_payload(payload, args.url)
            result = ingest_record(db, record)
            outputs = refresh_outputs(db)
        print(
            json.dumps(
                {
                    "tender_id": result.tender_id,
                    "title": record.title,
                    "status": result.decision.status,
                    "score": result.decision.score,
                    "report": str(outputs.report_path),
                    "digest": str(outputs.digest_path),
                    "review": str(outputs.review_path),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "verify":
        with TenderDatabase(args.db) as db:
            tender_id = db.verify_tender(
                args.url,
                project_id=args.project_id,
                budget_yuan=args.budget_yuan,
                budget_raw=args.budget_raw,
                deadline=args.deadline,
                content=args.content,
                verification_source=args.verification_source,
                notes=args.notes,
                evidence_path=args.evidence_path,
            )
            row = next(row for row in db.list_tenders() if row["id"] == tender_id)
            outputs = refresh_outputs(db)
        print(
            json.dumps(
                {
                    "tender_id": tender_id,
                    "verification_status": row["verification_status"],
                    "budget_status": row["budget_status"],
                    "filter_status": row["filter_status"],
                    "report": str(outputs.report_path),
                    "digest": str(outputs.digest_path),
                    "review": str(outputs.review_path),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "report":
        with TenderDatabase(args.db) as db:
            output = (
                export_csv(db, args.output, limit=args.limit)
                if args.format == "csv"
                else export_json(db, args.output, limit=args.limit)
            )
        print(f"报告已生成：{output}")
        return
    if args.command == "review":
        with TenderDatabase(args.db) as db:
            queue_count = sum(
                row["filter_status"] in ACTIONABLE_FILTER_STATUSES
                and row["verification_status"] != "VERIFIED"
                for row in db.list_tenders()
            )
            output = export_review_queue(db, args.output, limit=args.limit)
        print(f"人工复核队列已生成：{output}（{queue_count} 条）")
        return
    if args.command == "refresh":
        with TenderDatabase(args.db) as db:
            outputs = refresh_outputs(
                db,
                report_output=args.report_output,
                digest_output=args.digest_output,
                review_output=args.review_output,
                max_items=args.limit if args.limit is not None else 20,
            )
        print(
            json.dumps(
                {
                    "reclassified": outputs.reclassified,
                    "review_queue_count": outputs.review_queue_count,
                    "report": str(outputs.report_path),
                    "digest": str(outputs.digest_path),
                    "review": str(outputs.review_path),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "digest":
        with TenderDatabase(args.db) as db:
            digest = format_digest(db.list_tenders(), max_items=args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(digest, encoding="utf-8")
        print(f"摘要已生成：{args.output}")
        return
    if args.command in {"notify-serverchan", "notify-wechat"}:
        if args.limit <= 0:
            parser.error("--limit 必须大于 0")
        sendkey = args.sendkey or os.environ.get("SERVERCHAN_SENDKEY")
        if not sendkey:
            parser.error("请提供 --sendkey 或设置 SERVERCHAN_SENDKEY")
        with TenderDatabase(args.db) as db:
            rows = db.list_unnotified_actionable(channel="serverchan")[: args.limit]
        if not rows:
            print("没有新的命中目标，未发送通知")
            return
        digest = format_digest(rows, max_items=args.limit)
        try:
            send_serverchan_message(
                digest,
                sendkey,
                title=args.title,
            )
        except (NotificationError, ValueError) as exc:
            parser.error(str(exc))
        with TenderDatabase(args.db) as db:
            marked = db.mark_notifications_sent(
                (row["id"] for row in rows),
                channel="serverchan",
            )
        print(f"Server酱摘要已发送，已记录 {marked} 条新命中")
        return
    if args.command == "notify-pushplus":
        if args.limit <= 0:
            parser.error("--limit 必须大于 0")
        token = args.token or os.environ.get("PUSHPLUS_TOKEN")
        if not token:
            parser.error("请提供 --token 或设置 PUSHPLUS_TOKEN")
        channel = args.channel or os.environ.get("PUSHPLUS_CHANNEL", "wechat")
        option = args.option or os.environ.get("PUSHPLUS_OPTION")
        with TenderDatabase(args.db) as db:
            rows = db.list_tenders()
        if not actionable_rows(rows):
            print("没有命中目标，未发送通知")
            return
        digest = format_digest(rows, max_items=args.limit)
        try:
            send_pushplus_message(
                digest,
                token,
                title=args.title,
                channel=channel,
                option=option,
            )
        except (NotificationError, ValueError) as exc:
            parser.error(str(exc))
        print(f"PushPlus 摘要已发送（{channel}）")
        return
    if args.command == "collect-okcis":
        from .sources.okcis_taixing import OKCIS_RATE_LIMIT_POLICY

        guard = RequestGuard(OKCIS_RATE_LIMIT_POLICY)
        with httpx.Client(
            follow_redirects=True,
            timeout=30,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "JiangsuTenderMonitor/0.1 (local research)",
            },
        ) as client:
            def fetch_page_once(url: str) -> tuple[int, str]:
                response = client.get(url)
                return response.status_code, response.text

            def fetch_page(url: str) -> tuple[int, str]:
                return _fetch_page_with_retries(
                    fetch_page_once,
                    url,
                    retry_delays=(
                        OKCIS_RATE_LIMIT_POLICY.min_request_interval_seconds,
                        OKCIS_RATE_LIMIT_POLICY.min_request_interval_seconds * 2,
                    ),
                )

            with TenderDatabase(args.db) as db:
                result = collect_list_pages(
                    fetch_page,
                    db,
                    pages=args.pages,
                    page_size=args.page_size,
                    time_type=args.time_type,
                    guard=guard,
                    snapshot_dir=args.snapshot_dir,
                )
                outputs = refresh_outputs(db)
        print(
            json.dumps(
                {
                    "source": "okcis_taixing",
                    "pages_fetched": result.pages_fetched,
                    "records_seen": result.records_seen,
                    "saved_count": result.saved_count,
                    "status_counts": result.status_counts,
                    "stopped_reason": result.stopped_reason,
                    "reclassified": outputs.reclassified,
                    "review_queue_count": outputs.review_queue_count,
                    "report": str(outputs.report_path),
                    "digest": str(outputs.digest_path),
                    "review": str(outputs.review_path),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "auto-refresh":
        if args.pages < 1 or args.pages > 2:
            parser.error("--pages 必须在 1 到 2 之间")
        if args.limit <= 0:
            parser.error("--limit 必须大于 0")
        from .sources.okcis_taixing import OKCIS_RATE_LIMIT_POLICY

        guard = RequestGuard(OKCIS_RATE_LIMIT_POLICY)
        with httpx.Client(
            follow_redirects=True,
            timeout=30,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "JiangsuTenderMonitor/0.1 (local research)",
            },
        ) as client:
            def fetch_page_once(url: str) -> tuple[int, str]:
                response = client.get(url)
                return response.status_code, response.text

            def fetch_page(url: str) -> tuple[int, str]:
                return _fetch_page_with_retries(
                    fetch_page_once,
                    url,
                    retry_delays=(
                        OKCIS_RATE_LIMIT_POLICY.min_request_interval_seconds,
                        OKCIS_RATE_LIMIT_POLICY.min_request_interval_seconds * 2,
                    ),
                )

            source = JIANGSU_SOURCE if args.scope == "jiangsu" else None
            source_name = source.name if source else "okcis_taixing"
            try:
                result = run_automatic_cycle(
                    fetch_page,
                    db_path=args.db,
                    pages=args.pages,
                    page_size=args.page_size,
                    time_type=args.time_type,
                    guard=guard,
                    snapshot_dir=args.snapshot_dir,
                    report_output=args.report_output,
                    digest_output=args.digest_output,
                    review_output=args.review_output,
                    max_items=args.limit,
                    lock_path=args.lock_path,
                    base_url=source.base_url if source else "https://taixingshi.okcis.cn",
                    source_name=source.name if source else "okcis_taixing",
                    province=source.province if source else "江苏",
                    city=source.city if source else "泰兴市",
                    snapshot_prefix=source.snapshot_prefix if source else "okcis-taixing",
                )
            except (
                BudgetExceededError,
                CircuitOpenError,
                CycleAlreadyRunningError,
                OkcisListError,
                httpx.HTTPError,
            ) as exc:
                if isinstance(exc, CircuitOpenError):
                    attention_kind = "RATE_LIMIT"
                elif isinstance(exc, OkcisListError) and "验证码" in str(exc):
                    attention_kind = "CAPTCHA_REQUIRED"
                else:
                    attention_kind = "AUTOMATION_FAILED"
                attention_message = (
                    f"网络请求失败：{exc}"
                    if isinstance(exc, httpx.HTTPError)
                    else str(exc)
                )
                write_attention(
                    Path(args.db).parent / "attention.json",
                    kind=attention_kind,
                    message=attention_message,
                    source=source_name,
                    url=source.base_url if source else "https://taixingshi.okcis.cn",
                )
                parser.error(str(exc))
        print(
            json.dumps(
                {
                    "source": source_name,
                    "scope": args.scope,
                    "pages_fetched": result.collection.pages_fetched,
                    "records_seen": result.collection.records_seen,
                    "saved_count": result.collection.saved_count,
                    "status_counts": result.collection.status_counts,
                    "stopped_reason": result.collection.stopped_reason,
                    "reclassified": result.reclassified,
                    "review_queue_count": result.review_queue_count,
                    "report": str(result.report_path),
                    "digest": str(result.digest_path),
                    "review": str(result.review_path),
                    "finished_at": result.finished_at,
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "collect-ccgp-manual":
        if args.pages < 1 or args.pages > 10:
            parser.error("--pages 必须在 1 到 10 之间")
        try:
            start_ms, end_ms = _date_range_ms(args.start_date, args.end_date)
        except ValueError as exc:
            parser.error(str(exc))
        query = SearchQuery(
            start_ms=start_ms,
            end_ms=end_ms,
            parent_region_code=args.parent_region_code or "",
            region_code=args.region_code or "",
            announcement_type=args.announcement_type or "",
            procurement_method=args.procurement_method or "",
            title=args.title or "",
        )
        try:
            manual_result = collect_manual_query(
                query,
                max_pages=args.pages,
                snapshot_dir=args.snapshot_dir,
            )
        except ManualQueryError as exc:
            write_attention(
                Path(args.db).parent / "attention.json",
                kind="CAPTCHA_REQUIRED" if "验证码" in str(exc) else "MANUAL_ACTION_REQUIRED",
                message=str(exc),
                source="ccgp_jiangsu",
                url="http://www.ccgp-jiangsu.gov.cn/jiangsu/cggg_search.html",
            )
            parser.error(str(exc))
        status_counts: Counter[str] = Counter()
        saved_count = 0
        with TenderDatabase(args.db) as db:
            for item in manual_result.items:
                if not item.detail_url():
                    continue
                result = ingest_record(db, item.to_tender_record())
                saved_count += 1
                status_counts[result.decision.status] += 1
            outputs = refresh_outputs(db)
        print(
            json.dumps(
                {
                    "source": "ccgp_jiangsu",
                    "pages_fetched": manual_result.pages,
                    "total": manual_result.total,
                    "records_seen": len(manual_result.items),
                    "saved_count": saved_count,
                    "status_counts": dict(status_counts),
                    "reclassified": outputs.reclassified,
                    "review_queue_count": outputs.review_queue_count,
                    "report": str(outputs.report_path),
                    "digest": str(outputs.digest_path),
                    "review": str(outputs.review_path),
                },
                ensure_ascii=False,
            )
        )
        return
    parser.print_help()


if __name__ == "__main__":
    main()
