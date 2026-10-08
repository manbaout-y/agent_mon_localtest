from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentmon.experiments.experiment_cli import main as experiment_cli_main
from agentmon.experiments.mechanisms import mechanism_definition, mechanism_registry
from agentmon.experiments.orchestration import (
    evaluate_activation_probe,
    orchestrate_experiment,
    select_analysis_route,
)
from agentmon.experiments.request import generate_experiment_contract, load_experiment_request


class ExperimentOrchestrationTests(unittest.TestCase):
    def test_ready_for_formal_when_contract_activation_and_pilot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            write_jsonl(
                events,
                [
                    {
                        "schema_version": "1.0",
                        "source": "control",
                        "event_type": "control.model_gate.acquire",
                        "payload": {"reason": "advise_only", "request_id": "req-1"},
                    }
                ],
            )
            batch = make_batch(root, row_count=3, success_count=3)
            contract = base_contract(batch, events)
            contract["analysis"] = {
                "batch_root": str(batch),
                "task_file": str(root / "tasks.tsv"),
                "analysis_preset": "standard",
                "analysis_workers": 1,
            }
            contract["commands"] = {"formal_matrix": ["echo", "formal"]}

            report = orchestrate_experiment(contract, output_dir=root / "orchestration")

            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["ready_for_formal"], "YES")
            self.assertEqual(report["analysis_route"]["route"], "batch_native_analyze_existing")
            self.assertIn("--analyze-existing", report["analysis_route"]["command"])
            formal = [row for row in report["command_results"] if row["stage"] == "formal_matrix"][0]
            self.assertEqual(formal["status"], "SKIPPED")
            self.assertEqual(formal["reason"], "formal_matrix_requires_--run-formal")
            self.assertTrue((root / "orchestration" / "ready_for_formal.env").is_file())

    def test_formal_is_blocked_when_activation_probe_observes_disabled_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            write_jsonl(
                events,
                [
                    {
                        "schema_version": "1.0",
                        "source": "control",
                        "event_type": "control.model_gate.acquire",
                        "payload": {"reason": "gate_disabled", "request_id": "req-1"},
                    }
                ],
            )
            batch = make_batch(root, row_count=3, success_count=3)

            report = orchestrate_experiment(base_contract(batch, events), output_dir=root / "orchestration")

            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["ready_for_formal"], "NO")
            self.assertIn("mechanism_not_active:B:model_gate", report["blocking_reasons"])
            probe = report["activation_probe_report"]["probes"][0]
            self.assertEqual(probe["matched_count"], 0)
            self.assertEqual(probe["negative_reason_counts"]["gate_disabled"], 1)

    def test_disallowed_diff_and_wrong_execution_path_block_formal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            write_jsonl(
                events,
                [
                    {
                        "schema_version": "1.0",
                        "source": "control",
                        "event_type": "control.model_gate.acquire",
                        "payload": {"reason": "advise_only"},
                    }
                ],
            )
            batch = make_batch(root, row_count=3, success_count=3)
            contract = base_contract(batch, events)
            contract["variants"]["B"]["execution_path"] = "direct_model_endpoint"
            contract["variants"]["B"]["config"]["model"]["name"] = "changed-model"

            report = orchestrate_experiment(contract, output_dir=root / "orchestration")

            self.assertEqual(report["ready_for_formal"], "NO")
            self.assertTrue(any(reason.startswith("wrong_execution_path:B:model_gate") for reason in report["blocking_reasons"]))
            self.assertIn("disallowed_variant_diff:B:model.name", report["blocking_reasons"])

    def test_unknown_history_directory_routes_to_analysis_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history"
            history.mkdir()
            route = select_analysis_route({"analysis": {"input_path": str(history), "output_path": str(root / "analysis")}})

            self.assertEqual(route["status"], "PASS")
            self.assertEqual(route["route"], "analysis_runtime_run_all")
            self.assertIn("run-all", route["command"])

    def test_mechanism_registry_drives_activation_profile_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            write_jsonl(
                events,
                [
                    {
                        "event_type": "context_compression.decision",
                        "payload": {"context_compression_status": "APPLIED"},
                    }
                ],
            )
            registry = mechanism_registry()
            definition = mechanism_definition("context_compression")

            self.assertIn("context_compression", registry)
            self.assertEqual(definition["coverage"]["min_ratio"], 0.95)

            report = evaluate_activation_probe(
                {
                    "variant": "B",
                    "mechanism": "context_compression",
                    "evidence_paths": [str(events)],
                    "coverage_denominator": 1,
                }
            )

            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["matched_count"], 1)
            self.assertEqual(report["coverage_status"], "PASS")
            self.assertEqual(report["observed_coverage_ratio"], 1.0)

    def test_request_generator_creates_contract_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks.tsv"
            tasks.write_text("task_id\nT-1\n", encoding="utf-8")
            request_path = root / "experiment.toml"
            request_path.write_text(
                "\n".join(
                    [
                        'mechanism = ["context_compression"]',
                        "",
                        "[experiment]",
                        'name = "Context Compression Test"',
                        "",
                        "[source]",
                        f'repo = "{root}"',
                        "",
                        "[workload]",
                        f'tasks = "{tasks}"',
                        "",
                        "[pilot]",
                        "tasks = 3",
                        "",
                        "[formal]",
                        "tasks = 50",
                        "concurrency = [1, 5, 10]",
                    ]
                ),
                encoding="utf-8",
            )

            contract = generate_experiment_contract(load_experiment_request(request_path))

            self.assertEqual(contract["experiment_id"], "context-compression-test")
            self.assertIn("context_compression.*", contract["allowed_variant_differences"])
            self.assertEqual(contract["variants"]["B"]["mechanisms"], ["context_compression"])
            self.assertEqual(contract["variants"]["B"]["execution_path"], "agent_adapter_context_compression")
            self.assertTrue(contract["variants"]["B"]["config"]["runtime_optimization"]["enabled"])
            self.assertTrue(contract["variants"]["B"]["config"]["context_compression"]["enabled"])
            self.assertFalse(contract["variants"]["A"]["config"]["runtime_optimization"]["enabled"])
            self.assertEqual(contract["pilot"]["min_task_count"], 3)
            self.assertEqual(contract["formal"]["concurrency"], [1, 5, 10])
            self.assertEqual(contract["analysis"]["task_file"], str(tasks))

    def test_unified_prepare_and_smoke_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_path = root / "experiment.toml"
            request_path.write_text(
                "\n".join(
                    [
                        'mechanism = ["model_gate"]',
                        "",
                        "[experiment]",
                        'name = "Model Gate Test"',
                        "",
                        "[source]",
                        f'repo = "{root}"',
                    ]
                ),
                encoding="utf-8",
            )
            out = root / "prepare"

            prepare_rc = experiment_cli_main(["prepare", "--request", str(request_path), "--output-dir", str(out)])

            self.assertEqual(prepare_rc, 0)
            contract_path = out / "experiment-contract.json"
            self.assertTrue(contract_path.is_file())

            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            events = root / "events.jsonl"
            write_jsonl(
                events,
                [
                    {
                        "event_type": "control.model_gate.acquire",
                        "payload": {"reason": "advise_only"},
                    }
                ],
            )
            contract["variants"]["B"]["activation_evidence_paths"] = [str(events)]
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            smoke_out = root / "smoke"

            smoke_rc = experiment_cli_main(["smoke", "--contract", str(contract_path), "--output-dir", str(smoke_out)])

            self.assertEqual(smoke_rc, 0)
            self.assertTrue((smoke_out / "ready_for_pilot.env").is_file())
            self.assertIn("READY_FOR_PILOT=YES", (smoke_out / "ready_for_pilot.env").read_text(encoding="utf-8"))


