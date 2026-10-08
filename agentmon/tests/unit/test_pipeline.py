from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentmon.pipeline import COMPLETE, FAILED, PARTIAL, UNRESOLVED, ArtifactRef, PipelineExecutor, PipelineStageResult, StageSpec
from agentmon.pipeline.executor import file_sha256


class PipelineExecutorTests(unittest.TestCase):
    def test_capability_contract_allows_partial_evidence_and_blocks_missing_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specs = (
                StageSpec("inventory", "test"),
                StageSpec("replay", "test", dependencies=("inventory",), required_capabilities=("timeline",)),
                StageSpec("blocked", "test", dependencies=("inventory",), required_capabilities=("missing",)),
            )
            executor = PipelineExecutor(output_root=root, specs=specs)
            results = executor.run(
                {
                    "inventory": lambda context: PipelineStageResult(outcome=PARTIAL, provided_capabilities=("timeline",)),
                    "replay": lambda context: PipelineStageResult(outcome=COMPLETE),
                    "blocked": lambda context: PipelineStageResult(outcome=COMPLETE),
                }
            )

            self.assertEqual(results["replay"].outcome, COMPLETE)
            self.assertEqual(results["blocked"].outcome, "SKIPPED")
            self.assertEqual(results["blocked"].missing_capabilities, ("missing",))

    def test_resume_uses_artifact_hash_and_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact.txt"
            artifact.write_text("stable\n", encoding="utf-8")
            specs = (StageSpec("cached", "test"),)
            identity = {"source_tree_sha256": "fixture", "source_identity_mode": "working_tree_unsealed"}
            first = PipelineExecutor(output_root=root, specs=specs, source_identity=identity)
            first.run({"cached": lambda context: PipelineStageResult(artifacts=(ArtifactRef.from_path(artifact, sha256=file_sha256(artifact)),))})
            second = PipelineExecutor(output_root=root, specs=specs, source_identity=identity, resume=True)
            results = second.run({"cached": lambda context: self.fail("cached stage must not run")})

            self.assertEqual(results["cached"].outcome, COMPLETE)
            self.assertEqual(second.dispositions["cached"], "CACHED")

    def test_pipeline_status_preserves_unresolved_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executor = PipelineExecutor(output_root=root, specs=(StageSpec("inventory", "test"),))

            executor.run({"inventory": lambda context: PipelineStageResult(outcome=UNRESOLVED)})

            status = json.loads((root / "pipeline_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["state"], COMPLETE)
            self.assertEqual(status["detail"]["outcome"], UNRESOLVED)

    def test_stage_can_explicitly_accept_failed_dependency_for_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specs = (
                StageSpec("work", "test"),
                StageSpec("report", "test", dependencies=("work",), accepted_upstream_outcomes=(FAILED,)),
            )
            executor = PipelineExecutor(output_root=root, specs=specs)

            results = executor.run(
                {
                    "work": lambda context: PipelineStageResult(outcome=FAILED),
                    "report": lambda context: PipelineStageResult(outcome=COMPLETE),
                }
            )

            self.assertEqual(results["report"].outcome, COMPLETE)


if __name__ == "__main__":
    unittest.main()
