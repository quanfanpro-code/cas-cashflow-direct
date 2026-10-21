"""一条原始非现金分录可按原账证据分给多笔现金业务。"""

from copy import deepcopy

import pytest

from cashflow_direct.components import build_cashflow_components, confirm_cash_scope, discover_cash_scope
from tests.fixture_factory import _component_entry


def _rows():
    return (
        _component_entry(1, "V-SHARE", "银行存款_结算户", debit_cent=600, summary="客户甲回款"),
        _component_entry(2, "V-SHARE", "银行存款_结算户", debit_cent=400, summary="客户乙回款"),
        _component_entry(3, "V-SHARE", "应收账款_平台汇总", credit_cent=1000, summary="平台汇总结算"),
    )


def _plan():
    return {
        "business_groups": [
            {
                "main_entry_ids": ["E3"], "settlement_entry_ids": [],
                "entry_shares": [{"entry_id": "E3", "amount_cent": -600}],
                "cash_shares": [{"entry_id": "E1", "amount_cent": 600}],
            },
            {
                "main_entry_ids": ["E3"], "settlement_entry_ids": [],
                "entry_shares": [{"entry_id": "E3", "amount_cent": -400}],
                "cash_shares": [{"entry_id": "E2", "amount_cent": 400}],
            },
        ],
        "noncash_groups": [],
    }


def _build(plan):
    rows = _rows()
    proposal = discover_cash_scope(rows)
    scope = confirm_cash_scope(proposal, {item.account_key: "include" for item in proposal.candidates})
    return build_cashflow_components(rows, scope, structure_agent_plans={"V-SHARE": plan})


def test_原始行分摊后每笔现金业务及原账金额均闭合():
    result = _build(_plan())
    assert not result.structure_requests
    assert sorted(component.cash_delta_cent for component in result.components) == [400, 600]
    assert {(share.entry_id, share.allocated_cent) for share in result.source_allocations} == {
        ("E1", 600), ("E2", 400),
    }
    assert sorted(share.amount_cent for group in result.structure_plans[0].business_groups
                  for share in group.entry_shares) == [-600, -400]


@pytest.mark.parametrize("invalid", (-399, -401, 400, 0))
def test_金额拆多拆少反向或为零都不得通过(invalid):
    plan = deepcopy(_plan())
    plan["business_groups"][1]["entry_shares"][0]["amount_cent"] = invalid
    with pytest.raises(ValueError):
        _build(plan)
