from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from cashflow_direct.money import stable_id


@dataclass(frozen=True, slots=True)
class StructureAITask:
    task_id: str
    voucher_key: str
    review_round: str
    candidate_entry_id_combinations: tuple[tuple[str, ...], ...]
    context: str


@dataclass(frozen=True, slots=True)
class StructureAIResult:
    task_id: str
    voucher_key: str
    review_round: str
    selected_entry_ids: tuple[str, ...]
    confidence: str
    reason: str
    reviewer_id: str
    model_id: str
    reviewed_at: str


@dataclass(frozen=True, slots=True)
class StructureAIValidation:
    valid_results: tuple[StructureAIResult, ...]
    missing_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    invalid_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class StructureResolution:
    status: str
    selected_entry_ids: tuple[str, ...] = ()
    basis_type: str = "existing_evidence"


def build_structure_ai_tasks(
    request: Mapping[str, object],
    materiality_level: str,
    rounds: Sequence[str],
) -> tuple[StructureAITask, ...]:
    voucher_key = str(request["voucher_key"])
    candidates = tuple(
        tuple(str(value) for value in combination)
        for combination in request["candidate_entry_id_combinations"]
    )
    details = tuple(str(value) for value in request.get("candidate_details", ()))
    context = json.dumps(
        {
            "凭证": voucher_key,
            "现金变化金额分": int(request["cash_delta_cent"]),
            "既有候选组合": candidates,
            "候选明细": details,
            "限制": (
                "只能选择既有候选组合；不得新增事实、改写金额或创造新组合；"
                "必须说明金额守恒检查和选择理由"
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return tuple(
        StructureAITask(
            stable_id("SAI", voucher_key, materiality_level, review_round),
            voucher_key,
            review_round,
            candidates,
            context,
        )
        for review_round in rounds
    )


def validate_structure_ai_results(
    expected: Sequence[StructureAITask],
    payloads: Sequence[Mapping[str, object]],
) -> StructureAIValidation:
    expected_by_id = {task.task_id: task for task in expected}
    counts = Counter(str(payload.get("task_id", "")) for payload in payloads)
    duplicate_ids = tuple(
        sorted(task_id for task_id, count in counts.items() if task_id and count > 1)
    )
    invalid: set[str] = set(duplicate_ids)
    valid: list[StructureAIResult] = []
    required = {
        "task_id",
        "voucher_key",
        "review_round",
        "selected_entry_ids",
        "confidence",
        "reason",
        "reviewer_id",
        "model_id",
        "reviewed_at",
    }
    for payload in payloads:
        task_id = str(payload.get("task_id", ""))
        task = expected_by_id.get(task_id)
        if task is None or counts[task_id] != 1 or not required.issubset(payload):
            if task_id:
                invalid.add(task_id)
            continue
        selected_raw = payload.get("selected_entry_ids")
        selected = (
            tuple(str(value) for value in selected_raw)
            if isinstance(selected_raw, (list, tuple))
            else ()
        )
        confidence = str(payload.get("confidence", ""))
        try:
            reviewed_at = datetime.fromisoformat(
                str(payload.get("reviewed_at", "")).replace("Z", "+00:00")
            )
            time_valid = reviewed_at.tzinfo is not None
        except ValueError:
            time_valid = False
        candidate_valid = selected in set(task.candidate_entry_id_combinations)
        is_valid = (
            str(payload.get("voucher_key", "")) == task.voucher_key
            and str(payload.get("review_round", "")) == task.review_round
            and confidence in {"high", "low", "conflict"}
            and (candidate_valid or (not selected and confidence != "high"))
            and (confidence != "high" or candidate_valid)
            and bool(str(payload.get("reason", "")).strip())
            and bool(str(payload.get("reviewer_id", "")).strip())
            and bool(str(payload.get("model_id", "")).strip())
            and time_valid
        )
        if not is_valid:
            invalid.add(task_id)
            continue
        valid.append(
            StructureAIResult(
                task_id,
                task.voucher_key,
                task.review_round,
                selected,
                confidence,
                str(payload["reason"]).strip(),
                str(payload["reviewer_id"]).strip(),
                str(payload["model_id"]).strip(),
                str(payload["reviewed_at"]),
            )
        )
    blind = [result for result in valid if result.review_round in {"A", "B", "C"}]
    reviewer_counts = Counter(result.reviewer_id for result in blind)
    repeated_reviewers = {
        reviewer for reviewer, count in reviewer_counts.items() if count > 1
    }
    if repeated_reviewers:
        invalid.update(
            result.task_id
            for result in blind
            if result.reviewer_id in repeated_reviewers
        )
    valid = [result for result in valid if result.task_id not in invalid]
    valid_ids = {result.task_id for result in valid}
    missing_ids = tuple(sorted(set(expected_by_id) - valid_ids))
    status = (
        "AI 已完成"
        if not missing_ids and not duplicate_ids and not invalid
        else "AI 未完成"
    )
    return StructureAIValidation(
        tuple(valid),
        missing_ids,
        duplicate_ids,
        tuple(sorted(invalid)),
        status,
    )


def resolve_structure_ai_request(
    request: Mapping[str, object],
    materiality_level: str,
    tasks: Sequence[StructureAITask],
    results: Sequence[StructureAIResult],
    failed_task_ids: set[str] | frozenset[str],
) -> StructureResolution:
    del request, failed_task_ids
    result_by_round = {result.review_round: result for result in results}
    task_rounds = {task.review_round for task in tasks}
    if materiality_level == "M3":
        return StructureResolution("needs_user")
    if materiality_level in {"M0", "M1"}:
        first = result_by_round.get("single")
        if first is not None and first.confidence == "high":
            return StructureResolution("selected", first.selected_entry_ids)
        if "second" not in task_rounds:
            return StructureResolution("needs_second")
        second = result_by_round.get("second")
        if second is None or second.confidence != "high":
            return StructureResolution("needs_user")
        if (
            first is not None
            and first.selected_entry_ids
            and first.selected_entry_ids != second.selected_entry_ids
        ):
            return StructureResolution("needs_user")
        return StructureResolution("selected", second.selected_entry_ids)
    if materiality_level == "M2":
        first = result_by_round.get("A")
        second = result_by_round.get("B")
        if (
            first is not None
            and second is not None
            and first.confidence == second.confidence == "high"
            and first.selected_entry_ids == second.selected_entry_ids
        ):
            return StructureResolution("selected", first.selected_entry_ids)
        if "C" not in task_rounds:
            return StructureResolution("needs_c")
        adjudicator = result_by_round.get("C")
        if adjudicator is not None and adjudicator.confidence == "high":
            return StructureResolution("selected", adjudicator.selected_entry_ids)
        return StructureResolution("needs_user")
    raise ValueError(f"未知业务组成重要性档位：{materiality_level}")
