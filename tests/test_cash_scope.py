from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from cashflow_direct.components import confirm_cash_scope, discover_cash_scope
from cashflow_direct.pipeline import confirm_cash_scope as confirm_run_cash_scope
from cashflow_direct.pipeline import run_preflight
from tests.fixture_factory import _component_entry, write_end_to_end_case


def _deposit_facts():
    return {
        "status": "include",
        "cash_basis": "withdrawable_deposit",
        "withdrawable_on_demand": True,
        "full_principal_withdrawal": True,
        "unrestricted": True,
        "available_for_payment": True,
        "short_term": False,
        "basis": "半年存款可随时全额支取本金，无提款限制；提前支取仅损失利息",
    }


def _proposal(name="1002 银行存款_半年定期存款", summary="存入半年定期存款"):
    return discover_cash_scope((
        _component_entry(1, "V-DEPOSIT", name, debit_cent=10_000, summary=summary),
    ))


def test_withdrawable_six_month_deposit_is_cash_without_short_term_claim():
    scope = confirm_cash_scope(_proposal(), {"1002": _deposit_facts()})
    assert scope.included_keys == frozenset({"1002"})


def test_mapped_bank_deposit_uses_standard_level1_and_keeps_original_name():
    entry = replace(
        _component_entry(1, "V-DEPOSIT", "银行存款_半年定期存款", debit_cent=10_000),
        original_account_name="银行账户_半年定期存款",
    )
    proposal = discover_cash_scope((entry,))
    key = proposal.candidates[0].account_key
    scope = confirm_cash_scope(proposal, {key: _deposit_facts()})
    assert scope.included_keys == frozenset({key})
    assert scope.account_names_by_key == ((key, ("银行账户_半年定期存款",)),)


@pytest.mark.parametrize("field", [
    "withdrawable_on_demand", "full_principal_withdrawal",
    "unrestricted", "available_for_payment",
])
@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_deposit_requires_each_explicit_true_fact(field, value):
    facts = _deposit_facts()
    if value is None:
        facts.pop(field)
    else:
        facts[field] = value
    with pytest.raises(ValueError):
        confirm_cash_scope(_proposal(), {"1002": facts})


@pytest.mark.parametrize("name", [
    "1101 理财_半年定期存款", "1101 交易性金融资产_短期债券",
    "1101 货币基金_理财", "1002 银行存款_理财",
    "1002 银行存款_货币基金", "1002 银行存款_债券",
    "1101 交易性金融资产_银行存款_半年定期存款",
    "1002 银行存款_质押定期存款", "1002 银行存款_冻结定期存款",
])
def test_deposit_basis_cannot_bypass_investment_or_restriction(name):
    proposal = _proposal(name)
    key = proposal.candidates[0].account_key
    # 即使投资条件都填为真，也不能把投资或受限账户声明成随时支取的存款。
    facts = {**_deposit_facts(), "short_term": True, "high_liquidity": True,
             "known_cash_amount": True, "low_value_change_risk": True}
    with pytest.raises(ValueError):
        confirm_cash_scope(proposal, {key: facts})


def test_deposit_basis_does_not_bypass_period_pledge_change():
    with pytest.raises(ValueError, match="按期间"):
        confirm_cash_scope(_proposal(summary="解除质押"), {"1002": _deposit_facts()})


def test_pipeline_retains_original_cash_confirmation_in_state_and_database(tmp_path):
    preflight = run_preflight(
        write_end_to_end_case(tmp_path),
        ("1000000", "750000", "50000"), output_parent=tmp_path,
    )
    decisions = {key: _deposit_facts() for key in preflight.recommended_cash_decisions}
    confirm_run_cash_scope(preflight.run_dir, decisions)
    trace = preflight.run_dir / "计算留痕数据"
    state = json.loads((trace / "运行状态.json").read_text(encoding="utf-8-sig"))
    assert state["cash_scope"]["confirmation_details"] == decisions
    with sqlite3.connect(trace / "计算留痕.sqlite3") as connection:
        payload = connection.execute(
            "SELECT payload_json FROM cash_scope WHERE record_id=?",
            (state["cash_scope"]["scope_hash"],),
        ).fetchone()[0]
    assert json.loads(payload)["confirmation_details"] == decisions
