"""本地数据保留清理：快照 HTML 文件的按期清理。

数据库侧的旧公告级联删除见 ``storage.TenderDatabase.purge_tenders_older_than``；
本模块只负责磁盘侧的快照清理，供 ``cleanup`` 子命令和自动循环轮末调用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def purge_snapshot_files(
    snapshot_dir: str | Path,
    retention_days: int,
    *,
    now: datetime | None = None,
) -> int:
    """删除早于保留期的快照文件，返回删除数量。

    只清理 ``snapshot_dir`` 下的常规文件（快照 HTML），不递归处理子目录；
    目录不存在时视为没有可清理项。文件新旧按修改时间（mtime）判断。
    """

    if retention_days <= 0:
        raise ValueError("retention_days 必须大于 0")
    directory = Path(snapshot_dir)
    if not directory.exists():
        return 0
    reference = now or datetime.now(timezone.utc)
    cutoff_timestamp = (reference - timedelta(days=retention_days)).timestamp()
    removed = 0
    for candidate in sorted(directory.iterdir()):
        if not candidate.is_file():
            continue
        try:
            modified_at = candidate.stat().st_mtime
        except FileNotFoundError:
            # 并发清理时文件可能刚被删掉，跳过即可。
            continue
        if modified_at < cutoff_timestamp:
            candidate.unlink()
            removed += 1
    return removed
