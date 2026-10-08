#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import logging
import os
from io import StringIO
from pathlib import Path
import platform
import re
import sys
import time
from types import ModuleType
from typing import Any, Sequence


SCHEMA_VERSION = "1.0"
TARGET_VLLM_VERSION = "0.18.0"
_LOG = logging.getLogger("agentmon.p2_discovery")

DEFAULT_PACKAGES = [
    "vllm",
    "vllm_ascend",
    "torch",
    "torch_npu",
]

METADATA_ONLY_PACKAGES = {
    "torch",
    "torch_npu",
}

DEFAULT_SCAN_PACKAGES = [
    "vllm",
    "vllm_ascend",
]

DEFAULT_MODULES = [
    "vllm",
    "vllm.config",
    "vllm.entrypoints.metrics",
    "vllm.entrypoints.openai.api_server",
    "vllm.entrypoints.openai.protocol",
    "vllm.entrypoints.openai.serving_chat",
    "vllm.entrypoints.openai.serving_completion",
    "vllm.entrypoints.openai.serving_engine",
    "vllm.engine.async_llm_engine",
    "vllm.engine.llm_engine",
    "vllm.engine.metrics",
    "vllm.core.block_manager",
    "vllm.core.scheduler",
    "vllm.worker.cache_engine",
    "vllm.v1.core.block_pool",
    "vllm.v1.core.kv_cache_manager",
    "vllm.v1.core.sched.scheduler",
    "vllm.v1.engine.async_llm",
    "vllm.v1.metrics.prometheus",
    "vllm.v1.metrics.stats",
    "vllm.v1.request",
    "vllm_ascend",
    "vllm_ascend.platform",
    "vllm_ascend.worker.worker",
]

DEFAULT_OBJECTS = [
    "vllm.config:CacheConfig",
    "vllm.config:EngineArgs",
    "vllm.config:ModelConfig",
    "vllm.config:ParallelConfig",
    "vllm.entrypoints.openai.protocol:ChatCompletionRequest",
    "vllm.entrypoints.openai.protocol:CompletionRequest",
    "vllm.entrypoints.openai.serving_chat:OpenAIServingChat",
    "vllm.entrypoints.openai.serving_completion:OpenAIServingCompletion",
    "vllm.engine.async_llm_engine:AsyncLLMEngine",
    "vllm.engine.llm_engine:LLMEngine",
    "vllm.engine.metrics:PrometheusStatLogger",
    "vllm.engine.metrics:StatLoggerBase",
    "vllm.core.scheduler:Scheduler",
    "vllm.worker.cache_engine:CacheEngine",
    "vllm.v1.engine.async_llm:AsyncLLM",
    "vllm.v1.core.kv_cache_manager:KVCacheManager",
    "vllm.v1.core.sched.scheduler:Scheduler",
    "vllm.v1.request:Request",
]

INTERESTING_NAME_PARTS = (
    "request",
    "schedule",
    "scheduler",
    "cache",
    "block",
    "metric",
    "stat",
    "token",
    "prefix",
    "preempt",
    "queue",
    "prefill",
    "decode",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot = collect_snapshot(
        package_names=extend(DEFAULT_PACKAGES, args.package),
        module_names=extend(DEFAULT_MODULES, args.module),
        object_refs=extend(DEFAULT_OBJECTS, args.object),
        scan_package_names=[] if args.no_default_scan else extend(DEFAULT_SCAN_PACKAGES, args.scan_package),
        scan_limit=args.scan_limit,
        source_lines=args.source_lines,
    )
    output = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    if args.strict_version and snapshot["readiness"]["vllm_version_status"] != "compatible":
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect a read-only AgentMon P2 discovery snapshot from a vLLM Python environment."
    )
    parser.add_argument("--output", "-o", help="write JSON snapshot to this file; stdout is used when omitted")
    parser.add_argument(
        "--source-lines",
        type=int,
        default=160,
        help="maximum source lines to include per object",
    )
    parser.add_argument("--package", action="append", default=[], help="additional package name to inspect")
    parser.add_argument("--module", action="append", default=[], help="additional module name to inspect")
    parser.add_argument(
        "--scan-package",
        action="append",
        default=[],
        help="additional installed package to scan for candidate modules without importing submodules",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=800,
        help="maximum module files to list per scanned package",
    )
    parser.add_argument(
        "--no-default-scan",
        action="store_true",
        help="disable the default vllm/vllm_ascend package filesystem scan",
    )
    parser.add_argument(
        "--object",
        action="append",
        default=[],
        help="additional object reference in module:qualname form",
    )
    parser.add_argument(
        "--strict-version",
        action="store_true",
        help="exit 1 if the detected vLLM version is not compatible with 0.18.0",
    )
    return parser


