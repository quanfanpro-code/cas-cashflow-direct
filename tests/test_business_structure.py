from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from copy import deepcopy
import pytest

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


def equivalent_source_rows():
    rows = (
        replace(entry(1, "其他应收款_人员甲", 6000, "人员甲备用金"), counterpart_name="银行存款"),
        replace(entry(2, "其他应收款_人员乙", 6000, "人员乙备用金"), counterpart_name="银行存款"),
        replace(entry(3, "银行存款_结算户", -6000, "支付人员备用金"), counterpart_name="其他应收款", original_flow_item="支付其他与经营活动有关的现金"),
        replace(entry(4, "银行存款_结算户", -6000, "支付人员备用金"), counterpart_name="其他应收款", original_flow_item="支付其他与经营活动有关的现金"),
    )
    result = []
    for row in rows:
        content = asdict(row)
        content.pop("source")
        content.pop("entry_id")
        content.pop("source_row_hash", None)
        digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        result.append(replace(row, source_row_hash=digest))
    return tuple(result)


def test_identical_cash_source_records_keep_each_business_and_cash_row():
    rows = equivalent_source_rows()
    result = build(rows)
    assert not result.structure_requests
    assert len(result.components) == 2
    assert sorted(c.cash_delta_cent for c in result.components) == [-6000, -6000]
    assert {c.counterpart_accounts for c in result.components} == {
        ("其他应收款_人员甲",), ("其他应收款_人员乙",),
    }
    assert {(a.entry_id, a.allocated_cent) for a in result.source_allocations} == {("E3", -6000), ("E4", -6000)}
    assert all(c.original_item_text == "支付其他与经营活动有关的现金" for c in result.components)
    assert result == build(tuple(reversed(rows)))


@pytest.mark.parametrize("change", ("missing_hash", "auxiliary", "account", "summary", "original_item", "no_reciprocal"))
def test_equal_amounts_do_not_replace_full_source_equivalence(change):
    rows = list(equivalent_source_rows())
    if change == "missing_hash":
        rows = [replace(r, source_row_hash="") for r in rows]
    else:
        edits = {
            "auxiliary": {},
            "account": {"account_name": "银行存款_另一结算户"},
            "summary": {"summary": "其他人员备用金支付"},
            "original_item": {"original_flow_item": "购买商品、接受劳务支付的现金"},
            "no_reciprocal": {"counterpart_name": "管理费用"},
        }[change]
        rows[3] = replace(rows[3], **edits, source_row_hash=hashlib.sha256(change.encode()).hexdigest())
    result = build(rows)
    assert result.structure_requests
    assert sum(c.cash_delta_cent for c in result.components) == -12000
    assert all("structure_pending" in c.anomalies for c in result.components)


