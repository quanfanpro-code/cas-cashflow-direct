from dataclasses import replace
from pathlib import Path
from copy import deepcopy

from cashflow_direct.component_structure import resolve_voucher_structure, validate_structure_plan
from cashflow_direct.rule_registry import default_rule_registry
from cashflow_direct.classification import classify_all, load_rule_pack, route_classification_decisions
from cashflow_direct.models import MaterialityAmounts

from cashflow_direct.components import build_cashflow_components, confirm_cash_scope, discover_cash_scope
from tests.fixture_factory import _component_entry


def entry(n, account, amount, summary):
    return _component_entry(n, "V-STRUCT", account, debit_cent=max(amount, 0),
                            credit_cent=max(-amount, 0), summary=summary)


def build(rows, **kwargs):
    proposal = discover_cash_scope(rows)
    scope = confirm_cash_scope(proposal, {x.account_key: "include" for x in proposal.candidates})
    return build_cashflow_components(rows, scope, **kwargs)


def test_reciprocal_counterparts_identify_unique_cash_pairs_with_broader_cash_summary():
    rows = (
        replace(entry(1, "应交税费_未交增值税", 501, "缴纳上月增值税"), counterpart_name="银行存款"),
        replace(entry(2, "应交税费_城市维护建设税", 102, "缴纳上月附加税"), counterpart_name="银行存款"),
        replace(entry(3, "应交税费_教育费附加", 77, "缴纳上月附加税"), counterpart_name="银行存款"),
        replace(entry(4, "银行存款_甲银行", -501, "缴纳上月增值税及附加税"), counterpart_name="应交税费"),
        replace(entry(5, "银行存款_甲银行", -102, "缴纳上月增值税及附加税"), counterpart_name="应交税费"),
        replace(entry(6, "银行存款_甲银行", -77, "缴纳上月增值税及附加税"), counterpart_name="应交税费"),
    )

    result = build(rows)

    assert not result.structure_requests
    assert sorted(x.cash_delta_cent for x in result.components) == [-501, -102, -77]
    assert {x.entry_id for x in result.source_allocations} == {"E4", "E5", "E6"}
    assert result == build(tuple(reversed(rows)))


def test_reciprocal_counterpart_pairing_does_not_guess_equal_amounts_or_conflicting_accounts():
    base = (
        replace(entry(1, "应交税费_未交增值税", 100, "缴纳增值税"), counterpart_name="银行存款"),
        replace(entry(2, "应交税费_教育费附加", 100, "缴纳附加税"), counterpart_name="银行存款"),
        replace(entry(3, "银行存款_甲银行", -100, "缴纳税款甲"), counterpart_name="应交税费"),
        replace(entry(4, "银行存款_甲银行", -100, "缴纳税款乙"), counterpart_name="应交税费"),
    )
    assert build(base).structure_requests
    distinct = (
        base[0], replace(base[1], debit_cent=200),
        base[2], replace(base[3], credit_cent=200, counterpart_name="管理费用"),
    )
    assert build(distinct).structure_requests


