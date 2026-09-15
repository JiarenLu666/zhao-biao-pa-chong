"""SQLite 持久化：项目编号/标题去重，重复运行幂等更新。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .dedupe import duplicate_key, titles_similar
from .filtering import FilterDecision, evaluate_record
from .follow_up import ACTIONABLE_FILTER_STATUSES, EDITABLE_FOLLOW_UP_STATUSES
from .models import TenderRecord
from .normalization import budget_status as calculate_budget_status

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    province TEXT NOT NULL DEFAULT '江苏',
    city TEXT,
    district TEXT,
    announcement_type TEXT,
    procurement_method TEXT,
    project_id TEXT,
    published_at TEXT,
    deadline TEXT,
    budget_yuan REAL,
    budget_raw TEXT,
    budget_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    content TEXT,
    url TEXT NOT NULL UNIQUE,
    raw_hash TEXT,
    detail_hash TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    filter_status TEXT,
    filter_score INTEGER,
    filter_reasons_json TEXT NOT NULL DEFAULT '[]',
    filter_include_matches_json TEXT NOT NULL DEFAULT '[]',
    filter_exclude_matches_json TEXT NOT NULL DEFAULT '[]',
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    verified_at TEXT,
    verification_source TEXT,
    verification_notes TEXT,
    follow_up_status TEXT NOT NULL DEFAULT 'UNTRACKED',
    follow_up_at TEXT,
    follow_up_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tenders_published_at ON tenders(published_at);
CREATE INDEX IF NOT EXISTS idx_tenders_budget_status ON tenders(budget_status);
CREATE INDEX IF NOT EXISTS idx_tenders_project_id ON tenders(project_id);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL DEFAULT '',
    file_url TEXT NOT NULL DEFAULT '',
    file_path TEXT,
    content_type TEXT,
    extraction_status TEXT,
    extracted_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tender_id, file_name, file_url)
);
CREATE INDEX IF NOT EXISTS idx_attachments_tender_id ON attachments(tender_id);
CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel, tender_id)
);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_channel
    ON notification_deliveries(channel);
"""


