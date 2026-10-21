import json
import sqlite3
from dataclasses import replace

import pytest
from openpyxl import Workbook

from cashflow_direct import pipeline as p
from tests.test_business_structure import ambiguous_payroll, entry
from tests.fixture_factory import mark_dictionary_complete
from tests.test_pipeline import _complete_all_pending_ai


def start_run(tmp_path, additional_structure=False):
    rows = (*ambiguous_payroll(),
            replace(entry(8, "银行存款", -500, "支付银行手续费"), voucher_key="V-OTHER"),
            replace(entry(9, "财务费用 - 手续费", 500, "支付银行手续费"), voucher_key="V-OTHER"))
    if additional_structure:
        rows += tuple(replace(row, voucher_key="V-SECOND") for row in ambiguous_payroll())
    book = Workbook()
    sheet = book.active
    sheet.append(["日期", "凭证号", "摘要", "科目名称", "借方金额", "贷方金额"])
    for row in rows:
        sheet.append(["2026-03-01", row.voucher_key, row.summary, row.account_name,
                      row.debit_cent / 100, row.credit_cent / 100])
    path = tmp_path / "合成序时账.xlsx"
    book.save(path)
    book.close()
    run = p.run_preflight([path], ("100000", "50000", "5000"), tmp_path).run_dir
    state = p._load_state(run)
    p.confirm_cash_scope(run, {c["account_key"]: "include" for c in state["cash_scope_proposal"]["candidates"]})
    p.supplement_cash_balances(run, "100", "51" if additional_structure else "73", "0", "合成测试余额")
    mark_dictionary_complete(run)
    return run


def test_pending_structure_allows_independent_classification_and_restart(tmp_path):
    run = start_run(tmp_path)
    p.run_classification(run)
    _complete_all_pending_ai(run, tmp_path)
    state = p._load_state(run)
    assert state["component_structure_requests"]
    assert any(d["candidate_status"] != "structure_pending" for d in state["decisions"])
    old_tasks = state["ai_tasks"]
    old_decisions = {d["component_id"]: d for d in state["decisions"] if d["candidate_status"] != "structure_pending"}
    assert len(state["component_structure_ai_tasks"]) == 1
    p.run_classification(run)
    assert p._load_state(run)["ai_tasks"] == old_tasks
    with pytest.raises(RuntimeError, match="业务组成"):
        p.finalize_run(run)
    request = state["component_structure_requests"][0]
    p.confirm_component_structure(run, {request["voucher_key"]: {"plan_id": request["candidate_plans"][0]["plan_id"]}})
    mark_dictionary_complete(run)
    p.run_classification(run)
    after = p._load_state(run)
    assert not after.get("component_structure_requests")
    assert all(d["candidate_status"] != "structure_pending" for d in after["decisions"])
    assert all(next(d for d in after["decisions"] if d["component_id"] == key) == value for key, value in old_decisions.items())
    assert sum(a["allocated_cent"] for a in after["source_allocations"]) == -2700
    with sqlite3.connect(run / "计算留痕数据" / "计算留痕.sqlite3") as db:
        stored = [json.loads(r[0]) for r in db.execute('select payload_json from component_structure')]
    assert len(stored) == 1
    assert stored[0] == after["component_structure_plans"][0]


def test_changed_or_forged_structure_record_is_rejected(tmp_path):
    run = start_run(tmp_path)
    p.run_classification(run)
    state_path = run / "计算留痕数据" / "运行状态.json"
    state = p._load_state(run)
    state["component_structure_plans"][0]["unresolved_entry_ids"] = []
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RuntimeError, match="业务组成.*不一致"):
        p.run_classification(run)


def test_full_plan_ai_import_rebuilds_and_rejects_unknown_plan(tmp_path):
    run = start_run(tmp_path)
    p.run_classification(run)
    state = p._load_state(run)
    task = state["component_structure_ai_tasks"][0]
    payload = {"task_id": task["task_id"], "voucher_key": task["voucher_key"],
               "review_round": task["review_round"], "selected_entry_ids": [],
               "selected_plan_id": task["candidate_plan_ids"][0], "confidence": "high",
               "reason": "核对原文对象、角色和每条现金分配后选择完整候选", "reviewer_id": "test-reviewer",
               "model_id": "test-model", "reviewed_at": "2026-10-10T08:00:00+08:00"}
    path = tmp_path / "结构结果.jsonl"
    path.write_text(json.dumps(payload), encoding="utf-8")
    p.import_component_structure_ai_results(run, path)
    mark_dictionary_complete(run)
    p.run_classification(run)
    after = p._load_state(run)
    assert not after.get("component_structure_requests")
    assert after["component_structure_plans"][0]["plan_id"] == payload["selected_plan_id"]
    assert sum(a["allocated_cent"] for a in after["source_allocations"]) == -2700


