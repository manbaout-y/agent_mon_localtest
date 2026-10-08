import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISCOVERY_SCRIPT = ROOT / "scripts" / "agentmon_p2_discovery.py"


def load_discovery_module():
    spec = importlib.util.spec_from_file_location("agentmon_p2_discovery", DISCOVERY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class P2DiscoveryTests(unittest.TestCase):
    def test_vllm_version_status_accepts_local_suffix_only_for_target_version(self) -> None:
        module = load_discovery_module()

        self.assertEqual(module.vllm_version_status("0.18.0"), "compatible")
        self.assertEqual(module.vllm_version_status("0.18.0+empty"), "compatible")
        self.assertEqual(module.vllm_version_status("0.19.0"), "mismatch")
        self.assertEqual(module.vllm_version_status(None), "missing")

    def test_collect_snapshot_records_module_object_signature_and_source(self) -> None:
        module = load_discovery_module()
        with tempfile.TemporaryDirectory() as tmp:
            write_fake_vllm(Path(tmp))
            sys.path.insert(0, tmp)
            try:
                snapshot = module.collect_snapshot(
                    package_names=["vllm"],
                    module_names=["vllm.config", "vllm.core.scheduler", "vllm.legacy.missing"],
                    object_refs=[
                        "vllm.config:CacheConfig",
                        "vllm.core.scheduler:Scheduler",
                        "vllm.config:MissingObject",
                    ],
                    scan_package_names=["vllm"],
                    scan_limit=50,
                    source_lines=20,
                )
            finally:
                sys.path.remove(tmp)
                clear_fake_vllm_modules()

        self.assertEqual(snapshot["snapshot_type"], "agentmon.p2_discovery")
        self.assertEqual(snapshot["readiness"]["vllm_version_status"], "compatible")
        self.assertEqual(snapshot["packages"]["vllm"]["version"], "0.18.0+empty")
        self.assertEqual(snapshot["packages"]["vllm"]["version_source"], "module.__version__")
        self.assertEqual(snapshot["readiness"]["compatibility_status"], "compatible")
        self.assertEqual(snapshot["readiness"]["vllm_version_evidence"]["version_identity_status"], "exact_match")
        self.assertEqual(snapshot["modules"]["vllm.config"]["status"], "ok")
        cache = snapshot["objects"]["vllm.config:CacheConfig"]
        self.assertEqual(cache["status"], "ok")
        self.assertIn("block_size", cache["init_signature"])
        self.assertIn("source", cache)
        scheduler = snapshot["objects"]["vllm.core.scheduler:Scheduler"]
        self.assertEqual(scheduler["status"], "ok")
        self.assertTrue(any(row["name"] == "schedule" for row in scheduler["methods"]))
        scanned_names = {
            row["name"]
            for row in snapshot["package_scans"]["vllm"]["candidate_modules"]
        }
        self.assertIn("vllm.core.scheduler", scanned_names)
        hook_inputs = snapshot["hook_resolution_inputs"]
        self.assertIn("Use successful_modules", hook_inputs["policy"])
        self.assertTrue(any(row["name"] == "vllm.config" for row in hook_inputs["successful_modules"]))
        self.assertTrue(any(row["ref"] == "vllm.config:CacheConfig" for row in hook_inputs["successful_objects"]))
        self.assertFalse(any(row["name"] == "vllm.legacy.missing" for row in hook_inputs["successful_modules"]))
        self.assertTrue(any(row["name"] == "vllm.legacy.missing" for row in hook_inputs["ignored_failed_modules"]))
        self.assertTrue(any(row["ref"] == "vllm.config:MissingObject" for row in hook_inputs["ignored_failed_objects"]))

    def test_cli_writes_json_and_strict_version_fails_on_mismatch(self) -> None:
        module = load_discovery_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fake_vllm(root, version="0.19.0", noisy_import=True)
            sys.path.insert(0, tmp)
            try:
                output_path = root / "snapshot.json"
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = module.main(
                        [
                            "--output",
                            str(output_path),
                            "--strict-version",
                            "--package",
                            "vllm",
                            "--module",
                            "vllm.config",
                            "--no-default-scan",
                            "--object",
                            "vllm.config:CacheConfig",
                        ]
                    )
            finally:
                sys.path.remove(tmp)
                clear_fake_vllm_modules()

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(payload["readiness"]["vllm_version_status"], "mismatch")
        self.assertEqual(payload["readiness"]["compatibility_status"], "mismatch")
        self.assertEqual(payload["packages"]["torch"]["status"], "metadata_only")
        self.assertEqual(payload["packages"]["torch_npu"]["status"], "metadata_only")

    def test_strict_version_uses_imported_module_version_over_distribution_metadata(self) -> None:
        module = load_discovery_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fake_vllm(root, version="0.19.0", metadata_version="0.18.0", noisy_import=True)
            sys.path.insert(0, tmp)
            try:
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = module.main(
                        [
                            "--strict-version",
                            "--package",
                            "vllm",
                            "--module",
                            "vllm.config",
                            "--no-default-scan",
                            "--object",
                            "vllm.config:CacheConfig",
                        ]
                    )
            finally:
                sys.path.remove(tmp)
                clear_fake_vllm_modules()

        self.assertNotIn("INFO noisy vllm import", stdout.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["packages"]["vllm"]["module_version"], "0.19.0")
        self.assertEqual(payload["packages"]["vllm"]["metadata_version"], "0.18.0")
        self.assertEqual(payload["readiness"]["vllm_version_status"], "mismatch")
        self.assertEqual(payload["readiness"]["compatibility_status"], "mismatch")
        self.assertEqual(
            payload["readiness"]["vllm_version_evidence"]["selected_source"],
            "module.__version__",
        )
        self.assertEqual(
            payload["readiness"]["vllm_version_evidence"]["consistency"],
            "conflict",
        )
        self.assertEqual(
            payload["readiness"]["vllm_version_evidence"]["version_source_consistency"],
            "conflict",
        )

    def test_metadata_local_suffix_is_source_evidence_not_compatibility_failure(self) -> None:
        module = load_discovery_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fake_vllm(root, version="0.18.0", metadata_version="0.18.0+empty")
            sys.path.insert(0, tmp)
            try:
                snapshot = module.collect_snapshot(
                    package_names=["vllm"],
                    module_names=["vllm.config"],
                    object_refs=["vllm.config:CacheConfig"],
                    scan_package_names=[],
                )
            finally:
                sys.path.remove(tmp)
                clear_fake_vllm_modules()

        evidence = snapshot["readiness"]["vllm_version_evidence"]
        self.assertEqual(snapshot["readiness"]["vllm_version_status"], "compatible")
        self.assertEqual(snapshot["readiness"]["compatibility_status"], "compatible")
        self.assertEqual(evidence["selected_version"], "0.18.0")
        self.assertEqual(evidence["metadata_version"], "0.18.0+empty")
        self.assertEqual(evidence["module_release"], "0.18.0")
        self.assertEqual(evidence["metadata_release"], "0.18.0")
        self.assertEqual(evidence["version_source_consistency"], "consistent")
        self.assertEqual(evidence["version_identity_status"], "release_match")

    def test_cli_subprocess_strict_version_returns_nonzero_on_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fake_vllm(root, version="0.19.0", noisy_import=True)
            env = {
                **os.environ,
                "PYTHONPATH": str(root),
                "PYTHONNOUSERSITE": "1",
            }
            completed = subprocess.run(
                [
                    sys.executable,
                    str(DISCOVERY_SCRIPT),
                    "--strict-version",
                    "--package",
                    "vllm",
                    "--module",
                    "vllm.config",
                    "--no-default-scan",
                    "--object",
                    "vllm.config:CacheConfig",
                ],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertNotIn("INFO noisy vllm import", completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["readiness"]["vllm_version_status"], "mismatch")

    def test_default_discovery_targets_current_vllm_018_scheduler_path(self) -> None:
        module = load_discovery_module()

        self.assertIn("vllm.v1.core.sched.scheduler", module.DEFAULT_MODULES)
        self.assertIn("vllm.v1.core.sched.scheduler:Scheduler", module.DEFAULT_OBJECTS)
        self.assertNotIn("vllm.v1.core.scheduler", module.DEFAULT_MODULES)
        self.assertNotIn("vllm.v1.core.scheduler:Scheduler", module.DEFAULT_OBJECTS)


def write_fake_vllm(
    root: Path,
    *,
    version: str = "0.18.0+empty",
    metadata_version: str | None = None,
    noisy_import: bool = False,
) -> None:
    (root / "vllm" / "core").mkdir(parents=True)
    write_fake_vllm_distribution(root, version=metadata_version or version)
    noisy_line = "print('INFO noisy vllm import')\n" if noisy_import else ""
    (root / "vllm" / "__init__.py").write_text(
        f"{noisy_line}__version__ = {version!r}\n",
        encoding="utf-8",
    )
    (root / "vllm" / "config.py").write_text(
        """
class CacheConfig:
    def __init__(self, block_size=16, cache_dtype="bfloat16"):
        self.block_size = block_size
        self.cache_dtype = cache_dtype


class EngineArgs:
    pass
""".lstrip(),
        encoding="utf-8",
    )
    (root / "vllm" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (root / "vllm" / "core" / "scheduler.py").write_text(
        """
class Scheduler:
    def __init__(self, cache_config=None):
        self.cache_config = cache_config

    def schedule(self, request_id=None):
        return request_id
""".lstrip(),
        encoding="utf-8",
    )


def write_fake_vllm_distribution(root: Path, *, version: str) -> None:
    safe_version = version.replace("+", "_").replace("-", "_")
    dist = root / f"vllm-{safe_version}.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: vllm\nVersion: {version}\n",
        encoding="utf-8",
    )


def clear_fake_vllm_modules() -> None:
    for name in list(sys.modules):
        if name == "vllm" or name.startswith("vllm."):
            sys.modules.pop(name, None)
