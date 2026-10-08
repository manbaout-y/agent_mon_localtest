from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from agentmon.analysis.io import open_maybe_compressed, write_json


INVENTORY_SCHEMA_VERSION = "1.0"
EVENT_SUFFIXES = (".jsonl", ".jsonl.gz", ".jsonl.zst")


@dataclass(frozen=True)
class BaselineExpectations:
    expected_runs: int | None = None
    expected_request_attempts: int | None = None
    expected_p2_requests: int | None = None
    allow_mismatch: bool = False


def build_input_inventory(
    input_root: str | Path,
    *,
    sample_jsonl_lines: int = 25,
) -> dict[str, Any]:
    root = Path(input_root).expanduser().resolve()
    started_errors: list[dict[str, Any]] = []
    if not root.exists():
        return {
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "input_root": str(root),
            "inventory_status": "FAIL",
            "reason": "input_root_missing",
            "errors": [{"path": str(root), "error": "missing"}],
        }
    files = sorted(path for path in root.rglob("*") if path.is_file())
    event_files = [path for path in files if is_event_jsonl(path)]
    run_dirs = discover_lightweight_run_dirs(root, files)
    run_ids: list[str] = []
    missing_events: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        events = first_existing_event_file(run_dir)
        if events is None:
            missing_events.append({"run_dir": str(run_dir), "missing": "events.jsonl"})
        manifest = run_dir / "manifest.json"
        if manifest.exists():
            run_ids.append(read_run_id(manifest, default=run_dir.name))
        else:
            run_ids.append(run_dir.name)
    duplicate_ids = sorted(item for item, count in Counter(run_ids).items() if count > 1)
    schema_counts: Counter[str] = Counter()
    event_type_counts: Counter[str] = Counter()
    unreadable: list[dict[str, Any]] = []
    empty = []
    total_event_bytes = 0
    for path in event_files:
        size = path.stat().st_size
        total_event_bytes += size
        if size == 0:
            empty.append(str(path))
            continue
        sample = sample_jsonl(path, sample_jsonl_lines)
        if sample.get("error"):
            unreadable.append({"path": str(path), "error": sample["error"]})
            continue
        schema_counts.update(sample.get("schema_versions", {}))
        event_type_counts.update(sample.get("event_types", {}))
    fingerprint = input_metadata_fingerprint(root, files)
    status = "FAIL" if unreadable or missing_events else "PARTIAL" if duplicate_ids or empty else "PASS"
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "input_root": str(root),
        "run_directories_discovered": len(run_dirs),
        "events_jsonl_files": len(event_files),
        "empty_events_files": len(empty),
        "empty_events_paths": empty[:100],
        "unreadable_events_files": len(unreadable),
        "unreadable_events": unreadable[:100],
        "missing_events_run_directories": len(missing_events),
        "missing_events": missing_events[:100],
        "total_events_bytes": total_event_bytes,
        "duplicate_run_ids": duplicate_ids,
        "duplicate_run_id_count": len(duplicate_ids),
        "schema_version_counts": dict(sorted(schema_counts.items())),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "input_fingerprint": fingerprint,
        "fingerprint_algorithm": "relative-path:size:mtime-ns:boundary-sha256",
        "sample_jsonl_lines_per_file": sample_jsonl_lines,
        "inventory_status": status,
    }


def write_input_inventory(output_root: str | Path, inventory: dict[str, Any]) -> None:
    out = Path(output_root)
    write_json(out / "00_inventory" / "input_inventory_summary.json", inventory)


