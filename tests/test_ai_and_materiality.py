from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cashflow_direct.ai_review import (
    chunk_ai_tasks,
    redact_text,
    validate_basis_text,
    write_ai_tasks_jsonl,
)
from cashflow_direct.classification import load_rule_pack
from cashflow_direct.materiality import build_review_batches
from cashflow_direct.models import AITask, UnresolvedDecision
from cashflow_direct.pipeline import _review_text_pattern


ROOT = Path(__file__).resolve().parents[1]


def _task(index: int) -> AITask:
    return AITask(
        task_id=f"AI-{index}",
        component_id=f"C-{index}",
        context="摘要原文：匿名往来；完整对方科目路径：其他应付款_匿名对象",
        original_item="",
        system_item_id="CFO-03",
        rule_evidence="系统候选仅供复核",
        candidate_item_ids=("CFO-03",),
    )


def _unresolved(
    component_id: str,
    amount_cent: int,
    *,
    summary_pattern: str = "普通往来",
    alternatives: tuple[str, ...] = ("CFO-03", "CFI-05"),
    mandatory: bool = False,
) -> UnresolvedDecision:
    return UnresolvedDecision(
        component_id=component_id,
        cash_delta_cent=amount_cent,
        cash_direction="inflow" if amount_cent > 0 else "outflow",
        original_item="",
        system_item_id="CFO-03",
        review_status="统一动作表要求人工决定",
        counterpart_group="其他应付款_匿名对象",
        summary_pattern=summary_pattern,
        alternative_item_ids=alternatives,
        reason="结构化复核后仍不能形成唯一决定",
        mandatory=mandatory,
    )


def test_ai_batches_never_exceed_25() -> None:
    tasks = tuple(_task(index) for index in range(61))
    assert [len(batch) for batch in chunk_ai_tasks(tasks)] == [25, 25, 11]


def test_ai_batch_size_outside_supported_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="1 至 25"):
        chunk_ai_tasks((_task(1),), size=26)


def test_ordinary_ai_task_jsonl_is_bom_encoded_and_exposes_only_allowed_input() -> None:
    tasks = tuple(_task(index) for index in range(3))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "AI任务.jsonl"
        write_ai_tasks_jsonl(path, tasks)
        assert path.read_bytes().startswith(bytes.fromhex("EFBBBF"))
        rows = tuple(
            json.loads(line)
            for line in path.read_text(encoding="utf-8-sig").splitlines()
        )
        assert len(rows) == 3
        forbidden_fields = {
            "original_item",
            "system_item_id",
            "rule_evidence",
            "candidate_item_ids",
            "summary_candidate_item_ids",
            "account_path_candidate_item_ids",
        }
        assert all(not (forbidden_fields & set(row)) for row in rows)
        assert all("摘要原文" in row["context"] for row in rows)
        assert all("完整对方科目路径" in row["context"] for row in rows)


def test_sensitive_numbers_are_redacted_before_ai_request() -> None:
    text = "电话13800138000，身份证510101199001011234，账号6222021234567890123"
    masked = redact_text(text)
    assert "13800138000" not in masked
    assert "510101199001011234" not in masked
    assert "6222021234567890123" not in masked


@pytest.mark.parametrize(
    "basis",
    (
        "依据企业会计准则第31号第十条第（一）项",
        "依据企业会计准则第31号第十项",
        "应用指南第三十二章‘销售商品、提供劳务收到的现金’",
        "知识库第433行：代扣代缴的个人所得税款",
        "依据公司特殊规则：NOTE-01",
    ),
)
def test_basis_gate_accepts_traceable_basis(basis: str) -> None:
    assert validate_basis_text(basis) is None


@pytest.mark.parametrize(
    "basis",
    ("根据准则", "综合判断", "摘要支持", "根据业务实质", ""),
)
def test_basis_gate_rejects_vague_basis(basis: str) -> None:
    assert validate_basis_text(basis) is not None


def test_explicit_human_route_is_not_dropped_below_performance() -> None:
    batches = build_review_batches(
        (_unresolved("small", 74_999_999),),
        performance_cent=75_000_000,
    )
    assert len(batches) == 1
    assert batches[0].component_ids == ("small",)


def test_unresolved_without_a_real_alternative_is_rejected() -> None:
    with pytest.raises(ValueError, match="没有可供人工选择的备选现流项目"):
        build_review_batches(
            (_unresolved("no-alternative", 80_000_000, alternatives=()),),
            performance_cent=75_000_000,
        )


def test_each_human_item_gets_its_own_review_row() -> None:
    same_a = _unresolved("a", 40_000_000)
    same_b = _unresolved("b", 40_000_000)
    different = _unresolved(
        "c",
        80_000_000,
        summary_pattern="投资词",
        alternatives=("CFO-03", "CFI-01"),
    )

    batches = build_review_batches((same_a, same_b, different), 75_000_000)

    assert len(batches) == 3
    assert [batch.component_ids for batch in batches] == [("a",), ("b",), ("c",)]
    assert [batch.cash_delta_cent for batch in batches] == [40_000_000, 40_000_000, 80_000_000]


def test_review_pattern_keeps_business_text_but_removes_dates_and_numbers() -> None:
    first = _review_text_pattern("支付甲公司服务费 2026-01-01 1,000元")
    second = _review_text_pattern("支付乙公司服务费 2026-02-02 2,000元")
    assert first != second
    assert "2026" not in first
    assert "1000" not in first


def test_mandatory_batch_offers_every_other_leaf_item() -> None:
    rules = load_rule_pack(ROOT)
    leaf_ids = tuple(item.item_id for item in rules.statement_items if item.is_leaf)
    item = _unresolved(
        "CMP-BIG",
        -240_333_845,
        alternatives=(),
        mandatory=True,
    )

    batches = build_review_batches(
        (item,),
        1_000_000,
        all_leaf_item_ids=leaf_ids,
    )

    assert len(batches) == 1
    assert batches[0].mandatory
    assert len(batches[0].alternative_item_codes) == len(leaf_ids) - 1
    assert "CFO-03" not in batches[0].alternative_item_codes
