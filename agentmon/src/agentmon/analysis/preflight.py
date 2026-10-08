from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
from typing import Any

import agentmon
from agentmon.analysis.config import load_analysis_config
from agentmon.analysis.inventory import build_input_inventory
from agentmon.analysis.io import write_json
from agentmon.analysis.profiles import available_profiles


PREFLIGHT_SCHEMA_VERSION = "1.0"


def run_analysis_preflight(
    *,
    input_root: str | Path | None = None,
    output_root: str | Path | None = None,
    temp_dir: str | Path | None = None,
    profile: str = "auto",
    output_format: str = "parquet",
    min_free_bytes: int | None = None,
    json_output_path: str | Path | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    python_ok = sys.version_info >= (3, 10)
    checks.append(check_row("python_version", "REQUIRED", python_ok, platform.python_version()))

    cfg_status = "PASS"
    cfg_error = None
    try:
        parsed_cfg = load_analysis_config(overrides={"profile": profile})
        if profile != "auto" and profile not in available_profiles(parsed_cfg):
            raise ValueError(f"unknown analysis profile: {profile}")
    except Exception as exc:  # noqa: BLE001
        cfg_status = "FAIL"
        cfg_error = f"{type(exc).__name__}: {exc}"
    checks.append(check_row("profile_parse", "REQUIRED", cfg_status == "PASS", profile, cfg_error))

    input_status = check_path(input_root, kind="input", must_exist=True)
    output_status = check_path(output_root, kind="output", must_exist=False)
    temp_status = check_path(temp_dir or tempfile.gettempdir(), kind="temp", must_exist=False)
    checks.extend([input_status, output_status, temp_status])

    dependency_rows = dependency_checks(input_root=input_root, output_format=output_format)
    checks.extend(dependency_rows)
    checks.append(parquet_smoke_check(output_format=output_format))
    disk = disk_report(output_root or input_root or Path.cwd(), min_free_bytes=min_free_bytes)
    checks.append(disk)

    # Inventory samples event files. Do not touch them when a required runtime,
    # path, profile, or output capability has already failed.
    pre_inventory_blockers = [
        row for row in checks if row.get("category") == "REQUIRED" and row.get("status") != "PASS"
    ]
    inventory = (
        build_input_inventory(input_root, sample_jsonl_lines=5)
        if input_root and not pre_inventory_blockers
        else None
    )
    if inventory:
        checks.append(
            check_row(
                "input_inventory",
                "REQUIRED",
                inventory.get("inventory_status") in {"PASS", "PARTIAL"},
                inventory.get("inventory_status"),
                inventory.get("reason"),
            )
        )
    elif input_root and pre_inventory_blockers:
        checks.append(
            {
                "name": "input_inventory",
                "category": "REQUIRED",
                "status": "SKIPPED",
                "detail": None,
                "reason": "required_preflight_failed_before_input_scan",
            }
        )

    required = [row for row in checks if row.get("category") == "REQUIRED"]
    optional = [row for row in checks if row.get("category") == "OPTIONAL"]
    required_status = "PASS" if all(row.get("status") == "PASS" for row in required) else "FAIL"
    required_dependencies = [
        row
        for row in dependency_rows + [next(row for row in checks if row.get("name") == "parquet_smoke")]
        if row.get("category") == "REQUIRED"
    ]
    dependency_status = "PASS" if all(row.get("status") == "PASS" for row in required_dependencies) else "FAIL"
    optional_status = "PASS" if all(row.get("status") == "PASS" for row in optional) else "PARTIAL"
    result = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "agentmon_module_file": getattr(agentmon, "__file__", None),
        "agentmon_version": getattr(agentmon, "__version__", "unknown"),
        "platform": platform.platform(),
        "profile": profile,
        "output_format": output_format,
        "checks": checks,
        "required_dependencies_status": dependency_status,
        "optional_dependencies_status": optional_status,
        "python_preflight_status": "PASS" if required_status == "PASS" else "FAIL",
        "heavy_analysis_allowed": 1 if required_status == "PASS" else 0,
        "input_inventory": inventory,
    }
    if json_output_path:
        write_json(json_output_path, result)
    return result


def dependency_checks(*, input_root: str | Path | None, output_format: str) -> list[dict[str, Any]]:
    rows = [
        module_check("numpy", category="REQUIRED", purpose="core numerical analysis dependency"),
        module_check("pandas", category="REQUIRED", purpose="core tabular analysis and diagnostics dependency"),
    ]
    pyarrow_category = "REQUIRED" if output_format == "parquet" else "OPTIONAL"
    rows.append(module_check("pyarrow", category=pyarrow_category, purpose="Parquet table writer/reader"))
    zstd_required = input_has_zstd(input_root)
    rows.append(module_check("zstandard", category="REQUIRED" if zstd_required else "OPTIONAL", purpose="reading .jsonl.zst inputs"))
    return rows


