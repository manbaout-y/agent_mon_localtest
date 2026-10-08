from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
from typing import Any

import agentmon
from agentmon.analysis.config import AnalysisConfig
from agentmon.analysis.io import read_json, write_json


ANALYSIS_FORMAT_VERSION = "1.1"


@dataclass(frozen=True)
class ResumeDecision:
    status: str
    reason: str | None = None
    cached_normalized: bool = False
    manifest: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "cached_normalized": self.cached_normalized,
            "warnings": list(self.warnings),
        }


def build_analysis_manifest(
    *,
    input_root: str | Path,
    output_root: str | Path,
    cfg: AnalysisConfig,
    output_format: str,
    input_inventory: dict[str, Any] | None,
    stage_status: dict[str, Any] | None = None,
    final_status: str | None = None,
    cli_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = Path(output_root)
    existing = read_json(output / "analysis_manifest.json", default={})
    return {
        "analysis_format_version": ANALYSIS_FORMAT_VERSION,
        "agentmon_version": getattr(agentmon, "__version__", "unknown"),
        "agentmon_module_file": getattr(agentmon, "__file__", None),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "dependency_versions": dependency_versions(),
        "input_root": str(Path(input_root).expanduser().resolve()),
        "output_root": str(Path(output_root).expanduser().resolve()),
        "input_fingerprint": (input_inventory or {}).get("input_fingerprint"),
        "source_fingerprint": source_fingerprint(),
        "profile": cfg.profile,
        "output_format": output_format,
        "configuration_fingerprint": config_fingerprint(cfg),
        "configuration": cfg.to_dict(),
        "stage_completion_status": stage_status or {},
        "schema_versions": (input_inventory or {}).get("schema_version_counts", {}),
        "artifacts": artifact_inventory(output),
        "cli_arguments": cli_arguments or {},
        "created_at": existing.get("created_at") or now_iso(),
        "completed_at": now_iso() if final_status else None,
        "final_status": final_status,
    }


def write_analysis_manifest(bundle_root: str | Path, manifest: dict[str, Any]) -> None:
    write_json(Path(bundle_root) / "analysis_manifest.json", manifest)


def decide_resume(
    *,
    bundle_root: str | Path,
    input_root: str | Path,
    cfg: AnalysisConfig,
    output_format: str,
    input_inventory: dict[str, Any] | None,
    resume: bool,
    force_resume: bool,
) -> ResumeDecision:
    if not resume:
        return ResumeDecision(status="NOT_REQUESTED")
    path = Path(bundle_root) / "analysis_manifest.json"
    if not path.exists():
        return ResumeDecision(status="FRESH_REQUIRED", reason="analysis_manifest_missing")
    manifest = read_json(path, default={})
    incompatibilities = compare_manifest(
        manifest,
        input_root=input_root,
        cfg=cfg,
        output_format=output_format,
        input_inventory=input_inventory,
    )
    normalized_ready = normalized_cache_ready(Path(bundle_root), analysis_manifest=manifest)
    if incompatibilities and not force_resume:
        return ResumeDecision(
            status="REJECTED",
            reason=";".join(incompatibilities),
            cached_normalized=False,
            manifest=manifest,
        )
    warnings = tuple(f"forced_resume_incompatibility:{item}" for item in incompatibilities) if incompatibilities else ()
    return ResumeDecision(
        status="ACCEPTED" if normalized_ready else "FRESH_REQUIRED",
        reason=None if normalized_ready else "normalized_cache_missing_or_incomplete",
        cached_normalized=normalized_ready and (not incompatibilities or force_resume),
        manifest=manifest,
        warnings=warnings,
    )


def compare_manifest(
    manifest: dict[str, Any],
    *,
    input_root: str | Path,
    cfg: AnalysisConfig,
    output_format: str,
    input_inventory: dict[str, Any] | None,
) -> list[str]:
    expected = {
        "analysis_format_version": ANALYSIS_FORMAT_VERSION,
        "input_root": str(Path(input_root).expanduser().resolve()),
        "input_fingerprint": (input_inventory or {}).get("input_fingerprint"),
        "profile": cfg.profile,
        "output_format": output_format,
        "configuration_fingerprint": config_fingerprint(cfg),
        "agentmon_version": getattr(agentmon, "__version__", "unknown"),
        "agentmon_module_file": getattr(agentmon, "__file__", None),
        "source_fingerprint": source_fingerprint(),
        "dependency_versions": dependency_versions(),
    }
    reasons: list[str] = []
    for key, value in expected.items():
        if manifest.get(key) != value:
            reasons.append(f"{key}_changed")
    return reasons


def normalized_cache_ready(bundle: Path, *, analysis_manifest: dict[str, Any] | None = None) -> bool:
    manifest = bundle / "01_normalized" / "normalized_manifest.json"
    if not manifest.exists():
        return False
    payload = read_json(manifest, default={})
    tables = payload.get("tables", {}) if isinstance(payload, dict) else {}
    if not isinstance(tables, dict):
        return False
    if not tables:
        return False
    for artifact in tables.values():
        if not artifact_cache_ready(artifact):
            return False
    if analysis_manifest is not None:
        stages = analysis_manifest.get("stage_completion_status", {})
        required_stages = (
            "normalize_runs",
            "normalize_requests",
            "normalize_scheduler",
            "normalize_resources",
            "p1_p2_audit",
        )
        if not isinstance(stages, dict) or any(stages.get(stage) not in {"PASS", "CACHED"} for stage in required_stages):
            return False
    return True


def artifact_cache_ready(artifact: Any) -> bool:
    if not isinstance(artifact, dict):
        return False
    path = Path(str(artifact.get("path") or ""))
    if not path.is_file():
        return False
    try:
        stat = path.stat()
        expected_size = artifact.get("size_bytes")
        expected_mtime = artifact.get("mtime_ns")
        row_count = int(artifact.get("row_count"))
    except (OSError, TypeError, ValueError):
        return False
    if row_count < 0:
        return False
    if expected_size is not None and int(expected_size) != stat.st_size:
        return False
    if expected_mtime is not None and int(expected_mtime) != stat.st_mtime_ns:
        return False
    return True


def config_fingerprint(cfg: AnalysisConfig) -> str:
    raw = cfg.to_dict()
    semantic = dict(raw)
    runtime = dict(semantic.get("runtime", {}))
    for operational_key in (
        "jobs",
        "temp_dir",
        "keep_temp",
        "status_file",
        "progress_interval_seconds",
        "timeout_seconds",
        "memory_limit_bytes",
        "min_free_bytes",
    ):
        runtime.pop(operational_key, None)
    semantic["runtime"] = runtime
    payload = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_fingerprint() -> str:
    module = Path(getattr(agentmon, "__file__", "") or "")
    root = module.parent if module.exists() else Path.cwd()
    digest = hashlib.sha256()
    source_suffixes = {".py", ".yaml", ".yml", ".json", ".toml"}
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.suffix.lower() in source_suffixes):
        try:
            content_hash = hashlib.sha256(path.read_bytes()).digest()
        except OSError:
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(content_hash)
        digest.update(b"\n")
    return digest.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dependency_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for package in ("numpy", "pandas", "pyarrow", "zstandard"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def artifact_inventory(bundle: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for relative in (
        "01_normalized/normalized_manifest.json",
        "02_validation/validation_manifest.json",
        "03_analysis/analysis_manifest.json",
        "04_evidence/evidence_manifest.json",
    ):
        path = bundle / relative
        if not path.is_file():
            continue
        payload = read_json(path, default={})
        result[relative] = payload.get("tables", {}) if isinstance(payload, dict) else {}
    return result
