from __future__ import annotations

from hashlib import sha256
from importlib import metadata as importlib_metadata
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

from agentmon.batch.constants import BATCH_SCHEMA_VERSION, RUNNER_VERSION
from agentmon.batch.io import as_dict, read_text
from agentmon.batch.policy import (
    p2_index_cache_root,
    p2_service_global_missing_window_policy,
    should_build_combined_step_csv,
    should_build_timeline,
    should_write_p2_service_global_copy,
)

ROOT = Path(__file__).resolve().parents[3]
RUNNER_SOURCE_PATH = ROOT / "scripts" / "agentmon_batch_runtime.py"

def update_manifest_p2_metadata(manifest: dict[str, Any], p2_index: dict[str, Any]) -> None:
    service = as_dict(p2_index.get("service_bounds"))
    manifest["p2_source_sha256"] = p2_index.get("source_sha256")
    manifest["p2_service"] = service
    runtime = as_dict(manifest.get("runtime"))
    comparable = as_dict(manifest.get("comparability"))
    for key in ("vllm_version", "vllm_ascend_version"):
        if service.get(key) is not None:
            runtime[key] = service[key]
            comparable[key] = service[key]
    runtime["vllm_p2_configuration"] = {
        key: service.get(key)
        for key in (
            "async_scheduling_enabled",
            "engine_core_step_hooked",
            "engine_core_step_with_batch_queue_hooked",
            "engine_token_boundary_collection",
            "prefix_cache_enabled",
            "prefix_cache_expectation",
            "prefix_cache_expectation_status",
        )
    }
    manifest["runtime"] = runtime
    manifest["comparability"] = comparable
    manifest["configuration_fingerprint"] = sha256(
        json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_batch_manifest(
    args: argparse.Namespace,
    tasks: list[dict[str, str]],
    env: dict[str, str],
    *,
    started_wall_ns: int,
) -> dict[str, Any]:
    task_file = Path(args.task_file).expanduser().resolve()
    config_path = Path(args.agentmon_config).expanduser().resolve() if args.agentmon_config else None
    env_path = Path(args.env_file).expanduser().resolve() if args.env_file else None
    p2_path = Path(args.p2_events).expanduser().resolve() if args.p2_events else None
    launch_path = Path(args.vllm_launch_command_file).expanduser().resolve() if args.vllm_launch_command_file else None
    launch_command = read_text(launch_path).strip() if launch_path and launch_path.is_file() else None
    launch_settings = parse_vllm_launch_settings(launch_command)
    model_path = Path(args.model_path).expanduser().resolve() if args.model_path else None
    task_package = Path(args.task_package).expanduser().resolve() if args.task_package else None
    extra_metadata = parse_metadata(args.metadata)
    service_bounds: dict[str, Any] = {}
    vllm_version = service_bounds.get("vllm_version") or package_version("vllm")
    vllm_ascend_version = service_bounds.get("vllm_ascend_version") or package_version("vllm-ascend")
    cann_version = detect_cann_version(env)
    comparable = {
        "task_file_sha256": file_hash_if_exists(task_file),
        "task_package_sha256": path_hash_if_exists(task_package),
        "task_ids": sorted(task["task_id"] for task in tasks),
        "agentmon_version": package_version("agentmon"),
        "agentmon_git_commit": git_commit(ROOT),
        "agentmon_source_sha256": path_hash_if_exists(ROOT / "src"),
        "runner_sha256": file_hash_if_exists(RUNNER_SOURCE_PATH),
        "runner_version": RUNNER_VERSION,
        "agentmon_config_sha256": file_hash_if_exists(config_path),
        "vllm_version": vllm_version,
        "vllm_ascend_version": vllm_ascend_version,
        "cann_version": cann_version,
        "mini_swe_agent_version": package_version("mini-swe-agent"),
        "model_path": str(model_path) if model_path else None,
        "model_config_sha256": model_config_hash(model_path),
        "vllm_launch_command_sha256": file_hash_if_exists(launch_path),
        "vllm_launch_settings": launch_settings,
        "vllm_runtime_configuration": {
            key: service_bounds.get(key)
            for key in (
                "async_scheduling_enabled",
                "engine_core_step_hooked",
                "engine_core_step_with_batch_queue_hooked",
                "engine_token_boundary_collection",
                "prefix_cache_enabled",
                "prefix_cache_expectation",
                "prefix_cache_expectation_status",
            )
        },
        "scheduler_environment": safe_scheduler_environment(env),
        "temperature": args.temperature,
        "seed": args.seed,
        "p2_filter_mode": args.p2_filter_mode,
        "p2_quality": args.p2_quality,
        "include_p2_window_extras": args.include_p2_window_extras,
        "analysis_preset": args.analysis_preset,
        "p2_index_cache_root": str(p2_index_cache_root(args)) if p2_index_cache_root(args) else None,
        "p2_service_global_copy": "write" if should_write_p2_service_global_copy(args) else "skip",
        "p2_service_global_missing_window_policy": p2_service_global_missing_window_policy(args),
        "build_timeline": should_build_timeline(args),
        "build_combined_step_csv": should_build_combined_step_csv(args),
        "p1_mode": args.p1_mode,
        "workspace_policy": getattr(args, "workspace_policy", "isolated"),
        "runtime_source_isolation": getattr(args, "runtime_source_isolation", "snapshot"),
        "runtime_source_integrity": getattr(args, "runtime_source_integrity", "strict"),
        "concurrency": args.concurrency,
        "analysis_workers": args.analysis_workers,
        "p2_quality_thresholds": {
            "minimum_match_rate": args.min_p2_match_rate,
            "minimum_lifecycle_complete_rate": args.min_p2_lifecycle_rate,
        },
        "metadata": extra_metadata,
    }
    fingerprint = sha256(
        json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "manifest_type": "agentmon.batch_experiment",
        "status": "running",
        "start_wall_ns": started_wall_ns,
        "batch_root": str(Path(args.batch_root).expanduser().resolve()),
        "task_file": str(task_file),
        "task_file_sha256": comparable["task_file_sha256"],
        "task_package": str(task_package) if task_package else None,
        "task_package_sha256": comparable["task_package_sha256"],
        "task_count": len(tasks),
        "task_ids": comparable["task_ids"],
        "concurrency": args.concurrency,
        "command_template": args.command_template,
        "command_column": args.command_column,
        "judge_configured": bool(args.judge_command_template),
        "p1_mode": args.p1_mode,
        "workspace_policy": getattr(args, "workspace_policy", "isolated"),
        "p2_filter_mode": args.p2_filter_mode,
        "p2_quality": args.p2_quality,
        "p2_source": str(p2_path) if p2_path else None,
        "p2_source_sha256": None,
        "p2_service": service_bounds,
        "agentmon": {
            "version": comparable["agentmon_version"],
            "git_commit": comparable["agentmon_git_commit"],
            "python": sys.version,
            "config_path": str(config_path) if config_path else None,
            "config_sha256": comparable["agentmon_config_sha256"],
        },
        "runner": {
            "version": RUNNER_VERSION,
            "path": str(Path(__file__).resolve()),
            "sha256": comparable["runner_sha256"],
        },
        "runtime": {
            "vllm_version": comparable["vllm_version"],
            "vllm_ascend_version": comparable["vllm_ascend_version"],
            "cann_version": comparable["cann_version"],
            "mini_swe_agent_version": comparable["mini_swe_agent_version"],
            "visible_devices": env.get("ASCEND_RT_VISIBLE_DEVICES") or env.get("ASCEND_VISIBLE_DEVICES"),
            "vllm_p2_configuration": comparable["vllm_runtime_configuration"],
        },
        "model": {
            "path": comparable["model_path"],
            "config_sha256": comparable["model_config_sha256"],
        },
        "vllm_launch": {
            "command_file": str(launch_path) if launch_path else None,
            "command_file_sha256": file_hash_if_exists(launch_path),
            "command": launch_command,
            "settings": launch_settings,
        },
        "request_config": {"temperature": args.temperature, "seed": args.seed},
        "env_file": str(env_path) if env_path else None,
        "env_file_sha256": file_hash_if_exists(env_path),
        "scheduler_environment": safe_scheduler_environment(env),
        "metadata": extra_metadata,
        "comparability": comparable,
        "configuration_fingerprint": fingerprint,
        "analysis_workers": args.analysis_workers,
        "analysis_policy": {
            "analysis_preset": args.analysis_preset,
            "execution_only": bool(args.execution_only),
            "defer_analysis_if_p2_over_bytes": args.defer_analysis_if_p2_over_bytes,
            "analyze_existing": bool(args.analyze_existing),
            "p2_index_cache_root": str(p2_index_cache_root(args)) if p2_index_cache_root(args) else None,
            "p2_service_global_copy": "write" if should_write_p2_service_global_copy(args) else "skip",
            "p2_service_global_missing_window_policy": p2_service_global_missing_window_policy(args),
            "build_timeline": should_build_timeline(args),
            "build_combined_step_csv": should_build_combined_step_csv(args),
        },
        "p2_quality_thresholds": comparable["p2_quality_thresholds"],
    }


def parse_metadata(entries: list[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"metadata entry must be KEY=VALUE: {entry}")
        key, value = entry.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("metadata key cannot be empty")
        metadata[key] = value.strip()
    return metadata


def package_version(name: str) -> str | None:
    aliases = {
        "agentmon": ["agentmon"],
        "vllm": ["vllm"],
        "vllm-ascend": ["vllm-ascend", "vllm_ascend"],
        "mini-swe-agent": ["mini-swe-agent", "minisweagent"],
    }
    for candidate in aliases.get(name, [name]):
        try:
            return importlib_metadata.version(candidate)
        except importlib_metadata.PackageNotFoundError:
            continue
    if name == "agentmon":
        try:
            from agentmon import __version__

            return __version__
        except Exception:
            return None
    return None


def git_commit(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def model_config_hash(model_path: Path | None) -> str | None:
    if model_path is None or not model_path.exists():
        return None
    candidates = [
        model_path / name
        for name in ("config.json", "generation_config.json", "tokenizer_config.json")
        if (model_path / name).is_file()
    ] if model_path.is_dir() else [model_path]
    if not candidates:
        return None
    digest = sha256()
    for path in sorted(candidates):
        digest.update(path.name.encode("utf-8"))
        digest.update(bytes.fromhex(file_hash_if_exists(path) or ""))
    return digest.hexdigest()


def detect_cann_version(env: dict[str, str]) -> str | None:
    for key in ("CANN_VERSION", "ASCEND_VERSION"):
        if env.get(key):
            return env[key]
    roots = [env.get("ASCEND_HOME_PATH"), env.get("ASCEND_HOME")]
    for raw_root in roots:
        if not raw_root:
            continue
        root = Path(raw_root)
        for relative in ("version.info", "ascend_toolkit_install.info"):
            path = root / relative
            if path.is_file():
                text = read_text(path).strip()
                if text:
                    return text[:1000]
    return None


def safe_scheduler_environment(env: dict[str, str]) -> dict[str, str]:
    allowed_exact = {
        "ASCEND_RT_VISIBLE_DEVICES",
        "ASCEND_VISIBLE_DEVICES",
        "VLLM_USE_V1",
        "VLLM_WORKER_MULTIPROC_METHOD",
        "AGENTMON_VLLM_P2_ENABLE",
        "AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS",
        "AGENTMON_VLLM_P2_HOOK_KV_BLOCKS",
        "AGENTMON_VLLM_P2_DEEP",
        "AGENTMON_VLLM_P2_DEEP_MAX_BATCH_REQUESTS",
        "AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE",
    }
    return {key: value for key, value in env.items() if key in allowed_exact}


def file_hash_if_exists(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_hash_if_exists(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    if path.is_file():
        return file_hash_if_exists(path)
    digest = sha256()
    for child in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative = child.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        child_hash = file_hash_if_exists(child)
        if child_hash is not None:
            digest.update(bytes.fromhex(child_hash))
    return digest.hexdigest()


def parse_vllm_launch_settings(command: str | None) -> dict[str, Any]:
    if not command:
        return {}
    try:
        tokens = shlex.split(command)
    except ValueError:
        return {"parse_status": "invalid_shell_syntax"}
    aliases = {
        "--model": "model",
        "--served-model-name": "served_model_name",
        "--tensor-parallel-size": "tensor_parallel_size",
        "--max-model-len": "max_model_len",
        "--max-num-seqs": "max_num_seqs",
        "--max-num-batched-tokens": "max_num_batched_tokens",
        "--gpu-memory-utilization": "gpu_memory_utilization",
        "--enable-prefix-caching": "enable_prefix_caching",
        "--no-enable-prefix-caching": "enable_prefix_caching",
        "--enable-chunked-prefill": "enable_chunked_prefill",
        "--disable-chunked-prefill": "enable_chunked_prefill",
        "--no-enable-chunked-prefill": "enable_chunked_prefill",
    }
    settings: dict[str, Any] = {"parse_status": "ok"}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, separator, inline_value = token.partition("=")
        key = aliases.get(flag)
        if key is None:
            if "prefix" in flag and "cach" in flag:
                settings.setdefault("prefix_cache_cli_options_observed", []).append(flag)
            index += 1
            continue
        if flag.startswith("--no-enable-") or flag.startswith("--disable-"):
            value: Any = False if not separator else parse_scalar(inline_value)
        elif flag.startswith("--enable-"):
            value = True if not separator else parse_scalar(inline_value)
        elif flag in {
            "--disable-chunked-prefill",
            "--no-enable-chunked-prefill",
        }:
            value = False
        elif separator:
            value = parse_scalar(inline_value)
        elif index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
            index += 1
            value = parse_scalar(tokens[index])
        else:
            value = None
        settings[key] = value
        index += 1
    return settings


def parse_scalar(value: str) -> Any:
    text = value.strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text