class TenderDatabase:
    """一个 SQLite 数据库连接的轻量上下文封装。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> TenderDatabase:  # noqa: PYI034 - project supports Python 3.9
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._connection is not None:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
            self._connection.close()
            self._connection = None

    def connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(SCHEMA)
            self._ensure_schema_columns()
            self._connection.commit()
        return self._connection

    def _ensure_schema_columns(self) -> None:
        """为早期骨架数据库补齐筛选字段，避免升级时丢数据。"""

        connection = self._connection
        if connection is None:
            return
        existing = {
            row[1] for row in connection.execute("PRAGMA table_info(tenders)").fetchall()
        }
        additions = {
            "filter_status": "TEXT",
            "filter_score": "INTEGER",
            "filter_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
            "filter_include_matches_json": "TEXT NOT NULL DEFAULT '[]'",
            "filter_exclude_matches_json": "TEXT NOT NULL DEFAULT '[]'",
            "verification_status": "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
            "verified_at": "TEXT",
            "verification_source": "TEXT",
            "verification_notes": "TEXT",
            "follow_up_status": "TEXT NOT NULL DEFAULT 'UNTRACKED'",
            "follow_up_at": "TEXT",
            "follow_up_notes": "TEXT",
        }
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE tenders ADD COLUMN {name} {definition}")
        connection.execute(
            """UPDATE tenders SET follow_up_status='PENDING'
               WHERE verification_status='VERIFIED'
                 AND filter_status IN ('MATCH', 'OVER_BUDGET', 'REVIEW')
                 AND follow_up_status='UNTRACKED'"""
        )

    def upsert(self, record: TenderRecord, *, decision: FilterDecision | None = None) -> int:
        connection = self.connect()
        identity_key = duplicate_key(record)
        existing = connection.execute(
            "SELECT * FROM tenders WHERE identity_key = ? OR url = ? LIMIT 1",
            (identity_key, record.url),
        ).fetchone()
        if existing is None and record.project_id is None and record.published_at:
            day = record.published_at[:10]
            if len(day) == 10:
                candidates = connection.execute(
                    """SELECT * FROM tenders
                       WHERE project_id IS NULL AND published_at LIKE ?""",
                    (day + "%",),
                ).fetchall()
                for candidate in candidates:
                    if titles_similar(record.title, candidate["title"]):
                        existing = candidate
                        break
        if existing and existing["verification_status"] == "VERIFIED":
            try:
                existing_payload = json.loads(existing["raw_payload_json"] or "{}")
            except json.JSONDecodeError:
                existing_payload = {}
            if not isinstance(existing_payload, dict):
                existing_payload = {}
            verified_payload = existing_payload.get("manual_verification")
            existing_payload.update(record.raw_payload)
            if verified_payload is not None:
                existing_payload["manual_verification"] = verified_payload
            preserve_budget = record.budget_yuan is None
            record = TenderRecord(
                title=record.title,
                source=record.source,
                url=record.url,
                announcement_type=record.announcement_type,
                procurement_method=record.procurement_method,
                project_id=record.project_id if record.project_id is not None else existing["project_id"],
                province=record.province,
                city=record.city,
                district=record.district,
                published_at=record.published_at,
                deadline=record.deadline if record.deadline is not None else existing["deadline"],
                budget_yuan=record.budget_yuan if not preserve_budget else existing["budget_yuan"],
                budget_raw=record.budget_raw if not preserve_budget else existing["budget_raw"],
                budget_status=record.budget_status if not preserve_budget else existing["budget_status"],
                content=record.content if record.content is not None else existing["content"],
                raw_hash=record.raw_hash if record.raw_hash is not None else existing["raw_hash"],
                detail_hash=record.detail_hash
                if record.detail_hash is not None
                else existing["detail_hash"],
                raw_payload=existing_payload,
            )
            if decision is not None:
                decision = evaluate_record(record)

        raw_payload_json = json.dumps(record.raw_payload, ensure_ascii=False, sort_keys=True)
        filter_status = decision.status if decision else None
        filter_score = decision.score if decision else None
        filter_reasons_json = json.dumps(decision.reasons if decision else (), ensure_ascii=False)
        filter_include_matches_json = json.dumps(
            decision.include_matches if decision else (), ensure_ascii=False
        )
        filter_exclude_matches_json = json.dumps(
            decision.exclude_matches if decision else (), ensure_ascii=False
        )
        values = (
            duplicate_key(record),
            record.title,
            record.source,
            record.province,
            record.city,
            record.district,
            record.announcement_type,
            record.procurement_method,
            record.project_id,
            record.published_at,
            record.deadline,
            record.budget_yuan,
            record.budget_raw,
            record.budget_status,
            record.content,
            record.url,
            record.raw_hash,
            record.detail_hash,
            raw_payload_json,
            filter_status,
            filter_score,
            filter_reasons_json,
            filter_include_matches_json,
            filter_exclude_matches_json,
        )
        if existing:
            connection.execute(
                """UPDATE tenders SET identity_key=?, title=?, source=?, province=?, city=?, district=?,
                   announcement_type=?, procurement_method=?, project_id=?, published_at=?, deadline=?,
                   budget_yuan=?, budget_raw=?, budget_status=?, content=?, url=?, raw_hash=?, detail_hash=?,
                   raw_payload_json=?, filter_status=?, filter_score=?, filter_reasons_json=?,
                   filter_include_matches_json=?, filter_exclude_matches_json=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                values + (existing["id"],),
            )
            connection.commit()
            return int(existing["id"])

        cursor = connection.execute(
            """INSERT INTO tenders (
                identity_key, title, source, province, city, district, announcement_type,
                procurement_method, project_id, published_at, deadline, budget_yuan, budget_raw,
                budget_status, content, url, raw_hash, detail_hash, raw_payload_json, filter_status,
                filter_score, filter_reasons_json, filter_include_matches_json, filter_exclude_matches_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )""",
            values,
        )
        connection.commit()
        return int(cursor.lastrowid)

    def reclassify_filters(self) -> int:
        """按当前筛选规则重新评估已有公告，返回处理条数。"""

        connection = self.connect()
        rows = connection.execute("SELECT * FROM tenders ORDER BY id").fetchall()
        for row in rows:
            try:
                raw_payload = json.loads(row["raw_payload_json"] or "{}")
            except json.JSONDecodeError:
                raw_payload = {}
            if not isinstance(raw_payload, dict):
                raw_payload = {}
            record = TenderRecord(
                title=row["title"],
                source=row["source"],
                url=row["url"],
                announcement_type=row["announcement_type"],
                procurement_method=row["procurement_method"],
                project_id=row["project_id"],
                province=row["province"] or "江苏",
                city=row["city"],
                district=row["district"],
                published_at=row["published_at"],
                deadline=row["deadline"],
                budget_yuan=row["budget_yuan"],
                budget_raw=row["budget_raw"],
                budget_status=row["budget_status"] or "UNKNOWN",
                content=row["content"],
                raw_payload=raw_payload,
            )
            decision = evaluate_record(record)
            connection.execute(
                """UPDATE tenders SET filter_status=?, filter_score=?,
                   filter_reasons_json=?, filter_include_matches_json=?,
                   filter_exclude_matches_json=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (
                    decision.status,
                    decision.score,
                    json.dumps(decision.reasons, ensure_ascii=False),
                    json.dumps(decision.include_matches, ensure_ascii=False),
                    json.dumps(decision.exclude_matches, ensure_ascii=False),
                    row["id"],
                ),
            )
        connection.commit()
        return len(rows)

    def verify_tender(
        self,
        url: str,
        *,
        project_id: str | None = None,
        budget_yuan: float | None = None,
        budget_raw: str | None = None,
        deadline: str | None = None,
        content: str | None = None,
        verification_source: str = "manual",
        notes: str | None = None,
        evidence_path: str | None = None,
    ) -> int:
        """将人工核验结果合并回一条已有公告并重新筛选。"""

        if not url.strip():
            raise ValueError("url 不能为空")
        connection = self.connect()
        row = connection.execute("SELECT * FROM tenders WHERE url = ?", (url,)).fetchone()
        if row is None:
            raise KeyError(f"数据库中不存在该公告：{url}")
        try:
            raw_payload = json.loads(row["raw_payload_json"] or "{}")
        except json.JSONDecodeError:
            raw_payload = {}
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        verified_at = datetime.now(timezone.utc).isoformat()
        raw_payload["manual_verification"] = {
            "verified_at": verified_at,
            "source": verification_source,
            "notes": notes,
            "evidence_path": evidence_path,
        }
        next_record = TenderRecord(
            title=row["title"],
            source=row["source"],
            url=row["url"],
            announcement_type=row["announcement_type"],
            procurement_method=row["procurement_method"],
            project_id=project_id if project_id is not None else row["project_id"],
            province=row["province"] or "江苏",
            city=row["city"],
            district=row["district"],
            published_at=row["published_at"],
            deadline=deadline if deadline is not None else row["deadline"],
            budget_yuan=budget_yuan if budget_yuan is not None else row["budget_yuan"],
            budget_raw=budget_raw if budget_raw is not None else row["budget_raw"],
            budget_status=calculate_budget_status(
                budget_yuan if budget_yuan is not None else row["budget_yuan"]
            ),
            content=content if content is not None else row["content"],
            raw_payload=raw_payload,
        )
        decision = evaluate_record(next_record)
        existing_follow_up_status = row["follow_up_status"] or "UNTRACKED"
        next_follow_up_status = (
            existing_follow_up_status
            if existing_follow_up_status in EDITABLE_FOLLOW_UP_STATUSES
            else ("PENDING" if decision.status in ACTIONABLE_FILTER_STATUSES else "NOT_REQUIRED")
        )
        connection.execute(
            """UPDATE tenders SET identity_key=?, project_id=?, deadline=?, budget_yuan=?,
               budget_raw=?, budget_status=?, content=?, raw_payload_json=?,
               verification_status='VERIFIED', verified_at=?, verification_source=?,
               verification_notes=?, follow_up_status=?, follow_up_at=?, follow_up_notes=?,
               filter_status=?, filter_score=?,
               filter_reasons_json=?, filter_include_matches_json=?,
               filter_exclude_matches_json=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                duplicate_key(next_record),
                next_record.project_id,
                next_record.deadline,
                next_record.budget_yuan,
                next_record.budget_raw,
                next_record.budget_status,
                next_record.content,
                json.dumps(raw_payload, ensure_ascii=False, sort_keys=True),
                verified_at,
                verification_source,
                notes,
                next_follow_up_status,
                verified_at,
                None,
                decision.status,
                decision.score,
                json.dumps(decision.reasons, ensure_ascii=False),
                json.dumps(decision.include_matches, ensure_ascii=False),
                json.dumps(decision.exclude_matches, ensure_ascii=False),
                row["id"],
            ),
        )
        connection.commit()
        return int(row["id"])

    def update_follow_up(
        self,
        url: str,
        *,
        status: str,
        notes: str | None = None,
    ) -> int:
        """更新一条公告的跟进状态和备注。"""

        normalized_status = status.strip().upper()
        if normalized_status not in EDITABLE_FOLLOW_UP_STATUSES:
            allowed = "、".join(sorted(EDITABLE_FOLLOW_UP_STATUSES))
            raise ValueError(f"跟进状态必须是：{allowed}")
        if not url.strip():
            raise ValueError("url 不能为空")
        connection = self.connect()
        row = connection.execute("SELECT id, follow_up_notes FROM tenders WHERE url = ?", (url,)).fetchone()
        if row is None:
            raise KeyError(f"数据库中不存在该公告：{url}")
        next_notes = row["follow_up_notes"] if notes is None else notes
        follow_up_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """UPDATE tenders SET follow_up_status=?, follow_up_at=?, follow_up_notes=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (normalized_status, follow_up_at, next_notes, row["id"]),
        )
        connection.commit()
        return int(row["id"])

    def list_unnotified_actionable(
        self,
        *,
        channel: str = "serverchan",
    ) -> list[sqlite3.Row]:
        """返回某通知渠道尚未发送过的可跟进公告。

        去重键使用稳定的 tender id，而不是摘要文本或 ``updated_at``；这样
        同一公告被重复抓取、重新分类或人工核验时，不会反复打扰用户。
        口径与 ``notifications.actionable_rows`` 一致：无论是否已核验，
        只要跟进状态为 DONE 或 NOT_REQUIRED 就不再推送。
        """

        normalized_channel = channel.strip().lower()
        if not normalized_channel:
            raise ValueError("通知渠道不能为空")
        connection = self.connect()
        return connection.execute(
            """SELECT t.* FROM tenders AS t
               WHERE t.filter_status IN ('MATCH', 'OVER_BUDGET', 'REVIEW')
                 AND t.follow_up_status NOT IN ('DONE', 'NOT_REQUIRED')
                 AND NOT EXISTS (
                   SELECT 1 FROM notification_deliveries AS n
                   WHERE n.tender_id = t.id AND n.channel = ?
                 )
               ORDER BY t.id""",
            (normalized_channel,),
        ).fetchall()

    def mark_notifications_sent(
        self,
        tender_ids: Iterable[int],
        *,
        channel: str = "serverchan",
    ) -> int:
        """记录一批已经成功发送的公告，重复记录会被忽略。"""

        normalized_channel = channel.strip().lower()
        if not normalized_channel:
            raise ValueError("通知渠道不能为空")
        ids = [int(tender_id) for tender_id in tender_ids]
        if not ids:
            return 0
        connection = self.connect()
        before = connection.total_changes
        connection.executemany(
            """INSERT OR IGNORE INTO notification_deliveries
               (tender_id, channel) VALUES (?, ?)""",
            [(tender_id, normalized_channel) for tender_id in ids],
        )
        connection.commit()
        return connection.total_changes - before

    def purge_tenders_older_than(
        self,
        retention_days: int,
        *,
        now: datetime | None = None,
    ) -> int:
        """删除发布时间早于保留期的公告，返回删除条数。

        以公告发布日（缺失时回退到入库日）与保留截止日（按自然日）比较。
        ``attachments`` 和 ``notification_deliveries`` 依赖外键
        ``ON DELETE CASCADE`` 一并删除（连接已启用
        ``PRAGMA foreign_keys = ON``）。
        """

        if retention_days <= 0:
            raise ValueError("retention_days 必须大于 0")
        reference = now or datetime.now(timezone.utc)
        cutoff_date = (reference - timedelta(days=retention_days)).date().isoformat()
        connection = self.connect()
        cursor = connection.execute(
            """DELETE FROM tenders
               WHERE COALESCE(substr(published_at, 1, 10), substr(created_at, 1, 10), '')
                     < ?""",
            (cutoff_date,),
        )
        connection.commit()
        return int(cursor.rowcount)

    def upsert_attachments(
        self,
        tender_id: int,
        attachments: Iterable[Mapping[str, Any]],
    ) -> None:
        connection = self.connect()
        for item in attachments:
            file_name = str(item.get("name") or item.get("fileName") or "").strip()
            file_url = str(item.get("url") or item.get("fileUrl") or "").strip()
            if not file_name and not file_url:
                continue
            connection.execute(
                """INSERT INTO attachments (tender_id, file_name, file_url, file_path, content_type)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(tender_id, file_name, file_url) DO UPDATE SET
                     file_path=excluded.file_path, content_type=excluded.content_type,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    tender_id,
                    file_name,
                    file_url,
                    item.get("path"),
                    item.get("contentType"),
                ),
            )
        connection.commit()

    def list_tenders(self, *, limit: int | None = None) -> list[sqlite3.Row]:
        connection = self.connect()
        sql = "SELECT * FROM tenders ORDER BY published_at DESC, id DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit 必须大于 0")
            sql += " LIMIT ?"
            params = (limit,)
        return list(connection.execute(sql, params).fetchall())

    def list_attachments(self, tender_id: int) -> list[sqlite3.Row]:
        connection = self.connect()
        return list(
            connection.execute(
                "SELECT * FROM attachments WHERE tender_id = ? ORDER BY id", (tender_id,)
            ).fetchall()
        )

    def update_attachment_extraction(
        self,
        attachment_id: int,
        *,
        file_path: str | None,
        status: str,
        text: str | None,
    ) -> None:
        connection = self.connect()
        connection.execute(
            """UPDATE attachments SET file_path=?, extraction_status=?, extracted_text=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (file_path, status, text, attachment_id),
        )
        connection.commit()
