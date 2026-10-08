from __future__ import annotations

import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SCRIPT = ROOT / "scripts" / "package_agentmon_source.py"


def load_package_module():
    spec = importlib.util.spec_from_file_location("package_agentmon_source", PACKAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SourcePackageTests(unittest.TestCase):
    def test_source_package_excludes_macos_metadata_and_bytecode(self) -> None:
        module = load_package_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            output = Path(tmp) / "dist" / "agentmon-test-src.tar.gz"
            write_fixture_repo(repo)

            summary = module.build_source_package(repo, output, prefix="agentmon-test")

            with tarfile.open(output, mode="r:gz") as tar:
                members = tar.getnames()
                source_manifest = json.loads(
                    tar.extractfile("agentmon-test/.agentmon/source-manifest.json").read().decode("utf-8")
                )

            self.assertEqual(summary["status"], "PASS")
            self.assertTrue((output.with_name(output.name + ".sha256")).exists())
            build_manifest_path = output.with_name(output.name + ".BUILD_MANIFEST.json")
            self.assertTrue(build_manifest_path.exists())
            build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(build_manifest["manifest_type"], "agentmon.source_package.build")
            self.assertEqual(build_manifest["package_sha256"], summary["sha256"])
            self.assertEqual(build_manifest["archive_prefix"], "agentmon-test")
            self.assertEqual(build_manifest["forbidden_member_count"], 0)
            self.assertIn("agentmon-test/src/agentmon/__init__.py", members)
            self.assertIn("agentmon-test/.agentmon/source-manifest.json", members)
            self.assertIn("agentmon-test/scripts/run.py", members)
            self.assertNotIn("agentmon-test/.DS_Store", members)
            self.assertFalse(any("/.DS_Store" in member for member in members))
            self.assertFalse(any("/._" in member for member in members))
            self.assertFalse(any("__MACOSX" in member for member in members))
            self.assertFalse(any("__pycache__" in member for member in members))
            self.assertFalse(any(member.endswith(".pyc") for member in members))
            self.assertFalse(any("artifacts/packages/old.tar.gz" in member for member in members))
            self.assertEqual(module.forbidden_archive_members(members), [])
            self.assertEqual(source_manifest["manifest_type"], "agentmon.source_tree")
            self.assertTrue(source_manifest["source_tree_sha256"])


def write_fixture_repo(repo: Path) -> None:
    (repo / "src" / "agentmon" / "__pycache__").mkdir(parents=True)
    (repo / "scripts" / "__pycache__").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    (repo / "examples").mkdir(parents=True)
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "__MACOSX").mkdir(parents=True)
    (repo / "artifacts" / "packages").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname='agentmon-test'\n", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    (repo / ".DS_Store").write_text("metadata\n", encoding="utf-8")
    (repo / "src" / ".DS_Store").write_text("metadata\n", encoding="utf-8")
    (repo / "src" / "agentmon" / "__init__.py").write_text("__version__='0'\n", encoding="utf-8")
    (repo / "src" / "agentmon" / "._shadow.py").write_text("appledouble\n", encoding="utf-8")
    (repo / "src" / "agentmon" / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"pyc")
    (repo / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "scripts" / "__pycache__" / "run.cpython-312.pyc").write_bytes(b"pyc")
    (repo / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
    (repo / "examples" / "config.json").write_text("{}\n", encoding="utf-8")
    (repo / "tests" / "unit" / "test_example.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (repo / "__MACOSX" / "ignored").write_text("ignored\n", encoding="utf-8")
    (repo / "artifacts" / "README.md").write_text("artifacts readme\n", encoding="utf-8")
    (repo / "artifacts" / "packages" / "old.tar.gz").write_bytes(b"old")


if __name__ == "__main__":
    unittest.main()
