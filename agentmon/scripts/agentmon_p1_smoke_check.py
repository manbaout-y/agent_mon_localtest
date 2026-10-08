#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = load_summary(Path(args.run_dir))
        smoke_rc = rc_value(args.smoke_rc, "SMOKE_RC")
        final_test_rc = rc_value(args.final_test_rc, "FINAL_TEST_RC")
        result = build_smoke_result(summary, smoke_rc=smoke_rc, final_test_rc=final_test_rc)
    except Exception as exc:
        print(f"agentmon p1 smoke check error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if args.no_strict or result["task_success"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check an AgentMon P1 smoke run from summary/report JSON")
    parser.add_argument("run_dir", help="AgentMon run directory containing summary.json or report.json")
    parser.add_argument("--smoke-rc", type=int, help="wrapper smoke command return code; defaults to SMOKE_RC")
    parser.add_argument("--final-test-rc", type=int, help="final test command return code; defaults to FINAL_TEST_RC")
    parser.add_argument("--no-strict", action="store_true", help="always exit 0 after printing JSON")
    return parser


def load_summary(run_dir: Path) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if "data_integrity_pass" in summary and "command_nonzero_count" in summary:
            return summary
    report_path = run_dir / "report.json"
    if report_path.exists():
        return summarize_report(json.loads(report_path.read_text(encoding="utf-8")))
    raise FileNotFoundError(f"summary.json/report.json not found under {run_dir}")


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    manifest = as_dict(report.get("manifest"))
    agent = as_dict(report.get("agent"))
    event_log = as_dict(report.get("event_log"))
    p1 = as_dict(report.get("p1_spans"))
    tool_attr = as_dict(report.get("tool_attribution"))
    proc = as_dict(report.get("proc"))
    kv_cache = as_dict(report.get("kv_cache"))
    data_quality = as_dict(report.get("data_quality"))
    vllm_p2 = as_dict(report.get("vllm_p2"))
    ascend = as_dict(report.get("ascend"))
    ascend_device_summary = as_dict(ascend.get("device_summary"))
    run_status = report.get("status") or manifest.get("status")
    exit_code = report.get("exit_code") if report.get("exit_code") is not None else manifest.get("exit_code")
    agent_exit_status = agent.get("exit_status")
    prompt_tokens = number_or_none(agent.get("prompt_tokens"))
    generation_tokens = number_or_none(agent.get("completion_tokens"))
    cached_tokens = number_or_none(agent.get("cached_tokens"))
    token_source = agent.get("token_source")
    token_missing_reason = agent.get("token_missing_reason")
    if prompt_tokens is None and generation_tokens is None and cached_tokens is None:
        token_source = token_source or "unavailable"
        token_missing_reason = (
            token_missing_reason
            or "trajectory_usage_fields_absent;p1_usage_fields_absent;p2_attribution_not_exclusive"
        )
    summary = {
        "schema_version": "1.0",
        "summary_type": "agentmon.p1_smoke_summary",
        "run_id": report.get("run_id") or manifest.get("run_id"),
        "run_status": run_status,
        "exit_code": exit_code,
        "agent_exit_status": agent_exit_status,
        "data_quality": data_quality,
        "p1_status": p1.get("status"),
        "invalid_json_lines": number_or_none(event_log.get("jsonl_invalid_count", event_log.get("jsonl_invalid_lines"))),
        "duplicate_event_ids": number_or_none(event_log.get("duplicate_event_ids")),
        "open_spans": number_or_none(p1.get("open_span_count")),
        "negative_durations": number_or_none(p1.get("negative_duration_count")),
        "event_count_mismatch": bool(event_log.get("event_count_mismatch") or event_log.get("p0_jsonl_sqlite_mismatch")),
        "instrumentation_error_count": count_instrumentation_errors(report),
        "command_nonzero_count": count_nonzero_commands(tool_attr),
        "expected_test_failure_count": number_or_none(manifest.get("expected_test_failure_count")),
        "submission_control_flow_count": count_submission_control_flow(report),
        "proc_agent_process_count_max": number_or_none(proc.get("agent_process_count_max")),
        "npu_target_attribution_status": ascend_device_summary.get("target_attribution_status") or "unavailable",
        "prompt_tokens": prompt_tokens,
        "generation_tokens": generation_tokens,
        "cached_tokens": cached_tokens,
        "token_source": token_source,
        "token_missing_reason": token_missing_reason,
        "prefix_cache_status": kv_cache.get("prefix_cache_status"),
        "prefix_cache_hit_rate": kv_cache.get("prefix_cache_hit_rate"),
        "p2_status": vllm_p2.get("status"),
        "p2_request_count": number_or_none(vllm_p2.get("request_count")),
    }
    summary["data_integrity_pass"] = data_integrity_pass(summary)
    summary["task_success"] = (
        run_status == "success"
        and exit_code == 0
        and str(agent_exit_status).lower() == "submitted"
        and summary["data_integrity_pass"]
    )
    return summary