def test_equivalent_sources_only_complete_unassigned_part_of_voucher():
    rows = (*equivalent_source_rows(),
        replace(entry(5, "管理费用_差旅费", 2000, "报销差旅费"), counterpart_name="银行存款"),
        replace(entry(6, "管理费用_差旅费", 8000, "报销差旅费"), counterpart_name="银行存款"),
        replace(entry(7, "银行存款_结算户", -2000, "报销差旅费"), counterpart_name="管理费用"),
        replace(entry(8, "银行存款_结算户", -8000, "报销差旅费"), counterpart_name="管理费用"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert sum(c.cash_delta_cent for c in result.components) == -22000
    assert {(a.entry_id, a.allocated_cent) for a in result.source_allocations} == {
        ("E3", -6000), ("E4", -6000), ("E7", -2000), ("E8", -8000)}


def test_explicit_summary_assignment_leaves_unique_reciprocal_business():
    rows = (
        replace(entry(1, "其他应收款_人员甲", 6000, "人员甲备用金"), counterpart_name="银行存款"),
        replace(entry(2, "其他应收款_人员乙", 6000, "人员乙备用金"), counterpart_name="银行存款"),
        replace(entry(3, "银行存款_结算户", -6000, "人员甲备用金"), counterpart_name="其他应收款"),
        replace(entry(4, "银行存款_结算户", -6000, "支付人员备用金"), counterpart_name="其他应收款"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert len(result.components) == 2
    assert {(a.entry_id, a.allocated_cent) for a in result.source_allocations} == {("E3", -6000), ("E4", -6000)}


def test_repeated_reciprocal_noncash_settlement_keeps_all_rows_without_pairing():
    rows = (
        replace(entry(1, "销售费用_差旅费", 6000, "人员甲机票"), counterpart_name="其他应收款"),
        replace(entry(2, "销售费用_差旅费", 6000, "人员乙机票"), counterpart_name="其他应收款"),
        replace(entry(3, "其他应收款_备用金", -6000, "员工报销抵扣备用金"), counterpart_name="销售费用"),
        replace(entry(4, "其他应收款_备用金", -6000, "员工报销抵扣备用金"), counterpart_name="销售费用"),
        replace(entry(5, "银行存款", 9000, "收回剩余备用金"), counterpart_name="其他应收款"),
        replace(entry(6, "其他应收款_备用金", -9000, "收回剩余备用金"), counterpart_name="银行存款"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [c.cash_delta_cent for c in result.components] == [9000]
    excluded = {i for p in result.structure_plans for g in p.noncash_groups for i in g.entry_ids}
    assert excluded == {"E1", "E2", "E3", "E4"}


def test_repeated_noncash_deductions_close_after_unique_cross_summary_pair():
    rows = (
        replace(entry(1, "应付职工薪酬_奖金", 100, "发放奖金"), counterpart_name="银行存款"),
        replace(entry(2, "应付职工薪酬_奖金", 11, "发放奖金"), counterpart_name="应交税费"),
        replace(entry(3, "应付职工薪酬_奖金", 11, "发放奖金"), counterpart_name="应交税费"),
        replace(entry(4, "应付职工薪酬_工资", 200, "支付留存绩效"), counterpart_name="银行存款"),
        replace(entry(5, "应付职工薪酬_工资", 7, "支付留存绩效"), counterpart_name="应交税费"),
        replace(entry(6, "银行存款", -100, "发放奖金及绩效"), counterpart_name="应付职工薪酬"),
        replace(entry(7, "应交税费_个人所得税", -11, "发放奖金"), counterpart_name="应付职工薪酬"),
        replace(entry(8, "应交税费_个人所得税", -7, "发放奖金"), counterpart_name="应付职工薪酬"),
        replace(entry(9, "银行存款", -200, "发放奖金及绩效"), counterpart_name="应付职工薪酬"),
        replace(entry(10, "应交税费_个人所得税", -11, "发放奖金"), counterpart_name="应付职工薪酬"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert sum(x.cash_delta_cent for x in result.components) == -300
    assert {x.entry_id for x in result.source_allocations} == {"E6", "E9"}
    assert {"E2", "E3", "E7", "E10"} in [set(g.entry_ids) for g in result.structure_plans[0].noncash_groups]
    assert result == build(tuple(reversed(rows)))


@pytest.mark.parametrize("direction", (1, -1))
def test_generic_bank_counterpart_cannot_hide_transfer_from_excluded_account(direction):
    rows = (
        replace(entry(1, "银行存款_活期户", direction * 1000, "定期与活期账户转移"), account_code="100201", counterpart_name="银行存款"),
        replace(entry(2, "银行存款_半年定期户", -direction * 1000, "定期与活期账户转移"), account_code="100202", counterpart_name="银行存款"),
        replace(entry(3, "银行存款_活期户", 7, "定期存款利息收款"), account_code="100201", counterpart_name="财务费用"),
        replace(entry(4, "财务费用_利息收入", -7, "定期存款利息收款"), counterpart_name="银行存款"),
    )
    alias = replace(entry(20, "银行存款", 1, "其他凭证账户标识"), voucher_key="OTHER")
    scope = confirm_cash_scope(discover_cash_scope((*rows, alias)), {
        "100201": "include", "100202": "exclude", "银行存款": "include",
    })
    result = build_cashflow_components(rows, scope)
    assert not result.structure_requests
    assert not result.excluded_internal_transfers
    assert sorted(x.cash_delta_cent for x in result.components) == sorted([7, direction * 1000])
    assert sum(x.allocated_cent for x in result.source_allocations) == direction * 1000 + 7


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


def test_payroll_group_keeps_cash_and_bank_sources_without_cross_matching_them():
    rows = (
        replace(entry(1, "应付职工薪酬_工资", 300, "支付留存绩效"), counterpart_name="银行存款"),
        replace(entry(2, "应付职工薪酬_奖金", 100, "支付留存绩效"), counterpart_name="库存现金"),
        replace(entry(3, "银行存款", -300, "职工款项结算"), counterpart_name="应付职工薪酬"),
        replace(entry(4, "库存现金", -100, "职工款项结算"), counterpart_name="应付职工薪酬"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert sum(x.cash_delta_cent for x in result.components) == -400
    assert {x.entry_id for x in result.source_allocations} == {"E3", "E4"}
    assert result == build(tuple(reversed(rows)))
    inconsistent = (*rows[:3], replace(rows[3], counterpart_name="管理费用"))
    assert build(inconsistent).structure_requests


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
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E2", "E4"}
    assert result.components[0].counterpart_accounts == ("低值易耗品_生产仪器工具",)


def test_equal_opposite_payable_cancels_without_using_source_order():
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
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E2", "E4"}
    assert result.components[0].counterpart_accounts == ("原材料_辅助材料_高温材料",)
    assert result == build(tuple(reversed(rows)))


def test_equal_opposite_payable_cancels_before_mixed_expense_allocation():
    rows = (
        entry(1, "银行存款", -120_000, "支付员工福利"),
        entry(2, "应付职工薪酬_应付福利费", 120_000, "支付员工福利"),
        entry(3, "生产成本_直接人工_福利费", 60_000, "计提员工福利"),
        entry(4, "销售费用_福利费", 60_000, "计提员工福利"),
        entry(5, "应付职工薪酬_应付福利费", -120_000, "计提员工福利"),
    )

    result = build(rows)

    assert not result.structure_requests
    assert sum(component.cash_delta_cent for component in result.components) == -120_000
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E2", "E5"}
    assert {account for component in result.components for account in component.counterpart_accounts} == {
        "生产成本_直接人工_福利费", "销售费用_福利费"
    }


def test_invoice_payable_offset_exposes_remaining_business_and_tax():
    rows = (
        entry(1, "其他应付款_供应商", 17_367, "支付服务费"),
        entry(2, "银行存款", -17_367, "支付服务费"),
        entry(3, "其他应付款_供应商", 16_383, "收到服务发票"),
        entry(4, "应交税费_进项税额", 984, "收到服务发票"),
        entry(5, "其他应付款_供应商", -17_367, "收到服务发票"),
    )

    result = build(rows)

    assert not result.structure_requests
    assert len(result.components) == 1
    assert result.components[0].cash_delta_cent == -17_367
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E1", "E5"}
    assert set(result.components[0].counterpart_accounts) == {"其他应付款_供应商", "应交税费_进项税额"}
    assert result == build(tuple(reversed(rows)))


def test_equal_opposite_tax_transfer_exposes_remaining_payment():
    rows = (
        entry(1, "应交税费_未交增值税", 324_788, "结转增值税"),
        entry(2, "应交税费_已交增值税", -324_788, "结转增值税"),
        entry(3, "应交税费_已交增值税", 324_788, "支付增值税及附加"),
        entry(4, "应交税费_城建税", 22_735, "支付增值税及附加"),
        entry(5, "银行存款", -347_523, "支付增值税及附加"),
    )

    result = build(rows)

    assert not result.structure_requests
    assert sum(component.cash_delta_cent for component in result.components) == -347_523
    assert set(result.structure_plans[0].noncash_groups[0].entry_ids) == {"E2", "E3"}
    assert result == build(tuple(reversed(rows)))


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


def test_equal_opposite_payable_cancels_even_when_summaries_match():
    rows = (
        entry(1, "银行存款", -120, "计提员工福利"),
        entry(2, "应付职工薪酬", 120, "计提员工福利"),
        entry(3, "制造费用", 120, "计提员工福利"),
        entry(4, "应付职工薪酬", -120, "计提员工福利"),
    )

    result = build(rows)

    assert not result.structure_requests
    assert result.components[0].cash_delta_cent == -120
    assert result.components[0].counterpart_accounts == ("制造费用",)


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


def test_agent_can_resolve_no_candidate_from_original_rows_with_full_reconciliation():
    rows = (
        entry(1, "银行存款", -1000, "支付维修费"),
        entry(2, "管理费用_维修费", 1000, "支付维修费"),
        entry(3, "预付账款", 200, "另项内部结算"),
        entry(4, "其他应收款", -200, "另项内部结算"),
    )
    assert build(rows).structure_requests
    proposal = {
        "business_groups": [{
            "main_entry_ids": ["E2"], "settlement_entry_ids": [],
            "cash_shares": [{"entry_id": "E1", "amount_cent": -1000}],
        }],
        "noncash_groups": [{"entry_ids": ["E3", "E4"]}],
    }

    result = build(rows, structure_agent_plans={"V-STRUCT": proposal})

    assert not result.structure_requests
    assert result.components[0].counterpart_accounts == ("管理费用_维修费",)
    assert result.components[0].cash_delta_cent == -1000
    assert result == build(tuple(reversed(rows)), structure_agent_plans={"V-STRUCT": proposal})
    forged = deepcopy(proposal)
    forged["business_groups"][0]["cash_shares"][0]["amount_cent"] = -900
    with pytest.raises(ValueError, match="不守恒"):
        build(rows, structure_agent_plans={"V-STRUCT": forged})


def test_unique_cross_account_cash_transfer_inside_mixed_voucher():
    rows = (
        entry(1, "银行存款_结算户", 1000, "退还保证金"),
        entry(2, "其他应收款_保证金", -1000, "退还保证金"),
        entry(3, "银行存款_结算户", 700, "平台提现"),
        entry(4, "其他货币资金_平台款项", -700, "平台提现"),
    )
    result = build(rows)
    assert {item.entry_id for item in result.excluded_internal_transfers} == {"E3", "E4"}
    assert [item.cash_delta_cent for item in result.components] == [1000]


def test_equal_opposite_cash_with_related_business_is_not_silently_excluded():
    rows = (
        entry(1, "银行存款_结算户", 700, "平台结算"),
        entry(2, "其他货币资金_平台款项", -700, "平台结算"),
        entry(3, "应收账款_货款", -100, "平台结算"),
        entry(4, "财务费用_手续费", 100, "平台结算"),
        entry(5, "银行存款_结算户", -50, "另付运费"),
        entry(6, "销售费用_运费", 50, "另付运费"),
    )
    result = build(rows)
    assert not result.excluded_internal_transfers


def test_same_cash_account_transfer_is_removed_from_mixed_interest_voucher():
    rows = (
        entry(1, "财务费用_利息收入", -40, "银行结息"),
        entry(2, "银行存款_本币", 40, "银行结息"),
        entry(3, "银行存款_本币", -713121, "销户转款至总部账户"),
        entry(4, "银行存款_本币", 713121, "销户转款至总部账户"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [item.cash_delta_cent for item in result.components] == [40]
    assert {item.entry_id for item in result.excluded_internal_transfers} == {"E3", "E4"}
    assert result.components == build(tuple(reversed(rows))).components


def test_single_cash_payment_with_same_business_deposit_offset():
    rows = (
        entry(1, "应付账款_货款", 76000, "支付药品款"),
        entry(2, "银行存款", -45600, "支付药品款"),
        entry(3, "其他应付款_保证金", -30400, "支付药品款（保证金抵扣）"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [item.cash_delta_cent for item in result.components] == [-45600]
    assert result.components[0].counterpart_accounts == ("应付账款_货款",)


def test_cash_payment_uses_payable_after_bill_and_deposit_offsets():
    rows = (
        entry(1, "应付账款_货款", 125000, "支付药品款（票据和保证金抵付）"),
        entry(2, "应收票据_银行承兑汇票", -50000, "支付药品款"),
        entry(3, "其他应付款_保证金", -30000, "支付药品款（保证金抵付）"),
        entry(4, "银行存款", -45000, "支付药品款"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [item.cash_delta_cent for item in result.components] == [-45000]
    assert result.components[0].counterpart_accounts == ("应付账款_货款",)
    assert result == build(tuple(reversed(rows)))


def test_multiple_bank_deductions_cover_one_fee_and_tax_business():
    rows = (
        entry(1, "财务费用_手续费", 63028, "银行扣手续费"),
        entry(2, "银行存款", -8446, "银行扣手续费"),
        entry(3, "应交税费_进项税", 3782, "银行扣手续费"),
        entry(4, "银行存款", -9300, "银行扣手续费"),
        entry(5, "银行存款", -49064, "银行扣手续费"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [item.cash_delta_cent for item in result.components] == [-66810]
    assert {(item.entry_id, item.allocated_cent) for item in result.source_allocations} == {
        ("E2", -8446), ("E4", -9300), ("E5", -49064)}
    assert result == build(tuple(reversed(rows)))


def test_year_end_bonus_with_tax_offset_keeps_all_bank_payments():
    rows = (
        entry(1, "应付职工薪酬_奖金", 100000, "支付年终奖"),
        entry(2, "银行存款", -60000, "支付年终奖"),
        entry(3, "其他应收款_代扣个人所得税", -10000, "支付年终奖"),
        entry(4, "银行存款", -30000, "支付年终奖"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [item.cash_delta_cent for item in result.components] == [-90000]
    assert {(item.entry_id, item.allocated_cent) for item in result.source_allocations} == {
        ("E2", -60000), ("E4", -30000)}


def test_payment_and_balanced_invoice_with_shared_payable_account():
    rows = (
        entry(1, "其他应付款_供应商", 40565, "支付推广费"),
        entry(2, "银行存款", -40565, "支付推广费"),
        entry(3, "其他应付款_供应商", 38175, "支付推广费/发票回票"),
        entry(4, "应交税费_进项税", 2290, "支付推广费/发票回票"),
        entry(5, "其他应付款_供应商", -40465, "支付推广费/发票回票"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [item.cash_delta_cent for item in result.components] == [-40565]
    assert {item.entry_ids for item in result.structure_plans[0].noncash_groups} == {("E3", "E4", "E5")}


def test_interleaved_balanced_postings_use_summary_and_full_account_not_row_order():
    rows = (
        entry(1, "其他应付款_供应商", 105, "付服务费"),
        entry(2, "其他应付款_供应商", 94, "服务费回票"),
        entry(3, "银行存款", -105, "付服务费"),
        entry(4, "应交税费_进项税", 6, "服务费回票"),
        entry(5, "其他应付款_供应商", -100, "服务费回票"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [item.cash_delta_cent for item in result.components] == [-105]
    assert {frozenset(group.entry_ids) for group in result.structure_plans[0].noncash_groups} == {
        frozenset({"E2", "E4", "E5"})}
    assert result == build(tuple(reversed(rows)))


def test_unrelated_balanced_summary_without_account_link_stays_pending():
    rows = (
        entry(1, "银行存款", -100, "付服务费"),
        entry(2, "管理费用_服务费", 100, "付服务费"),
        entry(3, "预付账款", 50, "另外事项"),
        entry(4, "其他应收款", -50, "另外事项"),
    )
    assert build(rows).structure_requests


def test_separate_receipt_payment_and_noncash_adjustment_keep_both_cash_sources():
    rows = (
        entry(1, "其他货币资金_平台", 1000, "客户货款"),
        entry(2, "应收账款_客户", -1000, "客户货款"),
        entry(3, "其他应收款_平台", 300, "平台扣款"),
        entry(4, "其他货币资金_平台", -300, "平台扣款"),
        entry(5, "应收账款_客户", 50, "保证金调整"),
        entry(6, "其他应收款_平台", -50, "保证金调整"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert sorted(item.cash_delta_cent for item in result.components) == [-300, 1000]
    assert {frozenset(group.entry_ids) for group in result.structure_plans[0].noncash_groups} == {
        frozenset({"E5", "E6"})}
    assert result == build(tuple(reversed(rows)))


def test_payroll_deductions_with_both_signs_keep_one_cash_business():
    rows = (
        entry(1, "应付职工薪酬_工资", 10000, "发放工资"),
        entry(2, "银行存款", -8800, "发放工资"),
        entry(3, "其他应收款_社保", -1400, "发放工资"),
        entry(4, "其他应收款_社保", 200, "发放工资（补扣社保）"),
    )
    result = build(rows)
    assert not result.structure_requests
    assert [item.cash_delta_cent for item in result.components] == [-8800]
    assert result.components[0].counterpart_accounts == ("应付职工薪酬_工资",)


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
