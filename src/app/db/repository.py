from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import UUID

from app.config import ORDER_AUDIT_RECORD_LIMIT
from app.models import (
    AgentRunResult,
    ApprovalRecord,
    ApprovalStatus,
    ToolCallRecord,
    utc_now,
)


class SQLiteRepository:
    def __init__(self, database_path: str) -> None:
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    request TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    call_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    action_key TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_approval_action
                    ON approvals (run_id, action_key);
                CREATE INDEX IF NOT EXISTS idx_tool_calls_order_timestamp
                    ON tool_calls (
                        json_extract(arguments_json, '$.order_id'),
                        started_at DESC,
                        sequence DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_approvals_order_timestamp
                    ON approvals (
                        json_extract(arguments_json, '$.order_id'),
                        created_at DESC
                    );
                """
            )

    def create_run(self, result: AgentRunResult) -> None:
        serialized = json.dumps(result.model_dump(mode="json"))
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO runs
                (run_id, request, mode, status, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(result.run_id),
                    result.request,
                    result.mode,
                    result.status.value,
                    serialized,
                    result.created_at.isoformat(),
                    result.updated_at.isoformat(),
                ),
            )

    def save_result(self, result: AgentRunResult) -> None:
        serialized = json.dumps(result.model_dump(mode="json"))
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE runs SET status = ?, result_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    result.status.value,
                    serialized,
                    result.updated_at.isoformat(),
                    str(result.run_id),
                ),
            )

    def append_event(
        self,
        run_id: UUID,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO events
                (run_id, sequence, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    sequence,
                    event_type,
                    json.dumps(payload),
                    utc_now().isoformat(),
                ),
            )

    def record_tool_call(self, run_id: UUID, record: ToolCallRecord) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO tool_calls
                (call_id, run_id, sequence, name, arguments_json, status, result_json,
                 error, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.call_id,
                    str(run_id),
                    record.sequence,
                    record.name,
                    json.dumps(record.arguments),
                    record.status.value,
                    json.dumps(record.result) if record.result is not None else None,
                    record.error,
                    record.started_at.isoformat(),
                    record.finished_at.isoformat(),
                ),
            )

    def create_approval(self, approval: ApprovalRecord, run_id: UUID) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO approvals
                (approval_id, run_id, action_key, tool_name, arguments_json, status,
                 created_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(approval.id),
                    str(run_id),
                    approval.action_key,
                    approval.tool_name,
                    json.dumps(approval.arguments),
                    approval.status.value,
                    approval.created_at.isoformat(),
                    approval.resolved_at.isoformat() if approval.resolved_at else None,
                ),
            )

    def resolve_approval(
        self,
        approval_id: UUID,
        expected_status: ApprovalStatus,
        status: ApprovalStatus,
        resolved_at: datetime,
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE approvals SET status = ?, resolved_at = ?
                WHERE approval_id = ? AND status = ?
                """,
                (
                    status.value,
                    resolved_at.isoformat(),
                    str(approval_id),
                    expected_status.value,
                ),
            )
            return cursor.rowcount == 1

    def get_approval(self, approval_id: UUID) -> ApprovalRecord | None:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (str(approval_id),),
        ).fetchone()
        if row is None:
            return None
        return ApprovalRecord(
            id=UUID(row["approval_id"]),
            action_key=row["action_key"],
            tool_name=row["tool_name"],
            arguments=json.loads(row["arguments_json"]),
            status=ApprovalStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
            ),
        )

    def get_result(self, run_id: UUID) -> AgentRunResult | None:
        row = self._connection.execute(
            "SELECT result_json FROM runs WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        if row is None:
            return None
        return AgentRunResult.model_validate(json.loads(row["result_json"]))

    def get_order_audit(self, order_id: str) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        tool_call_count = self._connection.execute(
            """
            SELECT COUNT(*)
            FROM tool_calls
            WHERE json_extract(arguments_json, '$.order_id') = ?
            """,
            (order_id,),
        ).fetchone()[0]
        tool_calls = self._connection.execute(
            """
            SELECT call_id, run_id, sequence, name, arguments_json, status,
                   CASE WHEN name = 'get_order_audit' THEN NULL ELSE result_json END
                       AS result_json,
                   error, started_at, finished_at
            FROM tool_calls
            WHERE json_extract(arguments_json, '$.order_id') = ?
            ORDER BY started_at DESC, sequence DESC
            LIMIT ?
            """,
            (order_id, ORDER_AUDIT_RECORD_LIMIT),
        )
        for row in tool_calls:
            arguments = json.loads(row["arguments_json"])
            records.append(
                {
                    "source": "tool_calls",
                    "call_id": row["call_id"],
                    "run_id": row["run_id"],
                    "sequence": row["sequence"],
                    "name": row["name"],
                    "arguments": arguments,
                    "status": row["status"],
                    "result": json.loads(row["result_json"]) if row["result_json"] else None,
                    "error": row["error"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                }
            )

        approval_count = self._connection.execute(
            """
            SELECT COUNT(*)
            FROM approvals
            WHERE json_extract(arguments_json, '$.order_id') = ?
            """,
            (order_id,),
        ).fetchone()[0]
        approvals = self._connection.execute(
            """
            SELECT approval_id, run_id, action_key, tool_name, arguments_json,
                   status, created_at, resolved_at
            FROM approvals
            WHERE json_extract(arguments_json, '$.order_id') = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (order_id, ORDER_AUDIT_RECORD_LIMIT),
        )
        for row in approvals:
            arguments = json.loads(row["arguments_json"])
            records.append(
                {
                    "source": "approvals",
                    "approval_id": row["approval_id"],
                    "run_id": row["run_id"],
                    "action_key": row["action_key"],
                    "tool_name": row["tool_name"],
                    "arguments": arguments,
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "resolved_at": row["resolved_at"],
                }
            )

        records.sort(
            key=lambda record: (
                record.get("started_at") or record["created_at"],
                record.get("sequence", -1),
            ),
            reverse=True,
        )
        record_count = tool_call_count + approval_count
        latest_records = records[:ORDER_AUDIT_RECORD_LIMIT]
        return {
            "order_id": order_id,
            "record_count": record_count,
            "returned_record_count": len(latest_records),
            "records": latest_records,
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()
