from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path


def _records_by_id(records: object, key: str = "component_id") -> dict[str, object]:
    if not isinstance(records, (list, tuple)):
        return {}
    result: dict[str, object] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        record_id = str(record.get(key, "")).strip()
        if record_id:
            result[record_id] = record
    return result


def _load_table(connection: sqlite3.Connection, table: str) -> dict[str, object]:
    return {
        str(record_id): json.loads(payload_json)
        for record_id, payload_json in connection.execute(
            f'SELECT record_id, payload_json FROM "{table}"'
        )
    }


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def assert_decision_store_consistent(
    run_dir: Path,
    state: Mapping[str, object],
    *,
    allow_finalized_recovery: bool = False,
) -> None:
    """阻止直接编辑运行状态绕过SQLite计算留痕。"""
    database = Path(run_dir) / "计算留痕数据" / "计算留痕.sqlite3"
    if not database.is_file() or "decisions" not in state:
        return
    with closing(sqlite3.connect(database)) as connection:
        stored_decisions = _load_table(connection, "classification_decision")
        stored_human = _load_table(connection, "human_decision")
    state_decisions = _records_by_id(state.get("decisions", ()))
    if stored_decisions and (
        set(stored_decisions) != set(state_decisions)
        or any(
            _canonical(stored_decisions[record_id])
            != _canonical(state_decisions[record_id])
            for record_id in stored_decisions
        )
    ):
        raise RuntimeError("运行状态与计算留痕不一致：分类决定可能被直接修改")

    state_human = _records_by_id(state.get("human_decisions", ()))
    if stored_human and (
        set(stored_human) != set(state_human)
        or any(
            _canonical(stored_human[record_id]) != _canonical(state_human[record_id])
            for record_id in stored_human
        )
    ):
        raise RuntimeError("运行状态与计算留痕不一致：人工决定可能被直接修改")

    pending = [
        record
        for record in state_decisions.values()
        if not bool(record.get("resolved")) and not bool(record.get("excluded"))
    ]
    if pending:
        has_ai = any(
            str(record.get("decision_action", ""))
            in {
                "ai_review",
                "double_ai_review",
                "ai_double_followup_review",
                "ai_third_review",
            }
            for record in pending
        )
        expected_stage = "waiting_ai" if has_ai else "waiting_human"
        actual_stage = str(state.get("stage", ""))
        finalized_recovery = (
            allow_finalized_recovery
            and actual_stage == "finalized"
            and bool(state.get("overall_status"))
            and bool(state.get("workbook_path"))
        )
        if actual_stage != expected_stage and not finalized_recovery:
            raise RuntimeError(
                "运行阶段与待处理事项不一致：运行状态可能被直接修改"
            )
