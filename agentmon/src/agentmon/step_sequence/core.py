#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmon.adapters.inference.vllm_018.importer import is_p2_global_event
from agentmon.step_sequence.constants import SCHEMA_VERSION
from agentmon.step_sequence.formatters import format_output
from agentmon.step_sequence.io import read_json_file, read_jsonl, read_optional_json
from agentmon.step_sequence.p2 import (
    build_p2_identity_audit,
    build_p2_index,
    build_totals,
    build_warnings,
    find_p2_match,
    nearest_kv_snapshot,
    p2_projection,
    step_kv_summary,
)
from agentmon.step_sequence.resources import (
    ascend_window_summary,
    build_resource_context,
    proc_window_summary,
    scheduler_window_summary,
)
from agentmon.step_sequence.spans import build_spans
from agentmon.step_sequence.utils import (
    as_dict,
    byte_count,
    cached_tokens_from_usage,
    first_int,
    first_value,
    integer_or_none,
    list_dicts,
    max_int,
    min_int,
    nonzero,
    ns_delta,
    ns_to_s,
    number_or_none,
    parse_timestamp_seconds,
    ratio_or_none,
    run_id_from_events,
    sort_time,
    sum_numeric,
    text_or_none,
)
from agentmon.step_sequence.utils import *  # noqa: F403 - shared internal step-sequence helpers

def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        analyses = [
            build_step_sequence(
                Path(run_dir),
                p2_events=Path(args.p2_events) if args.p2_events else None,
                service_p2_events=Path(args.service_p2_events) if args.service_p2_events else None,
                ascend_devices=args.ascend_devices,
            )
            for run_dir in args.run_dirs
        ]
        payload: dict[str, Any] | list[dict[str, Any]]
        payload = analyses[0] if len(analyses) == 1 else analyses
        if args.output_dir:
            write_output_bundle(Path(args.output_dir), payload)
        print(format_output(payload, args.format))
    except Exception as exc:
        print(f"agentmon step sequence error: {exc}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze AgentMon P1/P2 events as a per-agent-step execution sequence",
    )
    parser.add_argument("run_dirs", nargs="+", help="AgentMon run directory/directories containing events.jsonl")
    parser.add_argument(
        "--p2-events",
        help="optional service-scoped P2 events.jsonl to correlate with one run",
    )
    parser.add_argument(
        "--service-p2-events",
        help="optional service P2 stream used only for global KV/capability context; request rows are not imported",
    )
    parser.add_argument(
        "--ascend-devices",
        help="comma-separated npu-smi device ids for target/model attribution; defaults to P2 visible_devices when available",
    )
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table")
    parser.add_argument(
        "--output-dir",
        help="build the sequence once and write step_sequence.json/.csv/.txt into this directory",
    )
    return parser


