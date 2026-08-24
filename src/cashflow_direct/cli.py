from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from collections.abc import Sequence
from pathlib import Path

from cashflow_direct.intake import choose_input_files
from cashflow_direct.decision_policy import (
    AUTOMATIC_CHANGE_SCORE_OPTIONS,
    DEFAULT_AUTOMATIC_CHANGE_SCORE,
)
from cashflow_direct.pipeline import (
    confirm_company_notes,
    confirm_component_structure,
    confirm_manual_decisions,
    confirm_account_mapping,
    confirm_mapping,
    confirm_cash_scope,
    finalize_run,
    import_ai_results,
    import_component_structure_ai_results,
    import_dictionary_results,
    import_summary_results,
    run_classification,
    run_preflight,
    scan_accounts,
    supplement_cash_balances,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="一般企业直接法现金流量表编制与校验")
    commands = parser.add_subparsers(dest="command")
    preflight = commands.add_parser("preflight", help="选择并登记资料")
    preflight.add_argument("--overall", required=True, help="财务报表整体重要性，单位元")
    preflight.add_argument("--performance", required=True, help="实际执行的重要性，单位元")
    preflight.add_argument("--trivial", required=True, help="明显微小错报临界值，单位元")
    preflight.add_argument("--output-parent", help="由 Skill 自动传递的输出父目录")
    preflight.add_argument("--statement-path", help="客户现有现金流量表正表文件；指定后只认该文件，识别失败即报错")
    preflight.add_argument("--notes", help="公司特殊规则注意事项文本文件路径（UTF-8），可选")
    preflight.add_argument(
        "--automatic-change-threshold",
        type=int,
        choices=AUTOMATIC_CHANGE_SCORE_OPTIONS,
        default=DEFAULT_AUTOMATIC_CHANGE_SCORE,
        help="系统自动修改客户原项目的最低证据分；可选50、55、70、90，默认并推荐70",
    )
    preflight.add_argument("--paths", nargs="+", help="直接给出输入文件路径（可多个）；给定后不弹文件选择窗口")
    for name in (
        "confirm-mapping",
        "confirm-account-mapping",
        "confirm-cash",
        "confirm-notes",
        "confirm-components",
        "import-component-ai",
        "supplement-cash",
        "scan-accounts",
        "import-dictionary",
        "import-summary",
        "classify",
        "import-ai",
        "confirm-manual",
        "finalize",
        "status",
    ):
        command = commands.add_parser(name)
        command.add_argument("--run-dir", required=True, help="由上一阶段自动传递的运行目录")
        if name == "confirm-mapping":
            command.add_argument("--decisions", required=True, help="字段映射确认 JSON")
        elif name == "confirm-account-mapping":
            command.add_argument("--decisions", required=True, help="客户一级科目映射确认 JSON")
        elif name == "confirm-cash":
            command.add_argument("--decisions", required=True, help="现金范围决定 JSON")
        elif name == "confirm-notes":
            command.add_argument("--decisions", required=True, help="公司特殊规则清单 JSON 数组")
        elif name == "confirm-components":
            command.add_argument(
                "--decisions",
                required=True,
                help="业务组成候选确认 JSON；键为凭证，值为所选来源行编号数组",
            )
        elif name == "import-component-ai":
            command.add_argument(
                "--result-path",
                required=True,
                help="业务组成AI返回结果 JSONL",
            )
        elif name == "supplement-cash":
            command.add_argument("--opening", required=True, help="期初现金余额，单位元")
            command.add_argument("--closing", required=True, help="期末现金余额，单位元")
            command.add_argument("--fx", required=True, help="汇率变动影响，单位元；没有则填零")
            command.add_argument("--source-note", required=True, help="补充数据的资料来源说明")
        elif name == "import-dictionary":
            command.add_argument("--result-path", required=True, help="科目语义 AI 返回结果 JSONL")
        elif name == "import-summary":
            command.add_argument("--result-path", required=True, help="摘要语义 AI 返回结果 JSONL")
        elif name == "import-ai":
            command.add_argument("--result-path", required=True, help="AI 返回结果 JSONL")
        elif name == "confirm-manual":
            command.add_argument(
                "--decisions",
                required=True,
                help="人工决定 JSON 数组；逐项填写业务编号、项目或排除、依据和处理人",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command is None:
        build_parser().print_help()
        return 0
    try:
        if arguments.command == "preflight":
            notes = (
                Path(arguments.notes).read_text(encoding="utf-8")
                if arguments.notes
                else None
            )
            inputs = (
                tuple(Path(item) for item in arguments.paths)
                if arguments.paths
                else choose_input_files()
            )
            result = run_preflight(
                inputs,
                (arguments.overall, arguments.performance, arguments.trivial),
                None if arguments.output_parent is None else Path(arguments.output_parent),
                None if arguments.statement_path is None else Path(arguments.statement_path),
                notes=notes,
                automatic_change_threshold=arguments.automatic_change_threshold,
            )
        elif arguments.command == "confirm-mapping":
            result = confirm_mapping(Path(arguments.run_dir), json.loads(arguments.decisions))
        elif arguments.command == "confirm-account-mapping":
            result = confirm_account_mapping(
                Path(arguments.run_dir), json.loads(arguments.decisions)
            )
        elif arguments.command == "confirm-cash":
            result = confirm_cash_scope(Path(arguments.run_dir), json.loads(arguments.decisions))
        elif arguments.command == "confirm-notes":
            result = confirm_company_notes(Path(arguments.run_dir), json.loads(arguments.decisions))
        elif arguments.command == "confirm-components":
            result = confirm_component_structure(
                Path(arguments.run_dir), json.loads(arguments.decisions)
            )
        elif arguments.command == "import-component-ai":
            result = import_component_structure_ai_results(
                Path(arguments.run_dir), Path(arguments.result_path)
            )
        elif arguments.command == "supplement-cash":
            result = supplement_cash_balances(
                Path(arguments.run_dir),
                arguments.opening,
                arguments.closing,
                arguments.fx,
                arguments.source_note,
            )
        elif arguments.command == "scan-accounts":
            result = scan_accounts(Path(arguments.run_dir))
        elif arguments.command == "import-dictionary":
            result = import_dictionary_results(Path(arguments.run_dir), Path(arguments.result_path))
        elif arguments.command == "import-summary":
            result = import_summary_results(Path(arguments.run_dir), Path(arguments.result_path))
        elif arguments.command == "classify":
            result = run_classification(Path(arguments.run_dir))
        elif arguments.command == "import-ai":
            result = import_ai_results(Path(arguments.run_dir), Path(arguments.result_path))
        elif arguments.command == "confirm-manual":
            result = confirm_manual_decisions(
                Path(arguments.run_dir), json.loads(arguments.decisions)
            )
        elif arguments.command == "finalize":
            result = finalize_run(Path(arguments.run_dir))
        else:
            state_path = Path(arguments.run_dir) / "计算留痕数据" / "运行状态.json"
            result = json.loads(state_path.read_text(encoding="utf-8-sig"))
        payload = asdict(result) if hasattr(result, "__dataclass_fields__") else result
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
