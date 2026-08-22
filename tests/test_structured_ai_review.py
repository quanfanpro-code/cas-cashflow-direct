from __future__ import annotations

import importlib
from dataclasses import replace

from cashflow_direct.models import AITask, CashflowComponent, ClassificationDecision


def _review():
    return importlib.import_module("cashflow_direct.ai_review")


def _task() -> AITask:
    return AITask(
        task_id="AI-1",
        component_id="CMP-1",
        context=(
            "摘要：销售商品收到货款；完整对方科目路径：合同负债_销售；"
            "现金方向：inflow；公司特殊规则：NOTE-01"
        ),
        original_item="销售商品、提供劳务收到的现金",
        system_item_id="CFO-01",
        rule_evidence="系统候选仅供复核",
        candidate_item_ids=("CFO-01", "CFO-03"),
    )


def _payload() -> dict[str, object]:
    return {
        "task_id": "AI-1",
        "component_id": "CMP-1",
        "summary": {
            "candidate_item_id": "CFO-01",
            "quality": "strong",
            "basis_text": "销售商品收到货款",
            "classification_facts": ["action:sale_collection"],
            "conflict": False,
        },
        "account_path": {
            "candidate_item_id": "CFO-01",
            "quality": "medium",
            "basis_text": "合同负债_销售",
            "classification_facts": ["account:contract_liability"],
            "conflict": False,
        },
        "sources_independent": True,
        "business_conflict": False,
        "direction_status": "compatible",
        "reason": "摘要说明收款动作，路径说明销售往来对象",
        "alternative_item_ids": [],
        "note_ids": ["NOTE-01"],
        "review_round": "single",
        "reviewer_id": "reviewer-single",
        "model_id": "test-model",
        "reviewed_at": "2026-08-21T00:00:00+08:00",
        "prior_result_difference": "首轮复核，无前序结果",
    }


def test_complete_structured_result_is_accepted_without_ai_score() -> None:
    review = _review()

    validation = review.validate_structured_ai_results(
        (_task(),), (_payload(),), {"CFO-01", "CFO-03"}
    )

    assert validation.status == "AI 已完成"
    assert validation.invalid_ids == ()
    result = validation.valid_results[0]
    assert result.summary.quality.value == 45
    assert result.account_path.quality.value == 25
    assert not hasattr(result, "score")
    assert not hasattr(result, "action")

    evidence = review.recalculate_ai_evidence(result)
    assert evidence.score == 70
    assert evidence.independent_source_count == 2


