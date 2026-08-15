from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string

from cashflow_direct.ai_review import (
    AIResult,
    build_adjudication_tasks,
    chunk_ai_tasks,
    merge_ai_results,
    resolve_automatic_decisions,
    select_ai_tasks,
)
from cashflow_direct.classification import classify_all, load_rule_pack
from cashflow_direct.components import (
    CashScope,
    build_cashflow_components,
    confirm_cash_scope as make_cash_scope,
    discover_cash_scope,
    flow_direction_source,
)
from cashflow_direct.duplicates import assign_duplicate_items, find_suspected_duplicates
from cashflow_direct.intake import register_inputs, validate_materiality
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    AITask,
    MaterialityAmounts,
    NormalizedEntry,
    SourceLocator,
    UnresolvedDecision,
)
from cashflow_direct.materiality import build_review_batches
from cashflow_direct.money import stable_id, statement_amount_cent, yuan_to_cent
from cashflow_direct.normalization import normalize_dataset, subtotal_exclusion_warning
from cashflow_direct.semantic_mapping import (
    DatasetMapping,
    MappingQuestion,
    infer_dataset_mappings,
)
from cashflow_direct.statement import (
    ExistingStatementResult,
    aggregate_statement,
    compare_statement,
    detect_statement_sheets,
    parse_existing_statement,
    reconcile_cash,
)
from cashflow_direct.storage import RunStore
from cashflow_direct.validation import (
    validate_classification,
    validate_final_output,
    validate_input_hashes,
    validate_statement,
)
from cashflow_direct.workbook_output import WorkbookModel, build_output_workbook
from cashflow_direct.workbook_structure import open_workbook_robust, scan_workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRACE_DIR_NAME = "计算留痕数据"
STATE_FILE_NAME = "运行状态.json"
DB_FILE_NAME = "计算留痕.sqlite3"
@dataclass(frozen=True, slots=True)
class PreflightResult:
    run_id: str
    run_dir: Path
    status: str
    recommended_cash_decisions: dict[str, str]
    mapping_question_count: int
    source_entry_count: int


@dataclass(frozen=True, slots=True)
class StageResult:
    run_id: str
    run_dir: Path
    stage: str
    status: str
    next_action: str


@dataclass(frozen=True, slots=True)
class ClassificationStageResult:
    run_id: str
    run_dir: Path
    component_count: int
    component_hash: str
    source_entry_count: int
    cash_delta_cent: int
    ai_tasks_missing: int
    status: str


@dataclass(frozen=True, slots=True)
class AIStageResult:
    run_id: str
    run_dir: Path
    valid_count: int
    missing_count: int
    status: str


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    run_id: str
    run_dir: Path
    workbook_path: Path
    overall_status: str


def _trace_dir(run_dir: Path) -> Path:
    return Path(run_dir) / TRACE_DIR_NAME


def _state_path(run_dir: Path) -> Path:
    return _trace_dir(run_dir) / STATE_FILE_NAME


def _store(run_dir: Path) -> RunStore:
    return RunStore(_trace_dir(run_dir) / DB_FILE_NAME)


def _load_state(run_dir: Path) -> dict[str, object]:
    path = _state_path(run_dir)
    if not path.is_file():
        raise RuntimeError("未找到运行状态，请先执行资料预检")
    with path.open("r", encoding="utf-8-sig") as source:
        return json.load(source)