def build_smoke_result(summary: dict[str, Any], *, smoke_rc: int | None, final_test_rc: int | None) -> dict[str, Any]:
    issues: list[str] = []
    if smoke_rc != 0:
        issues.append("smoke_rc")
    if final_test_rc != 0:
        issues.append("final_test_rc")
    if summary.get("run_status") != "success":
        issues.append("run_status")
    if str(summary.get("agent_exit_status")).lower() != "submitted":
        issues.append("agent_exit_status")
    if summary.get("data_integrity_pass") is not True:
        issues.append("data_integrity")
    quality_issues = data_quality_issues(summary)
    issues.extend(quality_issues)
    return {
        "schema_version": "1.0",
        "smoke_check_type": "agentmon.p1_smoke_check",
        "run_id": summary.get("run_id"),
        "task_success": not issues,
        "data_integrity_pass": summary.get("data_integrity_pass"),
        "instrumentation_error_count": summary.get("instrumentation_error_count"),
        "command_nonzero_count": summary.get("command_nonzero_count"),
        "expected_test_failure_count": summary.get("expected_test_failure_count"),
        "submission_control_flow_count": summary.get("submission_control_flow_count"),
        "criteria": {
            "smoke_rc": smoke_rc,
            "final_test_rc": final_test_rc,
            "run_status": summary.get("run_status"),
            "agent_exit_status": summary.get("agent_exit_status"),
            "invalid_json_lines": summary.get("invalid_json_lines"),
            "duplicate_event_ids": summary.get("duplicate_event_ids"),
            "open_spans": summary.get("open_spans"),
            "negative_durations": summary.get("negative_durations"),
            "event_count_mismatch": summary.get("event_count_mismatch"),
            "trajectory_status": nested_quality(summary, "trajectory"),
            "p1_status": summary.get("p1_status"),
            "proc_agent_process_count_max": summary.get("proc_agent_process_count_max"),
            "npu_target_attribution_status": summary.get("npu_target_attribution_status"),
            "token_source": summary.get("token_source"),
            "token_missing_reason": summary.get("token_missing_reason"),
            "prefix_cache_status": summary.get("prefix_cache_status"),
            "prefix_cache_hit_rate": summary.get("prefix_cache_hit_rate"),
            "p2_status": summary.get("p2_status"),
            "p2_request_count": summary.get("p2_request_count"),
        },
        "issues": issues,
    }


def data_integrity_pass(summary: dict[str, Any]) -> bool:
    return (
        not positive(summary.get("invalid_json_lines"))
        and not positive(summary.get("duplicate_event_ids"))
        and not positive(summary.get("open_spans"))
        and not positive(summary.get("negative_durations"))
        and not bool(summary.get("event_count_mismatch"))
    )


def count_nonzero_commands(tool_attr: dict[str, Any]) -> int | None:
    tools = tool_attr.get("tools")
    if not isinstance(tools, list):
        return None
    return sum(1 for tool in tools if isinstance(tool, dict) and isinstance(tool.get("returncode"), int) and tool["returncode"] != 0)


def count_instrumentation_errors(report: dict[str, Any]) -> int | None:
    spans = as_dict(report.get("p1_spans")).get("spans")
    if isinstance(spans, list):
        return sum(
            1
            for span in spans
            if isinstance(span, dict)
            and span.get("status") == "error"
            and span.get("terminal_reason") != "submitted"
            and not span.get("submission_control_flow")
        )
    errors = report.get("errors")
    if not isinstance(errors, list):
        return None
    return sum(1 for error in errors if isinstance(error, dict) and str(error.get("event_type", "")).endswith(".error"))


def count_submission_control_flow(report: dict[str, Any]) -> int | None:
    spans = as_dict(report.get("p1_spans")).get("spans")
    if not isinstance(spans, list):
        return None
    return sum(
        1
        for span in spans
        if isinstance(span, dict)
        and (span.get("terminal_reason") == "submitted" or span.get("submission_control_flow") is True)
    )


def rc_value(value: int | None, env_name: str) -> int | None:
    if value is not None:
        return value
    raw = os.environ.get(env_name)
    if raw in {None, ""}:
        return None
    return int(raw)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def positive(value: Any) -> bool:
    numeric = number_or_none(value)
    return numeric is not None and numeric > 0


def data_quality_issues(summary: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    data_quality = summary.get("data_quality") if isinstance(summary.get("data_quality"), dict) else {}
    if data_quality.get("trajectory") != "complete":
        issues.append("trajectory_quality")
    if summary.get("p1_status") != "complete":
        issues.append("p1_spans")
    process_count_max = numeric_or_none(summary.get("proc_agent_process_count_max"))
    if process_count_max is not None and process_count_max <= 0:
        issues.append("proc_agent_process_count")
    npu_status = summary.get("npu_target_attribution_status")
    if npu_status not in {None, "complete", "partial", "unavailable"}:
        issues.append("npu_target_attribution_status")
    if token_fields_missing(summary):
        issues.append("token_attribution")
    if summary.get("prefix_cache_status") == "disabled" and summary.get("prefix_cache_hit_rate") not in {None, "N/A"}:
        issues.append("prefix_cache_disabled_hit_rate")
    if summary.get("p2_status") not in {None, "missing", "complete", "partial"}:
        issues.append("p2_status")
    return issues


def token_fields_missing(summary: dict[str, Any]) -> bool:
    has_tokens = any(
        numeric_or_none(summary.get(key)) is not None
        for key in ("prompt_tokens", "generation_tokens", "cached_tokens")
    )
    if has_tokens:
        return not summary.get("token_source")
    return not (summary.get("token_source") or summary.get("token_missing_reason"))


def nested_quality(summary: dict[str, Any], key: str) -> Any:
    data_quality = summary.get("data_quality")
    if not isinstance(data_quality, dict):
        return None
    return data_quality.get(key)


def numeric_or_none(value: Any) -> float | int | None:
    return number_or_none(value)


if __name__ == "__main__":
    raise SystemExit(main())