def collect_snapshot(
    *,
    package_names: Sequence[str] = DEFAULT_PACKAGES,
    module_names: Sequence[str] = DEFAULT_MODULES,
    object_refs: Sequence[str] = DEFAULT_OBJECTS,
    scan_package_names: Sequence[str] = DEFAULT_SCAN_PACKAGES,
    scan_limit: int = 800,
    source_lines: int = 160,
) -> dict[str, Any]:
    packages = {name: inspect_package(name) for name in unique(package_names)}
    modules = {name: inspect_module(name) for name in unique(module_names)}
    objects = {ref: inspect_object(ref, source_lines=source_lines) for ref in unique(object_refs)}
    package_scans = {
        name: scan_package_modules(name, limit=scan_limit)
        for name in unique(scan_package_names)
    }
    vllm_package = packages.get("vllm", {})
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_type": "agentmon.p2_discovery",
        "collected_wall_time_ns": time.time_ns(),
        "python": python_snapshot(),
        "environment": environment_snapshot(),
        "packages": packages,
        "modules": modules,
        "objects": objects,
        "package_scans": package_scans,
        "hook_resolution_inputs": hook_resolution_inputs(modules, objects, package_scans),
        "readiness": readiness_snapshot(vllm_package, modules, objects),
    }


def inspect_package(name: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": "unknown",
        "name": name,
        "version": None,
        "release": None,
        "metadata_version": None,
        "metadata_release": None,
        "module_version": None,
        "module_release": None,
        "version_source": None,
        "version_source_consistency": "unknown",
        "source_consistency": "unknown",
        "version_consistency": "unknown",
        "version_identity_status": "unknown",
    }
    try:
        data["metadata_version"] = importlib.metadata.version(name)
        data["metadata_status"] = "ok"
    except importlib.metadata.PackageNotFoundError:
        data["metadata_status"] = "not_found"
    spec = safe_find_spec(name)
    data["spec"] = spec_snapshot(spec)
    if name in METADATA_ONLY_PACKAGES:
        data["status"] = "metadata_only"
        apply_version_selection(data)
        return data
    try:
        module = import_module_without_stdout_noise(name)
    except Exception as exc:
        data.update({"status": "import_error", "error": exception_summary(exc)})
        apply_version_selection(data)
        return data
    data["status"] = "ok"
    data["file"] = getattr(module, "__file__", None)
    data["module_version"] = getattr(module, "__version__", None)
    apply_version_selection(data)
    return data


def apply_version_selection(data: dict[str, Any]) -> None:
    metadata_version = none_if_empty(data.get("metadata_version"))
    module_version = none_if_empty(data.get("module_version"))
    metadata_release = base_version(metadata_version)
    module_release = base_version(module_version)
    data["metadata_release"] = metadata_release
    data["module_release"] = module_release
    if module_version is not None:
        data["version"] = module_version
        data["release"] = module_release
        data["version_source"] = "module.__version__"
    elif metadata_version is not None:
        data["version"] = metadata_version
        data["release"] = metadata_release
        data["version_source"] = "importlib.metadata"
    else:
        data["version"] = None
        data["release"] = None
        data["version_source"] = None

    consistency = version_source_consistency(module_version, metadata_version)
    identity_status = version_identity_status(module_version, metadata_version)
    data["version_source_consistency"] = consistency
    data["source_consistency"] = consistency
    data["version_consistency"] = consistency
    data["version_identity_status"] = identity_status


