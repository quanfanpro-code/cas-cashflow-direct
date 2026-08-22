from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Mapping, Sequence

from cashflow_direct.evidence import split_account_levels
from cashflow_direct.models import CashflowComponent, NormalizedEntry


_CODE_PREFIX = re.compile(r"^\s*\d+(?:\.\d+)*\s*")


@dataclass(frozen=True, slots=True)
class StandardAccount:
    sequence: int
    category: str
    standard_name: str
    aliases: tuple[str, ...]
    optional_codes: tuple[str, ...]
    source_locations: tuple[str, ...]
    report_note: str


@dataclass(frozen=True, slots=True)
class AccountMappingRecord:
    original_level1: str
    standard_level1: str
    status: str
    candidate_standard_names: tuple[str, ...]
    basis: str


def _split_values(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            part.strip()
            for part in re.split(r"[；;、]", value)
            if part.strip() and part.strip() != "—"
        )
    )


def load_standard_accounts(project_root: Path) -> tuple[StandardAccount, ...]:
    path = Path(project_root) / "references" / "标准一级科目并集去重表.md"
    accounts: list[StandardAccount] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        columns = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(columns) < 7:
            raise ValueError(f"标准一级科目基线行缺少字段：{line}")
        sequence, category, name, aliases, codes, sources, note = columns[:7]
        accounts.append(
            StandardAccount(
                int(sequence),
                category,
                name,
                _split_values(aliases),
                _split_values(codes),
                _split_values(sources),
                note,
            )
        )
    if len(accounts) != 201 or len({item.standard_name for item in accounts}) != 201:
        raise ValueError("标准一级科目基线必须恰好包含201个唯一科目")
    return tuple(accounts)


def extract_original_level1(account_path: str) -> str:
    levels = split_account_levels(account_path)
    if not levels:
        return ""
    return _CODE_PREFIX.sub("", levels[0]).strip()


def build_account_mappings(
    account_paths: Sequence[str],
    baseline: Sequence[StandardAccount],
) -> tuple[AccountMappingRecord, ...]:
    index: dict[str, list[str]] = {}
    for account in baseline:
        for name in (account.standard_name, *account.aliases):
            candidates = index.setdefault(name, [])
            if account.standard_name not in candidates:
                candidates.append(account.standard_name)
    originals = sorted(
        {
            level1
            for path in account_paths
            if (level1 := extract_original_level1(path))
        }
    )
    records: list[AccountMappingRecord] = []
    for original in originals:
        candidates = tuple(index.get(original, ()))
        if len(candidates) == 1:
            standard = candidates[0]
            records.append(
                AccountMappingRecord(
                    original,
                    standard,
                    "confirmed",
                    candidates,
                    "标准名称唯一命中" if original == standard else "别名唯一命中",
                )
            )
        elif candidates:
            records.append(
                AccountMappingRecord(original, "", "ambiguous", candidates, "同名或别名命中多个标准科目")
            )
        else:
            suggestions = tuple(
                item.standard_name
                for score, _, item in sorted(
                    (
                        (
                            SequenceMatcher(
                                None, original, item.standard_name
                            ).ratio(),
                            item.sequence,
                            item,
                        )
                        for item in baseline
                    ),
                    key=lambda value: (-value[0], value[1]),
                )[:5]
                if score >= 0.5
            )
            records.append(
                AccountMappingRecord(
                    original,
                    "",
                    "unmapped",
                    suggestions,
                    "201条基线中没有唯一匹配；相近名称仅作候选",
                )
            )
    return tuple(records)


def apply_account_mapping(
    account_path: str,
    records: Mapping[str, AccountMappingRecord],
) -> tuple[str, bool]:
    levels = split_account_levels(account_path)
    if not levels:
        return account_path, False
    original = extract_original_level1(account_path)
    record = records.get(original)
    if record is None or record.status != "confirmed" or not record.standard_level1:
        return account_path, False
    return "_".join((record.standard_level1, *levels[1:])), True


def resolve_account_mappings(
    records: Sequence[AccountMappingRecord],
    decisions: Mapping[str, str],
    baseline: Sequence[StandardAccount],
) -> tuple[AccountMappingRecord, ...]:
    original_level1_names = {record.original_level1 for record in records}
    unexpected = sorted(set(decisions) - original_level1_names)
    if unexpected:
        raise ValueError(
            "一级科目映射只接受客户一级科目，不接受明细科目："
            + "、".join(unexpected)
        )
    standard_names = {item.standard_name for item in baseline}
    resolved: list[AccountMappingRecord] = []
    for record in records:
        choice = str(decisions.get(record.original_level1, "")).strip()
        if not choice and record.status == "confirmed":
            resolved.append(record)
            continue
        if not choice:
            raise ValueError(f"等待一级科目确认：{record.original_level1}")
        if choice == "manual":
            raise ValueError(
                f"一级科目必须映射至201条基线后才能继续：{record.original_level1}"
            )
        if choice not in standard_names:
            raise ValueError(f"不是201条基线中的标准一级科目：{choice}")
        resolved.append(
            replace(
                record,
                standard_level1=choice,
                status="confirmed",
                candidate_standard_names=tuple(
                    dict.fromkeys((*record.candidate_standard_names, choice))
                ),
                basis="用户确认",
            )
        )
    return tuple(resolved)


def standardize_entries(
    entries: Sequence[NormalizedEntry],
    records: Mapping[str, AccountMappingRecord],
) -> tuple[NormalizedEntry, ...]:
    pending = sorted(
        item.original_level1
        for item in records.values()
        if item.status != "confirmed" or not item.standard_level1
    )
    if pending:
        raise RuntimeError("一级科目映射未全部确认：" + "、".join(pending))

    standardized: list[NormalizedEntry] = []
    for entry in entries:
        account, account_confirmed = (
            apply_account_mapping(entry.account_name, records)
            if entry.account_name
            else ("", True)
        )
        counterpart, counterpart_confirmed = (
            apply_account_mapping(entry.counterpart_name, records)
            if entry.counterpart_name
            else ("", True)
        )
        if not account_confirmed or not counterpart_confirmed:
            missing = entry.account_name if not account_confirmed else entry.counterpart_name
            raise RuntimeError(f"一级科目映射未全部确认：{missing}")
        retained_side = entry.retained_side
        if account:
            retained_side = (
                "cash"
                if extract_original_level1(account)
                in {"库存现金", "银行存款", "其他货币资金", "现金等价物"}
                else "counterpart"
            )
        label_side = retained_side if entry.original_flow_item else "unknown"
        standardized.append(
            replace(
                entry,
                account_name=account,
                counterpart_name=counterpart,
                retained_side=retained_side,
                label_side=label_side,
                original_account_name=entry.original_account_name or entry.account_name,
                original_counterpart_name=(
                    entry.original_counterpart_name or entry.counterpart_name
                ),
            )
        )
    return tuple(standardized)


def standardize_component_accounts(
    component: CashflowComponent,
    records: Mapping[str, AccountMappingRecord],
) -> CashflowComponent:
    raw_paths = component.original_counterpart_accounts or component.counterpart_accounts
    standardized: list[str] = []
    for path in raw_paths:
        mapped, path_confirmed = apply_account_mapping(path, records)
        if not path_confirmed:
            raise RuntimeError(f"一级科目映射未全部确认：{path}")
        standardized.append(mapped)
    return replace(
        component,
        counterpart_accounts=tuple(standardized),
        original_counterpart_accounts=tuple(raw_paths),
        account_mapping_status="confirmed",
    )
