"""将规范化公告送入筛选、去重和 SQLite 存储。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .filtering import FilterDecision, evaluate_record
from .models import TenderRecord
from .storage import TenderDatabase


@dataclass(frozen=True)
class IngestResult:
    tender_id: int
    decision: FilterDecision


def ingest_record(db: TenderDatabase, record: TenderRecord) -> IngestResult:
    """评估一条公告并幂等保存，同时登记公开附件元数据。"""

    decision = evaluate_record(record)
    tender_id = db.upsert(record, decision=decision)
    attachments = record.raw_payload.get("files")
    if isinstance(attachments, list):
        valid_attachments = [item for item in attachments if isinstance(item, Mapping)]
        db.upsert_attachments(tender_id, valid_attachments)
    return IngestResult(tender_id=tender_id, decision=decision)

