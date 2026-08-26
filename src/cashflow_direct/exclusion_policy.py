from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cashflow_direct.models import CashflowComponent, ClassificationDecision


ALLOWED_EXCLUSION_TYPES = frozenset(
    {
        "internal_transfer",
        "non_cash",
        "zero_amount",
        "cash_scope_excluded",
        "confirmed_duplicate",
        "confirmed_adjustment",
    }
)


@dataclass(frozen=True, slots=True)
class ExclusionAuthorization:
    authorized: bool
    exclusion_type: str
    confirmed_adjustment_cent: int = 0
    reason: str = ""


def _record_component_ids(records: object) -> set[str]:
    if not isinstance(records, (list, tuple)):
        return set()
    result: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        result.update(
            str(value)
            for value in record.get("component_ids", ())
            if str(value)
        )
        component_id = str(record.get("component_id", "")).strip()
        if component_id:
            result.add(component_id)
    return result


def authorize_exclusion(
    component: CashflowComponent,
    decision: ClassificationDecision,
    exclusion_type: str,
    state: Mapping[str, object],
) -> ExclusionAuthorization:
    """只允许有结构化事实支持的排除；自由文字不能代替事实。"""
    request = state.get("requested_exclusion", {})
    request = request if isinstance(request, Mapping) else {}
    if not exclusion_type:
        raise ValueError(
            "无法分类、非法输入、低金额、证据不足、候选不唯一或来源冲突都不是排除依据；"
            "必须选择现流项目或提供受控排除类型"
        )
    if exclusion_type not in ALLOWED_EXCLUSION_TYPES:
        raise ValueError(f"排除类型无效：{exclusion_type}")

    if exclusion_type == "zero_amount":
        if component.cash_delta_cent != 0:
            raise ValueError("零金额排除与现有事实不符：业务金额不为零")
    elif exclusion_type == "internal_transfer":
        known = _record_component_ids(state.get("internal_transfers", ()))
        if decision.matched_rule_id != "INTERNAL-TRANSFER" and component.component_id not in known:
            raise ValueError("内部划转排除缺少已确认的内部划转记录")
    elif exclusion_type == "non_cash":
        non_cash_fact = bool(
            decision.matched_rule_id == "PATH-NONCASH"
            or "non_cash" in component.anomalies
        )
        if not non_cash_fact:
            raise ValueError("非现金排除与现有业务事实不符")
    elif exclusion_type == "cash_scope_excluded":
        cash_scope = state.get("cash_scope", {})
        cash_scope = cash_scope if isinstance(cash_scope, Mapping) else {}
        excluded_keys = {str(value) for value in cash_scope.get("excluded_keys", ())}
        if not set(component.source_keys).intersection(excluded_keys):
            raise ValueError("现金范围排除缺少已确认的范围决定")
    elif exclusion_type == "confirmed_duplicate":
        known = {
            str(value)
            for value in state.get("confirmed_duplicate_component_ids", ())
        }
        known.update(_record_component_ids(state.get("duplicate_groups", ())))
        if component.component_id not in known:
            raise ValueError("重复排除缺少已确认的重复记录")
    elif exclusion_type == "confirmed_adjustment":
        required = ("adjustment_type", "basis", "operator", "adjustment_cent")
        if any(
            key not in request
            or request[key] is None
            or (isinstance(request[key], str) and not request[key].strip())
            for key in required
        ):
            raise ValueError("确认调整必须提供结构化调整类型、依据、操作者和金额")
        try:
            adjustment_cent = int(request["adjustment_cent"])
        except (TypeError, ValueError) as error:
            raise ValueError("确认调整必须提供结构化调整金额") from error
        if adjustment_cent != component.cash_delta_cent:
            raise ValueError("确认调整金额必须等于该业务现金变化金额")
        return ExclusionAuthorization(
            True,
            exclusion_type,
            confirmed_adjustment_cent=adjustment_cent,
            reason="已记录结构化确认调整，金额继续进入现金调整桥",
        )

    return ExclusionAuthorization(True, exclusion_type, reason="现有结构化事实支持排除")
