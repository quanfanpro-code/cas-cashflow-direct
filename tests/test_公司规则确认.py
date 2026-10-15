from cashflow_direct.pipeline import run_preflight, confirm_cash_scope, confirm_company_notes, scan_accounts
from tests.test_pipeline import _write_ledger_case

def test_explicit_empty_notes_confirmation_unblocks_pending_raw_notes(tmp_path):
    source = _write_ledger_case(tmp_path)
    result = run_preflight([source], ("100000", "50000", "5000"), tmp_path, notes="待核实是否有特殊规则")
    confirm_cash_scope(result.run_dir, dict(result.recommended_cash_decisions))
    assert scan_accounts(result.run_dir)["status"] == "待确认公司特殊规则"
    confirm_company_notes(result.run_dir, [])
    assert scan_accounts(result.run_dir)["status"] != "待确认公司特殊规则"
