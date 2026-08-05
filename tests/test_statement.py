from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cashflow_direct.semantic_mapping import MappingQuestion
from cashflow_direct.statement import (
    aggregate_statement,
    compare_statement,
    parse_existing_statement,
    reconcile_cash,
)
from tests.fixture_factory import classified_components, write_existing_statement_fixture


class StatementTests(unittest.TestCase):
    def test_leaf_subtotals_and_net_cash_reconcile(self) -> None:
        case = classified_components()
        result = aggregate_statement(case.components, case.decisions, case.rules)
        self.assertEqual(35, len(result.values))
        self.assertEqual(result.values["CFO-IN"] - result.values["CFO-OUT"], result.values["CFO-NET"])
        expected = (
            result.values["CFO-NET"]
            + result.values["CFI-NET"]
            + result.values["CFF-NET"]
            + result.values["FX"]
        )
        self.assertEqual(expected, result.values["NET-CASH"])
        self.assertEqual(("S1",), result.support_component_ids["CFO-01"])
        self.assertIsNone(result.prior_values["CFO-01"])

    def test_opening_and_fx_are_injected_into_closing_cash_formula(self) -> None:
        case = classified_components()
        result = aggregate_statement(
            case.components,
            case.decisions,
            case.rules,
            opening_cent=1_000_000,
            fx_cent=12_300,
        )
        self.assertEqual(1_000_000, result.values["CASH-OPENING"])
        self.assertEqual(12_300, result.values["FX"])
        self.assertEqual(
            result.values["CASH-OPENING"] + result.values["NET-CASH"],
            result.values["CASH-CLOSING"],
        )

    def test_custom_rows_map_to_standard_parent_without_double_counting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "客户正表.xlsx"
            write_existing_statement_fixture(path, header_row=7, with_custom_rows=True)
            case = classified_components()
            existing = parse_existing_statement(path, case.rules)
            self.assertNotIsInstance(existing, MappingQuestion)
            self.assertEqual(35, len(existing.values))
            self.assertEqual("CFO-03", existing.custom_rows[0].parent_item_id)
            self.assertEqual(existing.values["CFO-03"], existing.standardized_values["CFO-03"])
            self.assertEqual(0, existing.values["CFO-02"])
            self.assertIsNone(existing.prior_values["CFO-02"])

    def test_existing_comparison_has_each_standard_row_and_source_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "客户正表.xlsx"
            write_existing_statement_fixture(path, header_row=11, with_custom_rows=False)
            case = classified_components()
            existing = parse_existing_statement(path, case.rules)
            computed = aggregate_statement(case.components, case.decisions, case.rules)
            comparison = compare_statement(existing, computed)
            self.assertEqual(35, len(comparison.rows))
            row = next(item for item in comparison.rows if item.item_id == "CFO-01")
            self.assertEqual(computed.values["CFO-01"] - existing.values["CFO-01"], row.difference_cent)
            self.assertEqual(("S1",), row.support_component_ids)

    def test_unmapped_total_returns_question_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "歧义正表.xlsx"
            write_existing_statement_fixture(path, 5, False, include_unknown=True)
            result = parse_existing_statement(path, classified_components().rules)
            self.assertIsInstance(result, MappingQuestion)

    def test_cash_reconciliation_never_plugs_missing_fx(self) -> None:
        case = classified_components()
        statement = aggregate_statement(case.components, case.decisions, case.rules)
        incomplete = reconcile_cash(statement, opening_cent=1_000, closing_cent=2_000, fx_cent=None)
        self.assertEqual("现金调节未完成", incomplete.status)
        self.assertIsNone(incomplete.fx_cent)
        fx = 100
        net = statement.values["CFO-NET"] + statement.values["CFI-NET"] + statement.values["CFF-NET"] + fx
        completed = reconcile_cash(statement, opening_cent=1_000, closing_cent=1_000 + net, fx_cent=fx)
        self.assertEqual("现金调节完成", completed.status)
        self.assertEqual(0, completed.difference_cent)


if __name__ == "__main__":
    unittest.main()
