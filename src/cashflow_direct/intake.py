from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cashflow_direct.models import MaterialityAmounts, validate_materiality_order
from cashflow_direct.money import stable_id, yuan_to_cent


class UnsupportedLegacyExcelError(ValueError):
    """客户选择了不支持的旧式 Excel 文件。"""


class UnreadableInputError(ValueError):
    """客户文件不存在、不可读或格式不受支持。"""


@dataclass(frozen=True, slots=True)
class RegisteredFile:
    file_id: str
    path: Path
    sha256: str
    duplicate_of: str | None
    is_macro_workbook: bool
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class IntakeResult:
    files: tuple[RegisteredFile, ...]
    run_dir: Path

    @property
    def active_files(self) -> tuple[RegisteredFile, ...]:
        return tuple(item for item in self.files if item.duplicate_of is None)


def choose_input_files(
    dialog: Callable[[], Sequence[str]] | None = None,
) -> tuple[Path, ...]:
    """通过 Windows 文件选择窗口取得输入，不让客户手填绝对路径。"""
    if dialog is None:
        from tkinter import Tk, filedialog

        root = Tk()
        root.withdraw()
        try:
            selected = filedialog.askopenfilenames(
                title="选择序时账、现流明细或需要核对的现金流量表",
                filetypes=(
                    ("Excel 工作簿", "*.xlsx *.xlsm"),
                    ("所有文件", "*.*"),
                ),
            )
        finally:
            root.destroy()
    else:
        selected = dialog()
    return tuple(Path(item) for item in selected if str(item).strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise UnreadableInputError(f"{path.name} 无法读取：{exc}") from exc
    return digest.hexdigest()


def _create_run_dir(parent: Path, base_name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    candidate = parent / base_name
    serial = 1
    while True:
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            serial += 1
            candidate = parent / f"{base_name}-{serial:02d}"


def register_inputs(
    paths: Sequence[Path],
    output_parent: Path | None = None,
    now: datetime | None = None,
) -> IntakeResult:
    """只读登记输入、排除文件级精确重复并建立独立运行目录。"""
    selected = tuple(Path(item) for item in paths)
    if not selected:
        raise UnreadableInputError("未选择任何输入文件")

    records: list[RegisteredFile] = []
    first_by_hash: dict[str, str] = {}
    for path in selected:
        suffix = path.suffix.lower()
        if suffix == ".xls":
            raise UnsupportedLegacyExcelError(
                f"{path.name} 是旧式 .xls 文件，请在 Excel 中另存为 .xlsx 后重新选择"
            )
        if suffix not in {".xlsx", ".xlsm"}:
            raise UnreadableInputError(f"{path.name} 格式不受支持，只接受 xlsx 或只读 xlsm")
        if not path.is_file():
            raise UnreadableInputError(f"{path.name} 无法读取：文件不存在")
        file_hash = _sha256(path)
        file_id = stable_id("FILE", file_hash, str(path.resolve()))
        duplicate_of = first_by_hash.get(file_hash)
        if duplicate_of is None:
            first_by_hash[file_hash] = file_id
        records.append(
            RegisteredFile(
                file_id=file_id,
                path=path.resolve(),
                sha256=file_hash,
                duplicate_of=duplicate_of,
                is_macro_workbook=suffix == ".xlsm",
            )
        )

    moment = now or datetime.now()
    run_key = stable_id("RUN", *(item.sha256 for item in records), moment.isoformat())[-8:]
    parent = Path(output_parent) if output_parent is not None else selected[0].parent / "直接法现流表结果"
    run_dir = _create_run_dir(parent, f"运行_{moment:%Y%m%d_%H%M%S}_{run_key}")
    return IntakeResult(files=tuple(records), run_dir=run_dir)


def validate_materiality(
    overall: object,
    performance: object,
    trivial: object,
) -> MaterialityAmounts:
    """把客户提供的元金额转换为整数分并校验三档顺序。"""
    return validate_materiality_order(
        MaterialityAmounts(
            overall_cent=yuan_to_cent(overall),
            performance_cent=yuan_to_cent(performance),
            trivial_cent=yuan_to_cent(trivial),
        )
    )
