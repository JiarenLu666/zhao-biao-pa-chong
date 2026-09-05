"""从 SQLite 生成便于人工查看的 CSV/JSON 报告。"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .follow_up import ACTIONABLE_FILTER_STATUSES, COMPLETED_FOLLOW_UP_STATUSES
from .notifications import format_digest
from .storage import TenderDatabase

REPORT_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "source",
    "province",
    "city",
    "district",
    "announcement_type",
    "procurement_method",
    "project_id",
    "published_at",
    "deadline",
    "budget_yuan",
    "budget_raw",
    "budget_status",
    "filter_status",
    "filter_score",
    "filter_reasons_json",
    "filter_include_matches_json",
    "filter_exclude_matches_json",
    "verification_status",
    "verified_at",
    "verification_source",
    "verification_notes",
    "follow_up_status",
    "follow_up_at",
    "follow_up_notes",
    "url",
    "content",
)

@dataclass(frozen=True)
class RefreshOutputsResult:
    """一次完整刷新产生的本地输出及统计。"""

    reclassified: int
    review_queue_count: int
    report_path: Path
    digest_path: Path
    review_path: Path


def _rows(db: TenderDatabase, limit: int | None) -> list[dict[str, Any]]:
    return [{field: row[field] for field in REPORT_FIELDS} for row in db.list_tenders(limit=limit)]


def export_csv(db: TenderDatabase, path: str | Path, *, limit: int | None = None) -> Path:
    """导出 UTF-8 BOM CSV，便于 Windows Excel 直接打开。"""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows(db, limit)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return output


def export_review_queue(
    db: TenderDatabase,
    path: str | Path,
    *,
    limit: int | None = None,
) -> Path:
    """导出尚未人工核验、但值得跟进的公告队列。"""

    if limit is not None and limit <= 0:
        raise ValueError("limit 必须大于 0")
    rows = [
        row
        for row in _rows(db, limit=None)
        if row["filter_status"] in ACTIONABLE_FILTER_STATUSES
        and row["verification_status"] != "VERIFIED"
        and row["follow_up_status"] not in COMPLETED_FOLLOW_UP_STATUSES
    ]
    if limit is not None:
        rows = rows[:limit]
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return output


def export_json(db: TenderDatabase, path: str | Path, *, limit: int | None = None) -> Path:
    """导出结构化 JSON；不把 SQLite Row 或连接对象暴露给调用方。"""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(_rows(db, limit), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def refresh_outputs(
    db: TenderDatabase,
    *,
    report_output: str | Path = "data/report.csv",
    digest_output: str | Path = "data/digest.txt",
    review_output: str | Path = "data/review.csv",
    max_items: int = 20,
) -> RefreshOutputsResult:
    """重新分类数据库并统一刷新报告、摘要和人工复核队列。"""

    if max_items <= 0:
        raise ValueError("max_items 必须大于 0")
    reclassified = db.reclassify_filters()
    rows = db.list_tenders()
    report_path = export_csv(db, report_output)
    digest_path = Path(digest_output)
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(
        format_digest(rows, max_items=max_items),
        encoding="utf-8",
    )
    review_path = export_review_queue(db, review_output, limit=max_items)
    review_queue_count = sum(
        row["filter_status"] in ACTIONABLE_FILTER_STATUSES
        and row["verification_status"] != "VERIFIED"
        and row["follow_up_status"] not in COMPLETED_FOLLOW_UP_STATUSES
        for row in rows
    )
    return RefreshOutputsResult(
        reclassified=reclassified,
        review_queue_count=review_queue_count,
        report_path=report_path,
        digest_path=digest_path,
        review_path=review_path,
    )
