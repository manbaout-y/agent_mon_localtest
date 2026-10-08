from __future__ import annotations

import glob
import json
import shutil
from pathlib import Path
from typing import Any

from agentmon.paths import chmod_private_file


def snapshot_trajectory_glob(pattern: str) -> dict[str, float]:
    if not pattern:
        return {}
    return {path: Path(path).stat().st_mtime for path in glob.glob(pattern, recursive=True) if Path(path).is_file()}


def find_new_or_modified_trajectories(pattern: str, before: dict[str, float]) -> list[Path]:
    if not pattern:
        return []
    candidates: list[Path] = []
    for path_str in glob.glob(pattern, recursive=True):
        path = Path(path_str)
        if not path.is_file():
            continue
        mtime = path.stat().st_mtime
        if path_str not in before or mtime > before[path_str]:
            candidates.append(path)
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates


def import_trajectory(
    path: str | Path,
    *,
    run_id: str | None = None,
    raw_dir: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(path).expanduser()
    raw_ref = None
    if raw_dir is not None:
        target = Path(raw_dir) / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        chmod_private_file(target)
        raw_ref = str(target)
    data = json.loads(source.read_text(encoding="utf-8"))
    summary = summarize_trajectory(data)
    return {
        "run_id": run_id,
        "source_path": str(source),
        "raw_ref": raw_ref,
        "summary": summary,
    }


def summarize_trajectory(data: dict[str, Any]) -> dict[str, Any]:
    info = data.get("info") or {}
    messages = data.get("messages") or data.get("trajectory") or []
    model_stats = info.get("model_stats") or data.get("model_stats") or {}
    summary: dict[str, Any] = {
        "status": "complete",
        "trajectory_format": data.get("trajectory_format") or info.get("trajectory_format"),
        "mini_version": info.get("mini_version") or info.get("version"),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "assistant_turns": 0,
        "api_calls": model_stats.get("api_calls"),
        "instance_cost": model_stats.get("instance_cost"),
        "exit_status": info.get("exit_status") or data.get("exit_status"),
        "tool_calls": 0,
        "tool_failures": 0,
        "prompt_tokens": None,
        "completion_tokens": None,
        "cached_tokens": None,
    }
    prompt_tokens = 0
    completion_tokens = 0
    cached_tokens = 0
    saw_prompt_tokens = False
    saw_completion_tokens = False
    saw_cached_tokens = False
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant":
                summary["assistant_turns"] += 1
            tool_calls = message.get("tool_calls") or message.get("actions") or []
            if isinstance(tool_calls, list):
                summary["tool_calls"] += len(tool_calls)
            usage = usage_from_message(message)
            if isinstance(usage, dict):
                prompt = usage_int(usage, "prompt_tokens", "input_tokens")
                completion = usage_int(usage, "completion_tokens", "generation_tokens", "output_tokens")
                cached = cached_tokens_from_usage(usage)
                if prompt is not None:
                    saw_prompt_tokens = True
                    prompt_tokens += prompt
                if completion is not None:
                    saw_completion_tokens = True
                    completion_tokens += completion
                if cached is not None:
                    saw_cached_tokens = True
                    cached_tokens += cached
            returncode = message.get("returncode")
            if isinstance(returncode, int) and returncode != 0:
                summary["tool_failures"] += 1
    if saw_prompt_tokens:
        summary["prompt_tokens"] = prompt_tokens
    if saw_completion_tokens:
        summary["completion_tokens"] = completion_tokens
    if saw_cached_tokens:
        summary["cached_tokens"] = cached_tokens
    return summary


def usage_from_message(message: dict[str, Any]) -> dict[str, Any] | None:
    extra = message.get("extra")
    candidates: list[Any] = []
    if isinstance(extra, dict):
        candidates.append(extra.get("usage"))
        response = extra.get("response")
        if isinstance(response, dict):
            candidates.append(response.get("usage"))
    response = message.get("response")
    if isinstance(response, dict):
        candidates.append(response.get("usage"))
    candidates.append(message.get("usage"))
    candidates.append(message.get("model_usage"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return None


def usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return None


def cached_tokens_from_usage(usage: dict[str, Any]) -> int | None:
    direct = usage_int(usage, "cached_tokens", "cached_prompt_tokens")
    if direct is not None:
        return direct
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    if isinstance(details, dict):
        return usage_int(details, "cached_tokens", "cache_read_tokens")
    return None
