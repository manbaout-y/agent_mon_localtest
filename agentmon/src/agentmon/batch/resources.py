from __future__ import annotations

from pathlib import Path
import resource
import sys
import time
from typing import Any

from agentmon.batch.io import as_dict
from agentmon.batch.metrics import optional_number
from agentmon.sampling.npu_mapping import collect_npu_process_mapping as collect_npu_process_mapping_evidence

def capture_pipeline_resources(
    root: Path | None = None,
    *,
    include_files: bool = False,
) -> dict[str, Any]:
    self_usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    io = read_proc_self_io()
    file_count = None
    file_bytes = None
    if include_files and root is not None and root.exists():
        inventory = capture_file_inventory(root)
        file_count = inventory.get("file_count")
        file_bytes = inventory.get("file_bytes")
    max_rss = max(self_usage.ru_maxrss, child_usage.ru_maxrss)
    return {
        "monotonic_ns": time.monotonic_ns(),
        "self_cpu_s": self_usage.ru_utime + self_usage.ru_stime,
        "children_cpu_s": child_usage.ru_utime + child_usage.ru_stime,
        "max_rss_bytes": int(max_rss if sys.platform == "darwin" else max_rss * 1024),
        "read_bytes": io.get("read_bytes"),
        "write_bytes": io.get("write_bytes"),
        "file_count": file_count,
        "file_bytes": file_bytes,
    }


def collect_npu_process_mapping(
    args: argparse.Namespace,
    env: dict[str, str],
    batch_root: Path,
) -> dict[str, Any]:
    return collect_npu_process_mapping_evidence(
        env=env,
        output_dir=batch_root,
        model_path=getattr(args, "model_path", None),
        process_match=("vllm",),
    )


def capture_file_inventory(root: Path) -> dict[str, Any]:
    file_count = 0
    file_bytes = 0
    error_count = 0
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            file_count += 1
            try:
                file_bytes += path.stat().st_size
            except OSError:
                error_count += 1
    except OSError:
        return {
            "file_count": None,
            "file_bytes": None,
            "error_count": error_count + 1,
            "missing_reason": "filesystem_walk_failed",
        }
    return {
        "file_count": file_count,
        "file_bytes": file_bytes,
        "error_count": error_count,
        "missing_reason": None if error_count == 0 else "partial_stat_failures",
    }


def aggregate_task_stage_profiles(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stage_names = sorted(
        {
            stage
            for result in results
            for stage in as_dict(result.get("analysis_stage_durations_s"))
        }
    )
    output: dict[str, dict[str, Any]] = {}
    for stage in stage_names:
        durations = [
            value
            for result in results
            if (value := optional_number(as_dict(result.get("analysis_stage_durations_s")).get(stage)))
            is not None
        ]
        cpu_values = [
            value
            for result in results
            if (
                value := optional_number(
                    as_dict(as_dict(result.get("analysis_stage_resources")).get(stage)).get("total_cpu_s")
                )
            )
            is not None
        ]
        output[stage] = {
            "sample_count": len(durations),
            "wall_s_total": sum(durations) if durations else None,
            "wall_s_mean": sum(durations) / len(durations) if durations else None,
            "wall_s_max": max(durations) if durations else None,
            "cpu_s_total": sum(cpu_values) if cpu_values else None,
            "resource_attribution": "shared_analysis_process_observation",
        }
    return output


def pipeline_resource_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    wall_s = numeric_delta(before.get("monotonic_ns"), after.get("monotonic_ns"), scale=1e-9)
    self_cpu_s = numeric_delta(before.get("self_cpu_s"), after.get("self_cpu_s"))
    child_cpu_s = numeric_delta(before.get("children_cpu_s"), after.get("children_cpu_s"))
    return {
        "wall_s": wall_s,
        "self_cpu_s": self_cpu_s,
        "child_cpu_s": child_cpu_s,
        "total_cpu_s": (
            self_cpu_s + child_cpu_s if self_cpu_s is not None and child_cpu_s is not None else None
        ),
        "cpu_wall_ratio": (
            (self_cpu_s + child_cpu_s) / wall_s
            if wall_s not in {None, 0} and self_cpu_s is not None and child_cpu_s is not None
            else None
        ),
        "peak_rss_bytes_end": after.get("max_rss_bytes"),
        "read_bytes_delta": numeric_delta(before.get("read_bytes"), after.get("read_bytes")),
        "write_bytes_delta": numeric_delta(before.get("write_bytes"), after.get("write_bytes")),
        "file_count_start": before.get("file_count"),
        "file_count_end": after.get("file_count"),
        "file_count_delta": numeric_delta(before.get("file_count"), after.get("file_count")),
        "file_bytes_start": before.get("file_bytes"),
        "file_bytes_end": after.get("file_bytes"),
        "file_bytes_delta": numeric_delta(before.get("file_bytes"), after.get("file_bytes")),
        "quality": "exact_process_counters" if wall_s is not None else "partial",
    }


def read_proc_self_io() -> dict[str, int]:
    path = Path("/proc/self/io")
    if not path.is_file():
        return {}
    result: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        try:
            result[key.strip()] = int(value.strip())
        except ValueError:
            continue
    return result


def numeric_delta(before: Any, after: Any, *, scale: float = 1.0) -> float | None:
    left = optional_number(before)
    right = optional_number(after)
    if left is None or right is None or right < left:
        return None
    return (right - left) * scale
