from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.analysis.execution import AnalysisRunContext
from agentmon.analysis.io import read_json, write_json, write_markdown, write_plain_csv, write_table


def write_failure_diagnosis(
    bundle_root: str | Path,
    result: dict[str, Any],
    context: AnalysisRunContext,
) -> dict[str, Any]:
    bundle = Path(bundle_root)
    failed_stage = str(result.get("failed_stage") or context.current_phase or "unknown")
    category = failure_category(result)
    record = context.records.get(failed_stage)
    manifest = read_json(bundle / "analysis_manifest.json", default={})
    ledger = {
        "schema_version": "1.0",
        "failure_id": f"{failed_stage}:{result.get('exception_type') or result.get('reason') or 'failure'}",
        "stage": failed_stage,
        "module": failed_stage,
        "command": result.get("command"),
        "input_paths": [manifest.get("input_root")] if manifest.get("input_root") else [],
        "output_paths": [str(bundle)],
        "exception_type": result.get("exception_type"),
        "exception_message": result.get("exception_message"),
        "traceback_file": str(bundle / "run_summary.json"),
        "start_time": record.started_at if record else context.started_at,
        "failure_time": record.finished_at if record else None,
        "wall_s": record.wall_s if record else None,
        "CPU_s": record.cpu_s if record else None,
        "peak_rss": record.peak_rss if record else None,
        "bytes_read": context.progress.get("EVENT_BYTES_READ"),
        "bytes_written": record.disk_growth_bytes if record else None,
        "files_opened": context.progress.get("EVENT_FILES_COMPLETED"),
        "retry_count": 0,
        "cache_hit": record.cache_hit if record else False,
        "resumable": failed_stage not in {"preflight", "input_inventory", "baseline_check"},
        "direct_cause": result.get("exception_message") or result.get("reason"),
        "failure_category": category,
        "root_cause": "NOT_VERIFIED_FROM_AVAILABLE_EVIDENCE",
        "proposed_fix": proposed_fix(category),
        "confidence": "medium",
        "direct_cause_confidence": "high" if result.get("exception_type") else "medium",
    }
    stage_rows = [item.to_dict() for item in context.records.values()]
    write_json(bundle / "analysis_failure_ledger.json", ledger)
    write_table(bundle, "analysis_failure_stages", stage_rows, output_format="csv")
    write_plain_csv(bundle / "analysis_failure_stages.csv", stage_rows)
    write_markdown(
        bundle / "analysis_failure_diagnosis.md",
        f"""# Analysis Failure Diagnosis

Failure stage: `{failed_stage}`

Category: `{category}`

Direct cause: `{ledger['direct_cause']}`

This diagnosis is generated from local stage evidence. It does not claim a server-data root cause.
""",
    )
    return ledger


def failure_category(result: dict[str, Any]) -> str:
    exception_type = str(result.get("exception_type") or "").lower()
    message = str(result.get("exception_message") or result.get("reason") or "").lower()
    if "memory" in exception_type or "memory" in message:
        return "MEMORY_EXHAUSTION"
    if "disk" in message or "space" in message:
        return "DISK_EXHAUSTION"
    if "timeout" in exception_type or "timeout" in message:
        return "TIMEOUT"
    if "repeated scan" in message or "repeated_scan" in message:
        return "REPEATED_SCAN"
    if "deadlock" in message:
        return "DEADLOCK"
    if any(token in message for token in ("brokenprocesspool", "multiprocessing", "pickle")):
        return "PARALLELISM_BUG"
    if any(token in message for token in ("subprocess", "external process", "child process")):
        return "EXTERNAL_PROCESS_FAILURE"
    if "schema" in message:
        return "SCHEMA_ERROR"
    if result.get("reason") in {"input_baseline_mismatch", "preflight_failed"}:
        return "DATA_ERROR"
    if result.get("reason") == "resume_rejected":
        return "INDEX_INVALIDATION"
    if result.get("exception_type"):
        return "IMPLEMENTATION_BUG"
    if result.get("final_status") == "FAIL":
        return "SEMANTIC_ERROR"
    return "UNKNOWN"


def proposed_fix(category: str) -> str:
    return {
        "MEMORY_EXHAUSTION": "reduce worker count or configure a lower memory limit",
        "DISK_EXHAUSTION": "free output/temp space or move the configured temp directory",
        "TIMEOUT": "resume from the latest compatible completed stage",
        "REPEATED_SCAN": "inspect P2 index compatibility and per-run query audit before rerunning",
        "DEADLOCK": "inspect the active stage and worker/process state before reducing concurrency",
        "PARALLELISM_BUG": "rerun the affected stage with one worker and inspect worker serialization evidence",
        "EXTERNAL_PROCESS_FAILURE": "inspect the captured child-process return code and stderr",
        "SCHEMA_ERROR": "select or add a compatible schema profile",
        "DATA_ERROR": "resolve the reported input/preflight evidence before heavy analysis",
        "INDEX_INVALIDATION": "run fresh or explicitly force resume after reviewing incompatibilities",
        "IMPLEMENTATION_BUG": "inspect the captured traceback and failed stage status",
        "SEMANTIC_ERROR": "inspect validation tables and forbidden conclusions",
    }.get(category, "inspect stage evidence and rerun only the affected stage")
