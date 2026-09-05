"""无人值守的低频采集循环。

自动循环只连接无需验证码的公开辅助源（默认是 OKCIS 江苏省级入口），
采集后立即刷新本地报告。官方江苏政府采购网仍需人工验证码，不由本模块调用。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .rate_limit import RequestGuard
from .report import refresh_outputs
from .sources.okcis_taixing import (
    BASE_URL,
    OKCIS_RATE_LIMIT_POLICY,
    SOURCE_NAME,
    FetchPage,
    OkcisCollectionResult,
    collect_list_pages,
)
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
) -> AutomaticCycleResult:
    """执行一次低频列表采集，并刷新本地报告、摘要和复核队列。"""

    if max_items <= 0:
        raise ValueError("max_items 必须大于 0")
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

    with _cycle_lock(cycle_lock_path), TenderDatabase(database_path) as db:
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

    return AutomaticCycleResult(
        collection=collection,
        reclassified=outputs.reclassified,
        review_queue_count=outputs.review_queue_count,
        report_path=outputs.report_path,
        digest_path=outputs.digest_path,
        review_path=outputs.review_path,
        finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
