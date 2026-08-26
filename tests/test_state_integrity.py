from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

from cashflow_direct.models import ClassificationDecision
from cashflow_direct.state_integrity import assert_decision_store_consistent
from cashflow_direct.storage import RunStore


def decision() -> ClassificationDecision:
    return ClassificationDecision(
        component_id="C1",
        system_item_id="CFO-07",
        system_item_name="支付其他与经营活动有关的现金",
        normal_direction="outflow",
        matched_rule_id="TEST",
        reason="测试决定",
        evidence_level="medium",
        resolved=True,
        evidence_score=45,
        decision_action="automatic_fill",
    )


def write_store(run_dir: Path, current: ClassificationDecision) -> None:
    store = RunStore(run_dir / "计算留痕数据" / "计算留痕.sqlite3")
    store.initialize()
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "INSERT INTO classification_decision(record_id, payload_json) VALUES (?, ?)",
            (current.component_id, json.dumps(asdict(current), ensure_ascii=False)),
        )


def test_decision_store_accepts_identical_state(tmp_path: Path) -> None:
    current = decision()
    write_store(tmp_path, current)

    assert_decision_store_consistent(
        tmp_path,
        {"decisions": [asdict(current)], "stage": "classification_completed"},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (("excluded", True), ("system_item_id", "CFO-04")),
)
def test_direct_decision_state_edit_is_detected(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    current = decision()
    write_store(tmp_path, current)
    tampered = asdict(current)
    tampered[field] = value

    with pytest.raises(RuntimeError, match="运行状态与计算留痕不一致"):
        assert_decision_store_consistent(
            tmp_path,
            {"decisions": [tampered], "stage": "classification_completed"},
        )


def test_direct_stage_edit_is_detected_when_decision_is_still_pending(
    tmp_path: Path,
) -> None:
    pending = ClassificationDecision(
        **{
            **asdict(decision()),
            "resolved": False,
            "decision_action": "human_decision",
        }
    )
    write_store(tmp_path, pending)

    with pytest.raises(RuntimeError, match="运行阶段与待处理事项不一致"):
        assert_decision_store_consistent(
            tmp_path,
            {"decisions": [asdict(pending)], "stage": "classification_completed"},
        )


def test_formally_finalized_run_can_enter_missing_workbook_recovery(
    tmp_path: Path,
) -> None:
    pending = ClassificationDecision(
        **{
            **asdict(decision()),
            "resolved": False,
            "decision_action": "human_decision",
        }
    )
    write_store(tmp_path, pending)

    assert_decision_store_consistent(
        tmp_path,
        {
            "decisions": [asdict(pending)],
            "stage": "finalized",
            "overall_status": "待完成人工确认",
            "workbook_path": str(tmp_path / "已生成底稿.xlsx"),
        },
        allow_finalized_recovery=True,
    )
