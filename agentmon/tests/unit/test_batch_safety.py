from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from agentmon.batch.safety import (
    WorkspacePolicyError,
    build_isolated_runtime_env,
    build_workspace_contracts,
    capture_source_manifest,
    compare_source_manifests,
    prepare_runtime_source_snapshot,
    render_workspace_contract,
)
from agentmon.batch.summary import build_batch_summary


def args_for(**overrides):
    values = {
        "workspace_template": "{batch_root}/workspaces/{task_id}",
        "cwd_template": "{workspace}",
        "workspace_policy": "isolated",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class BatchSafetyTests(unittest.TestCase):
    def test_default_workspace_is_isolated_and_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_root = root / "batch"
            protected = root / "agentmon"
            contract = render_workspace_contract(
                args_for(),
                {"task_id": "t-1"},
                batch_root,
                [protected],
            )

        self.assertEqual(contract["status"], "PASS")
        self.assertEqual(contract["policy"], "isolated")
        self.assertEqual(
            contract["workspace"],
            str((batch_root / "workspaces" / "t-1").resolve(strict=False)),
        )

    def test_workspace_cannot_point_to_or_contain_protected_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protected = root / "agentmon"
            protected.mkdir()
            task = {"task_id": "t-1"}

            with self.assertRaisesRegex(WorkspacePolicyError, "escapes the isolated workspace root|overlaps protected"):
                render_workspace_contract(
                    args_for(
                        workspace_template=str(protected),
                        workspace_policy="external",
                    ),
                    task,
                    root / "batch",
                    [protected],
                )
            with self.assertRaisesRegex(WorkspacePolicyError, "overlaps protected"):
                render_workspace_contract(
                    args_for(
                        workspace_template=str(root),
                        workspace_policy="external",
                    ),
                    task,
                    root / "batch",
                    [protected],
                )

    def test_symlink_escape_to_protected_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_root = root / "batch"
            workspace_root = batch_root / "workspaces"
            workspace_root.mkdir(parents=True)
            protected = root / "agentmon"
            protected.mkdir()
            (workspace_root / "t-1").symlink_to(protected, target_is_directory=True)

            with self.assertRaisesRegex(
                WorkspacePolicyError,
                "escapes the isolated workspace root|overlaps protected",
            ):
                render_workspace_contract(
                    args_for(),
                    {"task_id": "t-1"},
                    batch_root,
                    [protected],
                )

    def test_two_tasks_cannot_share_a_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = args_for(workspace_template=str(root / "shared"), workspace_policy="external")
            with self.assertRaisesRegex(WorkspacePolicyError, "same workspace"):
                build_workspace_contracts(
                    args,
                    [{"task_id": "t-1"}, {"task_id": "t-2"}],
                    root / "batch",
                    [root / "protected"],
                )

    def test_source_manifest_reports_added_modified_and_removed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src" / "agentmon"
            source.mkdir(parents=True)
            first = source / "first.py"
            removed = source / "removed.py"
            first.write_text("value = 1\n", encoding="utf-8")
            removed.write_text("old = True\n", encoding="utf-8")
            before = capture_source_manifest(root, relative_roots=("src",))

            first.write_text("value = 2\n", encoding="utf-8")
            removed.unlink()
            (source / "added.py").write_text("new = True\n", encoding="utf-8")
            after = capture_source_manifest(root, relative_roots=("src",))
            comparison = compare_source_manifests(before, after)

        self.assertEqual(comparison["status"], "CHANGED")
        self.assertEqual(comparison["modified_files"], ["src/agentmon/first.py"])
        self.assertEqual(comparison["removed_files"], ["src/agentmon/removed.py"])
        self.assertEqual(comparison["added_files"], ["src/agentmon/added.py"])

    def test_isolated_env_hides_original_source_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protected = root / "agentmon"
            protected_src = protected / "src"
            snapshot_src = root / "snapshot" / "src"
            safe_path = root / "dependencies"
            for path in (protected_src, snapshot_src, safe_path):
                path.mkdir(parents=True)
            env, report = build_isolated_runtime_env(
                {
                    "PYTHONPATH": f"{protected_src}:{safe_path}",
                    "AGENTMON_HOME": str(protected),
                    "KEEP_ME": "yes",
                },
                snapshot_pythonpath=snapshot_src,
                protected_roots=[protected],
            )

        self.assertEqual(env["PYTHONPATH"], f"{snapshot_src}:{safe_path}")
        self.assertNotIn("AGENTMON_HOME", env)
        self.assertEqual(env["KEEP_ME"], "yes")
        self.assertEqual(report["status"], "PASS")

    def test_runtime_snapshot_is_an_exact_read_only_source_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package" / "src" / "agentmon"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("__version__ = 'test'\n", encoding="utf-8")
            snapshot = prepare_runtime_source_snapshot(
                root / "package",
                root / "batch",
                identity="run-1",
            )
            copied = Path(snapshot["pythonpath"]) / "agentmon" / "__init__.py"

            self.assertEqual(snapshot["copy_verification"]["status"], "PASS")
            self.assertEqual(copied.read_text(encoding="utf-8"), "__version__ = 'test'\n")
            self.assertEqual(copied.stat().st_mode & 0o222, 0)

    def test_missing_agentmon_run_id_invalidates_experiment_even_with_zero_exit(self) -> None:
        summary = build_batch_summary(
            [
                {
                    "task_id": "t-1",
                    "status": "agentmon_run_id_missing",
                    "run_id_required": True,
                    "run_id": "",
                    "execution_success": True,
                }
            ],
            batch_wall_s=1.0,
            expected_task_count=1,
            runtime_source_integrity_status="PASS",
            analysis_complete=False,
        )

        self.assertFalse(summary["experiment_valid"])
        self.assertEqual(summary["validity_status"], "INVALID")
        self.assertEqual(summary["completion"]["agentmon_run_id_missing"], 1)
        self.assertIn("agentmon_run_id_missing", summary["completion"]["invalid_reasons"])
        self.assertTrue(summary["completion"]["forbidden_conclusions"])

    def test_unknown_source_integrity_keeps_validity_unresolved(self) -> None:
        summary = build_batch_summary(
            [{"task_id": "t-1", "status": "workload_complete", "run_id": "r-1"}],
            batch_wall_s=1.0,
            expected_task_count=1,
            runtime_source_integrity_status="UNKNOWN",
        )

        self.assertIsNone(summary["experiment_valid"])
        self.assertEqual(summary["validity_status"], "UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
