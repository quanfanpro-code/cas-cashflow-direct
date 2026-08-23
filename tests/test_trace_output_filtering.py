from __future__ import annotations

from dataclasses import replace

import pytest

from cashflow_direct.classification import RulePack, StatementItem
from cashflow_direct.components import ComponentSourceAllocation
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    NormalizedEntry,
    SourceLocator,
)
from cashflow_direct.trace_output import _scope_status, build_trace_rows


ITEM_ID = "CFO-05"
ITEM_NAME = "支付其他与经营活动有关的现金"


def _entry(
    entry_id: str,
    row: int,
    account: str,
    *,
    summary: str,
    debit_cent: int = 0,
    credit_cent: int = 0,
    original_item: str = "",
    retained_side: str,
) -> NormalizedEntry:
    return NormalizedEntry(
        entry_id=entry_id,
        source=SourceLocator("F1", "序时账", row, row, f"A{row}:J{row}"),
        voucher_key="V1",
        voucher_date="2025-06-30",
        voucher_word="记",
        voucher_no="81",
        summary=summary,
        account_name=account,
        counterpart_name="",
        debit_cent=debit_cent,
        credit_cent=credit_cent,
        flow_amount_cent=abs(debit_cent - credit_cent) if original_item else 0,
        original_flow_item=original_item,
        retained_side=retained_side,
    )


def _rules() -> RulePack:
    return RulePack(
        (
            StatementItem(
                ITEM_ID,
                ITEM_NAME,
                "经营活动",
                1,
                True,
                "outflow",
                (),
            ),
        ),
        (),
    )


def _decision(component_id: str) -> ClassificationDecision:
    return ClassificationDecision(
        component_id=component_id,
        system_item_id=ITEM_ID,
        system_item_name=ITEM_NAME,
        normal_direction="outflow",
        matched_rule_id="TEST",
        reason="测试决定",
        evidence_level="strong",
        candidate_item_ids=(ITEM_ID,),
        original_item_state="consistent",
        summary_quality=45,
        account_path_quality=45,
        sources_independent=True,
        evidence_score=90,
        decision_action="自动保留",
        materiality_level="M1",
    )


def _state() -> dict[str, object]:
    return {
        "cash_scope": {
            "included_keys": ["1002"],
            "excluded_keys": ["1012"],
        },
        "versions": {"scoring": "S1", "action_matrix": "A1"},
        "structured_ai_validation": {"valid_results": []},
        "human_decisions": [],
    }


def _materiality(component_id: str) -> tuple[dict[str, object], ...]:
    return (
        {
            "record_id": component_id,
            "single_amount_cent": 10_000,
            "same_class_total_cent": 10_000,
            "single_level": "M1",
        },
    )


def test_scope_status_recognizes_the_customer_name_kept_after_mapping() -> None:
    state = {
        "cash_scope": {
            "included_keys": ["银行存款_一般户"],
            "excluded_keys": [],
            "account_names_by_key": [
                ["银行存款_一般户", ["客户银行款_一般户"]]
            ],
        }
    }

    assert _scope_status("客户银行款_一般户", state) == "现金及现金等价物范围内"


def test_trace_names_a_new_reversal_pattern_instead_of_hiding_it_as_direction_error() -> None:
    cash = _entry(
        "E-CASH-REVERSAL",
        8,
        "1002 银行存款_基本户",
        summary="退回设备尾款",
        debit_cent=10_000,
        retained_side="cash",
    )
    counterpart = _entry(
        "E-COUNTERPART-REVERSAL",
        9,
        "应付账款_应付设备款_往来款",
        summary="退回设备尾款",
        credit_cent=10_000,
        original_item=ITEM_NAME,
        retained_side="counterpart",
    )
    component = CashflowComponent(
        "C-REVERSAL",
        "V1",
        "退回设备尾款",
        10_000,
        (counterpart.account_name,),
        ITEM_NAME,
        (cash.entry_id, counterpart.entry_id),
    )
    decision = replace(
        _decision(component.component_id),
        direction_status="incompatible",
        new_reversal_pattern=True,
        resolved=False,
        decision_action="confirm_reversal_rule",
    )

    row = build_trace_rows(
        (cash, counterpart),
        (component,),
        (decision,),
        (ComponentSourceAllocation(component.component_id, cash.entry_id, 10_000),),
        _materiality(component.component_id),
        _rules(),
        _state(),
        {"F1": "匿名序时账.xlsx"},
    )[0]

    assert row["强制检查"] == "新的退款或反向冲减模式待确认"
    assert "新的退款或反向冲减模式" in row["异常"]