def check_input_baseline(
    inventory: dict[str, Any],
    expectations: BaselineExpectations,
    *,
    parsed_request_attempts: int | None = None,
    parsed_p2_requests: int | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    add_count_check(
        checks,
        "runs",
        expected=expectations.expected_runs,
        observed=as_int(inventory.get("run_directories_discovered")),
        early=True,
    )
    add_count_check(
        checks,
        "request_attempts",
        expected=expectations.expected_request_attempts,
        observed=parsed_request_attempts,
        early=False,
    )
    add_count_check(
        checks,
        "p2_requests",
        expected=expectations.expected_p2_requests,
        observed=parsed_p2_requests,
        early=False,
    )
    mismatches = [row for row in checks if row["status"] == "FAIL"]
    pending = [row for row in checks if row["status"] == "PENDING"]
    status = "FAIL" if mismatches and not expectations.allow_mismatch else "PARTIAL" if mismatches or pending else "PASS"
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "input_root": inventory.get("input_root"),
        "input_fingerprint": inventory.get("input_fingerprint"),
        "allow_baseline_mismatch": expectations.allow_mismatch,
        "checks": checks,
        "mismatch_count": len(mismatches),
        "pending_count": len(pending),
        "input_baseline_status": status,
    }


def add_count_check(
    checks: list[dict[str, Any]],
    metric: str,
    *,
    expected: int | None,
    observed: int | None,
    early: bool,
) -> None:
    if expected is None:
        return
    status = "PENDING" if observed is None else "PASS" if observed == expected else "FAIL"
    checks.append(
        {
            "metric": metric,
            "expected": expected,
            "observed": observed,
            "early_check": early,
            "status": status,
            "reason": None if status == "PASS" else "baseline_count_mismatch" if status == "FAIL" else "observed_count_not_available_yet",
        }
    )


def discover_lightweight_run_dirs(root: Path, files: list[Path]) -> list[Path]:
    candidates: set[Path] = set()
    for path in files:
        if path.name in {"manifest.json", "report.json", "summary.json"}:
            parent = path.parent
            if first_existing_event_file(parent) is not None or path.name == "manifest.json":
                candidates.add(parent)
        elif path.name in {"events.jsonl", "events.jsonl.gz", "events.jsonl.zst"}:
            candidates.add(path.parent)
    return sorted(candidates)


def first_existing_event_file(run_dir: Path) -> Path | None:
    for name in ("events.jsonl", "events.jsonl.gz", "events.jsonl.zst"):
        path = run_dir / name
        if path.exists():
            return path
    return None


def is_event_jsonl(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(EVENT_SUFFIXES)


def sample_jsonl(path: Path, max_lines: int) -> dict[str, Any]:
    schema_versions: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    invalid = 0
    try:
        with open_maybe_compressed(path, "rt") as handle:
            for index, line in enumerate(handle):
                if index >= max_lines:
                    break
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                if not isinstance(item, dict):
                    invalid += 1
                    continue
                schema_versions[str(item.get("schema_version") or "unknown")] += 1
                event_types[str(item.get("event_type") or "unknown")] += 1
    except Exception as exc:  # noqa: BLE001 - represented as inventory evidence
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "schema_versions": dict(schema_versions),
        "event_types": dict(event_types),
        "invalid_sample_lines": invalid,
    }


def read_run_id(path: Path, *, default: str) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return default
    if isinstance(payload, dict) and payload.get("run_id") not in {None, ""}:
        return str(payload["run_id"])
    return default


def input_metadata_fingerprint(root: Path, files: list[Path]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path in files:
        try:
            stat = path.stat()
            relative = path.relative_to(root)
            digest.update(str(relative).encode("utf-8", errors="replace"))
            digest.update(b"\0")
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(b"\0")
            digest.update(boundary_content_sha256(path).encode("ascii"))
            digest.update(b"\n")
        except OSError:
            digest.update(str(path).encode("utf-8", errors="replace"))
            digest.update(b"\0missing\n")
    return digest.hexdigest()


def boundary_content_sha256(path: Path, *, window_bytes: int = 64 * 1024) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        head = handle.read(window_bytes)
        digest.update(head)
        size = path.stat().st_size
        if size > len(head):
            handle.seek(max(size - window_bytes, 0))
            digest.update(handle.read(window_bytes))
    return digest.hexdigest()


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
