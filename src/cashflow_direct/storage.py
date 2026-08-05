from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path


BUSINESS_TABLES = (
    "run_manifest",
    "source_file",
    "sheet_structure",
    "field_mapping",
    "source_entry",
    "voucher",
    "cash_scope",
    "cashflow_component",
    "classification_decision",
    "ai_task",
    "ai_result",
    "internal_transfer",
    "review_batch",
    "duplicate_group",
    "statement_value",
    "statement_comparison",
    "reconciliation",
)


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS stage_status ("
                "stage TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS run_event ("
                "event_id INTEGER PRIMARY KEY, stage TEXT NOT NULL, message TEXT NOT NULL)"
            )
            for table in BUSINESS_TABLES:
                connection.execute(
                    f'CREATE TABLE IF NOT EXISTS "{table}" ('
                    "record_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL DEFAULT '{}')"
                )

    def get_stage_status(self, name: str) -> str | None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            row = connection.execute(
                "SELECT status FROM stage_status WHERE stage = ?", (name,)
            ).fetchone()
        return None if row is None else str(row[0])

    def _set_stage_status(self, name: str, status: str) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO stage_status(stage, status) VALUES (?, ?) "
                "ON CONFLICT(stage) DO UPDATE SET status=excluded.status, updated_at=CURRENT_TIMESTAMP",
                (name, status),
            )

    @contextmanager
    def stage(self, name: str) -> Iterator[sqlite3.Connection]:
        if not name.strip():
            raise ValueError("阶段名称不能为空")
        self._set_stage_status(name, "running")
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("BEGIN")
            yield connection
        except BaseException:
            connection.rollback()
            connection.close()
            self._set_stage_status(name, "failed")
            raise
        else:
            connection.commit()
            connection.close()
            self._set_stage_status(name, "completed")