def test_reciprocal_counterparts_keep_identical_business_with_repeated_amounts_together():
    rows = (
        replace(entry(1, "应付职工薪酬_社会保险", 100, "支付本月社保"), counterpart_name="银行存款"),
        replace(entry(2, "应付职工薪酬_社会保险", 100, "支付本月社保"), counterpart_name="银行存款"),
        replace(entry(3, "银行存款_甲银行", -100, "代缴社会保险甲"), counterpart_name="应付职工薪酬"),
        replace(entry(4, "银行存款_甲银行", -100, "代缴社会保险乙"), counterpart_name="应付职工薪酬"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -200
    assert {x.entry_id for x in result.source_allocations} == {"E3", "E4"}


def test_reciprocal_invoice_entries_are_non_cash_without_guessing_a_balance_subset():
    rows = (
        replace(entry(1, "固定资产_设备", 80, "设备发票入账"), counterpart_name="应付账款"),
        replace(entry(2, "应交税费_进项税额", 20, "设备发票入账"), counterpart_name="应付账款"),
        replace(entry(3, "应付账款", -80, "设备发票入账"), counterpart_name="固定资产"),
        replace(entry(4, "应付账款", -20, "设备发票入账"), counterpart_name="应交税费"),
        replace(entry(5, "应付账款", 100, "支付设备款"), counterpart_name="银行存款"),
        replace(entry(6, "银行存款", -100, "支付设备款"), counterpart_name="应付账款"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -100
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E1", "E2", "E3", "E4"}


def test_reciprocal_groups_distinguish_staff_and_personal_insurance_with_repeated_amounts():
    rows = (
        replace(entry(1, "应付职工薪酬_社会保险", 100, "支付本月社保"), counterpart_name="银行存款"),
        replace(entry(2, "应付职工薪酬_社会保险", 100, "支付本月社保"), counterpart_name="银行存款"),
        replace(entry(3, "其他应付款_个人社保", 30, "支付个人社保"), counterpart_name="银行存款"),
        replace(entry(4, "银行存款", -100, "缴纳社保"), counterpart_name="应付职工薪酬"),
        replace(entry(5, "银行存款", -100, "缴纳社保"), counterpart_name="应付职工薪酬"),
        replace(entry(6, "银行存款", -30, "缴纳社保"), counterpart_name="其他应付款"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert sorted(x.cash_delta_cent for x in result.components) == [-200, -30]


def test_reciprocal_non_cash_withholding_is_not_a_second_cash_flow():
    rows = (
        replace(entry(1, "应付职工薪酬_奖金", 150, "年度奖励发放"), counterpart_name="银行存款"),
        replace(entry(2, "应付职工薪酬_奖金", 50, "年度奖励发放"), counterpart_name="应交税费"),
        replace(entry(3, "应交税费_个人所得税", -50, "年度奖励及提成代扣税"), counterpart_name="应付职工薪酬"),
        replace(entry(4, "银行存款", -150, "支付年度奖励"), counterpart_name="应付职工薪酬"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -150
    assert {x.entry_id for x in result.source_allocations} == {"E4"}


def test_explicit_staff_settlement_keeps_sources_without_guessing_bonus_and_salary_pairs():
    rows = (
        replace(entry(1, "应付职工薪酬_奖金", 100, "支付年度奖金"), counterpart_name="银行存款"),
        replace(entry(2, "应付职工薪酬_工资", 100, "支付留存工资"), counterpart_name="银行存款"),
        replace(entry(3, "银行存款", -100, "支付奖金及工资"), counterpart_name="应付职工薪酬"),
        replace(entry(4, "银行存款", -100, "支付奖金及工资"), counterpart_name="应付职工薪酬"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -200
    assert {x.entry_id for x in result.source_allocations} == {"E3", "E4"}
    assert sorted(x.allocated_cent for x in result.source_allocations) == [-100, -100]


def test_internal_withdrawal_by_full_cash_account_name_does_not_contaminate_a_real_payment():
    bank = "银行存款_甲银行_基本户"
    rows = (
        replace(entry(1, "其他应收款_往来单位", 700, "支付单位往来"), counterpart_name="银行存款"),
        replace(entry(2, bank, -700, "支付单位往来"), account_code="100201", counterpart_name="其他应收款"),
        replace(entry(3, "库存现金", 100, "取现"), account_code="1001", counterpart_name=bank),
        replace(entry(4, bank, -100, "取现"), account_code="100201", counterpart_name="库存现金"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -700
    assert result.components[0].counterpart_accounts == ("其他应收款_往来单位",)
    assert {x.entry_id for x in result.source_allocations} == {"E2"}
    assert {x.entry_id for x in result.excluded_internal_transfers} == {"E3", "E4"}


def test_pure_cash_transfer_does_not_require_both_counterpart_fields():
    bank = "银行存款_甲银行_基本户"
    rows = (
        replace(entry(1, "库存现金", 100, "取现"), account_code="1001", counterpart_name=""),
        replace(entry(2, bank, -100, "取现"), account_code="100201", counterpart_name="库存现金"),
    )
    result = build(rows)
    assert not result.components
    assert {x.entry_id for x in result.excluded_internal_transfers} == {"E1", "E2"}


def payroll():
    return (
        entry(1, "银行存款", -8400, "付3月装配车间工资"),
        entry(2, "应付职工薪酬 - 工资", 10000, "付3月装配车间工资"),
        entry(3, "其他应付款 - 社保", -500, "付3月装配车间工资"),
        entry(4, "其他应收款 - 借款 - 个人", -1000, "扣装配车间员工甲工资代还借款本金"),
        entry(5, "应收利息", -100, "扣装配车间员工甲工资代还借款利息"),
        entry(6, "应收利息", 100, "计提装配车间员工甲个人借款利息"),
        replace(entry(7, "财务费用 - 利息收入", 0, "计提装配车间员工甲个人借款利息"), debit_cent=-100),
        entry(8, "银行存款", -2000, "付3月采购部工资"),
        entry(9, "应付职工薪酬 - 工资", 2000, "付3月采购部工资"),
    )


def test_self_and_on_behalf_payment_keep_separate_business_and_actual_cash():
    rows = (entry(1, "应付账款", 7000, "付款甲园区2月电费"),
            entry(2, "其他应收款 - 往来单位", 3000, "付款代乙公司付甲园区2月电费"),
            entry(3, "银行存款", -10000, "付款甲园区2月电费"))
    result = build(rows)
    assert sorted((x.summary, x.cash_delta_cent) for x in result.components) == sorted([
        (rows[0].summary, -7000), (rows[1].summary, -3000)])
    assert {x.entry_id for x in result.source_allocations} == {"E3"}
    assert sorted(x.allocated_cent for x in result.source_allocations) == [-7000, -3000]
    assert not result.structure_requests


def test_paid_payable_is_split_from_same_voucher_invoice_accrual():
    rows = (
        entry(1, "银行存款", -14_000, "付曾瀚报销拉晶车间采购钢化玻璃1件货款1.7"),
        entry(2, "应付账款_应付账款_人民币", 14_000, "付曾瀚报销拉晶车间采购钢化玻璃1件货款1.7"),
        entry(3, "低值易耗品_生产仪器工具", 14_000, "收拉晶车间采购钢化玻璃发票24312000000406430491"),
        entry(4, "应付账款_应付账款_人民币", -14_000, "收拉晶车间采购钢化玻璃发票24312000000406430491"),
    )

    result = build(rows)

    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -14_000
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E3", "E4"}


def test_contiguous_balanced_voucher_groups_split_cash_and_non_cash_by_source_order():
    cash = replace(
        entry(1, "银行存款", -14_000, "付钢化玻璃货款"),
        original_flow_item="购买商品、接受劳务支付的现金",
        label_side="cash",
        retained_side="cash",
    )
    rows = (
        cash,
        entry(2, "应付账款", 14_000, "付钢化玻璃货款"),
        entry(3, "原材料_辅助材料_高温材料", 14_000, "收钢化玻璃验收单"),
        entry(4, "应付账款", -14_000, "收钢化玻璃验收单"),
    )

    result = build(rows)

    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -14_000
    assert result.components[0].original_item_text == "购买商品、接受劳务支付的现金"
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E3", "E4"}
    assert result == build(tuple(reversed(rows)))


def test_contiguous_mixed_expense_accrual_is_grouped_as_one_non_cash_event():
    rows = (
        entry(1, "银行存款", -120_000, "支付员工福利"),
        entry(2, "应付职工薪酬_应付福利费", 120_000, "支付员工福利"),
        entry(3, "生产成本_直接人工_福利费", 60_000, "计提员工福利"),
        entry(4, "销售费用_福利费", 60_000, "计提员工福利"),
        entry(5, "应付职工薪酬_应付福利费", -120_000, "计提员工福利"),
    )

    result = build(rows)

    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -120_000
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E3", "E4", "E5"}


def test_balanced_noncash_group_with_different_summaries_remains_pending():
    rows = payroll()[:5] + (
        entry(6, "制造费用", 100, "计提福利"),
        entry(7, "应付职工薪酬", -100, "冲回福利"),
    )

    result = build(rows)

    assert result.structure_requests
    assert all("structure_pending" in x.anomalies for x in result.components)


def test_balanced_noncash_group_with_unknown_account_pair_remains_pending():
    rows = payroll()[:5] + (
        entry(6, "预付账款", 100, "其他事项"),
        entry(7, "其他应收款", -100, "其他事项"),
    )

    result = build(rows)

    assert result.structure_requests
    assert all("structure_pending" in x.anomalies for x in result.components)


def test_noncash_group_with_cash_summary_remains_pending_even_if_it_is_an_accrual():
    rows = (
        entry(1, "银行存款", -120, "计提员工福利"),
        entry(2, "应付职工薪酬", 120, "计提员工福利"),
        entry(3, "制造费用", 120, "计提员工福利"),
        entry(4, "应付职工薪酬", -120, "计提员工福利"),
    )

    result = build(rows)

    assert result.structure_requests
    assert all("structure_pending" in x.anomalies for x in result.components)


def test_payroll_offsets_and_red_accrual_have_distinct_roles():
    result = build(payroll())
    assert sorted(x.cash_delta_cent for x in result.components) == [-8400, -2000]
    assert all(x.counterpart_accounts == ("应付职工薪酬 - 工资",) for x in result.components)
    plan = result.structure_plans[0]
    group = next(g for g in plan.business_groups if "E2" in g.main_entry_ids)
    assert set(group.settlement_entry_ids) == {"E3", "E4", "E5"}
    assert set(plan.noncash_groups[0].entry_ids) == {"E6", "E7"}
    assert all("path_depends_on_summary" in x.anomalies for x in result.components)


def test_same_summary_duplicate_bank_rows_survive_and_input_order_is_irrelevant():
    rows = (entry(1, "银行存款", -4000, "付3月研发部工资"),
            entry(2, "银行存款", -4000, "付3月研发部工资"),
            entry(3, "应付职工薪酬 - 工资", 5000, "付3月研发部工资"),
            entry(4, "应付职工薪酬 - 工资", 3000, "付3月研发部工资"))
    result = build(rows)
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -8000
    assert {(a.entry_id, a.allocated_cent) for a in result.source_allocations} == {("E1", -4000), ("E2", -4000)}
    assert result == build(tuple(reversed(rows)))


def test_unknown_zero_sum_rows_are_not_silently_discarded():
    rows = payroll()[:5] + (entry(6, "应收利息", 100, "未知事项甲"),
                           entry(7, "财务费用", -100, "未知事项乙"))
    result = build(rows)
    assert result.structure_requests
    assert all("structure_pending" in x.anomalies for x in result.components)
    assert sum(x.cash_delta_cent for x in result.components) == -8400


def test_independent_balanced_group_continues_while_other_group_is_pending():
    rows = payroll()[:5] + (entry(8, "银行存款", -1500, "付3月采购部工资"),
                           entry(9, "应付职工薪酬 - 工资", 1500, "付3月采购部工资"),
                           entry(10, "其他应收款", -100, "付3月装配车间工资"))
    # An unbalanced group is not repaired by consuming another department's entries.
    result = build(rows)
    assert any(x.cash_delta_cent == -1500 and "structure_pending" not in x.anomalies for x in result.components)
    assert any("structure_pending" in x.anomalies for x in result.components)
    assert result.structure_requests


def test_unresolved_opposite_cash_directions_remain_visible_at_net_zero():
    rows = (entry(1, "银行存款", 5000, "收甲项"), entry(2, "银行存款", -5000, "付乙项"),
            entry(3, "其他应收款", -5000, "不明甲"), entry(4, "其他应付款", 5000, "不明乙"))
    result = build(rows)
    assert result.structure_requests
    assert sorted(x.cash_delta_cent for x in result.components) == [-5000, 5000]
    assert all("structure_pending" in x.anomalies for x in result.components)


def ambiguous_payroll():
    return (entry(1, "银行存款", -1100, "付3月装配车间工资"),
            entry(2, "应付职工薪酬", 1200, "付3月装配车间工资"),
            entry(3, "银行存款", -1100, "付4月装配车间工资"),
            entry(4, "应付职工薪酬", 1200, "付4月装配车间工资"),
            entry(5, "其他应收款", -100, "扣装配车间员工甲工资代还借款本金"),
            entry(6, "其他应收款", -100, "扣装配车间员工乙工资代还借款本金"))


def test_complete_plans_include_competing_offsets_and_selection_is_revalidated():
    rows = ambiguous_payroll()
    result = build(rows)
    request = result.structure_requests[0]
    assert len(request.candidate_plans) == 2
    selected = request.candidate_plans[0]
    accepted = build(rows, structure_plan_selections={"V-STRUCT": selected.plan_id})
    assert not accepted.structure_requests
    assert accepted.structure_plans == (selected,)
    import pytest
    with pytest.raises(ValueError, match="过期"):
        build(rows, structure_plan_selections={"V-STRUCT": "old-plan"})


def test_search_limit_means_incomplete_not_unique_and_validator_rejects_forgery():
    rows = ambiguous_payroll()
    policy = deepcopy(default_rule_registry().special_policy["component_structure"]["relations"])
    policy["max_search_states"] = 1
    result = resolve_voucher_structure(rows, ("E1", "E3"), policy)
    assert not result.search_complete and not result.candidates
    assert not result.plan.business_groups
    good = build(payroll()).structure_plans[0]
    group = good.business_groups[0]
    forged = replace(good, business_groups=(*good.business_groups, group))
    assert validate_structure_plan(forged, payroll(), ("E1", "E8"))


def test_shortest_amount_subset_does_not_hide_longer_same_summary_business():
    rows = (entry(1, "银行存款", -1000, "支付工资"),
            entry(2, "应付职工薪酬", 1000, "支付工资"),
            entry(3, "应付职工薪酬", 600, "支付工资"),
            entry(4, "应付职工薪酬", 400, "支付工资"),
            entry(5, "管理费用", -1000, "支付工资"))
    result = build(rows)
    assert result.structure_requests
    assert all("structure_pending" in c.anomalies for c in result.components)


def test_structure_pending_cannot_enter_low_amount_fallback_or_classification_ai():
    result = build(ambiguous_payroll())
    rules = load_rule_pack(Path(__file__).resolve().parents[1])
    decisions = classify_all(result.components, rules)
    routed = route_classification_decisions(result.components, decisions, MaterialityAmounts(1000000, 700000, 100000))
    assert not routed.ai_tasks
    assert all(not d.resolved and not d.excluded and not d.system_item_id for d in routed.decisions)
    assert all(d.candidate_status == "structure_pending" for d in routed.decisions)