def test_trace_displays_m1_individual_tax_batch_as_general_human_batch() -> None:
    cash = _entry(
        "E-CASH-TAX",
        18,
        "1002 银行存款_基本户",
        summary="代扣个人所得税",
        credit_cent=10_000,
        retained_side="cash",
    )
    counterpart = _entry(
        "E-COUNTERPART-TAX",
        19,
        "应交税费_个人所得税",
        summary="代扣个人所得税",
        debit_cent=10_000,
        retained_side="counterpart",
    )
    component = CashflowComponent(
        "C-TAX-BATCH",
        "V1",
        "代扣个人所得税",
        -10_000,
        (counterpart.account_name,),
        "",
        (cash.entry_id, counterpart.entry_id),
    )
    decision = replace(
        _decision(component.component_id),
        resolved=False,
        decision_action="human_batch",
        individual_tax_fact_missing=True,
    )

    row = build_trace_rows(
        (cash, counterpart),
        (component,),
        (decision,),
        (ComponentSourceAllocation(component.component_id, cash.entry_id, -10_000),),
        _materiality(component.component_id),
        _rules(),
        _state(),
        {"F1": "匿名序时账.xlsx"},
    )[0]

    assert row["唯一动作"] == "人工批量决定"
    assert row["强制检查"] == "个税服务对象不明"
    assert row["复核状态"] == "等待人工决定"


def test_trace_keeps_only_effective_cashflow_segment_and_uses_component_original_item() -> None:
    entries = (
        _entry(
            "E-CASH",
            10,
            "1002 银行存款_基本户",
            summary="资金划转",
            credit_cent=10_000,
            retained_side="cash",
        ),
        _entry(
            "E-BUSINESS",
            11,
            "1012 其他货币资金_保证金户",
            summary="支付投标保证金",
            debit_cent=10_000,
            original_item=ITEM_NAME,
            retained_side="counterpart",
        ),
        _entry(
            "E-IRRELEVANT",
            12,
            "其他应付款_往来款",
            summary="凭证中的非现金流附带分录",
            retained_side="counterpart",
        ),
    )
    component = CashflowComponent(
        "C1",
        "V1",
        "支付投标保证金",
        -10_000,
        ("1012 其他货币资金_保证金户",),
        ITEM_NAME,
        ("E-CASH", "E-BUSINESS"),
    )

    rows = build_trace_rows(
        entries,
        (component,),
        (_decision("C1"),),
        (ComponentSourceAllocation("C1", "E-CASH", -10_000),),
        _materiality("C1"),
        _rules(),
        _state(),
        {"F1": "匿名序时账.xlsx"},
    )

    assert len(rows) == 1
    assert rows[0]["凭证号"] == "81"
    assert rows[0]["本行摘要"] == "资金划转"
    assert rows[0]["原始完整科目路径"] == "1002 银行存款_基本户"
    assert rows[0]["本行完整对方科目路径"] == "1012 其他货币资金_保证金户"
    assert rows[0]["原现流项目"] == ITEM_NAME
    assert rows[0]["本行分配现金变化"] == -100


