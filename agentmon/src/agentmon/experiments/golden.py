from __future__ import annotations

"""Deterministic, privacy-preserving comparison helpers for experiment outputs."""

import csv
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable


VOLATILE_KEYS = frozenset(
    {
        "generated_at",
        "started_at",
        "finished_at",
        "updated_at",
        "created_at",
        "created_wall_time_ns",
        "wall_s",
        "cpu_s",
        "peak_rss",
        "disk_growth_bytes",
        "elapsed_seconds",
        "run_rate_per_second",
        "byte_rate_per_second",
        "eta_seconds",
    }
)
PATH_KEYS = frozenset(
    {
        "path",
        "batch_root",
        "output_dir",
        "source_path",
        "events_path",
        "run_dir",
        "package",
        "repo",
    }
)


def semantic_hash(path: str | Path) -> str:
    """Hash a public artifact after removing only known non-semantic fields."""
    source = Path(path).expanduser().resolve()
    suffix = source.suffix.lower()
    if suffix == ".json":
        value = json.loads(source.read_text(encoding="utf-8"))
    elif suffix in {".csv", ".tsv"}:
        with source.open("r", encoding="utf-8", newline="") as handle:
            value = list(csv.DictReader(handle, delimiter="\t" if suffix == ".tsv" else ","))
    elif suffix == ".jsonl":
        value = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        value = source.read_text(encoding="utf-8")
    canonical = json.dumps(normalize_semantic_value(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def normalize_semantic_value(value: Any, *, key: str | None = None) -> Any:
    """Remove run-local noise without silently changing evidence values."""
    if key in VOLATILE_KEYS:
        return "<VOLATILE>"
    if isinstance(value, str) and (key in PATH_KEYS or Path(value).is_absolute()):
        return "<PATH>"
    if isinstance(value, dict):
        return {
            str(item_key): normalize_semantic_value(item_value, key=str(item_key))
            for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        # Row and event order are part of the canonical replay contract.
        return [normalize_semantic_value(item) for item in value]
    return value


def compare_semantic_artifacts(
    left: dict[str, str | Path],
    right: dict[str, str | Path],
) -> dict[str, Any]:
    """Compare named output files and retain hashes as reviewable evidence."""
    names = sorted(set(left) | set(right))
    rows = []
    for name in names:
        left_path = Path(left[name]).expanduser().resolve() if name in left else None
        right_path = Path(right[name]).expanduser().resolve() if name in right else None
        left_hash = semantic_hash(left_path) if left_path and left_path.is_file() else None
        right_hash = semantic_hash(right_path) if right_path and right_path.is_file() else None
        rows.append(
            {
                "artifact_name": name,
                "left_present": left_hash is not None,
                "right_present": right_hash is not None,
                "left_semantic_sha256": left_hash,
                "right_semantic_sha256": right_hash,
                "status": "PASS" if left_hash is not None and left_hash == right_hash else "FAIL",
            }
        )
    return {
        "schema_version": "1.0",
        "result_type": "agentmon.optimizer_experiment.golden_comparison",
        "status": "PASS" if rows and all(row["status"] == "PASS" for row in rows) else "FAIL",
        "artifact_count": len(rows),
        "artifacts": rows,
        "limitations": [
            "Only explicitly listed volatile fields and path identities are normalized.",
            "Golden equality does not establish causal runtime benefit.",
        ],
    }


def public_artifact_paths(root: str | Path, names: Iterable[str]) -> dict[str, Path]:
    """Resolve an explicit Golden set; callers choose which public files matter."""
    output_root = Path(root).expanduser().resolve()
    return {name: output_root / name for name in names}
