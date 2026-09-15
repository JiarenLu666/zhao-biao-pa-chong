"""无人值守的低频采集循环。

自动循环只连接无需验证码的 OKCIS 公开列表子站（默认泰州全域七站，
可切换到江苏 13 个地级市市级站），采集后立即刷新本地报告，并在轮末
清理超过保留期（默认 7 天）的旧公告与快照。江苏省级聚合入口的自动
采集轮已退役；官方江苏政府采购网仍需人工验证码，不由本模块调用。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from .attention import clear_recovered_attention
from .cleanup import purge_snapshot_files
from .rate_limit import RequestGuard
from .report import refresh_outputs
from .sources.okcis_jiangsu import OkcisSourceConfig
from .sources.okcis_taixing import (
    BASE_URL,
    OKCIS_RATE_LIMIT_POLICY,
    SOURCE_NAME,
    FetchPage,
    OkcisCollectionResult,
    collect_list_pages,
)
from .sources.okcis_taizhou import TAIZHOU_SOURCES
from .storage import TenderDatabase


class CycleAlreadyRunningError(RuntimeError):
    """已有另一个自动采集循环在运行。"""


@dataclass(frozen=True)
class AutomaticCycleResult:
    """一次自动采集与本地输出刷新的可审计结果。"""

    collection: OkcisCollectionResult
    reclassified: int
    review_queue_count: int
    report_path: Path
    digest_path: Path
    review_path: Path
    finished_at: str
    #: 多源循环时每个来源的独立汇总；单源循环留空。
    source_reports: tuple[tuple[str, OkcisCollectionResult], ...] = field(default=())
    #: 轮末保留清理的删除数量：旧公告条数与旧快照文件数。
    purged_tenders: int = 0
    purged_snapshots: int = 0


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _cycle_lock(path: str | Path) -> Iterator[None]:
    """创建跨平台锁文件，并清理已退出进程遗留的锁。"""

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    for _ in range(2):
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
            )
            break
        except FileExistsError:
            try:
                content = lock_path.read_text(encoding="utf-8").strip()
                existing_pid = int(content.splitlines()[0]) if content else 0
            except (OSError, ValueError):
                existing_pid = 0
            if _pid_is_alive(existing_pid):
                raise CycleAlreadyRunningError(f"自动采集仍在运行：{lock_path}")
            try:
                lock_path.unlink()
            except FileNotFoundError:
                continue
    if descriptor is None:
        raise CycleAlreadyRunningError(f"无法取得自动采集锁：{lock_path}")
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
    finally:
        os.close(descriptor)
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def run_automatic_cycle(
    fetch_page: FetchPage,
    *,
    db_path: str | Path = "data/tenders.sqlite3",
    pages: int = 2,
    page_size: int = 50,
    time_type: int = 1,
    guard: RequestGuard | None = None,
    sleep: Callable[[float], None] = time.sleep,
    snapshot_dir: str | Path | None = "data/raw/okcis-auto",
    report_output: str | Path = "data/report.csv",
    digest_output: str | Path = "data/digest.txt",
    review_output: str | Path = "data/review.csv",
    max_items: int = 20,
    lock_path: str | Path | None = None,
    base_url: str = BASE_URL,
    source_name: str = SOURCE_NAME,
    province: str = "江苏",
    city: str | None = "泰兴市",
    snapshot_prefix: str = "okcis-taixing",
    retention_days: int | None = 7,
) -> AutomaticCycleResult:
    """执行一次低频列表采集，并刷新本地报告、摘要和复核队列。

    ``retention_days`` 控制轮末保留清理：采集前删除超过保留期的旧公告
    （级联删附件与通知账本），采集后删除超过保留期的快照 HTML；
    置为 ``None`` 可完全跳过清理。
    """

    if max_items <= 0:
        raise ValueError("max_items 必须大于 0")
    if retention_days is not None and retention_days <= 0:
        raise ValueError("retention_days 必须大于 0（置 None 可跳过清理）")
    database_path = Path(db_path)
    cycle_lock_path = (
        Path(lock_path)
        if lock_path is not None
        else database_path.with_suffix(".cycle.lock")
    )
    request_guard = guard or RequestGuard(OKCIS_RATE_LIMIT_POLICY)
    report_path = Path(report_output)
    digest_path = Path(digest_output)
    review_path = Path(review_output)
    purged_tenders = 0
    purged_snapshots = 0

    with _cycle_lock(cycle_lock_path), TenderDatabase(database_path) as db:
        if retention_days is not None:
            purged_tenders = db.purge_tenders_older_than(retention_days)
        collection = collect_list_pages(
            fetch_page,
            db,
            pages=pages,
            page_size=page_size,
            time_type=time_type,
            base_url=base_url,
            source_name=source_name,
            province=province,
            city=city,
            snapshot_prefix=snapshot_prefix,
            guard=request_guard,
            sleep=sleep,
            snapshot_dir=snapshot_dir,
        )
        outputs = refresh_outputs(
            db,
            report_output=report_path,
            digest_output=digest_path,
            review_output=review_path,
            max_items=max_items,
        )
        if retention_days is not None and snapshot_dir is not None:
            purged_snapshots = purge_snapshot_files(snapshot_dir, retention_days)
        clear_recovered_attention(
            database_path.parent / "attention.json",
            source=source_name,
        )

    return AutomaticCycleResult(
        collection=collection,
        reclassified=outputs.reclassified,
        review_queue_count=outputs.review_queue_count,
        report_path=outputs.report_path,
        digest_path=outputs.digest_path,
        review_path=outputs.review_path,
        finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        purged_tenders=purged_tenders,
        purged_snapshots=purged_snapshots,
    )


def _merge_collection_results(
    results: list[tuple[str, OkcisCollectionResult]],
) -> OkcisCollectionResult:
    """把多个来源的采集结果合并成一份汇总（数字相加、状态计数合并）。"""

    status_counts: dict[str, int] = {}
    for _, result in results:
        for status, count in result.status_counts.items():
            status_counts[status] = status_counts.get(status, 0) + count
    stopped_reasons = [
        f"{name}:{result.stopped_reason}"
        for name, result in results
        if result.stopped_reason
    ]
    return OkcisCollectionResult(
        pages_fetched=sum(result.pages_fetched for _, result in results),
        records_seen=sum(result.records_seen for _, result in results),
        saved_count=sum(result.saved_count for _, result in results),
        status_counts=status_counts,
        stopped_reason="；".join(stopped_reasons) if stopped_reasons else None,
    )


def run_okcis_cycle(
    fetch_page: FetchPage,
    *,
    sources: tuple[OkcisSourceConfig, ...] = TAIZHOU_SOURCES,
    db_path: str | Path = "data/tenders.sqlite3",
    pages: int = 1,
    page_size: int = 50,
    time_type: int = 1,
    guard: RequestGuard | None = None,
    sleep: Callable[[float], None] = time.sleep,
    snapshot_dir: str | Path | None = "data/raw/okcis-auto",
    report_output: str | Path = "data/report.csv",
    digest_output: str | Path = "data/digest.txt",
    review_output: str | Path = "data/review.csv",
    max_items: int = 20,
    lock_path: str | Path | None = None,
    retention_days: int | None = 7,
) -> AutomaticCycleResult:
    """多源 OKCIS 自动循环：按顺序采集每个子站并刷新一次本地输出。

    ``sources`` 接受任意一组 ``OkcisSourceConfig``（泰州全域七站、
    江苏 13 个地级市市级站等），所有来源共享同一个 RequestGuard，
    既保证轮内全局的 15 秒节流，又把页数/记录数预算按来源数量动态放宽。
    任何单站抛出 CircuitOpenError 都立即停止整体循环，绝不吞掉限流信号。

    ``retention_days`` 控制轮末保留清理（默认 7 天）：采集前删除超过
    保留期的旧公告（级联删附件与通知账本），采集后删除超过保留期的
    快照 HTML；置为 ``None`` 可完全跳过清理。
    """

    if not sources:
        raise ValueError("sources 不能为空")
    if max_items <= 0:
        raise ValueError("max_items 必须大于 0")
    if retention_days is not None and retention_days <= 0:
        raise ValueError("retention_days 必须大于 0（置 None 可跳过清理）")
    database_path = Path(db_path)
    cycle_lock_path = (
        Path(lock_path)
        if lock_path is not None
        else database_path.with_suffix(".cycle.lock")
    )
    if guard is not None:
        request_guard = guard
    else:
        # 共享 guard 时按来源数量放宽单次运行的预算，节流间隔保持不变。
        relaxed_policy = replace(
            OKCIS_RATE_LIMIT_POLICY,
            max_pages_per_run=len(sources) * pages,
            max_records_per_run=len(sources) * pages * page_size,
        )
        request_guard = RequestGuard(relaxed_policy)
    report_path = Path(report_output)
    digest_path = Path(digest_output)
    review_path = Path(review_output)
    purged_tenders = 0
    purged_snapshots = 0

    with _cycle_lock(cycle_lock_path), TenderDatabase(database_path) as db:
        if retention_days is not None:
            purged_tenders = db.purge_tenders_older_than(retention_days)
        source_reports: list[tuple[str, OkcisCollectionResult]] = []
        for source in sources:
            result = collect_list_pages(
                fetch_page,
                db,
                pages=pages,
                page_size=page_size,
                time_type=time_type,
                base_url=source.base_url,
                source_name=source.name,
                province=source.province,
                city=source.city,
                snapshot_prefix=source.snapshot_prefix,
                guard=request_guard,
                sleep=sleep,
                snapshot_dir=snapshot_dir,
            )
            source_reports.append((source.name, result))
        collection = _merge_collection_results(source_reports)
        outputs = refresh_outputs(
            db,
            report_output=report_path,
            digest_output=digest_path,
            review_output=review_path,
            max_items=max_items,
        )
        if retention_days is not None and snapshot_dir is not None:
            purged_snapshots = purge_snapshot_files(snapshot_dir, retention_days)
        for source in sources:
            clear_recovered_attention(
                database_path.parent / "attention.json",
                source=source.name,
            )

    return AutomaticCycleResult(
        collection=collection,
        reclassified=outputs.reclassified,
        review_queue_count=outputs.review_queue_count,
        report_path=outputs.report_path,
        digest_path=outputs.digest_path,
        review_path=outputs.review_path,
        finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_reports=tuple(source_reports),
        purged_tenders=purged_tenders,
        purged_snapshots=purged_snapshots,
    )


def run_taizhou_cycle(
    fetch_page: FetchPage,
    **kwargs: object,
) -> AutomaticCycleResult:
    """泰州全域多源循环；已由 ``run_okcis_cycle`` 泛化取代。

    保留别名以兼容既有调用方与测试：默认来源即 ``TAIZHOU_SOURCES``，
    其余参数原样透传给 ``run_okcis_cycle``。
    """

    return run_okcis_cycle(fetch_page, **kwargs)  # type: ignore[arg-type]