def _save_state(run_dir: Path, state: Mapping[str, object]) -> None:
    target = _state_path(run_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="\n") as output:
        json.dump(state, output, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(target)


def _write_trace_jsonl(run_dir: Path, filename: str, records: Sequence[object]) -> None:
    target = _trace_dir(run_dir) / filename
    with target.open("w", encoding="utf-8-sig", newline="\n") as output:
        for record in records:
            payload = asdict(record) if hasattr(record, "__dataclass_fields__") else record
            output.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _entry_from_dict(payload: Mapping[str, object]) -> NormalizedEntry:
    data = dict(payload)
    data["source"] = SourceLocator(**data["source"])
    return NormalizedEntry(**data)


def _component_from_dict(payload: Mapping[str, object]) -> CashflowComponent:
    data = dict(payload)
    for key in ("counterpart_accounts", "source_keys", "anomalies", "source_file_ids"):
        data[key] = tuple(data.get(key, ()))
    return CashflowComponent(**data)


def _decision_from_dict(payload: Mapping[str, object]) -> ClassificationDecision:
    data = dict(payload)
    data["excluded_conflict_rule_ids"] = tuple(data.get("excluded_conflict_rule_ids", ()))
    return ClassificationDecision(**data)


def _scope_from_dict(payload: Mapping[str, object]) -> CashScope:
    return CashScope(
        frozenset(payload["included_keys"]),
        frozenset(payload["excluded_keys"]),
        tuple((item[0], tuple(item[1])) for item in payload["account_names_by_key"]),
        str(payload["scope_hash"]),
    )


def _materiality_from_state(state: Mapping[str, object]) -> MaterialityAmounts:
    return MaterialityAmounts(**state["materiality"])


def _review_text_pattern(text: str) -> str:
    without_dates = re.sub(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", "", text)
    without_numbers = re.sub(r"\d[\d,，.]*", "", without_dates)
    normalized = re.sub(r"[\s，。；：、,.!！?？（）()《》\[\]【】_-]+", "", without_numbers)
    return normalized.lower() or "空白"


def _persist_ai_results(
    run_dir: Path,
    stage_name: str,
    results: Sequence[AIResult],
) -> None:
    with _store(run_dir).stage(f"ai_result_{stage_name}") as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO ai_result(record_id, payload_json) VALUES (?, ?)",
            (
                (
                    f"{stage_name}:{item.task_id}",
                    json.dumps({"阶段": stage_name, **asdict(item)}, ensure_ascii=False),
                )
                for item in results
            ),
        )


def _assert_inputs_unchanged(state: Mapping[str, object]) -> None:
    result = validate_input_hashes(state["files"])
    if not result.valid:
        raise RuntimeError("输入文件已被修改，请建立新运行目录后重新处理")


def _read_cash_balances(path: Path) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    workbook = open_workbook_robust(path)
    try:
        for sheet in workbook.worksheets:
            priority = 2 if "余额" in sheet.title else 1
            unit_text = "|".join(
                str(value)
                for row in sheet.iter_rows(min_row=1, max_row=20, max_col=10, values_only=True)
                for value in row
                if value not in (None, "")
            )
            multiplier = 10_000 if "万元" in unit_text else 1
            for row in sheet.iter_rows(min_row=1, max_row=100, max_col=10, values_only=True):
                for index, value in enumerate(row[:-1]):
                    if not isinstance(value, str) or row[index + 1] in (None, ""):
                        continue
                    key = None
                    if "期初现金及现金等价物余额" in value:
                        key = "opening_cent"
                    elif "期末现金及现金等价物余额" in value:
                        key = "closing_cent"
                    elif "汇率变动对现金及现金等价物的影响" in value:
                        key = "fx_cent"
                    if key is not None:
                        try:
                            amount = yuan_to_cent(row[index + 1]) * multiplier
                            if key not in found or priority > found[key][0]:
                                found[key] = (priority, amount)
                        except ValueError:
                            continue
    finally:
        workbook.close()
    return found


def _result_from_classification(state: Mapping[str, object], run_dir: Path) -> ClassificationStageResult:
    data = state["classification_summary"]
    return ClassificationStageResult(
        str(state["run_id"]),
        Path(run_dir),
        int(data["component_count"]),
        str(data["component_hash"]),
        int(data["source_entry_count"]),
        int(data["cash_delta_cent"]),
        int(data["ai_tasks_missing"]),
        str(data["status"]),
    )


def run_preflight(
    inputs: Sequence[Path],
    materiality: tuple[object, object, object],
    output_parent: Path | None = None,
    statement_path: Path | None = None,
) -> PreflightResult:
    amounts = validate_materiality(*materiality)
    intake = register_inputs(inputs, output_parent=output_parent)
    run_dir = intake.run_dir
    trace_dir = _trace_dir(run_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    store = _store(run_dir)
    store.initialize()
    rules = load_rule_pack(PROJECT_ROOT)

    entries: list[NormalizedEntry] = []
    mappings: list[dict[str, object]] = []
    questions: list[dict[str, object]] = []
    normalization_issues: list[dict[str, object]] = []
    sheet_structures: list[dict[str, object]] = []
    existing_statement_path: str | None = None
    designated_target = statement_path.resolve() if statement_path is not None else None
    designated_hit = False
    balance_candidates: dict[str, tuple[int, int]] = {}
    statement_candidates: list[dict[str, object]] = []
    for registered in intake.active_files:
        for key, candidate in _read_cash_balances(registered.path).items():
            if key not in balance_candidates or candidate[0] > balance_candidates[key][0]:
                balance_candidates[key] = candidate
        is_designated = designated_target is not None and registered.path.resolve() == designated_target
        statement_by_sheet = detect_statement_sheets(registered.path, rules)
        statement_hits = {
            name: result
            for name, result in statement_by_sheet.items()
            if isinstance(result, ExistingStatementResult)
        }
        if is_designated:
            designated_hit = True
            if not statement_hits:
                question = next(
                    (item for item in statement_by_sheet.values() if isinstance(item, MappingQuestion)),
                    None,
                )
                raise ValueError(
                    f"指定的正表文件识别失败：{registered.path.name}（{question.sample_values[0] if question is not None else '未找到项目列或可用金额列'}）"
                )
            if len(statement_hits) > 1:
                raise ValueError(
                    f"指定的正表文件识别到多个现金流量表工作表：{registered.path.name}"
                )
            existing_statement_path = str(registered.path)
            exclude_sheets = frozenset(statement_hits)
        else:
            exclude_sheets = frozenset()
        snapshot = scan_workbook(registered.path)
        sheet_structures.extend(
            {
                "file_id": registered.file_id,
                "sheet": sheet.name,
                "sample_rows": len(sheet.rows),
                "merged_ranges": sheet.merged_ranges,
                "hidden_columns": sheet.hidden_columns,
            }
            for sheet in snapshot.sheets
        )
        detected = infer_dataset_mappings(snapshot, exclude_sheets=exclude_sheets)
        mapped_dataset_sheets = {
            mapping.sheet_name for mapping in detected if isinstance(mapping, DatasetMapping)
        }
        for mapping in detected:
            if isinstance(mapping, DatasetMapping):
                normalized = normalize_dataset(registered.path, registered.file_id, mapping)
                entries.extend(normalized.entries)
                mappings.append(
                    {
                        "file_id": registered.file_id,
                        "file": registered.path.name,
                        "sheet": mapping.sheet_name,
                        "header_rows": f"{mapping.header_row_start}:{mapping.header_row_end}",
                        "roles": {role: column.column_letter for role, column in mapping.role_to_column.items()},
                    }
                )
                normalization_issues.extend(
                    {
                        "file_id": registered.file_id,
                        "file": registered.path.name,
                        "sheet": issue.source.sheet_name,
                        "cell": issue.source.cell_range,
                        "kind": "错误" if hasattr(issue, "message") else "排除",
                        "message": getattr(issue, "message", getattr(issue, "discard_reason", "")),
                    }
                    for issue in (*normalized.errors, *normalized.exclusions)
                )
                warning = subtotal_exclusion_warning(normalized)
                if warning is not None:
                    normalization_issues.append(
                        {
                            "file_id": registered.file_id,
                            "file": registered.path.name,
                            "sheet": mapping.sheet_name,
                            "cell": "",
                            **warning,
                        }
                    )
            else:
                questions.append(
                    {
                        "file_id": registered.file_id,
                        "file": registered.path.name,
                        "sheet": mapping.sheet_name,
                        "role": mapping.role,
                        "recommended": mapping.recommended.column_letter,
                    }
                )
        if not is_designated:
            auto_hits = {
                name: result
                for name, result in statement_hits.items()
                if name not in mapped_dataset_sheets
            }
            if len(auto_hits) == 1:
                hit = next(iter(auto_hits.values()))
                questions.append(
                    {
                        "file_id": registered.file_id,
                        "file": registered.path.name,
                        "sheet": hit.sheet_name,
                        "role": "statement_sheet",
                        "recommended": hit.sheet_name,
                        "message": f"检测到疑似正表工作表《{hit.sheet_name}》，请确认是否作为客户现有正表核对",
                        "kind": "statement",
                    }
                )
                statement_candidates.append(
                    {
                        "file_id": registered.file_id,
                        "file": registered.path.name,
                        "sheet": hit.sheet_name,
                    }
                )
            elif len(auto_hits) > 1:
                questions.append(
                    {
                        "file_id": registered.file_id,
                        "file": registered.path.name,
                        "sheet": "",
                        "role": "statement_sheet",
                        "recommended": "",
                        "message": f"识别到多个现金流量表工作表：{'、'.join(auto_hits)}",
                        "kind": "statement",
                    }
                )
    if designated_target is not None and not designated_hit:
        raise ValueError(f"指定的正表文件不在已选输入中：{statement_path}")

    proposal = discover_cash_scope(entries)
    recommended = {
        candidate.account_key: candidate.system_suggestion
        for candidate in proposal.candidates
        if candidate.system_suggestion in {"include", "exclude"}
    }
    run_id = stable_id("RUN", run_dir.name, *(item.sha256 for item in intake.files))
    state: dict[str, object] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "stage": "preflight",
        "files": [
            {
                "file_id": item.file_id,
                "path": str(item.path),
                "sha256": item.sha256,
                "duplicate_of": item.duplicate_of,
                "is_macro_workbook": item.is_macro_workbook,
            }
            for item in intake.files
        ],
        "materiality": asdict(amounts),
        "entries": [asdict(entry) for entry in entries],
        "mappings": mappings,
        "mapping_questions": questions,
        "cash_scope_proposal": asdict(proposal),
        "recommended_cash_decisions": recommended,
        "existing_statement_path": existing_statement_path,
        "statement_candidates": statement_candidates,
        "statement_confirmations": {},
        "cash_balances": {key: value for key, (_, value) in balance_candidates.items()},
        "normalization_issues": normalization_issues,
    }
    _assert_inputs_unchanged(state)
    with store.stage("preflight") as connection:
        connection.execute(
            "INSERT INTO run_manifest(record_id, payload_json) VALUES (?, ?)",
            (run_id, json.dumps({"materiality": asdict(amounts)}, ensure_ascii=False)),
        )
        connection.executemany(
            "INSERT INTO source_entry(record_id, payload_json) VALUES (?, ?)",
            ((entry.entry_id, json.dumps(asdict(entry), ensure_ascii=False)) for entry in entries),
        )
        connection.executemany(
            "INSERT INTO source_file(record_id, payload_json) VALUES (?, ?)",
            (
                (item.file_id, json.dumps(asdict(item), ensure_ascii=False, default=str))
                for item in intake.files
            ),
        )
        connection.executemany(
            "INSERT INTO sheet_structure(record_id, payload_json) VALUES (?, ?)",
            (
                (
                    stable_id("SHT", item["file_id"], item["sheet"]),
                    json.dumps(item, ensure_ascii=False),
                )
                for item in sheet_structures
            ),
        )
        connection.executemany(
            "INSERT INTO field_mapping(record_id, payload_json) VALUES (?, ?)",
            (
                (
                    stable_id("MAP", item["file_id"], item["sheet"]),
                    json.dumps(item, ensure_ascii=False),
                )
                for item in mappings
            ),
        )
    _save_state(run_dir, state)
    status = "waiting_mapping" if questions else "waiting_cash_scope"
    return PreflightResult(run_id, run_dir, status, recommended, len(questions), len(entries))


def confirm_mapping(run_dir: Path, decisions: Mapping[str, str]) -> StageResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    confirmations = dict(state.get("mapping_confirmations", {}))
    confirmations.update(decisions)
    files_by_id = {str(item["file_id"]): Path(str(item["path"])) for item in state["files"]}

    # ---- 疑似正表确认（取值 use / ignore）----
    statement_candidates = list(state.get("statement_candidates", ()))
    statement_confirmations = dict(state.get("statement_confirmations", {}))
    for candidate in statement_candidates:
        key = f"{candidate['file_id']}:statement:{candidate['sheet']}"
        choice = confirmations.get(key)
        if choice not in {"use", "ignore"}:
            raise RuntimeError(f"等待疑似正表确认：{key}（取值 use 或 ignore）")
        statement_confirmations[key] = choice
    candidate_keys = {
        f"{c['file_id']}:statement:{c['sheet']}" for c in statement_candidates
    }
    ambiguous_statements = [
        question for question in state.get("mapping_questions", ())
        if question.get("kind") == "statement"
        and f"{question['file_id']}:statement:{question.get('sheet', '')}" not in candidate_keys
    ]
    if ambiguous_statements:
        messages = "；".join(
            str(question.get("message") or "存在多个现金流量表工作表")
            for question in ambiguous_statements
        )
        raise RuntimeError(f"{messages}；请合并工作表或明确目标正表后重新预检")

    # ---- 字段映射确认 ----
    pending_questions = [
        question for question in state.get("mapping_questions", ())
        if question.get("kind") != "statement"
    ]
    if not pending_questions and not statement_candidates:
        state["mapping_confirmations"] = confirmations
        _save_state(run_dir, state)
        return StageResult(str(state["run_id"]), Path(run_dir), "mapping", "completed", "确认现金范围")

    pending_by_file: dict[str, list[dict[str, object]]] = {}
    for question in pending_questions:
        pending_by_file.setdefault(str(question["file_id"]), []).append(question)

    new_questions: list[dict[str, object]] = []
    new_entries: list[NormalizedEntry] = []
    new_mappings: list[dict[str, object]] = []
    new_issues: list[dict[str, object]] = []
    mapped_sheets = {
        (str(item["file_id"]), str(item["sheet"])) for item in state.get("mappings", ())
    }
    for file_id, questions in pending_by_file.items():
        overrides_by_sheet: dict[str, dict[str, int]] = {}
        for question in questions:
            role = str(question["role"])
            sheet_name = str(question.get("sheet", ""))
            full_key = f"{file_id}:{sheet_name}:{role}"
            legacy_key = f"{file_id}:{role}"
            choice = confirmations.get(full_key, confirmations.get(legacy_key))
            if not choice:
                raise ValueError(f"等待字段确认：缺少 {full_key}")
            try:
                overrides_by_sheet.setdefault(sheet_name, {})[role] = column_index_from_string(
                    str(choice).strip().upper()
                )
            except ValueError as error:
                raise ValueError(f"字段 {full_key} 的确认值必须是 Excel 列字母") from error
        path = files_by_id[file_id]
        for mapping in infer_dataset_mappings(scan_workbook(path), overrides_by_sheet):
            if isinstance(mapping, MappingQuestion):
                new_questions.append(
                    {
                        "file_id": file_id,
                        "file": path.name,
                        "sheet": mapping.sheet_name,
                        "role": mapping.role,
                        "recommended": mapping.recommended.column_letter,
                    }
                )
                continue
            if (file_id, mapping.sheet_name) in mapped_sheets:
                continue
            normalized = normalize_dataset(path, file_id, mapping)
            new_entries.extend(normalized.entries)
            new_mappings.append(
                {
                    "file_id": file_id,
                    "file": path.name,
                    "sheet": mapping.sheet_name,
                    "header_rows": f"{mapping.header_row_start}:{mapping.header_row_end}",
                    "roles": {role: column.column_letter for role, column in mapping.role_to_column.items()},
                }
            )
            new_issues.extend(
                {
                    "file_id": file_id,
                    "file": path.name,
                    "sheet": issue.source.sheet_name,
                    "cell": issue.source.cell_range,
                    "kind": "错误" if hasattr(issue, "message") else "排除",
                    "message": getattr(issue, "message", getattr(issue, "discard_reason", "")),
                }
                for issue in (*normalized.errors, *normalized.exclusions)
            )

    entries = [_entry_from_dict(item) for item in state["entries"]] + new_entries
    proposal = discover_cash_scope(entries)
    state["entries"] = [asdict(item) for item in entries]
    state["mappings"] = [*state.get("mappings", ()), *new_mappings]
    state["normalization_issues"] = [*state.get("normalization_issues", ()), *new_issues]
    state["mapping_questions"] = new_questions
    state["mapping_confirmations"] = confirmations
    state["statement_confirmations"] = statement_confirmations
    use_paths = {
        str(files_by_id[str(candidate["file_id"])])
        for candidate in statement_candidates
        if statement_confirmations.get(f"{candidate['file_id']}:statement:{candidate['sheet']}") == "use"
    }
    if len(use_paths) > 1:
        raise RuntimeError("多个文件被确认为客户现有正表，请只保留一个")
    state["existing_statement_path"] = next(iter(use_paths), None)
    state["cash_scope_proposal"] = asdict(proposal)
    state["recommended_cash_decisions"] = {
        candidate.account_key: candidate.system_suggestion
        for candidate in proposal.candidates
        if candidate.system_suggestion in {"include", "exclude"}
    }
    state["stage"] = "waiting_mapping" if new_questions else "waiting_cash_scope"
    if new_entries:
        with _store(run_dir).stage("mapping") as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO source_entry(record_id, payload_json) VALUES (?, ?)",
                ((entry.entry_id, json.dumps(asdict(entry), ensure_ascii=False)) for entry in new_entries),
            )
            connection.executemany(
                "INSERT OR REPLACE INTO field_mapping(record_id, payload_json) VALUES (?, ?)",
                (
                    (
                        stable_id("MAP", item["file_id"], item["sheet"]),
                        json.dumps(item, ensure_ascii=False),
                    )
                    for item in new_mappings
                ),
            )
    _save_state(run_dir, state)
    return StageResult(
        str(state["run_id"]),
        Path(run_dir),
        "mapping",
        "waiting" if new_questions else "completed",
        "继续确认剩余字段" if new_questions else "确认现金范围",
    )


