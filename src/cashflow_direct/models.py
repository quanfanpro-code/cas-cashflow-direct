from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceLocator:
    file_id: str
    sheet_name: str
    row_start: int
    row_end: int
    cell_range: str


@dataclass(frozen=True, slots=True)
class EvidenceProfile:
    full_voucher: bool
    matched_counterparty: bool
    has_flow_item: bool
    label_sides: frozenset[str]
    retained_side_values: frozenset[str]
    has_flow_amount: bool
    summary_only: bool
    split_duplication_risk: bool


@dataclass(frozen=True, slots=True)
class NormalizedEntry:
    entry_id: str
    source: SourceLocator
    voucher_key: str
    voucher_date: str
    voucher_no: str
    summary: str
    account_name: str
    counterpart_name: str
    debit_cent: int
    credit_cent: int
    flow_amount_cent: int
    original_flow_item: str
    label_side: str = "unknown"
    retained_side: str = "unknown"
    voucher_word: str = ""
    account_code: str = ""
    source_debit_cent: int | None = None
    source_credit_cent: int | None = None
    source_flow_amount_cent: int | None = None
    input_issues: tuple[str, ...] = ()
    original_account_name: str = ""
    original_counterpart_name: str = ""


@dataclass(frozen=True, slots=True)
class CashflowComponent:
    component_id: str
    voucher_key: str
    summary: str
    cash_delta_cent: int
    counterpart_accounts: tuple[str, ...] = ()
    original_item_text: str = ""
    source_keys: tuple[str, ...] = ()
    anomalies: tuple[str, ...] = ()
    evidence_strength: str = "strong"
    voucher_date: str = ""
    voucher_no: str = ""
    source_file_ids: tuple[str, ...] = ()
    original_counterpart_accounts: tuple[str, ...] = ()
    account_mapping_status: str = "confirmed"

    @property
    def original_flow_item(self) -> str:
        return self.original_item_text


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    component_id: str
    system_item_id: str
    system_item_name: str
    normal_direction: str
    matched_rule_id: str
    reason: str
    evidence_level: str
    excluded_conflict_rule_ids: tuple[str, ...] = ()
    decision_source: str = "system"
    resolved: bool = True
    excluded: bool = False
    evidence_score: int | None = 0
    evidence_sources: tuple[str, ...] = ()
    candidate_item_ids: tuple[str, ...] = ()
    summary_candidate_item_ids: tuple[str, ...] | None = None
    account_path_candidate_item_ids: tuple[str, ...] | None = None
    summary_preferred_item_id: str = ""
    account_path_preferred_item_id: str = ""
    fallback_source: str = ""
    fallback_step: str = ""
    exclusion_type: str = ""
    confirmed_adjustment_cent: int = 0
    original_item_state: str = ""
    summary_quality: int = 0
    account_path_quality: int = 0
    sources_independent: bool = False
    source_conflict: bool = False
    business_conflict: bool = False
    company_rule_conflict: bool = False
    vat_base_missing: bool = False
    net_item_facts_missing: bool = False
    individual_tax_fact_missing: bool = False
    direction_status: str = "compatible"
    decision_action: str = ""
    ai_review_policy: str = ""
    materiality_level: str = ""
    business_object: str = ""
    purpose: str = ""
    candidate_status: str = "available"
    original_standard_item_id: str = ""
    single_materiality_level: str = ""
    vat_base_component_id: str = ""
    vat_relation_status: str = ""

    @property
    def item_code(self) -> str:
        return self.system_item_id

    @property
    def item_name(self) -> str:
        return self.system_item_name

    @property
    def confidence(self) -> str:
        return self.evidence_level


@dataclass(frozen=True, slots=True)
class MaterialityAmounts:
    overall_cent: int
    performance_cent: int
    trivial_cent: int


@dataclass(frozen=True, slots=True)
class ReviewBatch:
    batch_id: str
    component_ids: tuple[str, ...]
    proposed_item_code: str
    alternative_item_codes: tuple[str, ...]
    worst_case_impact_cent: int
    reason: str
    baseline_statement_amount_cent: int = 0
    cash_delta_cent: int = 0
    status: str = "待确认"
    representative_summary: str = ""
    counterpart_group: str = ""
    source_locations: tuple[str, ...] = ()
    mandatory: bool = False
    baseline_item_code: str = ""
    follows_component_id: str = ""

    @property
    def alternative_item_code(self) -> str:
        return "、".join(self.alternative_item_codes)


@dataclass(frozen=True, slots=True)
class AITask:
    task_id: str
    component_id: str
    context: str
    original_item: str
    system_item_id: str
    rule_evidence: str
    candidate_item_ids: tuple[str, ...] = ()
    summary_candidate_item_ids: tuple[str, ...] | None = None
    account_path_candidate_item_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class UnresolvedDecision:
    component_id: str
    cash_delta_cent: int
    cash_direction: str
    original_item: str
    system_item_id: str
    review_status: str
    counterpart_group: str
    summary_pattern: str
    alternative_item_ids: tuple[str, ...]
    reason: str
    system_statement_amount_cent: int = 0
    source_locations: tuple[str, ...] = ()
    group_impact_cent: int = 0
    mandatory: bool = False
    baseline_item_code: str = ""
    follows_component_id: str = ""
    decision_action: str = "human_decision"
    system_candidate_signature: str = ""
    account_path_signature: str = ""
    summary_business_signature: str = ""
    evidence_status: str = ""
    forced_check_reason: str = ""


def validate_materiality_order(amounts: MaterialityAmounts) -> MaterialityAmounts:
    """校验明显微小、实际执行和整体重要性的严格递增关系。"""
    if not 0 < amounts.trivial_cent < amounts.performance_cent < amounts.overall_cent:
        raise ValueError("必须满足：明显微小错报临界值 < 实际执行的重要性 < 财务报表整体重要性")
    return amounts
