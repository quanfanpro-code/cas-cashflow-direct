from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest
from openpyxl import Workbook

import cashflow_direct.pipeline as pipeline_module
from cashflow_direct.component_structure_ai import build_structure_ai_tasks
from cashflow_direct.versions import current_versions
from cashflow_direct.pipeline import (
    confirm_cash_scope,
    finalize_run,
    import_ai_results,
    run_classification,
    run_preflight,
)


def test_ai_task_becomes_terminal_only_after_three_explicit_technical_failures() -> None:
    pipeline = pipeline_module
    state: dict[str, object] = {}
    payload = ({"task_id": "AI-1"},)

    first = pipeline._register_ai_technical_attempts(
        state, payload, ("AI-1",), ()
    )
    second = pipeline._register_ai_technical_attempts(
        state, payload, ("AI-1",), ()
    )
    missing_only = pipeline._register_ai_technical_attempts(state, (), (), ())
    third = pipeline._register_ai_technical_attempts(
        state, payload, ("AI-1",), ()
    )

    assert first == set()
    assert second == set()
    assert missing_only == set()
    assert third == {"AI-1"}
    assert state["ai_task_attempts"] == {"AI-1": 3}
    assert [item["status"] for item in state["ai_technical_failure_log"]] == [
        "技术失败，可重试",
        "技术失败，可重试",
        "无有效结果",
    ]


def test_component_structure_confirmation_accepts_only_a_listed_complete_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state: dict[str, object] = {
        "run_id": "RUN-1",
        "component_structure_requests": [
            {
                "voucher_key": "V-1",
                "candidate_entry_id_combinations": [
                    ["E-1", "E-3"],
                    ["E-2", "E-3"],
                ],
            }
        ],
    }
    monkeypatch.setattr(pipeline_module, "_load_state", lambda _path: state)
    monkeypatch.setattr(pipeline_module, "_assert_inputs_unchanged", lambda _state: None)
    monkeypatch.setattr(pipeline_module, "_save_state", lambda _path, _state: None)

    with pytest.raises(ValueError, match="既有候选组合"):
        pipeline_module.confirm_component_structure(
            tmp_path, {"V-1": ("E-9",)}
        )

    result = pipeline_module.confirm_component_structure(
        tmp_path, {"V-1": ("E-2", "E-3")}
    )

    assert result.status == "completed"
    assert state["component_structure_selections"] == {
        "V-1": ["E-2", "E-3"]
    }
    assert "component_structure_requests" not in state