def confirm_cash_scope(
    run_dir: Path,
    decisions: Mapping[str, str],
) -> StageResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    if state.get("mapping_questions"):
        raise RuntimeError("字段映射仍有待确认项，请先完成字段确认")
    entries = tuple(_entry_from_dict(item) for item in state["entries"])
    scope = make_cash_scope(discover_cash_scope(entries), decisions)
    store = _store(run_dir)
    with store.stage("cash_scope") as connection:
        connection.execute(
            "INSERT OR REPLACE INTO cash_scope(record_id, payload_json) VALUES (?, ?)",
            (scope.scope_hash, json.dumps(asdict(scope), ensure_ascii=False, default=list)),
        )
    state["cash_scope"] = {
        "included_keys": sorted(scope.included_keys),
        "excluded_keys": sorted(scope.excluded_keys),
        "account_names_by_key": scope.account_names_by_key,
        "scope_hash": scope.scope_hash,
    }
    state["stage"] = "cash_scope_confirmed"
    _save_state(run_dir, state)
    return StageResult(str(state["run_id"]), Path(run_dir), "cash_scope", "completed", "执行自动分类")


def supplement_cash_balances(
    run_dir: Path,
    opening: object,
    closing: object,
    fx: object,
    source_note: str,
) -> StageResult:
    """补充缺失的现金余额和汇率影响，保留来源说明且不做倒挤。"""
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    if not source_note.strip():
        raise ValueError("补充现金余额时必须填写资料来源说明")
    state["cash_balances"] = {
        "opening_cent": yuan_to_cent(opening),
        "closing_cent": yuan_to_cent(closing),
        "fx_cent": yuan_to_cent(fx),
    }
    state["cash_balance_source"] = source_note.strip()
    state.pop("overall_status", None)
    state.pop("workbook_path", None)
    _save_state(run_dir, state)
    return StageResult(
        str(state["run_id"]), Path(run_dir), "cash_balances", "completed", "生成最终工作簿"
    )


