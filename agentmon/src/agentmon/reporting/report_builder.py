from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.reporting._legacy_report import *  # noqa: F401,F403 - domain summarizers used by report builder
from agentmon.reporting.event_log import read_event_log

def build_report(run_dir: str | Path, *, event_log: dict[str, Any] | None = None) -> dict[str, Any]:
    run_path = Path(run_dir)
    sqlite_path = run_path / "metrics.sqlite3"
    manifest = read_json(run_path / "manifest.json")
    event_log = event_log if event_log is not None else read_event_log(run_path)
    events = event_log["events"]
    sqlite_count = sqlite_event_count(sqlite_path)
    sqlite_ids = sqlite_event_ids(sqlite_path)
    event_log_summary = summarize_event_log(event_log, sqlite_count, sqlite_ids)
    p1_spans = summarize_p1_spans(
        events,
        run_end_mono_ns=manifest.get("end_mono_ns"),
        finalize=manifest.get("status") not in {None, "initializing", "running"},
    )
    vllm_p2 = summarize_vllm_p2_events(
        events,
        expected_prefix_cache_status=manifest.get("prefix_caching_status"),
    )
    semantic_integrity = summarize_semantic_integrity(manifest, events, p1_spans, vllm_p2)
    event_snapshot = summarize_event_snapshot(event_log, manifest)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": manifest.get("run_id"),
        "status": manifest.get("status"),
        "exit_code": manifest.get("exit_code"),
        "term_signal": manifest.get("term_signal"),
        "duration_s": ns_to_s(manifest.get("duration_ns")),
        "manifest": manifest,
        "data_quality": manifest.get("data_quality", {}),
        "event_count": len(events),
        "event_snapshot": event_snapshot,
        "finalization": as_dict(manifest.get("finalization")),
        "agent": summarize_trajectory_events(events),
        "event_log": event_log_summary,
        "analysis_state": summarize_analysis_state(manifest, events),
        "p1_spans": p1_spans,
        "tool_attribution": summarize_tool_attribution(events),
        "host": summarize_host_events(events),
        "proc": summarize_proc_events(events),
        "vllm": summarize_vllm_events(events),
        "vllm_p2": vllm_p2,
        "client_server": summarize_client_server_stages(events, vllm_p2),
        "semantic_integrity": semantic_integrity,
        "kv_cache": summarize_kv_cache(manifest, events),
        "ascend": summarize_ascend_events(events),
        "errors": summarize_errors(events),
        "evidence": {
            "events": str(run_path / "events.jsonl"),
            "sqlite": str(sqlite_path) if sqlite_path.is_file() else None,
            "raw": str(run_path / "raw"),
        },
    }
    return report


def write_reports(
    run_dir: str | Path,
    *,
    event_log: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from agentmon.reporting.summary import build_run_summary
    from agentmon.reporting.canonical import write_run_canonical_tables

    run_path = Path(run_dir)
    event_log = event_log if event_log is not None else read_event_log(run_path)
    report = build_report(run_path, event_log=event_log)
    quality_bundle = build_report_quality_bundle(report)
    report["analysis_publication"] = {
        "status": quality_bundle["validation_checks"]["publishability_status"],
        "validation_checks": "validation_checks.json",
        "metric_definition": "metric_definition.json",
        "data_quality": "data_quality.json",
        "confidence": "confidence.json",
        "limitations": "limitations.json",
    }
    write_private_text(
        run_path / "report.json",
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
    )
    write_private_text(
        run_path / "summary.json",
        json.dumps(build_run_summary(report), ensure_ascii=False, indent=2, sort_keys=True),
    )
    write_private_text(run_path / "report.txt", format_text_report(report))
    write_csv_report(report, run_path / "report.csv")
    for filename, payload in quality_bundle.items():
        write_private_text(
            run_path / f"{filename}.json",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        )
    canonical_manifest = write_run_canonical_tables(
        run_path,
        report=report,
        events=event_log["events"],
    )
    report["analysis_publication"]["canonical"] = "canonical/canonical_manifest.json"
    report["analysis_publication"]["canonical_parquet_status"] = canonical_manifest.get("parquet_status")
    write_private_text(
        run_path / "report.json",
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
    )
    return report