def test_component_structure_ai_low_first_result_gets_second_review_then_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request = {
        "voucher_key": "V-1",
        "cash_delta_cent": -10_000,
        "materiality_level": "M1",
        "candidate_entry_id_combinations": [
            ["E-1", "E-3"],
            ["E-2", "E-3"],
        ],
        "candidate_details": ["组合1", "组合2"],
    }
    first_task = build_structure_ai_tasks(request, "M1", ("single",))[0]
    state: dict[str, object] = {
        "run_id": "RUN-1",
        "component_structure_requests": [request],
        "component_structure_ai_tasks": [asdict(first_task)],
        "component_structure_ai_results": [],
    }
    monkeypatch.setattr(pipeline_module, "_load_state", lambda _path: state)
    monkeypatch.setattr(pipeline_module, "_assert_inputs_unchanged", lambda _state: None)
    monkeypatch.setattr(pipeline_module, "_save_state", lambda _path, _state: None)
    (tmp_path / "计算留痕数据").mkdir()

    first_path = tmp_path / "first.jsonl"
    first_path.write_text(
        json.dumps(
            {
                "task_id": first_task.task_id,
                "voucher_key": "V-1",
                "review_round": "single",
                "selected_entry_ids": ["E-1", "E-3"],
                "confidence": "low",
                "reason": "存在两个金额均守恒的组合",
                "reviewer_id": "R-1",
                "model_id": "M-1",
                "reviewed_at": "2026-08-22T18:00:00+08:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8-sig",
    )
    first = pipeline_module.import_component_structure_ai_results(
        tmp_path, first_path
    )
    assert first["status"] == "待AI继续判断业务组成"
    second_task = pipeline_module._structure_ai_task_from_dict(
        state["component_structure_ai_tasks"][-1]
    )

    second_path = tmp_path / "second.jsonl"
    second_path.write_text(
        json.dumps(
            {
                "task_id": second_task.task_id,
                "voucher_key": "V-1",
                "review_round": "second",
                "selected_entry_ids": ["E-1", "E-3"],
                "confidence": "high",
                "reason": "复核后该组合金额守恒且与现有摘要关系最清楚",
                "reviewer_id": "R-2",
                "model_id": "M-2",
                "reviewed_at": "2026-08-22T18:05:00+08:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8-sig",
    )
    completed = pipeline_module.import_component_structure_ai_results(
        tmp_path, second_path
    )

    assert completed["status"] == "业务组成AI判断已完成"
    assert state["component_structure_selections"] == {
        "V-1": ["E-1", "E-3"]
    }
    assert state["component_structure_selection_basis"] == {
        "V-1": "existing_evidence"
    }
    assert state["stage"] == "component_structure_confirmed"


@pytest.mark.parametrize(
    ("choice", "expected_status"),
    (("长期采用", "长期采用"), ("拒绝", "冲突未采用")),
)
def test_overall_material_reversal_confirmation_creates_a_scoped_company_note(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    choice: str,
    expected_status: str,
) -> None:
    state: dict[str, object] = {
        "run_id": "RUN-1",
        "company_notes": [],
        "reversal_confirmation_requests": [
            {
                "component_id": "CF-1",
                "候选项目": "CFI-06",
                "候选项目名称": "购建长期资产支付的现金",
                "完整对方科目路径": ["应付账款_应付设备款_往来款"],
                "摘要": "退回设备尾款",
                "现金变化金额分": 2_200_000_00,
            }
        ],
    }
    saved_states: list[dict[str, object]] = []

    def confirm_notes(_run_dir: Path, notes: object) -> None:
        state["company_notes"] = [dict(item) for item in notes]
        state["stage"] = "cash_scope_confirmed"

    monkeypatch.setattr(pipeline_module, "_load_state", lambda _path: state)
    monkeypatch.setattr(pipeline_module, "_assert_inputs_unchanged", lambda _state: None)
    monkeypatch.setattr(
        pipeline_module,
        "_save_state",
        lambda _path, new_state: saved_states.append(dict(new_state)),
    )
    monkeypatch.setattr(pipeline_module, "confirm_company_notes", confirm_notes)

    result = pipeline_module.confirm_reversal_patterns(
        tmp_path, {"CF-1": choice}
    )

    note = state["company_notes"][0]
    assert result.status == "completed"
    assert note["规则类型"] == "退款或反向冲减"
    assert note["状态"] == expected_status
    assert note["建议处理"] == "CFI-06"
    assert note["适用完整路径"] == ["应付账款_应付设备款_往来款"]
    assert note["适用摘要词"] == ["退回设备尾款"]
    assert note["适用公司"]
    assert note["适用期间"]
    assert note["影响业务组成数量"] == 1
    assert note["影响金额分"] == 2_200_000_00
    assert note["后续期间影响"]
    assert "reversal_confirmation_requests" not in state
    assert saved_states
from cashflow_direct.models import ClassificationDecision
from tests.fixture_factory import mark_dictionary_complete, write_end_to_end_case


def test_evidence_record_keeps_every_forced_check_that_can_change_the_route() -> None:
    decision = ClassificationDecision(
        component_id="CF-1",
        system_item_id="CFO-01",
        system_item_name="销售商品收到的现金",
        normal_direction="inflow",
        matched_rule_id="TEST",
        reason="构造测试",
        evidence_level="strong",
        company_rule_conflict=True,
        vat_base_missing=True,
        net_item_facts_missing=True,
        new_reversal_pattern=True,
    )

    payload = pipeline_module._evidence_assessment_payload(decision)

    assert payload["company_rule_conflict"] is True
    assert payload["vat_base_missing"] is True
    assert payload["net_item_facts_missing"] is True
    assert payload["new_reversal_pattern"] is True


def test_final_ai_records_include_valid_results_and_technical_failure_terminal_states() -> None:
    state = {
        "structured_ai_validation": {
            "valid_results": [{"task_id": "AI-OK", "status": "有效"}]
        },
        "ai_technical_failure_log": [
            {"task_id": "AI-FAIL", "attempt": 3, "status": "无有效结果"}
        ],
        "component_structure_ai_results": [
            {"task_id": "STRUCTURE-OK", "status": "有效"}
        ],
        "component_structure_ai_technical_failure_log": [
            {
                "task_id": "STRUCTURE-FAIL",
                "attempt": 3,
                "status": "无有效结果",
            }
        ],
    }

    records = pipeline_module._ai_records_from_state(state)

    assert {(item["阶段"], item["task_id"], item["status"]) for item in records} == {
        ("分类AI有效结果", "AI-OK", "有效"),
        ("分类AI技术失败", "AI-FAIL", "无有效结果"),
        ("业务组成结构AI有效结果", "STRUCTURE-OK", "有效"),
        ("业务组成结构AI技术失败", "STRUCTURE-FAIL", "无有效结果"),
    }


def test_finalization_is_blocked_before_agent_confirms_a_new_reversal_pattern() -> None:
    with pytest.raises(RuntimeError, match="退款或反向冲减"):
        pipeline_module._assert_agent_gates_closed(
            {
                "stage": "waiting_reversal_confirmation",
                "reversal_confirmation_requests": [{"component_id": "CF-1"}],
            }
        )


def test_reliable_group_has_no_separate_confirmation_command() -> None:
    from cashflow_direct.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["confirm-materiality-groups", "--run-dir", "构造运行目录"]
        )


def test_state_save_retries_when_windows_temporarily_locks_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_replace = Path.replace
    attempts = 0

    def temporarily_locked(source: Path, target: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(5, "Windows临时拒绝访问", str(target))
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", temporarily_locked)
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "构造运行目录"
        pipeline_module._save_state(run_dir, {"stage": "test"})

        assert attempts == 2
        assert json.loads(
            (
                run_dir / "计算留痕数据" / "运行状态.json"
            ).read_text(encoding="utf-8-sig")
        ) == {"stage": "test"}


def test_state_save_raises_after_five_persistent_windows_lock_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def persistently_locked(source: Path, target: Path) -> Path:
        nonlocal attempts
        attempts += 1
        raise PermissionError(5, "Windows持续拒绝访问", str(target))

    monkeypatch.setattr(Path, "replace", persistently_locked)
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(PermissionError, match="Windows持续拒绝访问"):
            pipeline_module._save_state(Path(tmp) / "构造运行目录", {"stage": "test"})

    assert attempts == 5


def test_state_save_does_not_retry_non_permission_file_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def other_file_error(source: Path, target: Path) -> Path:
        nonlocal attempts
        attempts += 1
        raise OSError(123, "其他文件错误", str(target))

    monkeypatch.setattr(Path, "replace", other_file_error)
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(OSError, match="其他文件错误"):
            pipeline_module._save_state(Path(tmp) / "构造运行目录", {"stage": "test"})

    assert attempts == 1


def _write_ai_routed_case(root: Path) -> Path:
    path = root / "构造AI分流.xlsx"
    workbook = Workbook()
    detail = workbook.active
    detail.title = "序时账"
    detail.append(["日期", "凭证号", "摘要", "科目", "借方", "贷方", "现流项目"])
    detail.append(
        [
            "2026-01-01",
            "记-1",
            "销售回款",
            "1002 银行存款",
            5_000,
            None,
            "收到其他与经营活动有关的现金",
        ]
    )
    detail.append(
        [
            "2026-01-01",
            "记-1",
            "销售回款",
            "主营业务收入",
            None,
            5_000,
            "收到其他与经营活动有关的现金",
        ]
    )
    balances = workbook.create_sheet("现金余额资料")
    balances.append(["项目", "金额"])
    balances.append(["期初现金及现金等价物余额", 0])
    balances.append(["期末现金及现金等价物余额", 5_000])
    balances.append(["汇率变动对现金及现金等价物的影响", 0])
    workbook.save(path)
    workbook.close()
    return path


def _write_illegal_summary_case(root: Path) -> Path:
    path = root / "构造空摘要.xlsx"
    workbook = Workbook()
    detail = workbook.active
    detail.title = "序时账"
    detail.append(["日期", "凭证号", "摘要", "科目", "借方", "贷方", "现流项目"])
    detail.append(["2026-01-01", "记-1", None, "1002 银行存款", 100, None, None])
    detail.append(["2026-01-01", "记-1", None, "主营业务收入", None, 100, None])
    balances = workbook.create_sheet("现金余额资料")
    balances.append(["项目", "金额"])
    balances.append(["期初现金及现金等价物余额", 0])
    balances.append(["期末现金及现金等价物余额", 100])
    balances.append(["汇率变动对现金及现金等价物的影响", 0])
    workbook.save(path)
    workbook.close()
    return path


def test_run_classification_persists_the_automatic_fill_route() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preflight = run_preflight(
            write_end_to_end_case(root),
            ("1000000", "750000", "50000"),
            output_parent=root,
        )
        confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
        mark_dictionary_complete(preflight.run_dir)

        result = run_classification(preflight.run_dir)

        state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        assert result.status == "consistency_completed"
        assert state["ai_tasks"] == []
        assert state["materiality_assessments"]
        assert state["source_allocations"]
        assert {
            decision["decision_action"] for decision in state["decisions"]
        } == {"automatic_fill"}
        assert {
            decision["materiality_level"] for decision in state["decisions"]
        } == {"M0"}
        assert all(decision["resolved"] for decision in state["decisions"])
        connection = sqlite3.connect(
            preflight.run_dir / "计算留痕数据" / "计算留痕.sqlite3"
        )
        try:
            counts = {
                table: connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in (
                    "source_allocation",
                    "evidence_assessment",
                    "materiality_assessment",
                    "decision_route",
                    "run_version",
                )
            }
        finally:
            connection.close()
        assert counts["source_allocation"] == len(state["source_allocations"])
        assert counts["evidence_assessment"] == len(state["decisions"])
        assert counts["materiality_assessment"] == len(state["decisions"])
        assert counts["decision_route"] == len(state["decisions"])
        assert counts["run_version"] == len(
            current_versions(Path(__file__).resolve().parents[1])
        )


def test_pipeline_imports_structured_ai_sources_then_system_recalculates_action() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preflight = run_preflight(
            [_write_ai_routed_case(root)],
            ("100000", "50000", "5000"),
            output_parent=root,
        )
        confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
        mark_dictionary_complete(preflight.run_dir)
        classified = run_classification(preflight.run_dir)
        assert classified.status == "waiting_ai"

        state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        task = state["ai_tasks"][0]
        result_path = root / "AI结构化结果.jsonl"
        result_path.write_text(
            json.dumps(
                {
                    "task_id": task["task_id"],
                    "component_id": task["component_id"],
                    "summary": {
                        "candidate_item_id": "CFO-01",
                        "quality": "strong",
                        "basis_text": "销售回款",
                        "classification_facts": ["action:sale_collection"],
                        "conflict": False,
                    },
                    "account_path": {
                        "candidate_item_id": "CFO-01",
                        "quality": "strong",
                        "basis_text": "主营业务收入",
                        "classification_facts": ["account:main_revenue"],
                        "conflict": False,
                    },
                    "sources_independent": True,
                    "business_conflict": False,
                    "direction_status": "compatible",
                    "reason": "只重新解释摘要和完整路径",
                    "alternative_item_ids": [],
                    "note_ids": [],
                    "review_round": "single",
                    "reviewer_id": "test-reviewer-single",
                    "model_id": "test-model",
                    "reviewed_at": "2026-08-21T00:00:00+08:00",
                    "prior_result_difference": "首轮复核，无前序结果",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8-sig",
        )

        imported = import_ai_results(preflight.run_dir, result_path)

        assert imported.status == "AI 已完成"
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        decision = state["decisions"][0]
        assert decision["system_item_id"] == "CFO-01"
        assert decision["evidence_score"] == 90
        assert decision["decision_action"] == "automatic_change"
        assert decision["resolved"] is True
        connection = sqlite3.connect(
            preflight.run_dir / "计算留痕数据" / "计算留痕.sqlite3"
        )
        try:
            stored_decision = json.loads(
                connection.execute(
                    "SELECT payload_json FROM classification_decision WHERE record_id = ?",
                    (decision["component_id"],),
                ).fetchone()[0]
            )
            stored_route = json.loads(
                connection.execute(
                    "SELECT payload_json FROM decision_route WHERE record_id = ?",
                    (decision["component_id"],),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        assert stored_decision["evidence_score"] == 90
        assert stored_route["action"] == "automatic_change"


def test_three_invalid_ai_submissions_clear_the_queue_and_use_the_cell_failure_exit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preflight = run_preflight(
            [_write_ai_routed_case(root)],
            ("100000", "50000", "5000"),
            output_parent=root,
        )
        confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
        mark_dictionary_complete(preflight.run_dir)
        classified = run_classification(preflight.run_dir)
        assert classified.status == "waiting_ai"
        state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
        task_id = json.loads(state_path.read_text(encoding="utf-8-sig"))["ai_tasks"][0][
            "task_id"
        ]

        for attempt in range(1, 4):
            invalid_path = root / f"无效AI结果_{attempt}.jsonl"
            invalid_path.write_text(
                json.dumps({"task_id": task_id}, ensure_ascii=False) + "\n",
                encoding="utf-8-sig",
            )
            result = import_ai_results(preflight.run_dir, invalid_path)

        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        assert result.missing_count == 0
        assert state["classification_summary"]["ai_tasks_missing"] == 0
        assert state["ai_terminal_failure_ids"] == [task_id]
        assert state["ai_technical_failure_log"][-1]["status"] == "无有效结果"
        assert state["decisions"][0]["decision_action"] == "automatic_keep"
        assert state["decisions"][0]["resolved"] is True


def test_three_missing_ai_submissions_also_clear_the_queue_and_reach_a_terminal_exit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preflight = run_preflight(
            [_write_ai_routed_case(root)],
            ("100000", "50000", "5000"),
            output_parent=root,
        )
        confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
        mark_dictionary_complete(preflight.run_dir)
        classified = run_classification(preflight.run_dir)
        assert classified.status == "waiting_ai"
        state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
        task_id = json.loads(state_path.read_text(encoding="utf-8-sig"))["ai_tasks"][0][
            "task_id"
        ]

        for attempt in range(1, 4):
            missing_path = root / f"漏答AI结果_{attempt}.jsonl"
            missing_path.write_text("", encoding="utf-8-sig")
            result = import_ai_results(preflight.run_dir, missing_path)

        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        assert result.missing_count == 0
        assert state["classification_summary"]["ai_tasks_missing"] == 0
        assert state["ai_terminal_failure_ids"] == [task_id]
        assert state["ai_task_attempts"] == {task_id: 3}
        assert state["ai_technical_failure_log"][-1]["status"] == "无有效结果"


def test_pipeline_restores_valid_original_when_ai_does_not_prove_change() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preflight = run_preflight(
            [_write_ai_routed_case(root)],
            ("100000", "50000", "5000"),
            output_parent=root,
        )
        confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
        mark_dictionary_complete(preflight.run_dir)
        run_classification(preflight.run_dir)
        state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        first_task = state["ai_tasks"][0]

        def payload(task, *, review_round, account_quality):
            return {
                "task_id": task["task_id"],
                "component_id": task["component_id"],
                "summary": {
                    "candidate_item_id": "CFO-01",
                    "quality": "strong",
                    "basis_text": "销售回款",
                    "classification_facts": ["action:sale_collection"],
                    "conflict": False,
                },
                "account_path": {
                    "candidate_item_id": "CFO-01",
                    "quality": account_quality,
                    "basis_text": "主营业务收入",
                    "classification_facts": ["account:main_revenue"],
                    "conflict": False,
                },
                "sources_independent": True,
                "business_conflict": False,
                "direction_status": "compatible",
                "reason": "只重新解释摘要和完整路径",
                "alternative_item_ids": [],
                "note_ids": [],
                "review_round": review_round,
                "reviewer_id": f"test-reviewer-{review_round}",
                "model_id": "test-model",
                "reviewed_at": "2026-08-21T00:00:00+08:00",
                "prior_result_difference": (
                    "首轮复核，无前序结果"
                    if review_round == "single"
                    else "第二轮重新核对完整路径的业务属性"
                ),
            }

        first_path = root / "AI首轮结果.jsonl"
        first_path.write_text(
            json.dumps(payload(first_task, review_round="single", account_quality="weak"), ensure_ascii=False) + "\n",
            encoding="utf-8-sig",
        )
        first = import_ai_results(preflight.run_dir, first_path)

        assert first.status == "AI 已完成"
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        assert state["decisions"][0]["resolved"] is True
        assert state["decisions"][0]["decision_action"] == "automatic_keep"
        assert state["decisions"][0]["system_item_id"] == "CFO-03"


def test_old_or_changed_scoring_version_cannot_continue_in_the_same_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preflight = run_preflight(
            write_end_to_end_case(root),
            ("1000000", "750000", "50000"),
            output_parent=root,
        )
        state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        assert {
            "schema",
            "scoring",
            "action_matrix",
            "account_mapping",
            "company_notes",
            "rule_pack",
            "account_dictionary",
        } <= set(state["versions"])
        state["versions"]["scoring"] = "旧评分版本"
        state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8-sig"
        )

        with pytest.raises(RuntimeError, match="旧运行目录.*新建运行目录"):
            confirm_cash_scope(
                preflight.run_dir, preflight.recommended_cash_decisions
            )


def test_illegal_blank_summary_enters_the_same_workbook_for_user_decision() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preflight = run_preflight(
            [_write_illegal_summary_case(root)],
            ("100000", "50000", "5000"),
            output_parent=root,
        )
        confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
        mark_dictionary_complete(preflight.run_dir)

        classified = run_classification(preflight.run_dir)

        state = json.loads(
            (
                preflight.run_dir / "计算留痕数据" / "运行状态.json"
            ).read_text(encoding="utf-8-sig")
        )
        assert classified.status == "waiting_human"
        assert len(state["components"]) == 1
        assert state["components"][0]["cash_delta_cent"] == 10_000
        assert "summary_empty" in state["components"][0]["anomalies"]
        assert state["decisions"][0]["decision_action"] == "isolate_invalid_input"
        assert state["decisions"][0]["resolved"] is False
        assert state["ai_tasks"] == []
        assert any(
            issue["kind"] == "警告" and "摘要为空" in issue["message"]
            for issue in state["normalization_issues"]
        )

        final = finalize_run(preflight.run_dir)
        assert final.workbook_path.is_file()
        assert final.overall_status == "待完成人工确认"
        state = json.loads(
            (
                preflight.run_dir / "计算留痕数据" / "运行状态.json"
            ).read_text(encoding="utf-8-sig")
        )
        assert state["review_batches"]
        assert state["components"][0]["component_id"] in state["review_batches"][0][
            "component_ids"
        ]
        assert not any(
            "非法输入" in error for error in state["final_readiness"]["errors"]
        )