def module_check(name: str, *, category: str, purpose: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        return {
            "name": name,
            "category": category,
            "status": "FAIL" if category == "REQUIRED" else "UNAVAILABLE",
            "version": None,
            "reason": f"{name}_not_installed",
            "detail": str(exc),
            "purpose": purpose,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "category": category,
            "status": "FAIL" if category == "REQUIRED" else "UNAVAILABLE",
            "version": None,
            "reason": f"{name}_import_error",
            "detail": f"{type(exc).__name__}: {exc}",
            "purpose": purpose,
        }
    return {
        "name": name,
        "category": category,
        "status": "PASS",
        "version": getattr(module, "__version__", None),
        "reason": None,
        "purpose": purpose,
    }


def parquet_smoke_check(*, output_format: str) -> dict[str, Any]:
    category = "REQUIRED" if output_format == "parquet" else "OPTIONAL"
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "smoke.parquet"
            pq.write_table(pa.table({"x": pa.array([1])}), path)
            rows = pq.read_table(path).to_pylist()
        ok = rows == [{"x": 1}]
        return check_row("parquet_smoke", category, ok, "roundtrip_ok" if ok else "roundtrip_mismatch")
    except ModuleNotFoundError as exc:
        row = check_row("parquet_smoke", category, False, "pyarrow_not_installed", str(exc))
        if category == "OPTIONAL":
            row["status"] = "UNAVAILABLE"
        return row
    except Exception as exc:  # noqa: BLE001
        return check_row("parquet_smoke", category, False, "parquet_roundtrip_failed", f"{type(exc).__name__}: {exc}")


def check_path(path: str | Path | None, *, kind: str, must_exist: bool) -> dict[str, Any]:
    if path is None or str(path) == "":
        return check_row(f"{kind}_path", "REQUIRED", not must_exist, None, "path_not_provided" if must_exist else None)
    target = Path(path).expanduser()
    if must_exist and not target.exists():
        return check_row(f"{kind}_path", "REQUIRED", False, str(target), "path_missing")
    try:
        if kind in {"output", "temp"}:
            target.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=target, delete=True) as handle:
                handle.write("agentmon-preflight\n")
        elif not os.access(target, os.R_OK):
            return check_row(f"{kind}_path", "REQUIRED", False, str(target), "path_not_readable")
    except Exception as exc:  # noqa: BLE001
        return check_row(f"{kind}_path", "REQUIRED", False, str(target), f"{type(exc).__name__}: {exc}")
    return check_row(f"{kind}_path", "REQUIRED", True, str(target))


def disk_report(path: str | Path, *, min_free_bytes: int | None) -> dict[str, Any]:
    target = Path(path).expanduser()
    probe = target if target.exists() else target.parent
    try:
        usage = shutil.disk_usage(probe)
    except Exception as exc:  # noqa: BLE001
        return check_row("disk_free", "OPTIONAL", False, str(probe), f"{type(exc).__name__}: {exc}")
    ok = min_free_bytes is None or usage.free >= min_free_bytes
    return {
        "name": "disk_free",
        "category": "REQUIRED" if min_free_bytes is not None else "OPTIONAL",
        "status": "PASS" if ok else "FAIL",
        "path": str(probe),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "min_free_bytes": min_free_bytes,
        "reason": None if ok else "free_space_below_configured_minimum",
    }


def check_row(name: str, category: str, ok: bool, detail: Any = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "status": "PASS" if ok else "FAIL",
        "detail": detail,
        "reason": reason,
    }


def input_has_zstd(input_root: str | Path | None) -> bool:
    if not input_root:
        return False
    root = Path(input_root).expanduser()
    if not root.exists():
        return False
    return any(path.name.endswith(".jsonl.zst") for path in root.rglob("*.zst"))


def preflight_status_lines(result: dict[str, Any]) -> list[str]:
    checks = {row.get("name"): row for row in result.get("checks", []) if isinstance(row, dict)}
    modules = {row.get("name"): row for row in result.get("checks", []) if isinstance(row, dict) and row.get("name") in {"numpy", "pandas", "pyarrow", "zstandard"}}
    return [
        f"PYTHON_PREFLIGHT_STATUS={result.get('python_preflight_status')}",
        f"PYTHON_EXECUTABLE={result.get('python_executable')}",
        f"PYTHON_VERSION={result.get('python_version')}",
        f"AGENTMON_MODULE_FILE={result.get('agentmon_module_file')}",
        f"AGENTMON_VERSION={result.get('agentmon_version')}",
        f"NUMPY_STATUS={modules.get('numpy', {}).get('status', 'UNKNOWN')}",
        f"PANDAS_STATUS={modules.get('pandas', {}).get('status', 'UNKNOWN')}",
        f"PYARROW_STATUS={modules.get('pyarrow', {}).get('status', 'UNKNOWN')}",
        f"ZSTANDARD_STATUS={modules.get('zstandard', {}).get('status', 'UNKNOWN')}",
        f"PARQUET_SMOKE_STATUS={checks.get('parquet_smoke', {}).get('status', 'UNKNOWN')}",
        f"REQUIRED_DEPENDENCIES_STATUS={result.get('required_dependencies_status')}",
        f"OPTIONAL_DEPENDENCIES_STATUS={result.get('optional_dependencies_status')}",
        f"HEAVY_ANALYSIS_ALLOWED={result.get('heavy_analysis_allowed')}",
    ]


def preflight_json_for_stdout(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
