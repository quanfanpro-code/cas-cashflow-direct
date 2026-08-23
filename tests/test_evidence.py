from __future__ import annotations

import unittest

from cashflow_direct.account_dictionary import (
    AccountDictionary,
    AccountSemanticEntry,
    score_dictionary_hits,
)
from cashflow_direct.evidence import (
    RuleScore,
    aggregate_evidence,
    split_account_levels,
)
from cashflow_direct.models import CashflowComponent


def make_score(**overrides) -> RuleScore:
    base = dict(
        rule_id="R1",
        item_id="CFO-05",
        priority=20,
        source="summary",
        score=90,
        summary_part=45,
        account_part=45,
        direction_compatible=True,
        summary_hits=("工资",),
        account_hits=("工资",),
        channels=("summary", "account_path"),
        summary_facts=("business:CFO-05", "summary_context:发放工资"),
        account_facts=("business:CFO-05", "account_context:应付职工薪酬工资"),
    )
    base.update(overrides)
    return RuleScore(**base)


def make_component(
    summary: str = "发放工资",
    accounts: tuple[str, ...] = ("应付职工薪酬_工资",),
    cent: int = -10000,
) -> CashflowComponent:
    return CashflowComponent(
        component_id="C1",
        voucher_key="V1",
        summary=summary,
        cash_delta_cent=cent,
        counterpart_accounts=accounts,
    )


class SplitAccountLevelsTests(unittest.TestCase):
    def test_supports_common_separators(self) -> None:
        for name in (
            "应交税费_应交增值税_进项税额",
            "应交税费/应交增值税/进项税额",
            "应交税费\\应交增值税\\进项税额",
            "应交税费>应交增值税>进项税额",
            "应交税费|应交增值税|进项税额",
            "应交税费:应交增值税:进项税额",
            "应交税费：应交增值税：进项税额",
            "应交税费 - 应交增值税 - 进项税额",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    ("应交税费", "应交增值税", "进项税额"), split_account_levels(name)
                )

    def test_plain_hyphen_without_spaces_stays_intact(self) -> None:
        self.assertEqual(("财务费用-利息收入",), split_account_levels("财务费用-利息收入"))


class AggregateEvidenceTests(unittest.TestCase):
    def test_strong_summary_plus_strong_detail_equals_90(self) -> None:
        agg = aggregate_evidence([make_score()])
        self.assertIsNotNone(agg)
        self.assertEqual(90, agg.score)
        self.assertEqual({"summary", "account_path"}, set(agg.sources))

    def test_structure_selected_from_existing_summary_cannot_count_path_as_independent(self) -> None:
        agg = aggregate_evidence(
            [make_score()],
            sources_independent=False,
        )

        self.assertIsNotNone(agg)
        self.assertEqual(45, agg.score)
        self.assertFalse(agg.sources_independent)

    def test_strong_summary_plus_medium_level1_equals_70(self) -> None:
        agg = aggregate_evidence(
            [
                make_score(
                    score=70,
                    account_part=25,
                    account_hits=("职工薪酬",),
                )
            ]
        )
        self.assertIsNotNone(agg)
        self.assertEqual(70, agg.score)

    def test_label_agreement_adds_nothing(self) -> None:
        # 汇总不接受原标签参数：标签一致不加分
        agg = aggregate_evidence([make_score()])
        self.assertIsNotNone(agg)
        self.assertEqual(90, agg.score)

    def test_two_sources_pointing_to_different_items_have_no_usable_score(self) -> None:
        summary_source = make_score(
            rule_id="A", score=45, account_part=0, account_hits=(),
            channels=("summary",), account_facts=(),
        )
        path_source = make_score(
            rule_id="B", item_id="CFI-06", source="account_path", score=45,
            summary_part=0, summary_hits=(), channels=("account_path",),
            summary_facts=(), account_hits=("厂房",),
            account_facts=("business:CFI-06", "account_context:在建工程厂房"),
        )
        agg = aggregate_evidence([summary_source, path_source])
        self.assertIsNotNone(agg)
        self.assertIsNone(agg.score)
        self.assertTrue(agg.conflict)
        self.assertEqual(("CFI-06", "CFO-05"), agg.conflict_item_ids)

    def test_summary_that_only_repeats_the_path_does_not_add_score(self) -> None:
        agg = aggregate_evidence(
            [
                make_score(
                    score=90,
                    summary_facts=("business:CFO-05",),
                    account_facts=("business:CFO-05",),
                )
            ]
        )
        self.assertIsNotNone(agg)
        self.assertEqual(45, agg.score)
        self.assertEqual(("account_path",), agg.sources)

    def test_direction_incompatible_does_not_reduce_evidence_score(self) -> None:
        agg = aggregate_evidence([make_score(direction_compatible=False)])
        self.assertIsNotNone(agg)
        self.assertEqual(90, agg.score)

    def test_two_source_score_and_independence_are_recorded_without_deciding_action(self) -> None:
        agg = aggregate_evidence([make_score()])
        self.assertIsNotNone(agg)
        self.assertEqual(90, agg.score)
        self.assertTrue(agg.sources_independent)
        only_summary = aggregate_evidence(
            [
                make_score(
                    score=45,
                    account_part=0,
                    account_hits=(),
                    channels=("summary",),
                    account_facts=(),
                )
            ]
        )
        self.assertIsNotNone(only_summary)
        self.assertEqual(45, only_summary.score)
        self.assertFalse(only_summary.sources_independent)


class DictionaryScoreTests(unittest.TestCase):
    def _dictionary(self, layer: str, confidence: str) -> AccountDictionary:
        return AccountDictionary(
            (
                AccountSemanticEntry(
                    account="工资",
                    semantic="职工薪酬",
                    item_id="CFO-05",
                    basis="应用指南第三十二章'支付给职工以及为职工支付的现金'",
                    confidence=confidence,
                    layer=layer,
                    note_id="NOTE-01" if layer == "custom" else "",
                ),
            )
        )

    def test_custom_high_scores_45_and_carries_note_id(self) -> None:
        scores = score_dictionary_hits(
            make_component(summary="转账"), self._dictionary("custom", "high")
        )
        self.assertEqual(1, len(scores))
        self.assertEqual(45, scores[0].account_part)
        self.assertEqual("NOTE-01", scores[0].note_id)

    def test_custom_medium_scores_25(self) -> None:
        scores = score_dictionary_hits(
            make_component(summary="转账"), self._dictionary("custom", "medium")
        )
        self.assertEqual(25, scores[0].account_part)

    def test_common_high_scores_45(self) -> None:
        scores = score_dictionary_hits(
            make_component(summary="转账"), self._dictionary("common", "high")
        )
        self.assertEqual(45, scores[0].account_part)
        self.assertEqual("", scores[0].note_id)


if __name__ == "__main__":
    unittest.main()
