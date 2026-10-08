from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from agentmon.pipeline.identity import SOURCE_MANIFEST_RELATIVE_PATH, build_source_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agentmon_release_gate.py"


def load_release_gate_module():
    spec = importlib.util.spec_from_file_location("agentmon_release_gate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseGateTests(unittest.TestCase):
    def test_python_probe_rejects_interpreter_below_project_minimum(self) -> None:
        module = load_release_gate_module()
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "executable": sys.executable,
                    "version": "3.9.0",
                    "version_info": [3, 9, 0, "final", 0],
                    "pytest_available": True,
                    "numpy_available": True,
                    "pandas_available": True,
                    "vllm_available": False,
                    "torch_npu_available": False,
                }
            ),
            stderr="",
        )

        with patch.object(module.subprocess, "run", return_value=completed):
            result = module.probe_python(
                "agentmon_python_probe",
                Path(sys.executable),
                require_pytest=True,
            )

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("python_version_below_3_10", result["detail"]["failures"])

    def test_installed_source_must_match_package_files(self) -> None:
        module = load_release_gate_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "source.tar.gz"
            installed = root / "installed"
            (installed / "src").mkdir(parents=True)
            (installed / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            with tarfile.open(package, "w:gz") as tar:
                add_bytes(tar, "release/src/module.py", b"VALUE = 1\n")

            matching = module.check_installed_source_matches_package(package, installed)
            (installed / "src" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
            modified = module.check_installed_source_matches_package(package, installed)

        self.assertEqual(matching["status"], "PASS")
        self.assertEqual(modified["status"], "FAIL")
        self.assertEqual(modified["detail"]["modified_files"], ["src/module.py"])

    def test_dry_run_writes_test_manifest_with_planned_gates(self) -> None:
        module = load_release_gate_module()
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "TEST_MANIFEST.json"
            exit_code = module.main(
                [
                    "--source-root",
                    str(ROOT),
                    "--dry-run",
                    "--skip-full-tests",
                    "--test-manifest",
                    str(manifest_path),
                ]
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(manifest["manifest_type"], "agentmon.release_gate.test")
        self.assertEqual(manifest["status"], "PLANNED")
        self.assertEqual(manifest["gate_level"], "quick")
        self.assertEqual(manifest["quick_smoke_status"], "PLANNED")
        self.assertTrue(manifest["skip_full_tests"])
        self.assertTrue(any(item["name"] == "p2_discovery_directed" for item in manifest["tests"]))
        planned = [item for item in manifest["tests"] if item["name"] == "p2_discovery_directed"]
        self.assertEqual(planned[0]["status"], "PLANNED")

    def test_archive_member_checks_reject_traversal_and_macos_metadata(self) -> None:
        module = load_release_gate_module()
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "bad.tar.gz"
            with tarfile.open(package, "w:gz") as tar:
                add_bytes(tar, "../escape.txt", b"bad")
                add_bytes(tar, "repo/.DS_Store", b"bad")
                add_bytes(tar, "repo/src/agentmon/__init__.py", b"")

            checks = module.check_archive_members(package)

        self.assertEqual(checks["safety"]["status"], "FAIL")
        self.assertEqual(checks["clean"]["status"], "FAIL")
        self.assertEqual(checks["safety"]["detail"]["unsafe_member_count"], 1)
        self.assertEqual(checks["clean"]["detail"]["forbidden_member_count"], 1)

    def test_dry_run_writes_provenance_and_delivery_checksum_manifest(self) -> None:
        module = load_release_gate_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "TEST_MANIFEST.quick.json"
            provenance = root / "provenance"
            checksums = root / "SHA256SUMS"
            exit_code = module.main(
                [
                    "--source-root",
                    str(ROOT),
                    "--dry-run",
                    "--level",
                    "quick",
                    "--test-manifest",
                    str(manifest_path),
                    "--provenance-dir",
                    str(provenance),
                    "--checksum-manifest",
                    str(checksums),
                ]
            )
            quick_gate = provenance / "quick-gate.json"
            payload = json.loads(quick_gate.read_text(encoding="utf-8"))
            checksum_text = checksums.read_text(encoding="utf-8")
            manifest_sidecar_exists = manifest_path.with_name(manifest_path.name + ".sha256").is_file()
            quick_sidecar_exists = quick_gate.with_name(quick_gate.name + ".sha256").is_file()

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["gate_level"], "quick")
        self.assertEqual(payload["status"], "PLANNED")
        self.assertIn("manifest_integrity", payload)
        self.assertTrue(manifest_sidecar_exists)
        self.assertTrue(quick_sidecar_exists)
        self.assertIn("TEST_MANIFEST.quick.json", checksum_text)
        self.assertNotIn("provenance/quick-gate.json", checksum_text)

    def test_quick_deadline_failure_marks_no_next_stage(self) -> None:
        module = load_release_gate_module()
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "TEST_MANIFEST.quick.json"
            stdout = io.StringIO()
            with (
                redirect_stdout(stdout),
                patch.object(
                    module,
                    "probe_python",
                    return_value={"name": "python_probe", "status": "PASS", "required": True},
                ),
                patch.object(module, "remaining_deadline_seconds", return_value=-1.0),
            ):
                exit_code = module.main(
                    [
                        "--source-root",
                        str(ROOT),
                        "--level",
                        "quick",
                        "--deadline-seconds",
                        "0.000001",
                        "--test-manifest",
                        str(manifest_path),
                    ]
                )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["status_reason"], "timeout")
        self.assertFalse(payload["eligible_for_full_source_gate"])
        self.assertFalse(payload["eligible_for_next_stage"])
        self.assertIn("QUICK_SMOKE=FAIL", stdout.getvalue())
        self.assertIn("ELIGIBLE_FOR_FULL_SOURCE_GATE=NO", stdout.getvalue())
        self.assertIn("ELIGIBLE_FOR_NEXT_STAGE=NO", stdout.getvalue())

    def test_previous_gate_requires_matching_package_sha256(self) -> None:
        module = load_release_gate_module()
        with tempfile.TemporaryDirectory() as tmp:
            gate = Path(tmp) / "quick-gate.json"
            gate.write_text(
                json.dumps(
                    {
                        "gate_level": "quick",
                        "status": "PASS",
                        "package_sha256": "aaa",
                    }
                ),
                encoding="utf-8",
            )
            ok = module.check_previous_gate(gate, package_sha256="aaa")
            mismatch = module.check_previous_gate(gate, package_sha256="bbb")

        self.assertEqual(ok["status"], "PASS")
        self.assertEqual(mismatch["status"], "FAIL")
        self.assertIn("package_sha256_mismatch", mismatch["detail"]["failures"])

    def test_embedded_manifest_rejects_unexpected_managed_source_file(self) -> None:
        module = load_release_gate_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = root / "release"
            installed = root / "installed"
            for target in (release, installed):
                (target / "src").mkdir(parents=True)
                (target / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
                (target / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            manifest = build_source_manifest(release)
            package = root / "source.tar.gz"
            with tarfile.open(package, "w:gz") as tar:
                for path in sorted(release.rglob("*")):
                    if path.is_file():
                        tar.add(path, arcname=f"release/{path.relative_to(release).as_posix()}")
                add_bytes(tar, f"release/{SOURCE_MANIFEST_RELATIVE_PATH.as_posix()}", json.dumps(manifest).encode("utf-8"))
            (installed / "src" / "unexpected.py").write_text("VALUE = 2\n", encoding="utf-8")

            result = module.check_installed_source_matches_package(package, installed)

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["detail"]["unexpected_files"], ["src/unexpected.py"])


def add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    import io

    tar.addfile(info, io.BytesIO(data))


if __name__ == "__main__":
    unittest.main()