def write_output_bundle(output_dir: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.chmod(0o700)
    except OSError:
        pass
    for filename, fmt in (
        ("step_sequence.json", "json"),
        ("step_sequence.csv", "csv"),
        ("step_sequence.txt", "table"),
    ):
        path = output_dir / filename
        path.write_text(format_output(payload, fmt) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass


def build_step_sequence(
    run_dir: Path,
    *,
    p2_events: Path | None = None,
    service_p2_events: Path | None = None,
    ascend_devices: str | None = None,
    run_event_log: dict[str, Any] | None = None,
    p2_event_log: dict[str, Any] | None = None,
    service_event_log: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = read_optional_json(run_dir / "manifest.json")
    run_event_log = run_event_log if run_event_log is not None else read_jsonl(run_dir / "events.jsonl")
    events = run_event_log["events"]
    p2_event_log = p2_event_log if p2_event_log is not None else (
        read_jsonl(p2_events) if p2_events is not None else {"events": [], "invalid_lines": 0}
    )
    service_event_log = service_event_log if service_event_log is not None else (
        read_jsonl(service_p2_events)
        if service_p2_events is not None
        else {"events": [], "invalid_lines": 0}
    )
    service_global_events = [event for event in service_event_log["events"] if is_service_global_p2_event(event)]
    all_p2_events = (
        [event for event in events if event.get("source") == "vllm_p2"]
        + list(p2_event_log["events"])
        + service_global_events
    )
    p2_index = build_p2_index(all_p2_events)
    spans = build_spans(events)
    resources = build_resource_context(events, all_p2_events, ascend_devices=ascend_devices)
    analysis = assemble_sequence(run_dir, events, spans, p2_index, resources)
    if not analysis["steps"]:
        fallback = build_trajectory_fallback_sequence(run_dir, events, p2_index, resources)
        if fallback is not None:
            analysis = fallback
    annotate_sequence_times(analysis, manifest)
    add_count_sources(analysis, run_dir, spans)
    analysis["invalid_json_lines"] = run_event_log["invalid_lines"]
    analysis["external_p2_invalid_json_lines"] = p2_event_log["invalid_lines"]
    analysis["service_p2_invalid_json_lines"] = service_event_log["invalid_lines"]
    analysis["p2"] = {
        "source": "run_events" if p2_events is None else "run_events+external",
        "service_global_source": str(service_p2_events) if service_p2_events is not None else None,
        "service_global_event_count": len(service_global_events),
        "service_global_observation_scope": (
            "shared_service_observation" if service_p2_events is not None else None
        ),
        "request_count": len(p2_index["rows"]),
        "direct_agent_request_count": sum(1 for row in p2_index["rows"] if row.get("agent_request_id")),
    }
    analysis["run_wall_s"] = ns_to_s(integer_or_none(manifest.get("duration_ns")))
    analysis["run_wall_source"] = "manifest.duration_ns" if analysis["run_wall_s"] is not None else "unavailable"
    analysis["run_start_wall_ns"] = integer_or_none(manifest.get("start_wall_ns"))
    analysis["run_start_mono_ns"] = integer_or_none(manifest.get("start_mono_ns"))
    analysis["run_end_mono_ns"] = integer_or_none(manifest.get("end_mono_ns"))
    analysis["clock_domain_id"] = manifest.get("clock_domain_id")
    analysis["host_boot_id"] = manifest.get("host_boot_id")
    analysis["source_host"] = manifest.get("host_id")
    return analysis


def annotate_sequence_times(analysis: dict[str, Any], manifest: dict[str, Any]) -> None:
    run_start_ns = integer_or_none(manifest.get("start_mono_ns"))
    run_clock_domain = manifest.get("clock_domain_id")
    run_boot_id = manifest.get("host_boot_id")
    run_host = manifest.get("host_id")
    for step in analysis.get("steps") or []:
        step_start_ns = integer_or_none(step.get("start_mono_ns"))
        step_end_ns = integer_or_none(step.get("end_mono_ns"))
        step["step_start_offset_from_run_s"] = ns_to_s(ns_delta(run_start_ns, step_start_ns))
        step["step_end_offset_from_run_s"] = ns_to_s(ns_delta(run_start_ns, step_end_ns))
        step.setdefault("clock_domain_id", first_value_from_items(step.get("items"), "clock_domain_id", run_clock_domain))
        step.setdefault("host_boot_id", first_value_from_items(step.get("items"), "host_boot_id", run_boot_id))
        step.setdefault("source_host", first_value_from_items(step.get("items"), "source_host", run_host))
        for item in step.get("items") or []:
            item_start_ns = integer_or_none(item.get("start_mono_ns"))
            item_end_ns = integer_or_none(item.get("end_mono_ns"))
            item["item_start_mono_ns"] = item_start_ns
            item["item_end_mono_ns"] = item_end_ns
            item["item_start_offset_from_step_s"] = ns_to_s(ns_delta(step_start_ns, item_start_ns))
            item["item_start_offset_from_run_s"] = ns_to_s(ns_delta(run_start_ns, item_start_ns))
            item["item_end_offset_from_run_s"] = ns_to_s(ns_delta(run_start_ns, item_end_ns))
            item["start_offset_s"] = item["item_start_offset_from_step_s"]
            item.setdefault("clock_domain_id", step.get("clock_domain_id"))
            item.setdefault("host_boot_id", step.get("host_boot_id"))
            item.setdefault("source_host", step.get("source_host"))


def first_value_from_items(items: Any, key: str, fallback: Any) -> Any:
    for item in items or []:
        if isinstance(item, dict) and item.get(key) not in {None, ""}:
            return item[key]
    return fallback


def add_count_sources(analysis: dict[str, Any], run_dir: Path, spans: list[dict[str, Any]]) -> None:
    trajectory_path = find_mini_swe_trajectory(run_dir)
    trajectory_steps: list[dict[str, Any]] = []
    if trajectory_path is not None:
        trajectory_steps = mini_swe_trajectory_steps(read_json_file(trajectory_path))
    items = [item for step in analysis.get("steps") or [] for item in step.get("items") or []]
    tool_items = [item for item in items if item.get("item_type") == "tool"]
    resource_complete = sum(
        1
        for item in tool_items
        if all(
            as_dict(item.get("proc")).get(field) is not None
            for field in ("cpu_time_s", "peak_rss_bytes", "peak_pss_bytes")
        )
    )
    counts = {
        "trajectory_tool_call_count": sum(len(step.get("tool_items") or []) for step in trajectory_steps),
        "p1_tool_span_count": sum(1 for span in spans if span.get("span_type") == "tool_call"),
        "analyzer_tool_row_count": len(tool_items),
        "tool_resource_complete_count": resource_complete,
    }
    analysis["count_sources"] = counts
    as_dict(analysis.get("totals")).update(counts)




def is_service_global_p2_event(event: dict[str, Any]) -> bool:
    if event.get("source") != "vllm_p2":
        return False
    event_type = str(event.get("event_type") or "")
    return is_p2_global_event(event)










def assemble_sequence(
    run_dir: Path,
    events: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    p2_index: dict[str, Any],
    resources: dict[str, Any],
) -> dict[str, Any]:
    run_id = run_id_from_events(events) or run_dir.name
    step_spans = [span for span in spans if span.get("span_type") == "agent_step"]
    child_spans = [span for span in spans if span.get("span_type") in {"model_request", "tool_call"}]
    steps_by_id = {str(span["span_id"]): make_step(span) for span in step_spans}

    for child in child_spans:
        step_id = step_id_for_child(child)
        if step_id is None:
            continue
        steps_by_id.setdefault(step_id, synthetic_step(step_id))

    for child in child_spans:
        step_id = step_id_for_child(child)
        if step_id is None or step_id not in steps_by_id:
            continue
        item = make_item(child, p2_index, resources)
        steps_by_id[step_id]["items"].append(item)

    steps = list(steps_by_id.values())
    for step in steps:
        finalize_step(step, resources)
    steps.sort(key=lambda step: sort_time(step))
    for index, step in enumerate(steps, start=1):
        step["step_index"] = index

    unassigned = [make_item(span, p2_index, resources) for span in child_spans if step_id_for_child(span) is None]
    totals = build_totals(steps, unassigned)
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_type": "agentmon.step_sequence",
        "sequence_source": "p1_events",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "event_count": len(events),
        "totals": totals,
        "p2_identity_audit": build_p2_identity_audit(steps, unassigned, p2_index.get("rows", [])),
        "steps": steps,
        "unassigned_items": unassigned,
        "resource_attribution": {
            "proc_sample_count": len(resources["proc_events"]),
            "ascend_sample_count": len(resources["ascend_events"]),
            "scheduler_sample_count": len(resources["scheduler_events"]),
            "ascend_target_devices": sorted(resources["ascend_target_devices"])
            if resources["ascend_target_devices"]
            else None,
            "ascend_target_device_source": resources["ascend_target_device_source"],
            "npu_observation_scope": resources["npu_observation_scope"],
            "npu_request_attribution": resources["npu_request_attribution"],
        },
        "warnings": build_warnings(steps, unassigned),
    }


def build_trajectory_fallback_sequence(
    run_dir: Path,
    events: list[dict[str, Any]],
    p2_index: dict[str, Any],
    resources: dict[str, Any],
) -> dict[str, Any] | None:
    trajectory_path = find_mini_swe_trajectory(run_dir)
    if trajectory_path is None:
        return None
    trajectory = read_json_file(trajectory_path)
    trajectory_steps = mini_swe_trajectory_steps(trajectory)
    if not trajectory_steps and not p2_index["rows"]:
        return None

    run_id = run_id_from_events(events) or run_dir.name
    p2_rows = sorted(p2_index["rows"], key=p2_row_sort_key)
    p2_by_api_id = p2_rows_by_api_request_id(p2_rows)
    unmatched_p2_ids = {id(row) for row in p2_rows}
    ordinal_cursor = 0
    steps: list[dict[str, Any]] = []

    for trajectory_step in trajectory_steps:
        response_id = trajectory_step.get("response_id")
        p2_row: dict[str, Any] | None = None
        alignment_status = "p2_missing"
        alignment_quality = "unmatched"
        if response_id not in {None, ""}:
            candidates = p2_by_api_id.get(str(response_id), [])
            if len(candidates) == 1:
                p2_row = candidates[0]
                alignment_status = "matched"
                alignment_quality = "response_id"
            elif len(candidates) > 1:
                alignment_status = "multiple"
                alignment_quality = "ambiguous_response_id"
        else:
            p2_row, ordinal_cursor = next_unmatched_p2_row(p2_rows, unmatched_p2_ids, ordinal_cursor)
            if p2_row is not None:
                alignment_status = "matched"
                alignment_quality = "ordinal_fallback"

        if p2_row is not None:
            unmatched_p2_ids.discard(id(p2_row))
        steps.append(
            make_trajectory_step(
                trajectory_step,
                p2_row,
                p2_index,
                resources,
                alignment_status=alignment_status,
                alignment_quality=alignment_quality,
            )
        )

    for row in p2_rows:
        if id(row) not in unmatched_p2_ids:
            continue
        steps.append(make_extra_p2_step(row, p2_index, resources))

    steps.sort(key=lambda step: sort_time(step))
    for index, step in enumerate(steps, start=1):
        step["step_index"] = index

    unassigned: list[dict[str, Any]] = []
    totals = build_totals(steps, unassigned)
    warnings = build_warnings(steps, unassigned)
    warnings.append("p1_events_missing_using_mini_swe_trajectory_fallback")
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_type": "agentmon.step_sequence",
        "sequence_source": "mini_swe_trajectory_p2_fallback",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "trajectory_path": str(trajectory_path),
        "event_count": len(events),
        "totals": totals,
        "p2_identity_audit": build_p2_identity_audit(steps, unassigned, p2_index.get("rows", [])),
        "steps": steps,
        "unassigned_items": unassigned,
        "resource_attribution": {
            "proc_sample_count": len(resources["proc_events"]),
            "ascend_sample_count": len(resources["ascend_events"]),
            "scheduler_sample_count": len(resources["scheduler_events"]),
            "ascend_target_devices": sorted(resources["ascend_target_devices"])
            if resources["ascend_target_devices"]
            else None,
            "ascend_target_device_source": resources["ascend_target_device_source"],
            "npu_observation_scope": resources["npu_observation_scope"],
            "npu_request_attribution": resources["npu_request_attribution"],
        },
        "warnings": warnings,
    }


def find_mini_swe_trajectory(run_dir: Path) -> Path | None:
    candidates = [
        run_dir / "raw" / "trajectory" / "mini.traj.json",
        run_dir / "raw" / "trajectory" / "last_mini_run.traj.json",
        run_dir / "mini.traj.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    trajectory_dir = run_dir / "raw" / "trajectory"
    if trajectory_dir.exists():
        for pattern in ("*.traj.json", "*.json"):
            matches = sorted(path for path in trajectory_dir.glob(pattern) if path.is_file())
            if matches:
                return matches[0]
    return None






def mini_swe_trajectory_steps(trajectory: Any) -> list[dict[str, Any]]:
    messages = trajectory_messages(trajectory)
    tool_messages = tool_messages_by_call_id(messages)
    steps: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        tool_calls = list_dicts(message.get("tool_calls"))
        if not tool_calls:
            continue
        extra = as_dict(message.get("extra"))
        response = as_dict(extra.get("response"))
        usage = as_dict(response.get("usage")) or as_dict(extra.get("usage")) or as_dict(message.get("usage"))
        actions = list_dicts(extra.get("actions"))
        tool_items = []
        for index, tool_call in enumerate(tool_calls):
            tool_call_id = first_value(tool_call.get("id"), as_dict(actions[index]).get("tool_call_id") if index < len(actions) else None)
            tool_message = pop_tool_message(tool_messages, tool_call_id)
            tool_items.append(trajectory_tool_item(tool_call, tool_message, actions, index))
        steps.append(
            {
                "step_id": f"trajectory-step-{len(steps) + 1}",
                "response_id": response.get("id"),
                "response_created": parse_timestamp_seconds(response.get("created")),
                "usage": usage,
                "tool_items": tool_items,
            }
        )
    return steps


def trajectory_messages(trajectory: Any) -> list[dict[str, Any]]:
    if isinstance(trajectory, list):
        return [item for item in trajectory if isinstance(item, dict)]
    if not isinstance(trajectory, dict):
        return []
    messages = trajectory.get("messages")
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    nested = trajectory.get("trajectory")
    if isinstance(nested, dict):
        nested_messages = nested.get("messages")
        if isinstance(nested_messages, list):
            return [item for item in nested_messages if isinstance(item, dict)]
    return []


def tool_messages_by_call_id(messages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for message in messages:
        if message.get("role") != "tool":
            continue
        tool_call_id = message.get("tool_call_id")
        if tool_call_id not in {None, ""}:
            by_id.setdefault(str(tool_call_id), []).append(message)
    return by_id


def pop_tool_message(tool_messages: dict[str, list[dict[str, Any]]], tool_call_id: Any) -> dict[str, Any] | None:
    if tool_call_id in {None, ""}:
        return None
    rows = tool_messages.get(str(tool_call_id))
    if not rows:
        return None
    return rows.pop(0)


def trajectory_tool_item(
    tool_call: dict[str, Any],
    tool_message: dict[str, Any] | None,
    actions: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    extra = as_dict(tool_message.get("extra")) if tool_message else {}
    raw_output = extra.get("raw_output")
    command = trajectory_command(tool_call, actions, index)
    timeout = trajectory_timeout_details(extra)
    return {
        "tool_call_id": tool_call.get("id"),
        "tool_name": first_value(tool_call.get("type"), as_dict(tool_call.get("function")).get("name"), "tool"),
        "command": command,
        "returncode": integer_or_none(extra.get("returncode")),
        "timestamp_s": parse_timestamp_seconds(extra.get("timestamp")),
        "raw_output": raw_output,
        "output_bytes": byte_count(raw_output),
        **timeout,
    }


def trajectory_timeout_details(extra: dict[str, Any]) -> dict[str, Any]:
    exception_info = str(extra.get("exception_info") or "")
    explicit_timeout = extra.get("timed_out") is True or extra.get("timeout") is True
    match = re.search(r"timed out after\s+([0-9]+(?:\.[0-9]+)?)\s+seconds?", exception_info, re.IGNORECASE)
    timed_out = True if explicit_timeout or match else False if extra.get("timed_out") is False else None
    timeout_limit = number_or_none(extra.get("timeout_limit_s"))
    if timeout_limit is None and match:
        timeout_limit = float(match.group(1))
    exception_type = first_value(extra.get("exception_type"), "TimeoutExpired" if timed_out else None)
    returncode = integer_or_none(extra.get("returncode"))
    return {
        "tool_status": (
            "timeout"
            if timed_out
            else "nonzero_return"
            if nonzero(returncode)
            else "success"
            if returncode == 0
            else "unknown"
        ),
        "timed_out": timed_out,
        "timeout_limit_s": timeout_limit,
        "termination_signal": integer_or_none(extra.get("termination_signal")),
        "exception_type": exception_type,
        "exception_info_present": bool(exception_info),
    }


def trajectory_command(tool_call: dict[str, Any], actions: list[dict[str, Any]], index: int) -> str | None:
    action = actions[index] if index < len(actions) else {}
    command = as_dict(action).get("command")
    if command not in {None, ""}:
        return str(command)
    function = as_dict(tool_call.get("function"))
    arguments = function.get("arguments")
    if isinstance(arguments, dict):
        value = arguments.get("command")
        return str(value) if value not in {None, ""} else None
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments if arguments else None
        value = as_dict(parsed).get("command")
        return str(value) if value not in {None, ""} else arguments
    return None


def p2_rows_by_api_request_id(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        api_request_id = row.get("api_request_id")
        if api_request_id not in {None, ""}:
            by_id.setdefault(str(api_request_id), []).append(row)
    return by_id


def p2_row_sort_key(row: dict[str, Any]) -> int:
    return first_int(row.get("received_ns"), row.get("queued_ns"), row.get("first_scheduled_ns"), row.get("finished_ns")) or 2**63 - 1


def next_unmatched_p2_row(
    rows: list[dict[str, Any]],
    unmatched_ids: set[int],
    cursor: int,
) -> tuple[dict[str, Any] | None, int]:
    while cursor < len(rows):
        row = rows[cursor]
        cursor += 1
        if id(row) in unmatched_ids:
            return row, cursor
    return None, cursor


def make_trajectory_step(
    trajectory_step: dict[str, Any],
    p2_row: dict[str, Any] | None,
    p2_index: dict[str, Any],
    resources: dict[str, Any],
    *,
    alignment_status: str,
    alignment_quality: str,
) -> dict[str, Any]:
    p2_snapshot = nearest_kv_snapshot(
        p2_index["snapshots"],
        first_int(p2_row.get("finished_ns"), p2_row.get("received_ns")) if p2_row else None,
    )
    model_item = make_trajectory_model_item(trajectory_step, p2_row, p2_snapshot, resources)
    if p2_row is not None and alignment_quality == "ordinal_fallback":
        model_item.update(
            {
                "p2_match_method": "ordinal_fallback",
                "p2_match_confidence": "low",
                "p2_missing_reason": None,
            }
        )
    elif alignment_status == "multiple":
        model_item.update(
            {
                "p2_match_status": "multiple",
                "p2_match_method": "trajectory_response_id",
                "p2_match_confidence": "none",
                "p2_missing_reason": "multiple_p2_requests_share_trajectory_response_id",
            }
        )
    tool_items = [
        make_trajectory_tool_sequence_item(tool, p2_row)
        for tool in trajectory_step.get("tool_items", [])
    ]
    step = {
        "step_index": None,
        "step_id": trajectory_step["step_id"],
        "step_type": "agent_step",
        "status": "trajectory_fallback",
        "response_id": trajectory_step.get("response_id"),
        "api_request_id": p2_row.get("api_request_id") if p2_row else None,
        "alignment_status": alignment_status,
        "alignment_quality": alignment_quality,
        "start_mono_ns": model_item.get("start_mono_ns"),
        "end_mono_ns": model_item.get("end_mono_ns"),
        "duration_s": model_item.get("duration_s"),
        "model_time_s": 0.0,
        "tool_time_s": 0.0,
        "unaccounted_time_s": None,
        "model_request_count": 0,
        "tool_call_count": 0,
        "items": [model_item, *tool_items],
    }
    finalize_step(step, resources)
    return step


def make_extra_p2_step(row: dict[str, Any], p2_index: dict[str, Any], resources: dict[str, Any]) -> dict[str, Any]:
    p2_snapshot = nearest_kv_snapshot(p2_index["snapshots"], first_int(row.get("finished_ns"), row.get("received_ns")))
    model_item = make_trajectory_model_item({"step_id": None, "usage": {}}, row, p2_snapshot, resources)
    model_item.update(
        {
            "p2_match_status": "p2_without_trajectory_step",
            "p2_match_method": "unmatched_extra",
            "p2_match_confidence": "none",
            "p2_missing_reason": "p2_request_has_no_trajectory_step",
        }
    )
    api_request_id = row.get("api_request_id")
    vllm_request_id = row.get("vllm_request_id")
    step = {
        "step_index": None,
        "step_id": f"model-extra-{first_value(api_request_id, vllm_request_id, 'unknown')}",
        "step_type": "model_extra",
        "status": "p2_without_trajectory_step",
        "response_id": None,
        "api_request_id": api_request_id,
        "alignment_status": "p2_without_trajectory_step",
        "alignment_quality": "unmatched",
        "start_mono_ns": model_item.get("start_mono_ns"),
        "end_mono_ns": model_item.get("end_mono_ns"),
        "duration_s": model_item.get("duration_s"),
        "model_time_s": 0.0,
        "tool_time_s": 0.0,
        "unaccounted_time_s": None,
        "model_request_count": 0,
        "tool_call_count": 0,
        "items": [model_item],
    }
    finalize_step(step, resources)
    return step


def make_trajectory_model_item(
    trajectory_step: dict[str, Any],
    p2_row: dict[str, Any] | None,
    p2_snapshot: dict[str, Any] | None,
    resources: dict[str, Any],
) -> dict[str, Any]:
    usage = as_dict(trajectory_step.get("usage"))
    p2 = p2_projection(p2_row or {}, p2_snapshot)
    window_start_ns = p2_row.get("received_ns") if p2_row else None
    window_end_ns = p2_row.get("finished_ns") if p2_row else None
    return {
        "item_index": None,
        "item_type": "model",
        "span_id": None,
        "step_id": trajectory_step.get("step_id"),
        "status": "trajectory_fallback",
        "start_mono_ns": window_start_ns,
        "end_mono_ns": window_end_ns,
        "duration_s": p2.get("e2e_s") if p2 else None,
        "start_offset_s": None,
        "end_event_type": None,
        "response_id": trajectory_step.get("response_id"),
        "api_request_id": p2_row.get("api_request_id") if p2_row else None,
        "p2_match_status": "matched" if p2_row else "unmatched",
        "p2_match_method": "trajectory_response_id" if p2_row else None,
        "p2_match_confidence": "exact" if p2_row else "none",
        "p2_missing_reason": None if p2_row else "trajectory_response_id_not_found_in_p2",
        "clock_domain_id": first_value(
            p2_row.get("received_clock_domain") if p2_row else None,
            p2_row.get("finished_clock_domain") if p2_row else None,
        ),
        "host_boot_id": first_value(
            p2_row.get("received_host_boot_id") if p2_row else None,
            p2_row.get("finished_host_boot_id") if p2_row else None,
        ),
        "source_host": first_value(
            p2_row.get("received_source_host") if p2_row else None,
            p2_row.get("finished_source_host") if p2_row else None,
        ),
        "prompt_tokens": first_int(p2.get("prompt_tokens") if p2 else None, usage.get("prompt_tokens")),
        "completion_tokens": first_int(p2.get("generation_tokens") if p2 else None, usage.get("completion_tokens")),
        "cached_tokens": first_int(p2.get("cached_prompt_tokens") if p2 else None, cached_tokens_from_usage(usage)),
        "p2": p2,
        "vllm_proc": proc_window_summary(resources["proc_events"], window_start_ns, window_end_ns, role="vllm"),
        "scheduler": scheduler_window_summary(resources["scheduler_events"], window_start_ns, window_end_ns),
        "npu": ascend_window_summary(
            resources["ascend_events"],
            window_start_ns,
            window_end_ns,
            target_devices=resources["ascend_target_devices"],
        ),
    }


def make_trajectory_tool_sequence_item(tool: dict[str, Any], p2_row: dict[str, Any] | None) -> dict[str, Any]:
    p2_finished_wall_time_s = ns_to_s(p2_row.get("finished_wall_time_ns")) if p2_row else None
    timestamp_s = number_or_none(tool.get("timestamp_s"))
    approximate_duration_s = (
        timestamp_s - p2_finished_wall_time_s
        if timestamp_s is not None and p2_finished_wall_time_s is not None
        else None
    )
    return {
        "item_index": None,
        "item_type": "tool",
        "span_id": tool.get("tool_call_id"),
        "step_id": None,
        "status": "trajectory_fallback",
        "start_mono_ns": None,
        "end_mono_ns": None,
        "duration_s": approximate_duration_s,
        "approx_tool_duration_s": approximate_duration_s,
        "start_offset_s": None,
        "end_event_type": None,
        "tool_call_id": tool.get("tool_call_id"),
        "tool_name": tool.get("tool_name"),
        "command": tool.get("command"),
        "returncode": tool.get("returncode"),
        "tool_status": tool.get("tool_status"),
        "timed_out": tool.get("timed_out"),
        "timeout_limit_s": tool.get("timeout_limit_s"),
        "termination_signal": tool.get("termination_signal"),
        "exception_type": tool.get("exception_type"),
        "output_bytes": tool.get("output_bytes"),
        "raw_output": tool.get("raw_output"),
        "proc": {"quality": "missing", "sample_count": 0},
    }


def make_step(span: dict[str, Any]) -> dict[str, Any]:
    start_payload = as_dict(span.get("start_payload"))
    end_payload = as_dict(span.get("end_payload"))
    attributes = as_dict(span.get("attributes"))
    return {
        "step_index": None,
        "step_id": span.get("span_id"),
        "step_type": "agent_step",
        "response_id": None,
        "api_request_id": None,
        "alignment_status": "native_p1",
        "alignment_quality": "native_p1",
        "status": span.get("status"),
        "start_mono_ns": span.get("start_mono_ns"),
        "end_mono_ns": span.get("end_mono_ns"),
        "duration_s": span.get("duration_s"),
        "model_time_s": 0.0,
        "tool_time_s": 0.0,
        "unaccounted_time_s": None,
        "model_request_count": 0,
        "tool_call_count": 0,
        "step_semantic_phase": end_payload.get("step_semantic_phase"),
        "step_action_count": integer_or_none(end_payload.get("step_action_count")),
        "path_expansion_count": integer_or_none(end_payload.get("path_expansion_count")),
        "path_expansion_observed": end_payload.get("path_expansion_observed"),
        "format_retry_observed": end_payload.get("format_retry_observed"),
        "messages_before": first_value(end_payload.get("messages_before"), start_payload.get("messages_before")),
        "messages_after": integer_or_none(end_payload.get("messages_after")),
        "messages_added": integer_or_none(end_payload.get("messages_added")),
        "agent_path_depth_before": first_value(
            start_payload.get("agent_path_depth_before"),
            attributes.get("agent_path_depth_before"),
        ),
        "consecutive_format_errors_before": first_value(
            end_payload.get("consecutive_format_errors_before"),
            start_payload.get("consecutive_format_errors_before"),
        ),
        "consecutive_format_errors_after": end_payload.get("consecutive_format_errors_after"),
        "items": [],
    }


def synthetic_step(step_id: str) -> dict[str, Any]:
    return {
        "step_index": None,
        "step_id": step_id,
        "step_type": "agent_step",
        "response_id": None,
        "api_request_id": None,
        "alignment_status": "native_p1_synthetic_step",
        "alignment_quality": "native_p1",
        "status": "synthetic",
        "start_mono_ns": None,
        "end_mono_ns": None,
        "duration_s": None,
        "model_time_s": 0.0,
        "tool_time_s": 0.0,
        "unaccounted_time_s": None,
        "model_request_count": 0,
        "tool_call_count": 0,
        "items": [],
    }


def finalize_step(step: dict[str, Any], resources: dict[str, Any]) -> None:
    items = step["items"]
    items.sort(key=lambda item: sort_time(item))
    if step.get("start_mono_ns") is None and items:
        step["start_mono_ns"] = min_int(item.get("start_mono_ns") for item in items)
    if step.get("end_mono_ns") is None and items:
        step["end_mono_ns"] = max_int(item.get("end_mono_ns") for item in items)
    if step.get("duration_s") is None:
        step["duration_s"] = ns_to_s(ns_delta(step.get("start_mono_ns"), step.get("end_mono_ns")))

    for index, item in enumerate(items, start=1):
        item["item_index"] = index
        item["start_offset_s"] = ns_to_s(ns_delta(step.get("start_mono_ns"), item.get("start_mono_ns")))

    model_items = [item for item in items if item.get("item_type") == "model"]
    tool_items = [item for item in items if item.get("item_type") == "tool"]
    model_time = sum_numeric(item.get("duration_s") for item in model_items)
    tool_time = sum_numeric(item.get("duration_s") for item in tool_items)
    step["model_time_s"] = model_time
    step["tool_time_s"] = tool_time
    step["model_request_count"] = sum(1 for item in items if item.get("item_type") == "model")
    step["tool_call_count"] = sum(1 for item in items if item.get("item_type") == "tool")
    step.update(step_kv_summary(items))
    step["agent_proc"] = proc_window_summary(
        resources["proc_events"],
        step.get("start_mono_ns"),
        step.get("end_mono_ns"),
        role="agent",
    )
    step["vllm_proc"] = proc_window_summary(
        resources["proc_events"],
        step.get("start_mono_ns"),
        step.get("end_mono_ns"),
        role="vllm",
    )
    step["npu"] = ascend_window_summary(
        resources["ascend_events"],
        step.get("start_mono_ns"),
        step.get("end_mono_ns"),
        target_devices=resources["ascend_target_devices"],
    )
    step["scheduler"] = scheduler_window_summary(
        resources["scheduler_events"],
        step.get("start_mono_ns"),
        step.get("end_mono_ns"),
    )
    duration = number_or_none(step.get("duration_s"))
    child_durations_complete = all(item.get("duration_s") is not None for item in items)
    step["unaccounted_time_s"] = (
        duration - float(model_time or 0) - float(tool_time or 0)
        if duration is not None and child_durations_complete
        else None
    )


def make_item(span: dict[str, Any], p2_index: dict[str, Any], resources: dict[str, Any]) -> dict[str, Any]:
    if span.get("span_type") == "model_request":
        return make_model_item(span, p2_index, resources)
    return make_tool_item(span, resources)


def make_model_item(span: dict[str, Any], p2_index: dict[str, Any], resources: dict[str, Any]) -> dict[str, Any]:
    start_payload = span["start_payload"]
    end_payload = span["end_payload"]
    attributes = span["attributes"]
    usage = as_dict(end_payload.get("usage"))
    p2_row, match = find_p2_match(span, p2_index)
    p2_snapshot = nearest_kv_snapshot(
        p2_index["snapshots"],
        first_int(p2_row.get("finished_ns"), span.get("end_mono_ns"), span.get("start_mono_ns")),
    )
    window_start_ns = first_int(p2_row.get("received_ns"), span.get("start_mono_ns"))
    window_end_ns = first_int(p2_row.get("finished_ns"), span.get("end_mono_ns"))
    return {
        **base_item(span, "model"),
        "model_name": first_value(start_payload.get("model_name"), attributes.get("model_name"), p2_row.get("model_name")),
        "headers_injected": first_value(start_payload.get("headers_injected"), attributes.get("headers_injected")),
        "message_count": first_value(start_payload.get("message_count"), attributes.get("message_count")),
        "input_chars": first_value(start_payload.get("input_chars"), attributes.get("input_chars")),
        "context_message_count": first_value(
            start_payload.get("context_message_count"), attributes.get("context_message_count")
        ),
        "context_message_delta": first_value(
            start_payload.get("context_message_delta"), attributes.get("context_message_delta")
        ),
        "context_input_chars": first_value(
            start_payload.get("context_input_chars"), attributes.get("context_input_chars")
        ),
        "context_input_chars_delta": first_value(
            start_payload.get("context_input_chars_delta"), attributes.get("context_input_chars_delta")
        ),
        "context_token_estimate": first_value(
            start_payload.get("context_token_estimate"), attributes.get("context_token_estimate")
        ),
        "context_token_estimate_delta": first_value(
            start_payload.get("context_token_estimate_delta"), attributes.get("context_token_estimate_delta")
        ),
        "context_token_estimate_quality": first_value(
            start_payload.get("context_token_estimate_quality"), attributes.get("context_token_estimate_quality")
        ),
        "context_prompt_tokens": integer_or_none(end_payload.get("context_prompt_tokens")),
        "context_prompt_tokens_delta": integer_or_none(end_payload.get("context_prompt_tokens_delta")),
        "context_change_type": first_value(
            start_payload.get("context_change_type"), attributes.get("context_change_type")
        ),
        "context_truncation_detected": first_value(
            start_payload.get("context_truncation_detected"), attributes.get("context_truncation_detected")
        ),
        "context_rewrite_detected": first_value(
            start_payload.get("context_rewrite_detected"), attributes.get("context_rewrite_detected")
        ),
        "context_summarization_detected": first_value(
            start_payload.get("context_summarization_detected"), attributes.get("context_summarization_detected")
        ),
        "context_removed_message_count": first_value(
            start_payload.get("context_removed_message_count"), attributes.get("context_removed_message_count")
        ),
        "context_compression_status": first_value(
            start_payload.get("context_compression_status"),
            attributes.get("context_compression_status"),
        ),
        "context_compression_trigger_strategy": first_value(
            start_payload.get("context_compression_trigger_strategy"),
            attributes.get("context_compression_trigger_strategy"),
        ),
        "context_compression_trigger_reason": first_value(
            start_payload.get("context_compression_trigger_reason"),
            attributes.get("context_compression_trigger_reason"),
        ),
        "context_compression_strategy": first_value(
            start_payload.get("context_compression_strategy"),
            attributes.get("context_compression_strategy"),
        ),
        "context_compression_applied": first_value(
            start_payload.get("context_compression_applied"),
            attributes.get("context_compression_applied"),
        ),
        "context_compression_original_tokens": first_value(
            start_payload.get("context_compression_original_tokens"),
            attributes.get("context_compression_original_tokens"),
        ),
        "context_compression_compressed_tokens": first_value(
            start_payload.get("context_compression_compressed_tokens"),
            attributes.get("context_compression_compressed_tokens"),
        ),
        "context_compression_removed_tokens": first_value(
            start_payload.get("context_compression_removed_tokens"),
            attributes.get("context_compression_removed_tokens"),
        ),
        "context_compression_target_tokens": first_value(
            start_payload.get("context_compression_target_tokens"),
            attributes.get("context_compression_target_tokens"),
        ),
        "context_compression_target_achieved": first_value(
            start_payload.get("context_compression_target_achieved"),
            attributes.get("context_compression_target_achieved"),
        ),
        "context_compression_measurement_quality": first_value(
            start_payload.get("context_compression_measurement_quality"),
            attributes.get("context_compression_measurement_quality"),
        ),
        "context_compression_started_mono_ns": first_value(
            start_payload.get("context_compression_started_mono_ns"),
            attributes.get("context_compression_started_mono_ns"),
        ),
        "context_compression_ended_mono_ns": first_value(
            start_payload.get("context_compression_ended_mono_ns"),
            attributes.get("context_compression_ended_mono_ns"),
        ),
        "context_compression_duration_s": first_value(
            start_payload.get("context_compression_duration_s"),
            attributes.get("context_compression_duration_s"),
        ),
        "context_summary_status": first_value(
            start_payload.get("context_summary_status"),
            attributes.get("context_summary_status"),
        ),
        "context_summary_request_id": first_value(
            start_payload.get("context_summary_request_id"),
            attributes.get("context_summary_request_id"),
        ),
        "context_summary_model": first_value(
            start_payload.get("context_summary_model"),
            attributes.get("context_summary_model"),
        ),
        "context_summary_input_tokens": first_value(
            start_payload.get("context_summary_input_tokens"),
            attributes.get("context_summary_input_tokens"),
        ),
        "context_summary_output_tokens": first_value(
            start_payload.get("context_summary_output_tokens"),
            attributes.get("context_summary_output_tokens"),
        ),
        "context_summary_cached_tokens": first_value(
            start_payload.get("context_summary_cached_tokens"),
            attributes.get("context_summary_cached_tokens"),
        ),
        "context_summary_uncached_prompt_tokens": first_value(
            start_payload.get("context_summary_uncached_prompt_tokens"),
            attributes.get("context_summary_uncached_prompt_tokens"),
        ),
        "context_summary_duration_s": first_value(
            start_payload.get("context_summary_duration_s"),
            attributes.get("context_summary_duration_s"),
        ),
        "context_summary_token_quality": first_value(
            start_payload.get("context_summary_token_quality"),
            attributes.get("context_summary_token_quality"),
        ),
        "tool_result_message_count": first_value(
            start_payload.get("tool_result_message_count"), attributes.get("tool_result_message_count")
        ),
        "tool_result_chars": first_value(
            start_payload.get("tool_result_chars"), attributes.get("tool_result_chars")
        ),
        "tool_result_tokens_estimated": first_value(
            start_payload.get("tool_result_tokens_estimated"), attributes.get("tool_result_tokens_estimated")
        ),
        "tool_result_token_quality": first_value(
            start_payload.get("tool_result_token_quality"), attributes.get("tool_result_token_quality")
        ),
        "agent_semantic_phase": first_value(
            start_payload.get("agent_semantic_phase"), attributes.get("agent_semantic_phase")
        ),
        "model_request_sequence": first_value(
            start_payload.get("model_request_sequence"), attributes.get("model_request_sequence")
        ),
        "model_call_index_in_step": first_value(
            start_payload.get("model_call_index_in_step"), attributes.get("model_call_index_in_step")
        ),
        "transport_attempt_count": integer_or_none(end_payload.get("transport_attempt_count")),
        "retry_count": integer_or_none(end_payload.get("retry_count")),
        "retry_observation_quality": end_payload.get("retry_observation_quality"),
        "response_id": first_value(end_payload.get("response_id"), end_payload.get("api_request_id")),
        "api_request_id": first_value(end_payload.get("api_request_id"), end_payload.get("response_id")),
        **match,
        "prompt_tokens": integer_or_none(usage.get("prompt_tokens")),
        "completion_tokens": integer_or_none(usage.get("completion_tokens")),
        "cached_tokens": cached_tokens_from_usage(usage),
        "cost": number_or_none(usage.get("cost")),
        "p2": p2_projection(p2_row, p2_snapshot, client_span=span),
        "vllm_proc": proc_window_summary(resources["proc_events"], window_start_ns, window_end_ns, role="vllm"),
        "scheduler": scheduler_window_summary(resources["scheduler_events"], window_start_ns, window_end_ns),
        "npu": ascend_window_summary(
            resources["ascend_events"],
            window_start_ns,
            window_end_ns,
            target_devices=resources["ascend_target_devices"],
        ),
    }


def make_tool_item(span: dict[str, Any], resources: dict[str, Any]) -> dict[str, Any]:
    start_payload = span["start_payload"]
    end_payload = span["end_payload"]
    attributes = span["attributes"]
    pid = integer_or_none(end_payload.get("pid"))
    pgid = integer_or_none(end_payload.get("pgid"))
    return {
        **base_item(span, "tool"),
        "tool_name": first_value(end_payload.get("tool_name"), start_payload.get("tool_name"), attributes.get("tool_name")),
        "executable": first_value(end_payload.get("executable"), start_payload.get("executable"), attributes.get("executable")),
        "action_type": first_value(end_payload.get("action_type"), start_payload.get("action_type"), attributes.get("action_type")),
        "command_hash": first_value(end_payload.get("command_hash"), start_payload.get("command_hash"), attributes.get("command_hash")),
        "command_argc": first_value(end_payload.get("command_argc"), start_payload.get("command_argc"), attributes.get("command_argc")),
        "cwd": first_value(end_payload.get("cwd"), start_payload.get("cwd"), attributes.get("cwd")),
        "returncode": integer_or_none(end_payload.get("returncode")),
        "tool_status": end_payload.get("tool_status"),
        "timed_out": end_payload.get("timed_out"),
        "timeout_limit_s": number_or_none(end_payload.get("timeout_limit_s")),
        "termination_signal": integer_or_none(end_payload.get("termination_signal")),
        "exception_type": end_payload.get("exception_type"),
        "pid": pid,
        "pgid": pgid,
        "stdout_bytes": integer_or_none(end_payload.get("stdout_bytes")),
        "stderr_bytes": integer_or_none(end_payload.get("stderr_bytes")),
        "output_bytes": integer_or_none(end_payload.get("output_bytes")),
        "attribution_quality": end_payload.get("attribution_quality"),
        "proc": tool_proc_summary(span, end_payload, resources["proc_events"], pid=pid, pgid=pgid),
    }


def tool_proc_summary(
    span: dict[str, Any],
    end_payload: dict[str, Any],
    proc_events: list[dict[str, Any]],
    *,
    pid: int | None,
    pgid: int | None,
) -> dict[str, Any]:
    summary = proc_window_summary(
        proc_events,
        span.get("start_mono_ns"),
        span.get("end_mono_ns"),
        pid=pid,
        pgid=pgid,
    )
    direct_cpu = number_or_none(end_payload.get("cpu_time_s"))
    if direct_cpu is not None:
        sampled_cpu = summary.get("cpu_time_s")
        wall_ratio = ratio_or_none(direct_cpu, span.get("duration_s"))
        summary.update(
            {
                "sampled_cpu_time_s": sampled_cpu,
                "cpu_time_s": direct_cpu,
                "cpu_user_time_s": number_or_none(end_payload.get("cpu_user_time_s")),
                "cpu_system_time_s": number_or_none(end_payload.get("cpu_system_time_s")),
                "cpu_time_quality": end_payload.get("cpu_time_quality") or "rusage_children_delta",
                "cpu_wall_ratio": wall_ratio,
                "avg_cpu_percent": wall_ratio * 100 if wall_ratio is not None else None,
                "minor_faults_delta": integer_or_none(end_payload.get("minor_faults_delta")),
                "major_faults_delta": integer_or_none(end_payload.get("major_faults_delta")),
                "input_blocks_delta": integer_or_none(end_payload.get("input_blocks_delta")),
                "output_blocks_delta": integer_or_none(end_payload.get("output_blocks_delta")),
                "voluntary_context_switches_delta": integer_or_none(
                    end_payload.get("voluntary_context_switches_delta")
                ),
                "involuntary_context_switches_delta": integer_or_none(
                    end_payload.get("involuntary_context_switches_delta")
                ),
            }
        )
    observed_rss = integer_or_none(end_payload.get("tool_peak_rss_bytes"))
    observed_pss = integer_or_none(end_payload.get("tool_peak_pss_bytes"))
    summary.update(
        {
            "peak_rss_bytes": max_present([summary.get("peak_rss_bytes"), observed_rss]),
            "peak_pss_bytes": max_present([summary.get("peak_pss_bytes"), observed_pss]),
            "read_bytes_delta": first_value(
                integer_or_none(end_payload.get("tool_read_bytes_delta")),
                summary.get("read_bytes_delta"),
            ),
            "write_bytes_delta": first_value(
                integer_or_none(end_payload.get("tool_write_bytes_delta")),
                summary.get("write_bytes_delta"),
            ),
            "tool_resource_sample_count": integer_or_none(end_payload.get("tool_resource_sample_count")),
            "tool_resource_sample_interval_s": number_or_none(
                end_payload.get("tool_resource_sample_interval_s")
            ),
            "tool_resource_quality": end_payload.get("tool_resource_quality"),
            "tool_pss_quality": end_payload.get("tool_pss_quality"),
        }
    )
    return summary


def base_item(span: dict[str, Any], item_type: str) -> dict[str, Any]:
    start_payload = as_dict(span.get("start_payload"))
    end_payload = as_dict(span.get("end_payload"))
    return {
        "item_index": None,
        "item_type": item_type,
        "span_id": span.get("span_id"),
        "step_id": step_id_for_child(span),
        "status": span.get("status"),
        "start_mono_ns": span.get("start_mono_ns"),
        "end_mono_ns": span.get("end_mono_ns"),
        "duration_s": span.get("duration_s"),
        "start_offset_s": None,
        "clock_domain_id": first_value(start_payload.get("clock_domain"), end_payload.get("clock_domain")),
        "host_boot_id": first_value(start_payload.get("host_boot_id"), end_payload.get("host_boot_id")),
        "source_host": first_value(start_payload.get("source_host"), end_payload.get("source_host")),
        "end_event_type": span.get("end_event_type"),
    }


def step_id_for_child(span: dict[str, Any]) -> str | None:
    for value in (span.get("step_id"), span.get("parent_span_id")):
        if value not in {None, ""}:
            return str(value)
    return None






























































































































































if __name__ == "__main__":
    raise SystemExit(main())