def test_trace_exposes_original_account_levels_and_source_amounts_for_manual_review() -> None:
    cash = _entry(
        "E-CASH-DETAIL",
        40,
        "1002 银行存款_基本户",
        summary="支付设备保证金",
        credit_cent=10_000,
        retained_side="cash",
    )
    business = replace(
        _entry(
            "E-BUSINESS-DETAIL",
            41,
            "其他货币资金_项目保证金_甲公司",
            summary="支付设备保证金",
            debit_cent=10_000,
            original_item=ITEM_NAME,
            retained_side="counterpart",
        ),
        account_code="1012.01.01",
        source_debit_cent=10_000,
        source_flow_amount_cent=10_000,
    )
    component = CashflowComponent(
        "C-DETAIL",
        "V1",
        "支付设备保证金",
        -10_000,
        (business.account_name,),
        ITEM_NAME,
        (cash.entry_id, business.entry_id),
    )
    state = _state()
    state["account_dictionary_completed"] = True

    row = build_trace_rows(
        (cash, business),
        (component,),
        (_decision(component.component_id),),
        (ComponentSourceAllocation(component.component_id, cash.entry_id, -10_000),),
        _materiality(component.component_id),
        _rules(),
        state,
        {"F1": "匿名序时账.xlsx"},
    )[0]

    assert row["原始一级科目"] == "其他货币资金"
    assert row["原始科目编码"] == ""
    assert row["原始完整科目路径"] == "1002 银行存款_基本户"
    assert row["标准一级科目"] == "其他货币资金"
    assert row["中间层级"] == "项目保证金"
    assert row["末级明细"] == "甲公司"
    assert row["映射状态"] == "已确认"
    assert row["借方"] == "未记录"
    assert row["贷方"] == 100
    assert row["流量金额（原币）"] == "未记录"

    with pytest.raises(ValueError, match="一级科目映射未全部确认"):
        build_trace_rows(
            (cash, business),
            (replace(component, account_mapping_status="manual"),),
            (_decision(component.component_id),),
            (ComponentSourceAllocation(component.component_id, cash.entry_id, -10_000),),
            _materiality(component.component_id),
            _rules(),
            state,
            {"F1": "构造临时表.xlsx"},
        )


def test_trace_ai_process_includes_round_reviewer_model_time_and_source_basis() -> None:
    cash = _entry(
        "E-CASH-AI",
        50,
        "1002 银行存款_基本户",
        summary="支付保证金",
        credit_cent=10_000,
        retained_side="cash",
    )
    business = _entry(
        "E-BUSINESS-AI",
        51,
        "其他货币资金_保证金户",
        summary="支付保证金",
        debit_cent=10_000,
        original_item=ITEM_NAME,
        retained_side="counterpart",
    )
    component = CashflowComponent(
        "C-AI-DETAIL",
        "V1",
        "支付保证金",
        -10_000,
        (business.account_name,),
        ITEM_NAME,
        (cash.entry_id, business.entry_id),
    )
    state = _state()
    state["structured_ai_validation"] = {
        "valid_results": [
            {
                "task_id": "AI-DETAIL",
                "component_id": component.component_id,
                "summary": {"candidate_item_id": ITEM_ID, "basis_text": "支付保证金"},
                "account_path": {"candidate_item_id": ITEM_ID, "basis_text": "其他货币资金_保证金户"},
                "reason": "两个来源一致",
                "review_round": "A",
                "reviewer_id": "复核人甲",
                "model_id": "模型甲",
                "reviewed_at": "2026-08-21T09:00:00+08:00",
            }
        ]
    }

    rows = build_trace_rows(
        (cash, business),
        (component,),
        (_decision(component.component_id),),
        (ComponentSourceAllocation(component.component_id, cash.entry_id, -10_000),),
        _materiality(component.component_id),
        _rules(),
        state,
        {"F1": "匿名序时账.xlsx"},
    )
    row = next(
        item
        for item in rows
        if item["原始完整科目路径"] == "1002 银行存款_基本户"
    )

    process = row["AI复核过程"]
    for expected in (
        "轮次A",
        "任务AI-DETAIL",
        "执行者复核人甲",
        "模型模型甲",
        "2026-08-21T09:00:00+08:00",
        "摘要依据支付保证金",
        "路径依据其他货币资金_保证金户",
    ):
        assert expected in process


