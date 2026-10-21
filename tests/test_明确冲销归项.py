from dataclasses import replace

from cashflow_direct.documented_reversals import apply_documented_reversals
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    NormalizedEntry,
    SourceLocator,
)
from cashflow_direct.rule_registry import default_rule_registry


def _cash(entry_id, day, voucher, summary, delta, account="1002001"):
    return NormalizedEntry(
        entry_id,
        SourceLocator("FILE-1", "原账", 1, 1, "A1"),
        voucher,
        day,
        "17" if voucher == "原凭证" else "2",
        summary,
        "银行存款",
        "",
        max(delta, 0),
        max(-delta, 0),
        0,
        "",
        retained_side="cash",
        voucher_word="记",
        account_code=account,
    )


def _decision(component_id, item_id="", resolved=False):
    return ClassificationDecision(
        component_id=component_id,
        system_item_id=item_id,
        system_item_name="收到其他与经营活动有关的现金" if item_id else "",
        normal_direction="inflow" if item_id else "net",
        matched_rule_id="TEST",
        reason="测试前决定",
        evidence_level="invalid",
        resolved=resolved,
        decision_action="automatic_keep" if resolved else "human_decision",
    )


def _case():
    original = _cash("原现金", "2026-02-01", "原凭证", "固定资产处置", 300000)
    reversal = _cash(
        "冲销现金",
        "2026-07-01",
        "冲销凭证",
        "[冲销2026.02.01 记-0017号凭证]固定资产处置",
        -300000,
    )
    original_component = CashflowComponent(
        "原业务", "原凭证", "固定资产清理", 300000,
        source_keys=(original.entry_id,), voucher_date="2026-02-01", voucher_no="17",
    )
    reversal_component = CashflowComponent(
        "冲销业务", "冲销凭证", reversal.summary, -300000,
        source_keys=(reversal.entry_id,), voucher_date="2026-07-01", voucher_no="2",
    )
    return original_component, reversal_component, original, reversal


def test_等额冲销沿用原项目并在同一项目相抵():
    original_component, reversal_component, original, reversal = _case()
    decisions = (_decision("原业务", "CFO-03", True), _decision("冲销业务"))
    policy = default_rule_registry().special_policy["documented_reversal"]
    result, links = apply_documented_reversals(
        (original_component, reversal_component), decisions,
        (original, reversal), overall_cent=130000000, policy=policy,
    )
    assert len(links) == 1
    assert result[1].system_item_id == "CFO-03"
    assert result[1].decision_source == "system_documented_reversal"
    assert result[1].resolved
    assert original_component.cash_delta_cent + reversal_component.cash_delta_cent == 0
    assert "原现金摘要" in result[1].reason


def test_缺少唯一原凭证或现金账户不同时保持待定():
    original_component, reversal_component, original, reversal = _case()
    policy = default_rule_registry().special_policy["documented_reversal"]
    decisions = (_decision("原业务", "CFO-03", True), _decision("冲销业务"))
    for components, entries in (
        ((original_component, reversal_component), (original, replace(reversal, account_code="1002002"))),
        ((original_component, reversal_component), (original, replace(reversal, debit_cent=0, credit_cent=299999))),
        ((original_component, replace(original_component, component_id="重复原业务"), reversal_component), (original, reversal)),
    ):
        chosen = decisions if len(components) == 2 else (
            decisions[0], _decision("重复原业务", "CFO-03", True), decisions[1]
        )
        result, links = apply_documented_reversals(
            components, chosen, entries, overall_cent=130000000, policy=policy,
        )
        assert not links
        assert result[-1].system_item_id == ""


def test_达到整体重要性不自动取消人工决定():
    original_component, reversal_component, original, reversal = _case()
    policy = default_rule_registry().special_policy["documented_reversal"]
    result, links = apply_documented_reversals(
        (original_component, reversal_component),
        (_decision("原业务", "CFO-03", True), _decision("冲销业务")),
        (original, reversal), overall_cent=300000, policy=policy,
    )
    assert not links
    assert not result[-1].resolved