def test_ai_cannot_approve_reversal_without_an_approved_rule() -> None:
    review = _review()
    payload = _payload()
    payload["direction_status"] = "approved_reversal"

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_ai_can_use_a_reversal_rule_already_approved_for_the_task() -> None:
    review = _review()
    task = replace(_task(), approved_reversal_rule_ids=("REFUND-SALES",))
    payload = _payload()
    payload["direction_status"] = "approved_reversal"

    validation = review.validate_structured_ai_results(
        (task,), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.status == "AI 已完成"
    assert validation.invalid_ids == ()


def test_ai_review_can_keep_multiple_weak_candidates_until_path_narrows_them() -> None:
    review = _review()
    payload = _payload()
    payload["summary"] = {
        "candidate_item_id": "",
        "candidate_item_ids": ["CFO-01", "CFO-03"],
        "quality": "weak",
        "basis_text": "销售商品收到货款",
        "classification_facts": ["action:collection"],
        "conflict": False,
    }
    payload["account_path"]["quality"] = "strong"

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.status == "AI 已完成"
    result = validation.valid_results[0]
    assert result.summary.candidate_item_ids == ("CFO-01", "CFO-03")
    evidence = review.recalculate_ai_evidence(result)
    assert evidence.candidate_item_id == "CFO-01"
    assert evidence.score == 55


def test_ai_review_cannot_rate_multiple_candidates_above_weak() -> None:
    review = _review()
    payload = _payload()
    payload["summary"] = {
        "candidate_item_id": "",
        "candidate_item_ids": ["CFO-01", "CFO-03"],
        "quality": "medium",
        "basis_text": "销售商品收到货款",
        "classification_facts": ["action:collection"],
        "conflict": False,
    }

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_numeric_four_level_quality_from_existing_review_is_accepted() -> None:
    review = _review()
    payload = _payload()
    payload["summary"]["quality"] = 45
    payload["account_path"]["quality"] = 25

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.status == "AI 已完成"
    assert validation.invalid_ids == ()


def test_summary_with_an_extra_business_fact_is_independent_from_the_path_fact() -> None:
    review = _review()
    payload = _payload()
    payload["summary"] = {
        **payload["summary"],
        "classification_facts": ["business:sale", "party:customer"],
    }
    payload["account_path"] = {
        **payload["account_path"],
        "classification_facts": ["party:customer"],
    }

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert len(validation.valid_results) == 1
    assert validation.invalid_ids == ()


def test_same_fact_repeated_by_summary_and_path_is_not_independent() -> None:
    review = _review()
    payload = _payload()
    payload["summary"] = {
        **payload["summary"],
        "classification_facts": ["party:customer"],
    }
    payload["account_path"] = {
        **payload["account_path"],
        "classification_facts": ["party:customer"],
    }

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_ai_self_reported_score_amount_or_action_invalidates_the_whole_result() -> None:
    review = _review()
    forbidden_values = {
        "score": 70,
        "amount_cent": 100,
        "action": "automatic_change",
    }

    for field, value in forbidden_values.items():
        payload = _payload()
        payload[field] = value
        validation = review.validate_structured_ai_results(
            (_task(),), (payload,), {"CFO-01", "CFO-03"}
        )
        assert validation.valid_results == (), field
        assert validation.invalid_ids == ("AI-1",), field


def test_missing_field_or_nonexistent_quote_invalidates_the_whole_result() -> None:
    review = _review()
    missing = _payload()
    missing.pop("sources_independent")
    invented = _payload()
    invented["summary"] = {
        **invented["summary"],
        "basis_text": "原始摘要中不存在的文字",
    }

    for payload in (missing, invented):
        validation = review.validate_structured_ai_results(
            (_task(),), (payload,), {"CFO-01", "CFO-03"}
        )
        assert validation.valid_results == ()
        assert validation.invalid_ids == ("AI-1",)


def test_missing_ai_audit_metadata_invalidates_the_whole_result() -> None:
    review = _review()

    for field in (
        "review_round",
        "reviewer_id",
        "model_id",
        "reviewed_at",
        "prior_result_difference",
    ):
        payload = _payload()
        payload.pop(field)

        validation = review.validate_structured_ai_results(
            (_task(),), (payload,), {"CFO-01", "CFO-03"}
        )

        assert validation.valid_results == (), field
        assert validation.invalid_ids == ("AI-1",), field


def test_blind_a_and_b_cannot_use_the_same_reviewer_instance() -> None:
    review = _review()
    task_a = replace(
        _task(),
        task_id="AI-A",
        context=_task().context + "；独立复核A：不得查看另一复核结果",
    )
    task_b = replace(
        _task(),
        task_id="AI-B",
        context=_task().context + "；独立复核B：不得查看另一复核结果",
    )
    payload_a = {
        **_payload(),
        "task_id": "AI-A",
        "review_round": "A",
        "reviewer_id": "same-reviewer",
        "prior_result_difference": "互盲复核，未查看另一结果",
    }
    payload_b = {
        **_payload(),
        "task_id": "AI-B",
        "review_round": "B",
        "reviewer_id": "same-reviewer",
        "reviewed_at": "2026-08-21T00:01:00+08:00",
        "prior_result_difference": "互盲复核，未查看另一结果",
    }

    validation = review.validate_structured_ai_results(
        (task_a, task_b),
        (payload_a, payload_b),
        {"CFO-01", "CFO-03"},
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-A", "AI-B")


def test_invented_or_inapplicable_note_is_rejected() -> None:
    review = _review()
    payload = _payload()
    payload["note_ids"] = ["NOTE-99"]

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_ai_cannot_introduce_a_new_candidate_even_if_it_is_a_valid_statement_item() -> None:
    review = _review()
    payload = _payload()
    payload["summary"] = {
        **payload["summary"],
        "candidate_item_id": "CFI-05",
    }
    payload["account_path"] = {
        **payload["account_path"],
        "candidate_item_id": "CFI-05",
    }

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03", "CFI-05"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_invalid_source_cannot_carry_candidate_or_facts() -> None:
    review = _review()
    payload = _payload()
    payload["summary"] = {
        "candidate_item_id": "CFO-01",
        "quality": "invalid",
        "basis_text": "",
        "classification_facts": ["action:sale_collection"],
        "conflict": False,
    }

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_each_ai_source_is_limited_to_its_own_candidate_set() -> None:
    review = _review()
    task = replace(
        _task(),
        summary_candidate_item_ids=("CFO-01",),
        account_path_candidate_item_ids=("CFO-03",),
    )
    payload = _payload()
    payload["summary"] = {**payload["summary"], "candidate_item_id": "CFO-03"}
    payload["account_path"] = {
        **payload["account_path"],
        "candidate_item_id": "CFO-03",
    }

    validation = review.validate_structured_ai_results(
        (task,), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_ai_source_with_no_candidate_cannot_borrow_the_other_source_candidate() -> None:
    review = _review()
    task = replace(
        _task(),
        summary_candidate_item_ids=(),
        account_path_candidate_item_ids=("CFO-01",),
    )

    validation = review.validate_structured_ai_results(
        (task,), (_payload(),), {"CFO-01", "CFO-03"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_legacy_serial_second_round_is_not_accepted_as_classification_ai() -> None:
    review = _review()
    task = replace(_task(), context=_task().context + "；串行第二次复核")
    payload = _payload()
    payload["review_round"] = "second"
    payload["prior_result_difference"] = "提供了新的说明"

    validation = review.validate_structured_ai_results(
        (task,), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.valid_results == ()
    assert validation.invalid_ids == ("AI-1",)


def test_two_source_disagreement_is_preserved_for_system_routing() -> None:
    review = _review()
    payload = _payload()
    payload["account_path"] = {
        **payload["account_path"],
        "candidate_item_id": "CFO-03",
        "conflict": True,
    }

    validation = review.validate_structured_ai_results(
        (_task(),), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.status == "AI 已完成"
    result = validation.valid_results[0]
    assert result.summary.candidate_item_id == "CFO-01"
    assert result.account_path.candidate_item_id == "CFO-03"
    assert result.account_path.conflict is True
    assert review.recalculate_ai_evidence(result).score is None


def test_ai_task_contains_only_original_business_sources_and_existing_candidates() -> None:
    review = _review()
    component = CashflowComponent(
        component_id="CMP-2",
        voucher_key="V-2",
        summary="销售商品收到货款",
        cash_delta_cent=123_456,
        counterpart_accounts=("2202_合同负债_销售",),
        original_item_text="客户原项目名称",
        anomalies=("test_anomaly",),
    )
    decision = ClassificationDecision(
        component_id="CMP-2",
        system_item_id="CFO-01",
        system_item_name="销售商品、提供劳务收到的现金",
        normal_direction="inflow",
        matched_rule_id="TEST",
        reason="系统内部判断理由",
        evidence_level="strong",
        candidate_item_ids=("CFO-01", "CFO-03"),
    )

    task = review.build_ai_task(
        component,
        decision,
        (
            {
                "note_id": "NOTE-01",
                "内容": "销售合同收款规则",
                "涉及科目或词": ["合同负债"],
                "状态": "采用",
            },
            {
                "note_id": "NOTE-02",
                "内容": "不相关规则",
                "涉及科目或词": ["借款"],
                "状态": "采用",
            },
        ),
    )

    assert "摘要原文：销售商品收到货款" in task.context
    assert "完整对方科目路径：2202_合同负债_销售" in task.context
    assert "现金方向" not in task.context
    assert "候选项目：CFO-01、CFO-03" in task.context
    assert "NOTE-01" in task.context
    assert "NOTE-02" not in task.context
    assert "客户原项目名称" not in task.context
    assert "123456" not in task.context
    assert "系统内部判断理由" not in task.context
    assert task.original_item == ""
    assert task.candidate_item_ids == ("CFO-01", "CFO-03")


def test_direction_control_fact_is_exposed_only_for_direction_forced_check() -> None:
    review = _review()
    component = CashflowComponent(
        component_id="CMP-DIRECTION",
        voucher_key="V-DIRECTION",
        summary="退款",
        cash_delta_cent=100_000,
        counterpart_accounts=("应付账款_供应商退款",),
    )
    decision = ClassificationDecision(
        component_id=component.component_id,
        system_item_id="CFO-04",
        system_item_name="购买商品、接受劳务支付的现金",
        normal_direction="outflow",
        matched_rule_id="TEST",
        reason="方向不相容",
        evidence_level="medium",
        candidate_item_ids=("CFO-04",),
        direction_status="incompatible",
    )

    task = review.build_ai_task(component, decision)

    assert "现金方向：inflow" in task.context


def test_one_time_reversal_task_may_confirm_current_item_without_creating_a_rule() -> None:
    review = _review()
    task = replace(
        _task(),
        allow_one_time_reversal=True,
        approved_reversal_rule_ids=(),
    )
    payload = _payload()
    payload["direction_status"] = "approved_reversal"

    validation = review.validate_structured_ai_results(
        (task,), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.status == "AI 已完成"


def test_blind_followup_tasks_are_built_only_from_original_sources() -> None:
    review = _review()
    component = CashflowComponent(
        component_id="CMP-3",
        voucher_key="V-3",
        summary="支付设备尾款",
        cash_delta_cent=-100_000,
        counterpart_accounts=("应付账款_应付设备款",),
        original_item_text="购买商品、接受劳务支付的现金",
    )
    decision = ClassificationDecision(
        component_id="CMP-3",
        system_item_id="CFI-06",
        system_item_name="购建固定资产、无形资产和其他长期资产支付的现金",
        normal_direction="outflow",
        matched_rule_id="TEST",
        reason="已有首轮意见不得传给互盲复核",
        evidence_level="medium",
        candidate_item_ids=("CFI-06", "CFO-04"),
    )

    tasks = review.build_blind_ai_tasks(component, decision, ("A", "B"))

    assert len(tasks) == 2
    assert len({task.task_id for task in tasks}) == 2
    assert "独立复核A" in tasks[0].context
    assert "独立复核B" in tasks[1].context
    assert all("已有首轮意见不得传给互盲复核" not in task.context for task in tasks)
    assert all("支付设备尾款" in task.context for task in tasks)
    assert all("应付账款_应付设备款" in task.context for task in tasks)


def test_third_blind_review_uses_round_c_and_passes_validation() -> None:
    review = _review()
    component = CashflowComponent(
        component_id="CMP-1",
        voucher_key="V-1",
        summary="销售商品收到货款",
        cash_delta_cent=100_000,
        counterpart_accounts=("合同负债_销售",),
    )
    decision = ClassificationDecision(
        component_id="CMP-1",
        system_item_id="CFO-01",
        system_item_name="销售商品、提供劳务收到的现金",
        normal_direction="inflow",
        matched_rule_id="TEST",
        reason="系统理由",
        evidence_level="weak",
        candidate_item_ids=("CFO-01", "CFO-03"),
    )
    task = review.build_blind_ai_tasks(component, decision, ("C",))[0]
    payload = _payload()
    payload.update(
        {
            "task_id": task.task_id,
            "review_round": "C",
            "reviewer_id": "reviewer-c",
            "note_ids": [],
            "prior_result_difference": "独立第三次复核，未查看前两份结果",
        }
    )

    validation = review.validate_structured_ai_results(
        (task,), (payload,), {"CFO-01", "CFO-03"}
    )

    assert validation.status == "AI 已完成"
    assert validation.valid_results[0].review_round == "C"


def test_round_c_cannot_reuse_a_reviewer_imported_in_an_earlier_batch() -> None:
    review = _review()
    base = _task()
    tasks = tuple(
        replace(
            base,
            task_id=f"AI-{slot}",
            context=base.context + f"；独立复核{slot}：不得查看其他复核结果",
        )
        for slot in ("A", "B", "C")
    )

    def payload(slot: str, reviewer_id: str) -> dict[str, object]:
        item = _payload()
        item.update(
            {
                "task_id": f"AI-{slot}",
                "review_round": slot,
                "reviewer_id": reviewer_id,
                "prior_result_difference": "互盲复核，未查看其他结果",
            }
        )
        return item

    first_batch = review.validate_structured_ai_results(
        tasks,
        (payload("A", "reviewer-a"), payload("B", "reviewer-b")),
        {"CFO-01", "CFO-03"},
    )

    merged = review.merge_structured_ai_results(
        tasks,
        first_batch.valid_results,
        (payload("C", "reviewer-a"),),
        {"CFO-01", "CFO-03"},
    )

    assert merged.status == "AI 未完成"
    assert "AI-C" in merged.invalid_ids


def test_company_note_with_full_path_scope_does_not_leak_to_same_leaf_under_another_parent() -> None:
    review = _review()
    decision = ClassificationDecision(
        component_id="CMP-SCOPE",
        system_item_id="CFO-05",
        system_item_name="支付其他与经营活动有关的现金",
        normal_direction="outflow",
        matched_rule_id="TEST",
        reason="测试候选",
        evidence_level="medium",
        candidate_item_ids=("CFO-05",),
    )
    note = {
        "note_id": "NOTE-01",
        "内容": "管理费用下的差旅费按管理活动处理",
        "适用完整路径": ["管理费用_差旅费"],
        "状态": "长期采用",
    }
    matching = CashflowComponent(
        "CMP-SCOPE",
        "V1",
        "支付差旅费",
        -10_000,
        ("管理费用_差旅费",),
    )
    other_parent = replace(
        matching,
        component_id="CMP-OTHER",
        counterpart_accounts=("合同履约成本_差旅费",),
    )

    assert "NOTE-01" in review.build_ai_task(matching, decision, (note,)).context
    assert "NOTE-01" not in review.build_ai_task(
        other_parent, replace(decision, component_id="CMP-OTHER"), (note,)
    ).context
