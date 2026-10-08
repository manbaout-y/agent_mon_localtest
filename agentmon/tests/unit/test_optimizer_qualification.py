from __future__ import annotations

import argparse
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agentmon_optimizer_qualification.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agentmon_optimizer_qualification", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class OptimizerQualificationTests(unittest.TestCase):
    def test_parses_pretty_printed_final_harness_json_after_progress_output(self) -> None:
        module = load_module()
        output = "[analysis:stage] progress\n" + json.dumps({"status": "PARTIAL", "snapshot_count": 1}, indent=2)

        self.assertEqual(module.parse_last_json_object(output)["snapshot_count"], 1)

    def test_nonqualified_manifest_only_records_diagnostic_hashes(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "batch"
            batch.mkdir()
            gate = root / "gate.json"
            gate.write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")
            payload = module.qualification_payload(
                status="NOT_QUALIFIED",
                reason="source_gate_failed",
                args=argparse.Namespace(batch_root=str(batch)),
                gate_path=gate,
                gate_payload={"status": "FAIL"},
                harness_result=None,
                harness_stdout="sensitive process output",
                harness_stderr="sensitive error output",
            )

        self.assertEqual(payload["status"], "NOT_QUALIFIED")
        self.assertNotIn("sensitive process output", json.dumps(payload))
        self.assertTrue(payload["execution_diagnostics"]["harness_stdout_sha256"])

    def test_target_environment_puts_verified_source_before_inherited_pythonpath(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "installed"
            environment = module.target_import_environment(root)

        self.assertEqual(environment["PYTHONPATH"].split(os.pathsep)[0], str(root / "src"))

    def test_analysis_identity_must_match_full_gate_source_tree(self) -> None:
        module = load_module()
        gate = {"source_tree_sha256": "verified-tree"}

        self.assertTrue(
            module.analysis_identity_matches_gate(
                {"source_identity": {"source_identity_mode": "packaged_verified", "source_tree_sha256": "verified-tree"}},
                gate,
            )
        )
        self.assertFalse(
            module.analysis_identity_matches_gate(
                {"source_identity": {"source_identity_mode": "working_tree_unsealed", "source_tree_sha256": "verified-tree"}},
                gate,
            )
        )


if __name__ == "__main__":
    unittest.main()
