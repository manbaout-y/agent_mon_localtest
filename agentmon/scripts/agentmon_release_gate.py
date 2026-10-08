#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmon.pipeline.identity import SOURCE_MANIFEST_RELATIVE_PATH, verify_source_tree  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    started_wall_time_ns = time.time_ns()
    gate_level = gate_level_for_args(args)
    deadline_seconds = deadline_seconds_for_args(args, gate_level)
    deadline = started + deadline_seconds if deadline_seconds is not None else None
    package = Path(args.package).expanduser().resolve() if args.package else None
    package_checks = inspect_package(package, allow_missing_sha256=args.allow_missing_sha256) if package else []
    if args.installed_source_root:
        installed_root = Path(args.installed_source_root).expanduser().resolve()
        package_checks.append(
            check_installed_source_matches_package(package, installed_root)
            if package
            else {
                "name": "installed_source_matches_package",
                "status": "FAIL",
                "required": True,
                "detail": {
                    "installed_source_root": str(installed_root),
                    "error": "package_required_for_installed_source_check",
                },
            }
        )
    if any(item["status"] == "FAIL" for item in package_checks):
        manifest = manifest_payload(
            args=args,
            source_root=None,
            python=agentmon_python_for_args(args),
            tests=package_checks,
            status="FAIL",
            status_reason="failed_checks",
            gate_level=gate_level,
            started=started,
            started_wall_time_ns=started_wall_time_ns,
            deadline_seconds=deadline_seconds,
        )
        write_gate_outputs(args, gate_level, manifest)
        emit_stage_markers(gate_level, "FAIL", "failed_checks")
        return 1

    source_root, extraction, source_root_check = resolve_source_root(args)
    python = agentmon_python_for_args(args)
    identity_checks = formal_source_identity_checks(
        package=package,
        installed_source_root=Path(args.installed_source_root).expanduser().resolve() if args.installed_source_root else None,
        python=python,
        gate_level=gate_level,
    )
    package_sha256 = package_sha256_from_checks(package_checks)
    python_checks = [
        probe_python("agentmon_python_probe", python, require_pytest=True),
        probe_python(
            "vllm_python_probe",
            Path(args.vllm_python).expanduser().resolve(),
            require_pytest=False,
            require_vllm=True,
        )
        if args.vllm_python
        else {
            "name": "vllm_python_probe",
            "status": "SKIPPED",
            "required": False,
            "detail": {"reason": "not supplied"},
        },
    ]
    previous_gate_check = check_previous_gate(
        Path(args.require_previous_gate).expanduser().resolve(),
        package_sha256=package_sha256,
    ) if args.require_previous_gate else {
        "name": "previous_gate",
        "status": "SKIPPED",
        "required": False,
        "detail": {"reason": "not supplied"},
    }
    tests = [
        *package_checks,
        *identity_checks,
        source_root_check,
        previous_gate_check,
        *python_checks,
        *planned_tests(source_root, full=gate_level == "full"),
    ]
    if args.dry_run:
        manifest = manifest_payload(
            args=args,
            source_root=source_root,
            python=python,
            tests=[
                item
                if item.get("status") in {"PASS", "FAIL", "SKIPPED"}
                else {**item, "status": "PLANNED"}
                for item in tests
            ],
            status="PLANNED",
            status_reason="planned",
            gate_level=gate_level,
            started=started,
            started_wall_time_ns=started_wall_time_ns,
            deadline_seconds=deadline_seconds,
        )
        write_gate_outputs(args, gate_level, manifest)
        cleanup_extraction(extraction)
        return 0

    results: list[dict[str, Any]] = [item for item in tests if item.get("status") in {"PASS", "FAIL", "SKIPPED"}]
    status = "PASS"
    status_reason = "pass"
    deadline_expired = False
    blocking_failure = any(item["status"] == "FAIL" for item in results if item.get("required", True))
    if blocking_failure:
        status = "FAIL"
        status_reason = "failed_checks"
    for item in tests:
        if item.get("status") in {"PASS", "FAIL", "SKIPPED"}:
            print(f"{item['name']}: {item['status']}")
            if item["status"] == "FAIL" and args.fail_fast:
                break
            continue
        if blocking_failure:
            result = {
                **item,
                "status": "SKIPPED",
                "required": True,
                "detail": {"reason": "blocking_gate_failed"},
            }
            results.append(result)
            print(f"{item['name']}: SKIPPED")
            continue
        remaining = remaining_deadline_seconds(deadline)
        if remaining is not None and remaining <= 0:
            result = deadline_expired_result(item)
            results.append(result)
            print(f"{item['name']}: FAIL")
            status = "FAIL"
            status_reason = "timeout"
            deadline_expired = True
            break
        item_timeout = args.timeout_seconds
        if remaining is not None:
            item_timeout = max(0.001, min(item_timeout, remaining))
        result = run_test_item(item, python=python, source_root=source_root, timeout=item_timeout)
        if result.get("error") == "TimeoutExpired" and remaining is not None and item_timeout < args.timeout_seconds:
            result = {
                **result,
                "detail": {
                    **result.get("detail", {}),
                    "reason": "total_deadline_expired",
                    "deadline_seconds": deadline_seconds,
                },
            }
        results.append(result)
        print(f"{item['name']}: {result['status']}")
        if result["status"] != "PASS":
            status = "FAIL"
            status_reason = "timeout" if result.get("error") == "TimeoutExpired" else "failed_checks"
            if args.fail_fast:
                break
        if deadline is not None and time.monotonic() >= deadline:
            deadline_expired = True
            if status == "PASS":
                status = "FAIL"
                status_reason = "timeout"
            break

    manifest = manifest_payload(
        args=args,
        source_root=source_root,
        python=python,
        tests=results,
        status=status,
        status_reason="timeout" if deadline_expired else status_reason,
        gate_level=gate_level,
        started=started,
        started_wall_time_ns=started_wall_time_ns,
        deadline_seconds=deadline_seconds,
    )
    write_gate_outputs(args, gate_level, manifest)
    cleanup_extraction(extraction)
    emit_stage_markers(gate_level, status, manifest["status_reason"])
    return 0 if status == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the versioned AgentMon release gate and write TEST_MANIFEST JSON.")
    parser.add_argument("--source-root", help="unpacked AgentMon source root; defaults to this repository")
    parser.add_argument("--package", help="optional source tarball to validate by extracting to a temp directory")
    parser.add_argument(
        "--installed-source-root",
        help="compare the actual installed source tree against --package; required for full server eligibility",
    )
    parser.add_argument("--python", default=None, help="deprecated alias for --agentmon-python")
    parser.add_argument("--agentmon-python", help="Python interpreter used for AgentMon source gates")
    parser.add_argument("--vllm-python", help="optional Python interpreter from the actual vLLM/CANN environment")
    parser.add_argument("--output", help="write release gate JSON to this path")
    parser.add_argument("--test-manifest", help="write TEST_MANIFEST JSON to this path")
    parser.add_argument("--provenance-dir", help="write quick-gate.json or full-source-gate.json under this directory")
    parser.add_argument(
        "--checksum-manifest",
        help=(
            "update a delivery SHA256SUMS-style file for the package and explicit "
            "--output/--test-manifest files; provenance gate JSON files keep only sidecar .sha256"
        ),
    )
    parser.add_argument("--require-previous-gate", help="require a prior quick-gate.json before running the full source gate")
    parser.add_argument("--level", choices=("quick", "full"), help="gate level; quick skips full tests")
    parser.add_argument("--skip-full-tests", action="store_true", help="run directed gates but skip full pytest suite")
    parser.add_argument("--allow-missing-sha256", action="store_true", help="do not fail --package when the adjacent .sha256 file is absent")
    parser.add_argument("--fail-fast", action="store_true", help="stop after the first failed gate")
    parser.add_argument("--dry-run", action="store_true", help="write the planned gate without executing commands")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        help="total wall-clock deadline for this gate; defaults to 600 for quick, disabled for full; use 0 to disable",
    )
    return parser