def run_classification(run_dir: Path) -> ClassificationStageResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    if "classification_summary" in state:
        return _result_from_classification(state, run_dir)
    if "cash_scope" not in state:
        raise RuntimeError("请确认现金范围后继续")
    entries = tuple(_entry_from_dict(item) for item in state["entries"])
    scope = _scope_from_dict(state["cash_scope"])
    build = build_cashflow_components(entries, scope)
    entry_by_id = {entry.entry_id: entry for entry in entries}
    components = tuple(
        replace(
            component,
            voucher_date=next((entry_by_id[key].voucher_date for key in component.source_keys if key in entry_by_id), ""),
            voucher_no=next((entry_by_id[key].voucher_no for key in component.source_keys if key in entry_by_id), ""),
            source_file_ids=tuple(
                sorted({entry_by_id[key].source.file_id for key in component.source_keys if key in entry_by_id})
            ),
        )
        for component in build.components
    )
    rules = load_rule_pack(PROJECT_ROOT)
    decisions = classify_all(components, rules)
    checked = validate_classification(components, decisions)
    if not checked.valid:
        raise RuntimeError("自动分类不变量失败：" + "；".join(checked.errors))
    tasks = select_ai_tasks(components, decisions, _materiality_from_state(state))
    serialized_components = [asdict(item) for item in components]
    serialized_decisions = [asdict(item) for item in decisions]
    digest_source = json.dumps(serialized_components, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    component_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    summary = {
        "component_count": len(components),
        "component_hash": component_hash,
        "source_entry_count": len(entries),
        "cash_delta_cent": sum(item.cash_delta_cent for item in components),
        "ai_tasks_missing": len(tasks),
        "status": "waiting_ai" if tasks else "classification_completed",
    }
    store = _store(run_dir)
    with store.stage("classification") as connection:
        vouchers: dict[str, list[str]] = {}
        for entry in entries:
            vouchers.setdefault(entry.voucher_key, []).append(entry.entry_id)
        connection.executemany(
            "INSERT OR REPLACE INTO voucher(record_id, payload_json) VALUES (?, ?)",
            (
                (
                    voucher_key,
                    json.dumps({"entry_ids": entry_ids}, ensure_ascii=False),
                )
                for voucher_key, entry_ids in vouchers.items()
            ),
        )
        connection.executemany(
            "INSERT INTO cashflow_component(record_id, payload_json) VALUES (?, ?)",
            ((item.component_id, json.dumps(asdict(item), ensure_ascii=False)) for item in components),
        )
        connection.executemany(
            "INSERT INTO classification_decision(record_id, payload_json) VALUES (?, ?)",
            ((item.component_id, json.dumps(asdict(item), ensure_ascii=False)) for item in decisions),
        )
        connection.executemany(
            "INSERT INTO ai_task(record_id, payload_json) VALUES (?, ?)",
            ((item.task_id, json.dumps(asdict(item), ensure_ascii=False)) for item in tasks),
        )
        connection.executemany(
            "INSERT INTO internal_transfer(record_id, payload_json) VALUES (?, ?)",
            (
                (
                    stable_id("ITR", item.voucher_key, item.entry_id, item.matched_cent),
                    json.dumps(asdict(item), ensure_ascii=False),
                )
                for item in build.excluded_internal_transfers
            ),
        )
    state["components"] = serialized_components
    state["decisions"] = serialized_decisions
    state["ai_tasks"] = [asdict(item) for item in tasks]
    state["internal_transfers"] = [asdict(item) for item in build.excluded_internal_transfers]
    state["classification_summary"] = summary
    state["stage"] = summary["status"]
    for batch_number, batch in enumerate(chunk_ai_tasks(tasks), 1):
        _write_trace_jsonl(run_dir, f"AI复核请求_第{batch_number:02d}批.jsonl", batch)
    _write_trace_jsonl(run_dir, "内部划转排除.jsonl", build.excluded_internal_transfers)
    _save_state(run_dir, state)
    return _result_from_classification(state, run_dir)


def import_ai_results(run_dir: Path, result_path: Path) -> AIStageResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    payloads = []
    with Path(result_path).open("r", encoding="utf-8-sig") as source:
        for line in source:
            if line.strip():
                payloads.append(json.loads(line))
    leaf_ids = {
        item.item_id for item in load_rule_pack(PROJECT_ROOT).statement_items if item.is_leaf
    }
    adjudication_task_ids = {
        str(item["task_id"]) for item in state.get("adjudication_tasks", ())
    }
    payload_task_ids = {str(item.get("task_id", "")) for item in payloads if item.get("task_id")}
    is_adjudication_import = bool(
        adjudication_task_ids
        and (
            state.get("stage") == "waiting_adjudication"
            or (payload_task_ids and payload_task_ids <= adjudication_task_ids)
        )
    )
    if is_adjudication_import:
        tasks = tuple(
            AITask(
                task_id=str(item["task_id"]),
                component_id=str(item["component_id"]),
                context=str(item["context"]),
                original_item="",
                system_item_id=str(item["system_item_id"]),
                rule_evidence=f"AI 首次分类：{item['ai_item_id']}",
            )
            for item in state["adjudication_tasks"]
        )
        prior_results = tuple(
            AIResult(**item)
            for item in state.get("adjudication_validation", {}).get("valid_results", ())
        )
        validation = merge_ai_results(tasks, prior_results, payloads, leaf_ids)
        state["adjudication_validation"] = {
            "valid_results": [asdict(item) for item in validation.valid_results],
            "missing_ids": validation.missing_ids,
            "duplicate_ids": validation.duplicate_ids,
            "invalid_ids": validation.invalid_ids,
            "status": validation.status,
        }
        _persist_ai_results(run_dir, "裁决", validation.valid_results)
        _write_trace_jsonl(run_dir, "AI裁决结果.jsonl", validation.valid_results)
        state["classification_summary"]["ai_tasks_missing"] = len(validation.missing_ids)
        if validation.status == "AI 已完成":
            system_decisions = tuple(_decision_from_dict(item) for item in state["system_decisions"])
            ai_results = tuple(AIResult(**item) for item in state["ai_validation"]["valid_results"])
            resolved = resolve_automatic_decisions(
                system_decisions, ai_results, validation.valid_results
            )
            item_by_id = load_rule_pack(PROJECT_ROOT).item_by_id
            resolved = tuple(
                replace(
                    decision,
                    system_item_name=item_by_id[decision.system_item_id].name,
                    normal_direction=item_by_id[decision.system_item_id].normal_direction,
                )
                if not decision.excluded
                else decision
                for decision in resolved
            )
            state["decisions"] = [asdict(item) for item in resolved]
            state["stage"] = "ai_completed"
        else:
            state["stage"] = "waiting_adjudication"
        _save_state(run_dir, state)
        return AIStageResult(
            str(state["run_id"]),
            Path(run_dir),
            len(validation.valid_results),
            len(validation.missing_ids),
            validation.status,
        )

    tasks = tuple(AITask(**item) for item in state.get("ai_tasks", ()))
    prior_results = tuple(
        AIResult(**item) for item in state.get("ai_validation", {}).get("valid_results", ())
    )
    validation = merge_ai_results(tasks, prior_results, payloads, leaf_ids)
    state["ai_validation"] = {
        "valid_results": [asdict(item) for item in validation.valid_results],
        "missing_ids": validation.missing_ids,
        "duplicate_ids": validation.duplicate_ids,
        "invalid_ids": validation.invalid_ids,
        "status": validation.status,
    }
    _persist_ai_results(run_dir, "首次复核", validation.valid_results)
    _write_trace_jsonl(run_dir, "AI复核结果.jsonl", validation.valid_results)
    if validation.status == "AI 已完成":
        system_decisions = tuple(_decision_from_dict(item) for item in state["decisions"])
        adjudication_tasks = build_adjudication_tasks(
            system_decisions,
            validation.valid_results,
            tasks,
        )
        if adjudication_tasks:
            state["system_decisions"] = [asdict(item) for item in system_decisions]
            state["adjudication_tasks"] = [asdict(item) for item in adjudication_tasks]
            for batch_number, batch in enumerate(chunk_ai_tasks(adjudication_tasks), 1):
                _write_trace_jsonl(run_dir, f"AI裁决请求_第{batch_number:02d}批.jsonl", batch)
            state["classification_summary"]["ai_tasks_missing"] = len(adjudication_tasks)
            state["stage"] = "waiting_adjudication"
            status = "AI 待裁决"
            missing_count = len(adjudication_tasks)
        else:
            state["classification_summary"]["ai_tasks_missing"] = 0
            state["stage"] = "ai_completed"
            status = validation.status
            missing_count = 0
    else:
        state["classification_summary"]["ai_tasks_missing"] = len(validation.missing_ids)
        state["stage"] = "waiting_ai"
        status = validation.status
        missing_count = len(validation.missing_ids)
    _save_state(run_dir, state)
    return AIStageResult(
        str(state["run_id"]), Path(run_dir), len(validation.valid_results), missing_count, status
    )


def finalize_run(run_dir: Path) -> FinalizeResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    if state.get("overall_status") and state.get("workbook_path"):
        existing_output = Path(str(state["workbook_path"]))
        if existing_output.is_file():
            try:
                workbook = load_workbook(existing_output, read_only=True, data_only=False)
                workbook.close()
                return FinalizeResult(
                    str(state["run_id"]),
                    Path(run_dir),
                    existing_output,
                    str(state["overall_status"]),
                )
            except Exception as error:
                state["recovery_note"] = f"原结果文件无法打开，已改为重建：{error}"
    if "classification_summary" not in state:
        raise RuntimeError("请先完成自动分类")
    if int(state["classification_summary"]["ai_tasks_missing"]) > 0:
        raise RuntimeError("AI 复核尚未逐编号完成，不能生成最终结果")

    components = tuple(_component_from_dict(item) for item in state["components"])
    decisions = tuple(_decision_from_dict(item) for item in state["decisions"])
    entry_by_id = {
        entry.entry_id: entry
        for entry in (_entry_from_dict(item) for item in state["entries"])
    }
    rules = load_rule_pack(PROJECT_ROOT)
    comparison = None
    existing = None
    existing_path = state.get("existing_statement_path")
    if existing_path:
        existing = parse_existing_statement(Path(str(existing_path)), rules)
        if isinstance(existing, MappingQuestion):
            raise RuntimeError("客户现有正表仍有无法映射的项目，请先确认")

    balances = state.get("cash_balances", {})
    statement = aggregate_statement(
        components,
        decisions,
        rules,
        opening_cent=balances.get("opening_cent"),
        fx_cent=balances.get("fx_cent"),
        prior_values=None if existing is None else existing.prior_values,
    )
    statement_check = validate_statement(statement)
    if not statement_check.valid:
        raise RuntimeError("正表金额勾稽失败：" + "；".join(statement_check.errors))
    if existing is not None:
        comparison = compare_statement(existing, statement)
    reconciliation = reconcile_cash(
        statement,
        balances.get("opening_cent"),
        balances.get("closing_cent"),
        balances.get("fx_cent"),
    )
    duplicate_groups = assign_duplicate_items(
        find_suspected_duplicates(
            components, _materiality_from_state(state).performance_cent
        ),
        decisions,
    )
    component_by_id = {item.component_id: item for item in components}
    file_name_by_id = {
        str(item["file_id"]): Path(str(item["path"])).name for item in state["files"]
    }
    adjudication_by_component = {
        str(item["component_id"]): item for item in state.get("adjudication_tasks", ())
    }
    adjudication_result_by_component = {
        str(item["component_id"]): item
        for item in state.get("adjudication_validation", {}).get("valid_results", ())
    }
    unresolved = tuple(
        UnresolvedDecision(
            component_id=decision.component_id,
            cash_delta_cent=component_by_id[decision.component_id].cash_delta_cent,
            cash_direction=(
                "inflow" if component_by_id[decision.component_id].cash_delta_cent > 0 else "outflow"
            ),
            original_item=component_by_id[decision.component_id].original_item_text,
            system_item_id=decision.system_item_id,
            adjudication_status="AI 裁决证据不足",
            counterpart_group=_review_text_pattern(
                "、".join(component_by_id[decision.component_id].counterpart_accounts)
            ),
            summary_pattern=_review_text_pattern(
                component_by_id[decision.component_id].summary
            ),
            alternative_item_ids=tuple(
                dict.fromkeys(
                    item
                    for item in (
                        str(adjudication_by_component.get(decision.component_id, {}).get("system_item_id", "")),
                        str(adjudication_by_component.get(decision.component_id, {}).get("ai_item_id", "")),
                        str(adjudication_result_by_component.get(decision.component_id, {}).get("item_id", "")),
                    )
                    if item and item != decision.system_item_id
                )
            ),
            reason=decision.reason,
            system_statement_amount_cent=statement_amount_cent(
                component_by_id[decision.component_id].cash_delta_cent,
                rules.item_by_id[decision.system_item_id].normal_direction,
            ),
            source_locations=tuple(
                dict.fromkeys(
                    f"{file_name_by_id.get(entry_by_id[key].source.file_id, entry_by_id[key].source.file_id)}|{entry_by_id[key].source.sheet_name}|{entry_by_id[key].source.cell_range}"
                    for key in component_by_id[decision.component_id].source_keys
                    if key in entry_by_id
                )
            ),
        )
        for decision in decisions
        if not decision.resolved and not decision.excluded
    )
    review_batches = build_review_batches(
        unresolved, _materiality_from_state(state).performance_cent
    )
    trace_rows_list: list[dict[str, object]] = []
    for component, decision in zip(components, decisions, strict=True):
        source_entries = tuple(
            entry_by_id[key] for key in component.source_keys if key in entry_by_id
        )
        trace_rows_list.append(
            {
                "记录类型": "现金流业务组成",
                "摘要": component.summary,
                "现金变化": component.cash_delta_cent / 100,
                "原现流项目": component.original_item_text,
                "对方科目": "、".join(component.counterpart_accounts),
                "系统项目": (
                    f"{decision.system_item_name}（{decision.system_item_id}）"
                    if decision.system_item_id
                    else "不进入正表"
                ),
                "判断理由": decision.reason,
                "证据强度": decision.evidence_level,
                "异常": "、".join(component.anomalies),
                "方向依据": "、".join(
                    dict.fromkeys(flow_direction_source(entry) for entry in source_entries)
                ),
                "来源文件": "、".join(
                    dict.fromkeys(
                        file_name_by_id.get(entry.source.file_id, entry.source.file_id)
                        for entry in source_entries
                    )
                ),
                "来源工作表": "、".join(
                    dict.fromkeys(entry.source.sheet_name for entry in source_entries)
                ),
                "来源单元格": "、".join(
                    dict.fromkeys(entry.source.cell_range for entry in source_entries)
                ),
                "决策来源(技术)": decision.decision_source,
                "命中规则(技术)": decision.matched_rule_id,
                "业务组成编号(技术)": component.component_id,
                "来源占用键(技术)": "、".join(component.source_keys),
            }
        )
    for transfer in state.get("internal_transfers", ()):
        entry = entry_by_id.get(str(transfer["entry_id"]))
        trace_rows_list.append(
            {
                "记录类型": "内部划转排除",
                "摘要": "" if entry is None else entry.summary,
                "现金变化": int(transfer["matched_cent"]) / 100,
                "原现流项目": "" if entry is None else entry.original_flow_item,
                "对方科目": "" if entry is None else entry.counterpart_name,
                "系统项目": "不进入正表",
                "判断理由": "现金及现金等价物内部划转",
                "证据强度": "high",
                "异常": "内部划转已排除",
                "方向依据": "内部划转",
                "来源文件": "" if entry is None else file_name_by_id.get(entry.source.file_id, entry.source.file_id),
                "来源工作表": "" if entry is None else entry.source.sheet_name,
                "来源单元格": "" if entry is None else entry.source.cell_range,
                "决策来源(技术)": "system",
                "命中规则(技术)": "INTERNAL-TRANSFER",
                "业务组成编号(技术)": transfer["entry_id"],
                "来源占用键(技术)": transfer["entry_id"],
            }
        )
    trace_rows = tuple(trace_rows_list)
    mapping_rows = tuple(
        {
            "文件": item["file"],
            "工作表": item["sheet"],
            "表头行": item["header_rows"],
            "字段映射": json.dumps(item["roles"], ensure_ascii=False),
        }
        for item in state.get("mappings", ())
    ) + tuple(
        {
            "文件": item["file"],
            "工作表": item["sheet"],
            "表头行": item["cell"],
            "字段映射": f"{item['kind']}：{item['message']}",
        }
        for item in state.get("normalization_issues", ())
    )
    statement_unconfirmed = any(
        state.get("statement_confirmations", {}).get(
            f"{item['file_id']}:statement:{item['sheet']}"
        ) != "use"
        for item in state.get("statement_candidates", ())
    )
    if statement_unconfirmed:
        status = "草稿：存在未核对的疑似正表"
    elif any(item.get("kind") == "错误" for item in state.get("normalization_issues", ())):
        status = "草稿：输入存在未处理错误"
    elif reconciliation.status != "现金调节完成":
        status = "草稿：现金调节未完成或存在差异"
    elif review_batches or any(group.blocks_manual_completion for group in duplicate_groups):
        status = "待完成人工确认"
    else:
        status = "最终可使用"
    model = WorkbookModel(
        statement=statement,
        rules=rules,
        comparison=comparison,
        review_batches=review_batches,
        duplicate_groups=duplicate_groups,
        ai_records=tuple(
            {"阶段": stage_name, **item}
            for stage_name, records in (
                ("首次复核", state.get("ai_validation", {}).get("valid_results", ())),
                ("裁决", state.get("adjudication_validation", {}).get("valid_results", ())),
            )
            for item in records
        ),
        cash_scope_rows=tuple(
            {"科目": key, "决定": "纳入"}
            for key in state["cash_scope"]["included_keys"]
        ),
        reconciliation=reconciliation,
        trace_rows=trace_rows,
        mapping_rows=mapping_rows,
        overall_status=status,
        unconfirmed_statement=statement_unconfirmed,
    )
    sequence = 1
    while True:
        suffix = "" if sequence == 1 else f"_重建{sequence}"
        workbook_path = Path(run_dir) / f"现金流量表正表及复核底稿{suffix}.xlsx"
        temporary_path = workbook_path.with_name(f"{workbook_path.stem}_生成中.xlsx")
        if not workbook_path.exists() and not temporary_path.exists():
            break
        sequence += 1
    build_output_workbook(model, temporary_path)
    output_check = validate_final_output(temporary_path, model)
    if not output_check.valid:
        raise RuntimeError("输出工作簿验收失败：" + "；".join(output_check.errors))
    temporary_path.replace(workbook_path)
    store = _store(run_dir)
    with store.stage("finalize") as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO classification_decision(record_id, payload_json) VALUES (?, ?)",
            ((item.component_id, json.dumps(asdict(item), ensure_ascii=False)) for item in decisions),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO statement_value(record_id, payload_json) VALUES (?, ?)",
            (
                (item_id, json.dumps({"amount_cent": amount}, ensure_ascii=False))
                for item_id, amount in statement.values.items()
            ),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO review_batch(record_id, payload_json) VALUES (?, ?)",
            ((item.batch_id, json.dumps(asdict(item), ensure_ascii=False)) for item in review_batches),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO duplicate_group(record_id, payload_json) VALUES (?, ?)",
            ((item.group_id, json.dumps(asdict(item), ensure_ascii=False)) for item in duplicate_groups),
        )
        if comparison is not None:
            connection.executemany(
                "INSERT OR REPLACE INTO statement_comparison(record_id, payload_json) VALUES (?, ?)",
                ((item.item_id, json.dumps(asdict(item), ensure_ascii=False)) for item in comparison.rows),
            )
        connection.execute(
            "INSERT OR REPLACE INTO reconciliation(record_id, payload_json) VALUES (?, ?)",
            ("cash_reconciliation", json.dumps(asdict(reconciliation), ensure_ascii=False)),
        )
    state["reconciliation"] = asdict(reconciliation)
    state["workbook_path"] = str(workbook_path)
    state["overall_status"] = status
    state["stage"] = "finalized"
    _save_state(run_dir, state)
    return FinalizeResult(str(state["run_id"]), Path(run_dir), workbook_path, status)
