"""无完整候选时，拆分参考应随原始凭证一起交给后续分析。"""

from cashflow_direct import pipeline
from openpyxl import Workbook

from tests.test_business_structure import entry


def test_无候选的业务组成请求包含对方程序参考(tmp_path, monkeypatch):
    from cashflow_direct import 对方科目拆分参考

    def fake_analyze(entries):
        assert entries
        return {"状态": "仅供分析", "拆分参考": [{"原始行ID": entries[0]["entry_id"]}]}

    monkeypatch.setattr(对方科目拆分参考, "analyze_counterpart_splits", fake_analyze)
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
    source = tmp_path / "待结构分析序时账.xlsx"
    book.save(source)
    book.close()
    run = pipeline.run_preflight([source], ("100000", "50000", "5000"), tmp_path).run_dir
    state = pipeline._load_state(run)
    pipeline.confirm_cash_scope(
        run, {item["account_key"]: "include" for item in state["cash_scope_proposal"]["candidates"]})
    pipeline.supplement_cash_balances(run, "100", "90", "0", "合成测试余额")
    pipeline.run_classification(run)
    requests = pipeline._load_state(run)["component_structure_requests"]
    assert requests
    assert any(not request["candidate_plans"] for request in requests)
    assert all(request.get("对方科目拆分参考", {}).get("状态") == "仅供分析"
               for request in requests if not request["candidate_plans"])