def gate_level_for_args(args: argparse.Namespace) -> str:
    if args.level:
        return str(args.level)
    return "quick" if args.skip_full_tests else "full"


def agentmon_python_for_args(args: argparse.Namespace) -> Path:
    value = args.agentmon_python or args.python or sys.executable
    return Path(value).expanduser().resolve()


def deadline_seconds_for_args(args: argparse.Namespace, gate_level: str) -> float | None:
    if args.deadline_seconds is not None:
        return None if args.deadline_seconds <= 0 else float(args.deadline_seconds)
    return 600.0 if gate_level == "quick" else None


def remaining_deadline_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def inspect_package(package: Path, *, allow_missing_sha256: bool = False) -> list[dict[str, Any]]:
    sha_check = check_package_sha256(package, allow_missing=allow_missing_sha256)
    archive_check = check_archive_members(package)
    return [
        sha_check,
        archive_check["safety"],
        archive_check["clean"],
    ]


def check_installed_source_matches_package(
    package: Path,
    installed_source_root: Path,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "package": str(package),
        "installed_source_root": str(installed_source_root),
    }
    if not package.is_file():
        return {
            "name": "installed_source_matches_package",
            "status": "FAIL",
            "required": True,
            "detail": {**detail, "error": "package_not_found"},
        }
    if not installed_source_root.is_dir():
        return {
            "name": "installed_source_matches_package",
            "status": "FAIL",
            "required": True,
            "detail": {**detail, "error": "installed_source_root_not_found"},
        }
    manifest_check = load_package_source_manifest(package)
    if manifest_check["status"] == "PASS":
        verification = verify_source_tree(installed_source_root, manifest_check["detail"]["source_manifest"])
        return {
            "name": "installed_source_matches_package",
            "status": verification["status"],
            "required": True,
            "detail": {**detail, **verification, "verification_mode": "embedded_source_manifest"},
        }
    missing: list[str] = []
    modified: list[str] = []
    checked = 0
    try:
        with tarfile.open(package, "r:gz") as tar:
            members = [member for member in tar.getmembers() if member.isfile()]
            top_levels = {Path(member.name).parts[0] for member in members if Path(member.name).parts}
            if len(top_levels) != 1:
                return {
                    "name": "installed_source_matches_package",
                    "status": "FAIL",
                    "required": True,
                    "detail": {**detail, "error": "package_has_ambiguous_top_level", "top_levels": sorted(top_levels)},
                }
            for member in members:
                parts = Path(member.name).parts
                relative = Path(*parts[1:])
                installed_path = installed_source_root / relative
                checked += 1
                if not installed_path.is_file():
                    missing.append(relative.as_posix())
                    continue
                source = tar.extractfile(member)
                if source is None:
                    missing.append(relative.as_posix())
                    continue
                with source:
                    package_digest = sha256_stream(source)
                if package_digest != sha256_file(installed_path):
                    modified.append(relative.as_posix())
    except Exception as exc:  # noqa: BLE001 - gate output must preserve package comparison failures.
        return {
            "name": "installed_source_matches_package",
            "status": "FAIL",
            "required": True,
            "detail": {**detail, "error": type(exc).__name__, "message": str(exc)},
        }
    status = "PASS" if not missing and not modified else "FAIL"
    return {
        "name": "installed_source_matches_package",
        "status": status,
        "required": True,
        "detail": {
            **detail,
            "checked_file_count": checked,
            "missing_file_count": len(missing),
            "modified_file_count": len(modified),
            "missing_files": missing[:100],
            "modified_files": modified[:100],
        },
    }