def test_trace_keeps_cross_scope_transfer_and_shows_both_scope_sides() -> None:
    entries = (
        _entry(
            "E-CASH",
            20,
            "1002 银行存款_基本户",
            summary="划入保证金户",
            credit_cent=10_000,
            retained_side="cash",
        ),
        _entry(
            "E-MARGIN",
            21,
            "1012 其他货币资金_保证金户",
            summary="支付履约保证金",
            debit_cent=10_000,
            original_item=ITEM_NAME,
            retained_side="cash",
        ),
    )
    component = CashflowComponent(
        "C2",
        "V1",
        "支付履约保证金",
        -10_000,
        ("1012 其他货币资金_保证金户",),
        ITEM_NAME,
        ("E-CASH", "E-MARGIN"),
    )

    row = build_trace_rows(
        entries,
        (component,),
        (_decision("C2"),),
        (ComponentSourceAllocation("C2", "E-CASH", -10_000),),
        _materiality("C2"),
        _rules(),
        _state(),
        {"F1": "匿名序时账.xlsx"},
    )[0]

    assert row["现金账户路径"] == "1002 银行存款_基本户"
    assert row["现金账户范围状态"] == "现金及现金等价物范围内"
    assert row["本行完整对方科目路径"] == "1012 其他货币资金_保证金户"
    assert row["对方科目范围状态"] == "现金及现金等价物范围外"
    assert row["原始完整科目路径"] == "1002 银行存款_基本户"


def test_trace_expands_one_component_by_cash_allocation_without_unrelated_rows() -> None:
    entries = (
        _entry(
            "E-CASH-1",
            30,
            "1002 银行存款_甲户",
            summary="分两户支付",
            credit_cent=6_000,
            retained_side="cash",
        ),
        _entry(
            "E-CASH-2",
            31,
            "1002 银行存款_乙户",
            summary="分两户支付",
            credit_cent=4_000,
            retained_side="cash",
        ),
        _entry(
            "E-BUSINESS",
            32,
            "1012 其他货币资金_保证金户",
            summary="支付保证金",
            debit_cent=10_000,
            original_item=ITEM_NAME,
            retained_side="counterpart",
        ),
    )
    component = CashflowComponent(
        "C3",
        "V1",
        "支付保证金",
        -10_000,
        ("1012 其他货币资金_保证金户",),
        ITEM_NAME,
        ("E-CASH-1", "E-CASH-2", "E-BUSINESS"),
    )

    rows = build_trace_rows(
        entries,
        (component,),
        (_decision("C3"),),
        (
            ComponentSourceAllocation("C3", "E-CASH-1", -6_000),
            ComponentSourceAllocation("C3", "E-CASH-2", -4_000),
        ),
        _materiality("C3"),
        _rules(),
        _state(),
        {"F1": "匿名序时账.xlsx"},
    )

    assert [row["本行分配现金变化"] for row in rows] == [-60, -40]
    assert [row["现金账户路径"] for row in rows] == [
        "1002 银行存款_甲户",
        "1002 银行存款_乙户",
    ]
    assert {row["组成明细"] for row in rows} == {
        "1002 银行存款_甲户（A30:J30）：-60.00元",
        "1002 银行存款_乙户（A31:J31）：-40.00元",
    }
    assert all(row["业务组成编号(技术)"] == "C3" for row in rows)


def test_trace_distinguishes_repeated_same_account_same_amount_allocations() -> None:
    entries = (
        _entry(
            "E-SAME-1",
            60,
            "1002 银行存款_同一户",
            summary="分两笔支付",
            credit_cent=5_000,
            retained_side="cash",
        ),
        _entry(
            "E-SAME-2",
            61,
            "1002 银行存款_同一户",
            summary="分两笔支付",
            credit_cent=5_000,
            retained_side="cash",
        ),
        _entry(
            "E-SAME-BUSINESS",
            62,
            "其他应付款_保证金",
            summary="支付保证金",
            debit_cent=10_000,
            original_item=ITEM_NAME,
            retained_side="counterpart",
        ),
    )
    component = CashflowComponent(
        "C-SAME",
        "V1",
        "支付保证金",
        -10_000,
        ("其他应付款_保证金",),
        ITEM_NAME,
        tuple(entry.entry_id for entry in entries),
    )

    rows = build_trace_rows(
        entries,
        (component,),
        (_decision(component.component_id),),
        (
            ComponentSourceAllocation(component.component_id, "E-SAME-1", -5_000),
            ComponentSourceAllocation(component.component_id, "E-SAME-2", -5_000),
        ),
        _materiality(component.component_id),
        _rules(),
        _state(),
        {"F1": "匿名序时账.xlsx"},
    )

    assert [row["组成明细"] for row in rows] == [
        "1002 银行存款_同一户（A60:J60）：-50.00元",
        "1002 银行存款_同一户（A61:J61）：-50.00元",
    ]