def inspect_module(name: str) -> dict[str, Any]:
    data: dict[str, Any] = {"status": "unknown", "name": name}
    spec = safe_find_spec(name)
    data["spec"] = spec_snapshot(spec)
    if spec is None:
        data["status"] = "not_found"
        return data
    try:
        module = import_module_without_stdout_noise(name)
    except Exception as exc:
        data.update({"status": "import_error", "error": exception_summary(exc)})
        return data
    data.update(
        {
            "status": "ok",
            "file": getattr(module, "__file__", None),
            "package": getattr(module, "__package__", None),
            "candidate_symbols": call_without_stdout_noise(
                f"listing candidate symbols for {name}",
                module_candidate_symbols,
                module,
            ),
        }
    )
    return data


def inspect_object(ref: str, *, source_lines: int) -> dict[str, Any]:
    data: dict[str, Any] = {"status": "unknown", "ref": ref}
    try:
        module_name, qualname = split_ref(ref)
        module = import_module_without_stdout_noise(module_name)
        obj = resolve_qualname(module, qualname)
    except Exception as exc:
        data.update({"status": "error", "error": exception_summary(exc)})
        return data
    data.update(object_metadata(obj))
    data["status"] = "ok"
    data["ref"] = ref
    data["signature"] = safe_signature(obj)
    if inspect.isclass(obj):
        data["init_signature"] = safe_signature(getattr(obj, "__init__", None))
        data["call_signature"] = safe_signature(getattr(obj, "__call__", None))
        data["methods"] = class_methods(obj)
    source = source_snapshot(obj, max_lines=source_lines)
    if source:
        data["source"] = source
    return data


def scan_package_modules(name: str, *, limit: int) -> dict[str, Any]:
    spec = safe_find_spec(name)
    data: dict[str, Any] = {
        "status": "unknown",
        "name": name,
        "spec": spec_snapshot(spec),
        "module_count": 0,
        "candidate_module_count": 0,
        "truncated": False,
        "modules": [],
        "candidate_modules": [],
    }
    if spec is None:
        data["status"] = "not_found"
        return data
    locations = list(getattr(spec, "submodule_search_locations", []) or [])
    if not locations:
        data["status"] = "not_package"
        return data
    modules: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for root_raw in locations:
        root = Path(root_raw)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part == "__pycache__" for part in path.parts):
                continue
            if len(modules) >= max(0, limit):
                data["truncated"] = True
                break
            module_name = module_name_from_path(name, root, path)
            if module_name is None:
                continue
            row = {
                "name": module_name,
                "file": str(path),
                "interesting_name_parts": interesting_name_parts(module_name),
            }
            modules.append(row)
            if row["interesting_name_parts"]:
                candidates.append(row)
        if data["truncated"]:
            break
    data["status"] = "ok"
    data["module_count"] = len(modules)
    data["candidate_module_count"] = len(candidates)
    data["modules"] = modules
    data["candidate_modules"] = candidates
    return data


def module_name_from_path(package_name: str, root: Path, path: Path) -> str | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([package_name, *parts]) if parts else package_name


def interesting_name_parts(value: str) -> list[str]:
    lowered = value.lower()
    return [part for part in INTERESTING_NAME_PARTS if part in lowered]