def test_database_structure_rows_cannot_disagree_with_snapshot(tmp_path):
    from contextlib import closing
    run = start_run(tmp_path)
    p.run_classification(run)
    with closing(sqlite3.connect(run / "计算留痕数据" / "计算留痕.sqlite3")) as db, db:
        db.execute("DELETE FROM component_structure")
    with pytest.raises(RuntimeError, match="业务组成.*不一致"):
        p.run_classification(run)


def test_structural_rebuild_does_not_reuse_a_forged_classification(tmp_path):
    run = start_run(tmp_path)
    p.run_classification(run)
    state = p._load_state(run)
    request = state["component_structure_requests"][0]
    p.confirm_component_structure(run, {request["voucher_key"]: {"plan_id": request["candidate_plans"][0]["plan_id"]}})
    mark_dictionary_complete(run)
    state_path = run / "计算留痕数据" / "运行状态.json"
    state = p._load_state(run)
    state["decisions"][-1]["reason"] = "伪造分类理由"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(RuntimeError, match="分类决定"):
        p.run_classification(run)


def test_partial_manual_confirmation_retires_only_its_structure_tasks(tmp_path):
    run = start_run(tmp_path, additional_structure=True)
    p.run_classification(run)
    before = p._load_state(run)
    request = before["component_structure_requests"][0]
    assert len(before["component_structure_ai_tasks"]) == 2
    p.confirm_component_structure(run, {request["voucher_key"]: {"plan_id": request["candidate_plans"][0]["plan_id"]}})
    after = p._load_state(run)
    assert len(after["component_structure_ai_tasks"]) == 1
    assert after["component_structure_ai_tasks"][0]["voucher_key"] != request["voucher_key"]
    assert len(after["component_structure_ai_task_history"]) == 1


def test_agent_original_journal_plan_is_accepted_only_when_cash_reconciles(tmp_path):
    rows = (
        entry(1, "银行存款", -1000, "支付维修费"),
        entry(2, "管理费用_维修费", 1000, "支付维修费"),
        entry(3, "预付账款", 200, "另项内部结算"),
        entry(4, "其他应收款", -200, "另项内部结算"),
    )
    book = Workbook()
    sheet = book.active
    sheet.append(["日期", "凭证号", "摘要", "科目名称", "借方金额", "贷方金额"])
    for row in rows:
        sheet.append(["2026-03-01", "7", row.summary, row.account_name,
                      row.debit_cent / 100, row.credit_cent / 100])
    path = tmp_path / "待结构分析序时账.xlsx"
    book.save(path)
    book.close()
    run = p.run_preflight([path], ("100000", "50000", "5000"), tmp_path).run_dir
    state = p._load_state(run)
    p.confirm_cash_scope(run, {c["account_key"]: "include" for c in state["cash_scope_proposal"]["candidates"]})
    p.supplement_cash_balances(run, "100", "90", "0", "合成测试余额")
    p.run_classification(run)
    before = p._load_state(run)
    request = before["component_structure_requests"][0]
    assert not request["candidate_plans"]
    source_rows = request["source_entries"]
    cash = next(row for row in source_rows if row["account_name"] == "银行存款")
    expense = next(row for row in source_rows if row["account_name"] == "管理费用_维修费")
    others = [row for row in source_rows if row not in (cash, expense)]
    proposal = {
        "business_groups": [{"main_entry_ids": [expense["entry_id"]], "settlement_entry_ids": [],
                             "cash_shares": [{"entry_id": cash["entry_id"], "amount_cent": -1000}]}],
        "noncash_groups": [{"entry_ids": [row["entry_id"] for row in others]}],
    }
    with pytest.raises(ValueError, match="依据"):
        p.confirm_component_structure(run, {request["voucher_key"]: {"agent_plan": proposal}})
    p.confirm_component_structure(run, {request["voucher_key"]: {
        "agent_plan": proposal, "reason": "原始账显示维修费由银行支付；另两行内部结算借贷相等。",
    }})
    mark_dictionary_complete(run)
    p.run_classification(run)
    after = p._load_state(run)
    assert not after.get("component_structure_requests")
    assert after["component_structure_agent_plans"][request["voucher_key"]] == proposal
    assert sum(item["allocated_cent"] for item in after["source_allocations"]) == -1000