def base_contract(batch: Path, events: Path) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "experiment_id": "model-gate-ab",
        "baseline_variant": "A",
        "allowed_variant_differences": [
            "runtime_optimization.enabled",
            "model_gate.enabled",
            "control.mode",
        ],
        "variants": {
            "A": {
                "role": "baseline",
                "config": {
                    "runtime_optimization": {"enabled": False},
                    "model_gate": {"enabled": False},
                    "control": {"mode": "advise"},
                    "model": {"name": "same-model"},
                },
            },
            "B": {
                "role": "candidate",
                "mechanisms": ["model_gate"],
                "execution_path": "runtime_controlled_model",
                "activation_evidence_paths": [str(events)],
                "config": {
                    "runtime_optimization": {"enabled": True},
                    "model_gate": {"enabled": True},
                    "control": {"mode": "advise"},
                    "model": {"name": "same-model"},
                },
            },
        },
        "pilot": {"batch_root": str(batch), "min_task_count": 3, "min_success_count": 3},
    }


def make_batch(root: Path, *, row_count: int, success_count: int) -> Path:
    batch = root / "batch"
    batch.mkdir()
    (batch / "batch-manifest.json").write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
    rows = []
    for index in range(row_count):
        rows.append(
            {
                "task_id": f"task-{index}",
                "run_id": f"run-{index}",
                "status": "success" if index < success_count else "failed",
                "execution_success": "true" if index < success_count else "false",
            }
        )
    with (batch / "batch-results.tsv").open("w", encoding="utf-8", newline="") as handle:
        handle.write("task_id\trun_id\tstatus\texecution_success\n")
        for row in rows:
            handle.write(f"{row['task_id']}\t{row['run_id']}\t{row['status']}\t{row['execution_success']}\n")
    return batch


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