def formal_source_identity_checks(
    *,
    package: Path | None,
    installed_source_root: Path | None,
    python: Path,
    gate_level: str,
) -> list[dict[str, Any]]:
    if gate_level != "full":
        return [{"name": "formal_source_identity", "status": "SKIPPED", "required": False, "detail": {"reason": "quick_gate"}}]
    if package is None:
        return [{"name": "package_source_manifest", "status": "FAIL", "required": True, "detail": {"error": "package_required_for_full_gate"}}]
    manifest = load_package_source_manifest(package)
    if installed_source_root is None:
        return [manifest, {"name": "installed_source_identity", "status": "FAIL", "required": True, "detail": {"error": "installed_source_root_required_for_full_gate"}}]
    return [manifest, check_imported_source_root(python, installed_source_root)]


def load_package_source_manifest(package: Path) -> dict[str, Any]:
    detail: dict[str, Any] = {"package": str(package)}
    try:
        with tarfile.open(package, "r:gz") as tar:
            members = [member for member in tar.getmembers() if member.isfile()]
            candidates = [member for member in members if Path(member.name).parts[1:] == SOURCE_MANIFEST_RELATIVE_PATH.parts]
            if len(candidates) != 1:
                return {"name": "package_source_manifest", "status": "FAIL", "required": True, "detail": {**detail, "error": "embedded_source_manifest_missing_or_ambiguous", "candidate_count": len(candidates)}}
            source = tar.extractfile(candidates[0])
            if source is None:
                return {"name": "package_source_manifest", "status": "FAIL", "required": True, "detail": {**detail, "error": "embedded_source_manifest_unreadable"}}
            with source:
                payload = json.loads(source.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - release output must preserve verification errors.
        return {"name": "package_source_manifest", "status": "FAIL", "required": True, "detail": {**detail, "error": type(exc).__name__, "message": str(exc)}}
    if not isinstance(payload, dict) or payload.get("manifest_type") != "agentmon.source_tree" or not payload.get("source_tree_sha256"):
        return {"name": "package_source_manifest", "status": "FAIL", "required": True, "detail": {**detail, "error": "embedded_source_manifest_invalid"}}
    return {"name": "package_source_manifest", "status": "PASS", "required": True, "detail": {**detail, "source_tree_sha256": payload["source_tree_sha256"], "source_manifest": payload}}


def check_imported_source_root(python: Path, installed_source_root: Path) -> dict[str, Any]:
    modules = ("agentmon", "agentmon.experiments.replay", "agentmon.optimizer.harness.runner")
    probe = "import importlib,json; names=" + repr(modules) + "; print(json.dumps({name:getattr(importlib.import_module(name),'__file__',None) for name in names}))"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([str(installed_source_root / "src"), environment.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    try:
        completed = subprocess.run([str(python), "-c", probe], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=30, check=False)
        paths = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except Exception as exc:  # noqa: BLE001
        return {"name": "imported_source_root", "status": "FAIL", "required": True, "detail": {"error": type(exc).__name__, "message": str(exc)}}
    outside = []
    for name, raw in paths.items():
        try:
            Path(str(raw)).resolve().relative_to(installed_source_root)
        except (TypeError, ValueError):
            outside.append({"module": name, "path": raw})
    return {
        "name": "imported_source_root",
        "status": "PASS" if len(paths) == len(modules) and not outside else "FAIL",
        "required": True,
        "detail": {"module_files": paths, "outside_installed_root": outside, "stderr": completed.stderr[-4000:]},
    }


def sha256_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def check_package_sha256(package: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    sha_path = package.with_name(package.name + ".sha256")
    detail: dict[str, Any] = {"package": str(package), "sha256_path": str(sha_path)}
    if not package.is_file():
        return {"name": "package_sha256", "status": "FAIL", "required": True, "detail": {**detail, "error": "package_not_found"}}
    actual = sha256_file(package)
    detail["actual_sha256"] = actual
    if not sha_path.is_file():
        return {
            "name": "package_sha256",
            "status": "SKIPPED" if allow_missing else "FAIL",
            "required": not allow_missing,
            "detail": {**detail, "error": "sha256_file_not_found"},
        }
    expected = expected_sha256(sha_path, package.name)
    detail["expected_sha256"] = expected
    return {
        "name": "package_sha256",
        "status": "PASS" if expected == actual else "FAIL",
        "required": True,
        "detail": detail,
    }


def package_sha256_from_checks(checks: Sequence[dict[str, Any]]) -> str | None:
    for item in checks:
        if item.get("name") == "package_sha256":
            value = item.get("detail", {}).get("actual_sha256")
            return str(value) if value else None
    return None


def check_previous_gate(path: Path, *, package_sha256: str | None) -> dict[str, Any]:
    detail: dict[str, Any] = {"path": str(path), "expected_package_sha256": package_sha256}
    if not path.is_file():
        return {
            "name": "previous_gate",
            "status": "FAIL",
            "required": True,
            "detail": {**detail, "error": "previous_gate_not_found"},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - preserve malformed provenance as gate data.
        return {
            "name": "previous_gate",
            "status": "FAIL",
            "required": True,
            "detail": {**detail, "error": type(exc).__name__, "message": str(exc)},
        }
    observed_sha256 = payload.get("package_sha256")
    failures: list[str] = []
    if payload.get("gate_level") != "quick":
        failures.append("previous_gate_not_quick")
    if payload.get("status") != "PASS":
        failures.append("previous_gate_not_pass")
    if package_sha256 and observed_sha256 != package_sha256:
        failures.append("package_sha256_mismatch")
    return {
        "name": "previous_gate",
        "status": "PASS" if not failures else "FAIL",
        "required": True,
        "detail": {
            **detail,
            "observed_package_sha256": observed_sha256,
            "observed_status": payload.get("status"),
            "observed_gate_level": payload.get("gate_level"),
            "failures": failures,
        },
    }


def expected_sha256(path: Path, package_name: str) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        if len(parts) == 1 or parts[-1] == package_name:
            return parts[0]
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_archive_members(package: Path) -> dict[str, dict[str, Any]]:
    detail: dict[str, Any] = {"package": str(package), "member_count": 0}
    unsafe: list[str] = []
    forbidden: list[str] = []
    try:
        with tarfile.open(package, "r:gz") as tar:
            members = tar.getmembers()
    except Exception as exc:  # noqa: BLE001 - report archive parse failures as gate data.
        failed = {
            "status": "FAIL",
            "required": True,
            "detail": {**detail, "error": type(exc).__name__, "message": str(exc)},
        }
        return {
            "safety": {"name": "archive_safety", **failed},
            "clean": {"name": "archive_clean", **failed},
        }
    detail["member_count"] = len(members)
    for member in members:
        reason = unsafe_member_reason(member)
        if reason:
            unsafe.append(f"{member.name}: {reason}")
        if forbidden_member(member.name):
            forbidden.append(member.name)
    return {
        "safety": {
            "name": "archive_safety",
            "status": "PASS" if not unsafe else "FAIL",
            "required": True,
            "detail": {**detail, "unsafe_members": unsafe[:50], "unsafe_member_count": len(unsafe)},
        },
        "clean": {
            "name": "archive_clean",
            "status": "PASS" if not forbidden else "FAIL",
            "required": True,
            "detail": {**detail, "forbidden_members": forbidden[:50], "forbidden_member_count": len(forbidden)},
        },
    }


def unsafe_member_reason(member: tarfile.TarInfo) -> str | None:
    path = Path(member.name)
    if path.is_absolute():
        return "absolute_path"
    if ".." in path.parts:
        return "path_traversal"
    if member.islnk() or member.issym():
        return "link_member"
    if not (member.isdir() or member.isfile()):
        return "non_regular_member"
    return None


def forbidden_member(member_name: str) -> bool:
    path = Path(member_name)
    parts = path.parts
    if any(part in {"__MACOSX", ".AppleDouble", "__pycache__"} for part in parts):
        return True
    name = path.name
    return name in {".DS_Store"} or name.startswith("._") or name.endswith((".pyc", ".pyo"))


def resolve_source_root(args: argparse.Namespace) -> tuple[Path, Path | None, dict[str, Any]]:
    if args.package:
        package = Path(args.package).expanduser().resolve()
        temp_root = Path(tempfile.mkdtemp(prefix="agentmon-release-gate."))
        with tarfile.open(package, "r:gz") as tar:
            safe_extract(tar, temp_root)
        roots = find_source_roots(temp_root)
        source_root = roots[0] if len(roots) == 1 else temp_root
        return (
            source_root,
            temp_root,
            {
                "name": "package_source_root",
                "status": "PASS" if len(roots) == 1 else "FAIL",
                "required": True,
                "detail": {"source_roots": [str(path) for path in roots], "selected": str(source_root)},
            },
        )
    source_root = Path(args.source_root).expanduser().resolve() if args.source_root else ROOT
    return (
        source_root,
        None,
        {
            "name": "package_source_root",
            "status": "SKIPPED",
            "required": False,
            "detail": {"reason": "--package not supplied", "selected": str(source_root)},
        },
    )


def find_source_roots(root: Path) -> list[Path]:
    roots: set[Path] = set()
    for marker in root.rglob("src/agentmon/__init__.py"):
        try:
            roots.add(marker.parents[2])
        except IndexError:
            continue
    return sorted(roots)


def safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in tar.getmembers():
        target = (destination / member.name).resolve()
        try:
            target.relative_to(destination)
        except ValueError:
            raise ValueError(f"archive member escapes destination: {member.name}") from None
        if member.islnk() or member.issym():
            raise ValueError(f"archive member uses a link: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise ValueError(f"archive member is not a regular file or directory: {member.name}")
    for member in tar.getmembers():
        target = (destination / member.name).resolve()
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = tar.extractfile(member)
        if source is None:
            raise ValueError(f"archive member has no file data: {member.name}")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def planned_tests(source_root: Path, *, full: bool) -> list[dict[str, Any]]:
    tests = [
        {
            "name": "source_compile",
            "argv": ["-m", "compileall", "-q", str(source_root / "src"), str(source_root / "scripts"), str(source_root / "tests")],
        },
        {
            "name": "agentmon_import",
            "argv": ["-c", "import agentmon; print('AGENTMON_IMPORT=PASS')"],
        },
        {
            "name": "agentmon_cli",
            "argv": ["-m", "agentmon", "--version"],
        },
        {
            "name": "p2_discovery_directed",
            "argv": [
                "-m",
                "pytest",
                "-q",
                str(source_root / "tests/unit/test_p2_discovery.py"),
                str(source_root / "tests/unit/test_p2_vllm_adapter.py::P2VllmAdapterTests::test_runtime_hook_unsupported_version_does_not_patch"),
            ],
        },
        {
            "name": "server_config_runner_directed",
            "argv": ["-m", "pytest", "-q", str(source_root / "tests/unit/test_server_test_config_runner.py")],
        },
    ]
    if full:
        tests.append(
            {
                "name": "full_tests",
                "argv": ["-m", "pytest", "-q", "--disable-warnings", str(source_root / "tests")],
            }
        )
    return tests


def run_test_item(item: dict[str, Any], *, python: Path, source_root: Path, timeout: float) -> dict[str, Any]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(source_root / "src"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(python), *item["argv"]],
            cwd=str(source_root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            **item,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "returncode": completed.returncode,
            "duration_seconds": time.monotonic() - started,
            "stdout_tail": completed.stdout[-20000:],
            "stderr_tail": completed.stderr[-20000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            **item,
            "status": "FAIL",
            "returncode": None,
            "duration_seconds": time.monotonic() - started,
            "stdout_tail": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-20000:] if isinstance(exc.stderr, str) else "",
            "error": "TimeoutExpired",
        }


def deadline_expired_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "status": "FAIL",
        "required": True,
        "returncode": None,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
        "error": "TotalDeadlineExpired",
        "detail": {"reason": "total_deadline_expired"},
    }


def probe_python(name: str, python: Path, *, require_pytest: bool, require_vllm: bool = False) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "python": str(python),
        "require_pytest": require_pytest,
        "require_vllm": require_vllm,
    }
    if not python.exists() or not os.access(python, os.X_OK):
        return {
            "name": name,
            "status": "FAIL",
            "required": True,
            "detail": {**detail, "error": "python_not_executable"},
        }
    script = (
        "import importlib.util,json,sys;"
        "print(json.dumps({"
        "'executable':sys.executable,"
        "'version':sys.version,"
        "'version_info':list(sys.version_info[:5]),"
        "'pytest_available':importlib.util.find_spec('pytest') is not None,"
        "'numpy_available':importlib.util.find_spec('numpy') is not None,"
        "'pandas_available':importlib.util.find_spec('pandas') is not None,"
        "'vllm_available':importlib.util.find_spec('vllm') is not None,"
        "'torch_npu_available':importlib.util.find_spec('torch_npu') is not None"
        "},sort_keys=True))"
    )
    completed = subprocess.run(
        [str(python), "-c", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    payload = last_json(completed.stdout)
    raw_version_info = payload.get("version_info")
    version_info = tuple(raw_version_info[:2]) if isinstance(raw_version_info, list) else ()
    failures: list[str] = []
    if completed.returncode != 0:
        failures.append("python_probe_failed")
    if not version_info or version_info < (3, 10):
        failures.append("python_version_below_3_10")
    if require_pytest and payload.get("pytest_available") is not True:
        failures.append("pytest_unavailable")
    if require_vllm and payload.get("vllm_available") is not True:
        failures.append("vllm_unavailable")
    ok = (
        not failures
    )
    return {
        "name": name,
        "status": "PASS" if ok else "FAIL",
        "required": True,
        "returncode": completed.returncode,
        "detail": {
            **detail,
            "minimum_python_version": "3.10",
            "failures": failures,
            "probe": payload,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        },
    }


def last_json(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def manifest_payload(
    *,
    args: argparse.Namespace,
    source_root: Path | None,
    python: Path,
    tests: list[dict[str, Any]],
    status: str,
    status_reason: str,
    gate_level: str,
    started: float,
    started_wall_time_ns: int,
    deadline_seconds: float | None,
) -> dict[str, Any]:
    pass_status = status == "PASS"
    planned_status = status == "PLANNED"
    finished_wall_time_ns = time.time_ns()
    package_path = Path(args.package).expanduser().resolve() if args.package else None
    package_sha256 = package_sha256_from_checks(tests)
    source_tree_sha256 = next(
        (
            item.get("detail", {}).get("source_tree_sha256")
            for item in tests
            if item.get("name") == "package_source_manifest" and isinstance(item.get("detail"), dict)
        ),
        None,
    )
    checks = {
        str(item.get("name")): {
            "status": item.get("status"),
            "required": item.get("required", True),
        }
        for item in tests
        if item.get("name")
    }
    python_probe = next(
        (
            item.get("detail", {}).get("probe")
            for item in tests
            if item.get("name") == "agentmon_python_probe"
            and isinstance(item.get("detail"), dict)
            and isinstance(item.get("detail", {}).get("probe"), dict)
        ),
        {},
    )
    return {
        "schema_version": "1.0",
        "manifest_type": "agentmon.release_gate.test",
        "status": status,
        "status_reason": status_reason,
        "gate_level": gate_level,
        "quick_smoke_status": "PLANNED" if gate_level == "quick" and planned_status else "PASS" if gate_level == "quick" and pass_status else "FAIL" if gate_level == "quick" else "NOT_RUN",
        "source_release_gate_status": "PLANNED" if gate_level == "full" and planned_status else "PASS" if gate_level == "full" and pass_status else "FAIL" if gate_level == "full" else "NOT_RUN",
        "eligible_for_full_source_gate": bool(gate_level == "quick" and pass_status),
        "eligible_for_server_preflight": bool(gate_level == "full" and pass_status),
        "eligible_for_next_stage": bool((gate_level == "quick" and pass_status) or (gate_level == "full" and pass_status)),
        "next_stage": "full_source_gate" if gate_level == "quick" else "server_preflight",
        "created_wall_time_ns": finished_wall_time_ns,
        "started_wall_time_ns": started_wall_time_ns,
        "finished_wall_time_ns": finished_wall_time_ns,
        "started_at": isoformat_wall_time_ns(started_wall_time_ns),
        "finished_at": isoformat_wall_time_ns(finished_wall_time_ns),
        "duration_seconds": time.monotonic() - started,
        "deadline_seconds": deadline_seconds,
        "source_root": str(source_root) if source_root else None,
        "package": str(package_path) if package_path else None,
        "installed_source_root": str(Path(args.installed_source_root).expanduser().resolve()) if args.installed_source_root else None,
        "package_sha256": package_sha256,
        "source_tree_sha256": source_tree_sha256,
        "source_identity_status": checks.get("package_source_manifest", {}).get("status", "NOT_VERIFIED"),
        "agentmon_python": str(python),
        "vllm_python": str(Path(args.vllm_python).expanduser().resolve()) if args.vllm_python else None,
        "python": {
            "executable": python_probe.get("executable", str(python)),
            "version": python_probe.get("version", "UNKNOWN"),
            "version_info": python_probe.get("version_info", []),
        },
        "platform": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "system": platform.system(),
        },
        "skip_full_tests": bool(args.skip_full_tests),
        "checks": checks,
        "tests": tests,
    }


def isoformat_wall_time_ns(wall_time_ns: int) -> str:
    return datetime.fromtimestamp(wall_time_ns / 1_000_000_000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def attach_manifest_integrity(payload: dict[str, Any]) -> dict[str, Any]:
    without_integrity = {key: value for key, value in payload.items() if key != "manifest_integrity"}
    canonical = json.dumps(without_integrity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        **without_integrity,
        "manifest_integrity": {
            "hash_algorithm": "sha256",
            "payload_canonical_sha256": hashlib.sha256(canonical).hexdigest(),
            "full_file_sha256": "see adjacent .sha256 sidecar",
        },
    }


def gate_output_name(gate_level: str) -> str:
    return "quick-gate.json" if gate_level == "quick" else "full-source-gate.json"


def output_destinations(args: argparse.Namespace, gate_level: str) -> list[Path]:
    destinations: list[Path] = []
    for raw in (args.output, args.test_manifest):
        if raw:
            destinations.append(Path(raw).expanduser().resolve())
    if args.provenance_dir:
        destinations.append(Path(args.provenance_dir).expanduser().resolve() / gate_output_name(gate_level))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in destinations:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def delivery_checksum_destinations(args: argparse.Namespace) -> list[Path]:
    destinations: list[Path] = []
    seen: set[Path] = set()
    for raw in (args.output, args.test_manifest):
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if path not in seen:
            seen.add(path)
            destinations.append(path)
    return destinations


def write_gate_outputs(args: argparse.Namespace, gate_level: str, payload: dict[str, Any]) -> None:
    payload = attach_manifest_integrity(payload)
    destinations = output_destinations(args, gate_level)
    if not destinations:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    written: list[Path] = []
    for path in destinations:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"MANIFEST={path}")
        write_sha256_sidecar(path)
        written.append(path)
    if args.checksum_manifest:
        files = [Path(args.package).expanduser().resolve()] if args.package else []
        files.extend(path for path in delivery_checksum_destinations(args) if path in written)
        update_checksum_manifest(Path(args.checksum_manifest).expanduser().resolve(), files)


def write_sha256_sidecar(path: Path) -> Path:
    digest = sha256_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    print(f"MANIFEST_SHA256={sidecar}")
    return sidecar


def update_checksum_manifest(path: Path, files: Sequence[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                entries[parts[1]] = parts[0]
    for file_path in files:
        if not file_path.is_file():
            continue
        entries[checksum_label(file_path, path.parent)] = sha256_file(file_path)
    lines = [f"{digest}  {label}" for label, digest in entries.items()]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"CHECKSUM_MANIFEST={path}")


def checksum_label(file_path: Path, base: Path) -> str:
    resolved = file_path.resolve()
    try:
        return resolved.relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def emit_stage_markers(gate_level: str, status: str, reason: str) -> None:
    eligible = status == "PASS"
    if gate_level == "quick":
        print(f"QUICK_SMOKE={status}")
        if status != "PASS":
            print(f"REASON={reason}")
        print(f"ELIGIBLE_FOR_FULL_SOURCE_GATE={'YES' if eligible else 'NO'}")
        print(f"ELIGIBLE_FOR_NEXT_STAGE={'YES' if eligible else 'NO'}")
        return
    print(f"SOURCE_RELEASE_GATE={status}")
    if status != "PASS":
        print(f"REASON={reason}")
    print(f"ELIGIBLE_FOR_SERVER_PREFLIGHT={'YES' if eligible else 'NO'}")
    print(f"ELIGIBLE_FOR_NEXT_STAGE={'YES' if eligible else 'NO'}")


def cleanup_extraction(path: Path | None) -> None:
    if path is None:
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
