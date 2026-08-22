from __future__ import annotations

from cashflow_direct.component_structure_ai import (
    build_structure_ai_tasks,
    resolve_structure_ai_request,
    validate_structure_ai_results,
)


REQUEST = {
    "voucher_key": "V-1",
    "cash_delta_cent": -10_000,
    "candidate_entry_id_combinations": (("E-1", "E-3"), ("E-2", "E-3")),
    "candidate_details": ("组合1", "组合2"),
}


def _payload(task, selected, *, confidence="high", reviewer="R-1"):
    return {
        "task_id": task.task_id,
        "voucher_key": task.voucher_key,
        "review_round": task.review_round,
        "selected_entry_ids": list(selected),
        "confidence": confidence,
        "reason": "只比较既有候选并完成金额守恒检查",
        "reviewer_id": reviewer,
        "model_id": "MODEL-1",
        "reviewed_at": "2026-08-22T18:00:00+08:00",
    }


def test_m0_high_confidence_single_review_selects_existing_combination() -> None:
    tasks = build_structure_ai_tasks(REQUEST, "M0", ("single",))
    validation = validate_structure_ai_results(
        tasks, (_payload(tasks[0], ("E-1", "E-3")),)
    )

    resolution = resolve_structure_ai_request(
        REQUEST, "M0", tasks, validation.valid_results, set()
    )

    assert resolution.status == "selected"
    assert resolution.selected_entry_ids == ("E-1", "E-3")


def test_m1_low_confidence_first_review_requests_serial_second() -> None:
    tasks = build_structure_ai_tasks(REQUEST, "M1", ("single",))
    validation = validate_structure_ai_results(
        tasks,
        (
            _payload(
                tasks[0],
                ("E-1", "E-3"),
                confidence="low",
            ),
        ),
    )

    resolution = resolve_structure_ai_request(
        REQUEST, "M1", tasks, validation.valid_results, set()
    )

    assert resolution.status == "needs_second"


def test_m2_disagreement_requests_c_and_c_may_only_choose_existing_candidate() -> None:
    tasks = build_structure_ai_tasks(REQUEST, "M2", ("A", "B"))
    validation = validate_structure_ai_results(
        tasks,
        (
            _payload(tasks[0], ("E-1", "E-3"), reviewer="R-A"),
            _payload(tasks[1], ("E-2", "E-3"), reviewer="R-B"),
        ),
    )
    first = resolve_structure_ai_request(
        REQUEST, "M2", tasks, validation.valid_results, set()
    )
    assert first.status == "needs_c"

    all_tasks = (*tasks, *build_structure_ai_tasks(REQUEST, "M2", ("C",)))
    all_payloads = (
        _payload(tasks[0], ("E-1", "E-3"), reviewer="R-A"),
        _payload(tasks[1], ("E-2", "E-3"), reviewer="R-B"),
        _payload(all_tasks[2], ("E-2", "E-3"), reviewer="R-C"),
    )
    completed = validate_structure_ai_results(all_tasks, all_payloads)
    final = resolve_structure_ai_request(
        REQUEST, "M2", all_tasks, completed.valid_results, set()
    )
    assert final.status == "selected"
    assert final.selected_entry_ids == ("E-2", "E-3")


def test_technical_failure_is_not_a_structure_vote() -> None:
    tasks = build_structure_ai_tasks(REQUEST, "M2", ("A", "B"))
    validation = validate_structure_ai_results(
        tasks,
        (_payload(tasks[0], ("E-1", "E-3"), reviewer="R-A"),),
    )

    resolution = resolve_structure_ai_request(
        REQUEST,
        "M2",
        tasks,
        validation.valid_results,
        {tasks[1].task_id},
    )

    assert resolution.status == "needs_c"


def test_unlisted_combination_is_invalid() -> None:
    tasks = build_structure_ai_tasks(REQUEST, "M2", ("A", "B"))
    validation = validate_structure_ai_results(
        tasks,
        (
            _payload(tasks[0], ("E-9",), reviewer="R-1"),
            _payload(tasks[1], ("E-1", "E-3"), reviewer="R-1"),
        ),
    )

    assert set(validation.invalid_ids) == {tasks[0].task_id}


def test_same_reviewer_cannot_supply_both_blind_results() -> None:
    tasks = build_structure_ai_tasks(REQUEST, "M2", ("A", "B"))
    validation = validate_structure_ai_results(
        tasks,
        (
            _payload(tasks[0], ("E-1", "E-3"), reviewer="R-1"),
            _payload(tasks[1], ("E-1", "E-3"), reviewer="R-1"),
        ),
    )

    assert set(validation.invalid_ids) == {tasks[0].task_id, tasks[1].task_id}
