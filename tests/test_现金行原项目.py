"""现金行原项目必须随实际现金分配保留，不得变成空白或借用其他现金行。"""
import pytest

from cashflow_direct.components import build_cashflow_components, confirm_cash_scope, discover_cash_scope
from tests.fixture_factory import _component_entry


def build(rows):
    proposal = discover_cash_scope(rows)
    scope = confirm_cash_scope(proposal, {c.account_key: "include" for c in proposal.candidates})
    return build_cashflow_components(rows, scope)


@pytest.mark.parametrize("receipt", [False, True])
def test_简单凭证保留现金行原项目(receipt):
    item = "销售商品、提供劳务收到的现金" if receipt else "支付其他与经营活动有关的现金"
    summary = "收销售货款" if receipt else "购买办公用品"
    rows = (
        _component_entry(1, "V", "1002 银行存款_活期", debit_cent=10000 if receipt else 0,
                         credit_cent=0 if receipt else 10000, item=item, retained_side="cash", summary=summary),
        _component_entry(2, "V", "主营业务收入_商品收入" if receipt else "管理费用_办公费",
                         debit_cent=0 if receipt else 10000, credit_cent=10000 if receipt else 0,
                         retained_side="counterpart", summary=summary),
    )
    result = build(rows)
    assert len(result.components) == 1
    component = result.components[0]
    assert component.original_item_text == item
    assert component.cash_delta_cent == (10000 if receipt else -10000)
    assert component.counterpart_accounts == (rows[1].account_name,)
    assert rows[0].original_flow_item == item


def test_同一业务多条现金来源保留不同原项目并标冲突():
    first, second = "购买商品、接受劳务支付的现金", "支付其他与经营活动有关的现金"
    rows = (
        _component_entry(1, "V", "1002 银行存款_A", credit_cent=10000, flow_amount_cent=10000,
                         item=first, retained_side="cash", summary="支付同一供应商货款"),
        _component_entry(2, "V", "1002 银行存款_B", credit_cent=20000, flow_amount_cent=20000,
                         item=second, retained_side="cash", summary="支付同一供应商货款"),
        _component_entry(3, "V", "应付账款_货款", debit_cent=30000, retained_side="counterpart",
                         summary="支付同一供应商货款"),
    )
    result = build(rows)
    assert len(result.components) == 1
    component = result.components[0]
    assert set(component.original_item_text.split("；")) == {first, second}
    assert "original_item_conflict" in component.anomalies
    assert component.cash_delta_cent == -30000
    assert sum(a.allocated_cent for a in result.source_allocations) == -30000


def test_两笔业务不能借用另一条现金来源的原项目():
    purchase, payroll = "购买商品、接受劳务支付的现金", "支付给职工以及为职工支付的现金"
    rows = (
        _component_entry(1, "V", "1002 银行存款_A", credit_cent=10000, item=purchase,
                         retained_side="cash", summary="支付供应商货款"),
        _component_entry(2, "V", "1002 银行存款_B", credit_cent=5000, item=payroll,
                         retained_side="cash", summary="支付员工工资"),
        _component_entry(3, "V", "应付账款_货款", debit_cent=10000,
                         retained_side="counterpart", summary="支付供应商货款"),
        _component_entry(4, "V", "应付职工薪酬_工资", debit_cent=5000,
                         retained_side="counterpart", summary="支付员工工资"),
    )
    result = build(rows)
    assert {(c.cash_delta_cent, c.original_item_text) for c in result.components} == {(-10000, purchase), (-5000, payroll)}
    assert all("original_item_conflict" not in c.anomalies for c in result.components)


def test_业务行与现金行原项目冲突时两边都保留():
    cash_item, business_item = "支付其他与经营活动有关的现金", "购买商品、接受劳务支付的现金"
    rows = (
        _component_entry(1, "V", "1002 银行存款_活期", credit_cent=10000,
                         item=cash_item, retained_side="cash", summary="支付供应商货款"),
        _component_entry(2, "V", "应付账款_货款", debit_cent=10000,
                         item=business_item, retained_side="counterpart", summary="支付供应商货款"),
    )
    component = build(rows).components[0]
    assert set(component.original_item_text.split("；")) == {cash_item, business_item}
    assert "original_item_conflict" in component.anomalies
