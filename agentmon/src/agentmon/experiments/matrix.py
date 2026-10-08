from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time
from typing import Any, Sequence

from agentmon import __version__
from agentmon.paths import make_private_dir, open_private_text, write_private_text


SCHEMA_VERSION = "1.0"


def run_repeated_matrix(
    command_argv: Sequence[str],
    *,
    repeat: int,
    output_root: str | Path,
    label: str | None = None,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    if repeat < 1:
        raise ValueError("repeat must be >= 1")
    if not command_argv:
        raise ValueError("matrix requires a command after --")
    root = make_private_dir(output_root)
    started_wall_ns = time.time_ns()
    rows: list[dict[str, Any]] = []
    for index in range(1, repeat + 1):
        repeat_dir = make_private_dir(root / f"repeat-{index:03d}")
        context = {
            "repeat": str(index),
            "repeat_index": str(index),
            "repeat_dir": str(repeat_dir),
            "matrix_root": str(root),
        }
        argv = [render_arg(value, context) for value in command_argv]
        stdout_path = repeat_dir / "command.stdout.log"
        stderr_path = repeat_dir / "command.stderr.log"
        env = dict(os.environ)
        env.update(
            {
                "AGENTMON_MATRIX_ROOT": str(root),
                "AGENTMON_MATRIX_REPEAT_INDEX": str(index),
                "AGENTMON_MATRIX_REPEAT_DIR": str(repeat_dir),
            }
        )
        start_wall_ns = time.time_ns()
        start_mono_ns = time.monotonic_ns()
        with open_private_text(stdout_path) as stdout, open_private_text(stderr_path) as stderr:
            completed = subprocess.run(argv, env=env, stdout=stdout, stderr=stderr, text=True, check=False)
        end_mono_ns = time.monotonic_ns()
        end_wall_ns = time.time_ns()
        batch_summary = read_json(repeat_dir / "batch-summary.json")
        rows.append(
            {
                "repeat_index": index,
                "repeat_dir": str(repeat_dir),
                "status": "success" if completed.returncode == 0 else "failed",
                "exit_code": completed.returncode,
                "start_wall_ns": start_wall_ns,
                "end_wall_ns": end_wall_ns,
                "start_mono_ns": start_mono_ns,
                "end_mono_ns": end_mono_ns,
                "wall_s": (end_mono_ns - start_mono_ns) / 1_000_000_000,
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
                "batch_pipeline_wall_s": number_or_none(batch_summary.get("pipeline_wall_s")),
                "batch_observed_success_throughput": number_or_none(
                    as_dict(batch_summary.get("throughput")).get("observed_success_throughput")
                ),
            }
        )
        if completed.returncode != 0 and not continue_on_error:
            break
    summary = {
        "schema_version": SCHEMA_VERSION,
        "experiment_type": "agentmon.repeated_matrix",
        "label": label,
        "agentmon_version": __version__,
        "repeat_requested": repeat,
        "repeat_completed": len(rows),
        "success_count": sum(row["exit_code"] == 0 for row in rows),
        "failure_count": sum(row["exit_code"] != 0 for row in rows),
        "started_wall_ns": started_wall_ns,
        "ended_wall_ns": time.time_ns(),
        "command_argv_template": list(command_argv),
        "statistics": {
            "wall_s": statistics_bundle(row.get("wall_s") for row in rows),
            "batch_pipeline_wall_s": statistics_bundle(row.get("batch_pipeline_wall_s") for row in rows),
            "observed_success_throughput": statistics_bundle(
                row.get("batch_observed_success_throughput") for row in rows
            ),
        },
        "results": rows,
    }
    write_private_text(root / "matrix-manifest.json", json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    write_results_csv(root / "matrix-results.csv", rows)
    return summary


def statistics_bundle(values: Any) -> dict[str, Any]:
    cleaned = [value for raw in values if (value := number_or_none(raw)) is not None]
    if not cleaned:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "p95": None,
        }
    return {
        "count": len(cleaned),
        "mean": statistics.fmean(cleaned),
        "median": percentile(cleaned, 50),
        "standard_deviation": statistics.stdev(cleaned) if len(cleaned) >= 2 else None,
        "p95": percentile(cleaned, 95),
    }


def write_results_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with open_private_text(path, newline="") as handle:
        if not fields:
            return
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_arg(value: str, context: dict[str, str]) -> str:
    result = value
    for key, replacement in context.items():
        result = result.replace("{" + key + "}", replacement)
    return result


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value in {None, "", "N/A"}:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