def hook_resolution_inputs(
    modules: dict[str, dict[str, Any]],
    objects: dict[str, dict[str, Any]],
    package_scans: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scan_candidates: list[dict[str, Any]] = []
    for package, scan in package_scans.items():
        if scan.get("status") != "ok":
            continue
        for row in scan.get("candidate_modules", []):
            scan_candidates.append({"package": package, **row})
    return {
        "policy": (
            "Use successful_modules, successful_objects, and package_scan_candidate_modules only. "
            "Do not bind hooks to failed module/object references."
        ),
        "successful_modules": [
            {"name": name, "file": item.get("file"), "candidate_symbols": item.get("candidate_symbols", [])}
            for name, item in modules.items()
            if item.get("status") == "ok"
        ],
        "successful_objects": [
            {"ref": ref, "kind": item.get("kind"), "file": item.get("file"), "signature": item.get("signature")}
            for ref, item in objects.items()
            if item.get("status") == "ok"
        ],
        "package_scan_candidate_modules": scan_candidates,
        "ignored_failed_modules": [
            {"name": name, "status": item.get("status"), "error": item.get("error")}
            for name, item in modules.items()
            if item.get("status") != "ok"
        ],
        "ignored_failed_objects": [
            {"ref": ref, "status": item.get("status"), "error": item.get("error")}
            for ref, item in objects.items()
            if item.get("status") != "ok"
        ],
    }


def python_snapshot() -> dict[str, Any]:
    return {
        "executable": sys.executable,
        "version": sys.version,
        "version_info": list(sys.version_info[:5]),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "path_head": sys.path[:8],
    }


def environment_snapshot() -> dict[str, Any]:
    keys = [
        "AGENTMON_REPO",
        "AGENTMON_HOME",
        "AGENTMON_RUN_ID",
        "AGENTMON_RUN_DIR",
        "AGENTMON_EVENTS_PATH",
        "VLLM_WORKER_MULTIPROC_METHOD",
        "ASCEND_HOME_PATH",
        "ASCEND_OPP_PATH",
        "ACL_HOME_PATH",
        "TOOLCHAIN_HOME",
        "TBE_IMPL_PATH",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
    ]
    return {
        "note": (
            "For vllm_ascend modules that import acl/CANN libraries, rerun discovery in the same "
            "environment used to launch the actual vLLM service."
        ),
        "variables": {key: os.environ.get(key) for key in keys if os.environ.get(key) is not None},
    }


def readiness_snapshot(
    vllm_package: dict[str, Any],
    modules: dict[str, dict[str, Any]],
    objects: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    module_ok = sum(1 for item in modules.values() if item.get("status") == "ok")
    object_ok = sum(1 for item in objects.values() if item.get("status") == "ok")
    vllm_version = vllm_package.get("version")
    vllm_import_status = vllm_package.get("status")
    version_status = vllm_version_status(vllm_version)
    if vllm_import_status != "ok":
        version_status = "unavailable"
    consistency = vllm_package.get("source_consistency") or vllm_package.get("version_consistency")
    return {
        "target_vllm_version": TARGET_VLLM_VERSION,
        "detected_vllm_version": vllm_version,
        "selected_version": vllm_version,
        "selected_release": vllm_package.get("release"),
        "selected_source": vllm_package.get("version_source"),
        "compatibility_status": version_status,
        "version_source_consistency": consistency,
        "vllm_version_status": version_status,
        "vllm_import_status": vllm_import_status,
        "vllm_version_evidence": {
            "selected_version": vllm_version,
            "selected_release": vllm_package.get("release"),
            "selected_source": vllm_package.get("version_source"),
            "module_version": vllm_package.get("module_version"),
            "module_release": vllm_package.get("module_release"),
            "metadata_version": vllm_package.get("metadata_version"),
            "metadata_release": vllm_package.get("metadata_release"),
            "consistency": consistency,
            "version_source_consistency": consistency,
            "source_consistency": consistency,
            "version_consistency": vllm_package.get("version_consistency"),
            "version_identity_status": vllm_package.get("version_identity_status"),
            "compatibility_status": version_status,
            "package_file": vllm_package.get("file"),
        },
        "module_ok_count": module_ok,
        "module_total_count": len(modules),
        "object_ok_count": object_ok,
        "object_total_count": len(objects),
        "missing_or_error_objects": [
            ref for ref, item in objects.items() if item.get("status") != "ok"
        ],
    }


def vllm_version_status(version: Any) -> str:
    if version in {None, ""}:
        return "missing"
    if base_version(version) == TARGET_VLLM_VERSION:
        return "compatible"
    return "mismatch"


def base_version(version: Any) -> str | None:
    release = release_version(version)
    if release is None:
        return None
    return ".".join(str(part) for part in release)


def release_version(version: Any) -> tuple[int, ...] | None:
    if version in {None, ""}:
        return None
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(version).strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def version_source_consistency(module_version: Any, metadata_version: Any) -> str:
    if module_version in {None, ""} or metadata_version in {None, ""}:
        return "unknown"
    module_release = release_version(module_version)
    metadata_release = release_version(metadata_version)
    if module_release is None or metadata_release is None:
        return "unknown"
    if module_release == metadata_release:
        return "consistent"
    return "conflict"


def version_identity_status(module_version: Any, metadata_version: Any) -> str:
    module = none_if_empty(module_version)
    metadata = none_if_empty(metadata_version)
    if module is None or metadata is None:
        return "unknown"
    if module == metadata:
        return "exact_match"
    if version_source_consistency(module, metadata) == "consistent":
        return "release_match"
    return "conflict"


def none_if_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def safe_find_spec(name: str) -> Any:
    try:
        return call_without_stdout_noise(f"finding spec for {name}", importlib.util.find_spec, name)
    except (ImportError, AttributeError, ValueError):
        return None


def import_module_without_stdout_noise(name: str) -> ModuleType:
    return call_without_stdout_noise(f"importing {name}", importlib.import_module, name)


def call_without_stdout_noise(where: str, func: Any, *args: Any, **kwargs: Any) -> Any:
    captured = StringIO()
    try:
        with redirect_stdout(captured):
            return func(*args, **kwargs)
    finally:
        noise = captured.getvalue()
        if noise:
            _LOG.debug("Suppressed stdout while %s:\n%s", where, noise.rstrip())


def spec_snapshot(spec: Any) -> dict[str, Any] | None:
    if spec is None:
        return None
    return {
        "name": getattr(spec, "name", None),
        "origin": getattr(spec, "origin", None),
        "has_location": getattr(spec, "has_location", None),
        "submodule_search_locations": list(getattr(spec, "submodule_search_locations", []) or []),
    }


def module_candidate_symbols(module: ModuleType) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in sorted(dir(module)):
        lowered = name.lower()
        if not any(part in lowered for part in INTERESTING_NAME_PARTS):
            continue
        try:
            obj = getattr(module, name)
        except Exception:
            continue
        rows.append({"name": name, "kind": object_kind(obj), "signature": safe_signature(obj)})
        if len(rows) >= 120:
            break
    return rows


def object_metadata(obj: Any) -> dict[str, Any]:
    return {
        "kind": object_kind(obj),
        "module": getattr(obj, "__module__", None),
        "qualname": getattr(obj, "__qualname__", getattr(obj, "__name__", None)),
        "file": safe_object_file(obj),
    }


def object_kind(obj: Any) -> str:
    if inspect.ismodule(obj):
        return "module"
    if inspect.isclass(obj):
        return "class"
    if inspect.iscoroutinefunction(obj):
        return "coroutine_function"
    if inspect.isfunction(obj):
        return "function"
    if inspect.ismethod(obj):
        return "method"
    if callable(obj):
        return "callable"
    return type(obj).__name__


def safe_signature(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return None


def safe_object_file(obj: Any) -> str | None:
    try:
        return inspect.getsourcefile(obj) or inspect.getfile(obj)
    except (TypeError, OSError):
        return None


def class_methods(cls: type) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    for name, member in inspect.getmembers(cls):
        lowered = name.lower()
        if name.startswith("__") and name not in {"__init__", "__call__"}:
            continue
        if name not in {"__init__", "__call__"} and not any(part in lowered for part in INTERESTING_NAME_PARTS):
            continue
        if not callable(member):
            continue
        rows.append({"name": name, "kind": object_kind(member), "signature": safe_signature(member)})
        if len(rows) >= 120:
            break
    return rows


def source_snapshot(obj: Any, *, max_lines: int) -> dict[str, Any] | None:
    try:
        lines, start_line = inspect.getsourcelines(obj)
    except (OSError, TypeError):
        return None
    text = "".join(lines)
    excerpt = lines[: max(0, max_lines)]
    return {
        "start_line": start_line,
        "line_count": len(lines),
        "sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
        "excerpt_truncated": len(lines) > len(excerpt),
        "excerpt": [line.rstrip("\n") for line in excerpt],
    }


def split_ref(ref: str) -> tuple[str, str]:
    if ":" not in ref:
        raise ValueError(f"object ref must be module:qualname, got {ref!r}")
    module_name, qualname = ref.split(":", 1)
    if not module_name or not qualname:
        raise ValueError(f"object ref must be module:qualname, got {ref!r}")
    return module_name, qualname


def resolve_qualname(module: ModuleType, qualname: str) -> Any:
    obj: Any = module
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def exception_summary(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)}


def unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def extend(defaults: Sequence[str], extras: Sequence[str]) -> list[str]:
    return unique([*defaults, *extras])


if __name__ == "__main__":
    raise SystemExit(main())
