from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "lifemem_kv_reuse_analysis.py"
SPEC = importlib.util.spec_from_file_location("lifemem_kv_reuse_analysis", SCRIPT)
assert SPEC and SPEC.loader
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)


def result(subject: str, content: str, score: float = 0.5) -> dict[str, object]:
    return {
        "content": content,
        "score": score,
        "metadata": {"subject": subject, "summary": "metadata-only summary"},
    }


def record(question_id: str, results: list[dict[str, object]], context: str) -> dict[str, object]:
    return {
        "question_id": question_id,
        "query": f"query for {question_id}",
        "conversation_id": "conversation-a",
        "results": results,
        "retrieval_metadata": {
            "is_multi_round": False,
            "round1_count": len(results),
            "round2_count": 0,
            "final_count": len(results),
            "formatted_context": context,
        },
    }


def wrapped(subject: str, content: str) -> str:
    return f"    {subject}: {content}\n---\n"


class LifeMemKVReuseAnalysisTests(unittest.TestCase):
    def test_normalize_hash_type_and_occurrence_helpers(self) -> None:
        normalized = ANALYZER.normalize_content("\r\nBody\r\nLine\r\n")
        self.assertEqual(normalized, "Body\nLine")
        self.assertEqual(
            ANALYZER.content_hash(normalized),
            ANALYZER.content_hash(ANALYZER.normalize_content("Body\nLine")),
        )
        self.assertEqual(ANALYZER.classify_chunk_type("[月摘要] 2025-m11"), "monthly_summary")
        self.assertEqual(ANALYZER.classify_chunk_type("[周摘要] 2025-m11-w01"), "weekly_summary")
        self.assertEqual(ANALYZER.classify_chunk_type("ordinary event"), "episode_or_event")
        self.assertEqual(ANALYZER.classify_chunk_type(None), "other")

        context = "x\r\nBody\r\nLine\r\ny\nBody\nLine\n"
        positions = ANALYZER.find_all_occurrences(context, "Body\nLine")
        self.assertEqual(len(positions), 2)
        self.assertEqual(context[positions[0][0] : positions[0][1]], "Body\r\nLine")
        self.assertEqual(context[positions[1][0] : positions[1][1]], "Body\nLine")

    def test_load_records_supports_json_array_object_container_and_jsonl(self) -> None:
        qa = record("q1", [], "")
        cases = (
            (json.dumps([qa]), "json_array"),
            (json.dumps(qa), "json_object"),
            (json.dumps({"records": [qa]}), "json_object_field:records"),
            (json.dumps({"results": [qa]}), "json_object_field:results"),
            (json.dumps(qa) + "\n" + json.dumps({**qa, "question_id": "q2"}), "jsonl"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            for index, (payload, expected_format) in enumerate(cases):
                path = Path(tmp) / f"input-{index}.json"
                path.write_text(payload, encoding="utf-8")
                records, input_format = ANALYZER.load_records(path)
                self.assertEqual(input_format, expected_format)
                self.assertGreaterEqual(len(records), 1)

    def test_period_summary_uses_retrieved_wrapper_as_canonical_position(self) -> None:
        monthly = "Monthly summary\nBody"
        event = "An ordinary event"
        context = (
            "[周期摘要 period_2025-m11]\n"
            f"{monthly}\n"
            "---\n\n"
            "Episodes memories:\n\n"
            + wrapped("event", event)
            + wrapped("[月摘要] 2025-m11", monthly)
        )
        analysis = ANALYZER.analyze_records(
            [
                record(
                    "q1",
                    [result("[月摘要] 2025-m11", monthly), result("event", event)],
                    context,
                )
            ]
        )

        monthly_row = next(row for row in analysis["chunks"] if row["chunk_type"] == "monthly_summary")
        event_row = next(row for row in analysis["chunks"] if row["subject"] == "event")
        duplicate = analysis["intra_context_duplicates"][0]

        self.assertEqual(monthly_row["context_occurrence_count"], 2)
        self.assertEqual(monthly_row["canonical_context_source"], "retrieved_result")
        self.assertGreater(monthly_row["context_char_start"], event_row["context_char_start"])
        self.assertEqual(monthly_row["preceding_chunk_hashes"], [event_row["content_hash"]])
        self.assertTrue(duplicate["same_body_different_wrapper"])
        self.assertTrue(duplicate["redundant_context_candidate"])
        self.assertIn("SAME_BODY_DIFFERENT_WRAPPER", duplicate["classifications"])
        self.assertIn("DIFFERENT_POSITION", duplicate["classifications"])
        self.assertEqual(analysis["summary"]["same_body_different_wrapper_groups"], 1)
        self.assertEqual(analysis["summary"]["redundant_context_chars"], len(monthly))

    def test_cross_question_different_preceding_sequence_is_non_prefix_candidate(self) -> None:
        shared = "shared memory"
        first = "first prefix memory"
        second = "second prefix memory"
        first_context = wrapped("first", first) + wrapped("shared", shared)
        second_context = wrapped("second", second) + wrapped("shared", shared)
        analysis = ANALYZER.analyze_records(
            [
                record("q1", [result("first", first), result("shared", shared)], first_context),
                record("q2", [result("second", second), result("shared", shared)], second_context),
            ]
        )

        shared_hash = ANALYZER.content_hash(shared)
        reuse = next(row for row in analysis["reuse_groups"] if row["content_hash"] == shared_hash)
        self.assertEqual(reuse["occurrence_count"], 2)
        self.assertEqual(reuse["question_count"], 2)
        self.assertTrue(reuse["different_preceding_memory_context"])
        self.assertTrue(reuse["cross_context_non_prefix_candidate"])
        self.assertEqual(reuse["potential_reusable_chars"], len(shared))
        self.assertEqual(analysis["summary"]["non_prefix_candidate_groups"], 1)
        self.assertEqual(analysis["summary"]["non_prefix_potential_reusable_chars"], len(shared))

    def test_logical_memory_context_pairs_have_mutually_exclusive_variation_counts(self) -> None:
        same_body = "same target body"
        changed_body = "changed target body"
        records = []
        cases = (
            ("q1", "prefix-a", "aaa", same_body),
            ("q2", "prefix-b", "bbb", same_body),
            ("q3", "prefix-c", "aaa", changed_body),
            ("q4", "prefix-d", "aaa", same_body),
        )
        for question_id, prefix_subject, prefix_body, target_body in cases:
            context = wrapped(prefix_subject, prefix_body) + wrapped("target", target_body)
            records.append(
                record(
                    question_id,
                    [result(prefix_subject, prefix_body), result("target", target_body)],
                    context,
                )
            )

        analysis = ANALYZER.analyze_records(records, max_pair_detail_rows=100)
        target = next(
            row for row in analysis["context_variation_groups"] if row["subject"] == "target"
        )
        target_pairs = [
            row for row in analysis["context_pair_comparisons"] if row["subject"] == "target"
        ]

        self.assertEqual(target["context_occurrence_count"], 4)
        self.assertEqual(target["comparable_context_pair_count"], 6)
        self.assertEqual(target["content_changed_pairs"], 3)
        self.assertEqual(target["same_content_prefix_or_position_changed_pairs"], 2)
        self.assertEqual(target["same_content_prefix_changed_only_pairs"], 2)
        self.assertEqual(target["same_content_position_changed_only_pairs"], 0)
        self.assertEqual(target["same_content_prefix_and_position_changed_pairs"], 0)
        self.assertEqual(target["all_same_pairs"], 1)
        self.assertEqual(len(target_pairs), 6)
        self.assertEqual(
            {
                row["classification"] for row in target_pairs
            },
            {"CONTENT_CHANGED", "SAME_CONTENT_PREFIX_CHANGED", "ALL_SAME"},
        )

    def test_missing_results_is_skipped_and_missing_context_is_warned(self) -> None:
        analysis = ANALYZER.analyze_records(
            [
                {"question_id": "missing-results"},
                {"question_id": "missing-context", "results": [result("event", "body")]},
            ]
        )
        self.assertEqual(analysis["summary"]["records_analyzed"], 1)
        self.assertEqual(analysis["summary"]["records_skipped"], 1)
        self.assertEqual(analysis["summary"]["warning_count"], 2)
        self.assertEqual(analysis["chunks"][0]["context_char_start"], None)

    def test_cli_writes_all_outputs_and_honors_limit(self) -> None:
        first = record("q1", [result("event", "body one")], wrapped("event", "body one"))
        second = record("q2", [result("event", "body two")], wrapped("event", "body two"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.json"
            output_dir = root / "output"
            input_path.write_text(json.dumps([first, second], ensure_ascii=False), encoding="utf-8")

            exit_code = ANALYZER.main(
                ["--input", str(input_path), "--output", str(output_dir), "--limit", "1"]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {
                    "records.csv",
                    "chunks.csv",
                    "reuse_groups.csv",
                    "intra_context_duplicates.csv",
                    "context_variation_groups.csv",
                    "context_pair_comparisons.csv",
                    "context_variation_report.md",
                    "summary.json",
                },
            )
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["records_analyzed"], 1)
            with (output_dir / "chunks.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertNotIn("content", rows[0])
            self.assertEqual(rows[0]["content_preview"], "body one")
            report = (output_dir / "context_variation_report.md").read_text(encoding="utf-8")
            self.assertIn("LifeMem 上下文变化直观报告", report)


if __name__ == "__main__":
    unittest.main()
