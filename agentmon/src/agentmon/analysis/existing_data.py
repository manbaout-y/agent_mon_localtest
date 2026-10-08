#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable, Sequence

from agentmon.profile_data import profile_quality_gate


SCHEMA_VERSION = "1.0"
MISSING = {"", "na", "n/a", "none", "null", "nan", "inf", "-inf"}
P2_REQUIRED_LIFECYCLE_EVENTS = {
    "vllm.request.received",
    "vllm.request.queued",
    "vllm.request.first_scheduled",
    "vllm.request.first_token",
    "vllm.request.finished",
}
DEFAULT_MIN_P2_MATCH_RATE = profile_quality_gate("min_p2_match_rate", profile="legacy_compat")
DEFAULT_MIN_P2_LIFECYCLE_RATE = profile_quality_gate("min_p2_lifecycle_rate", profile="legacy_compat")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        specs = [parse_batch_spec(value) for value in args.batch]
        analysis = analyze_existing_batches(
            specs,
            baseline_label=args.baseline,
            min_p2_match_rate=args.min_p2_match_rate,
            min_p2_lifecycle_rate=args.min_p2_lifecycle_rate,
        )
        write_analysis(analysis, Path(args.output_dir).expanduser().resolve())
    except Exception as exc:
        print(f"agentmon existing-data analysis error: {exc}", file=sys.stderr)
        return 2
    status = analysis["validation_checks"]["publishability_status"]
    print(
        f"AgentMon existing-data analysis: {status}; "
        f"batches={len(analysis['batches'])} requests={len(analysis['request_metrics'])} "
        f"output={Path(args.output_dir).expanduser().resolve()}"
    )
    return 1 if args.strict and status != "PUBLISHABLE" else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Derive auditable request, timeline, workload, device, and host analysis from existing AgentMon batches",
    )
    parser.add_argument(
        "--batch",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="existing AgentMon batch; repeat for each concurrency",
    )
    parser.add_argument("--baseline", help="baseline batch label; defaults to the first --batch label")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--min-p2-match-rate", type=rate_argument, default=DEFAULT_MIN_P2_MATCH_RATE)
    parser.add_argument("--min-p2-lifecycle-rate", type=rate_argument, default=DEFAULT_MIN_P2_LIFECYCLE_RATE)
    parser.add_argument("--strict", action="store_true")
    return parser


def parse_batch_spec(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError(f"batch must use LABEL=PATH: {value}")
    label, raw_path = value.split("=", 1)
    label = label.strip()
    if not label or not raw_path.strip():
        raise ValueError(f"batch must use non-empty LABEL=PATH: {value}")
    return label, Path(raw_path).expanduser().resolve()


def rate_argument(value: str) -> float:
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise argparse.ArgumentTypeError("rate must be between 0 and 1")
    return result


def analyze_existing_batches(
    specs: list[tuple[str, Path]],
    *,
    baseline_label: str | None = None,
    min_p2_match_rate: float | None = DEFAULT_MIN_P2_MATCH_RATE,
    min_p2_lifecycle_rate: float | None = DEFAULT_MIN_P2_LIFECYCLE_RATE,
) -> dict[str, Any]:
    if not specs:
        raise ValueError("at least one batch is required")
    labels = [label for label, _ in specs]
    if len(set(labels)) != len(labels):
        raise ValueError("batch labels must be unique")
    baseline = baseline_label or labels[0]
    if baseline not in labels:
        raise ValueError(f"baseline label not found: {baseline}")

    batches = [load_batch(label, path) for label, path in specs]
    request_metrics: list[dict[str, Any]] = []
    correlation_rows: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    timeline_violations: list[dict[str, Any]] = []
    timeline_checks: list[dict[str, Any]] = []
    device_rows: list[dict[str, Any]] = []
    host_rows: list[dict[str, Any]] = []
    run_summaries: list[dict[str, Any]] = []
    inventories: list[dict[str, Any]] = []

    for batch in batches:
        run_events = load_batch_run_events(batch)
        requests, audit = derive_request_metrics(batch, run_events)
        request_metrics.extend(requests)
        correlation_rows.extend(audit)
        batch_timeline, violations, checks = analyze_timeline(batch)
        timeline_rows.extend(batch_timeline)
        timeline_violations.extend(violations)
        timeline_checks.extend(checks)
        device_rows.extend(analyze_device_imbalance(batch))
        host_rows.extend(analyze_host_metrics(batch, run_events))
        summaries = summarize_runs(batch, requests)
        run_summaries.extend(summaries)
        inventories.append(build_inventory(batch, run_events, requests))

    attach_host_pressure(run_summaries, host_rows)
    paired_decomposition = decompose_paired_runs(run_summaries, baseline)
    paired_model_calls = pair_model_calls(request_metrics, baseline)
    inflight_analysis = analyze_inflight_thresholds(request_metrics)
    tail_rows = classify_tails(run_summaries, baseline)
    coverage_rows = build_coverage_rows(batches, request_metrics, host_rows, device_rows)
    correlation_summary = summarize_correlation(correlation_rows)
    request_summary = summarize_request_metrics(request_metrics)
    device_summary = summarize_device_imbalance(device_rows)
    device_long_rows = expand_device_metrics(device_rows)
    host_summary = summarize_host_metrics(host_rows)
    decomposition_summary = summarize_decomposition(paired_decomposition)
    tail_summary = summarize_tails(tail_rows)
    validation = build_validation_checks(
        batches,
        correlation_summary,
        timeline_checks,
        min_p2_match_rate=min_p2_match_rate,
        min_p2_lifecycle_rate=min_p2_lifecycle_rate,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_type": "agentmon.existing_data_analysis",
        "analysis_config": {
            "min_p2_match_rate": min_p2_match_rate,
            "min_p2_lifecycle_rate": min_p2_lifecycle_rate,
        },
        "baseline_label": baseline,
        "batches": batches,
        "inventory": inventories,
        "coverage": coverage_rows,
        "request_metrics": request_metrics,
        "request_metrics_summary": request_summary,
        "p2_correlation_audit": correlation_rows,
        "p2_correlation_summary": correlation_summary,
        "request_timeline": timeline_rows,
        "timeline_violations": timeline_violations,
        "timeline_checks": timeline_checks,
        "run_summaries": run_summaries,
        "paired_task_decomposition": paired_decomposition,
        "paired_model_calls": paired_model_calls,
        "inflight_observations": inflight_analysis["observations"],
        "inflight_thresholds": inflight_analysis["thresholds"],
        "inflight_summary": inflight_analysis["summary"],
        "batch_decomposition_summary": decomposition_summary,
        "tail_classification": tail_rows,
        "tail_summary": tail_summary,
        "device_imbalance": device_rows,
        "device_metrics_long": device_long_rows,
        "device_imbalance_summary": device_summary,
        "host_metrics": host_rows,
        "host_metrics_summary": host_summary,
        "validation_checks": validation,
        "quality_bundle": build_quality_bundle(validation, coverage_rows, correlation_summary),
    }


def load_batch(label: str, path: Path) -> dict[str, Any]:
    manifest_path = path / "batch-manifest.json"
    results_path = path / "batch-results.tsv"
    if not path.is_dir():
        raise FileNotFoundError(path)
    manifest = read_json(manifest_path)
    summary = read_json(path / "batch-summary.json")
    results = read_delimited(results_path, delimiter="\t") if results_path.is_file() else []
    step_paths = locate_step_csvs(path, results)
    step_rows: list[dict[str, str]] = []
    for step_path in step_paths:
        step_rows.extend(read_delimited(step_path))
    run_results = {str(row.get("run_id")): row for row in results if has_value(row.get("run_id"))}
    for row in step_rows:
        run_result = run_results.get(str(row.get("run_id") or ""), {})
        row["batch_label"] = label
        row["task_id"] = str(run_result.get("task_id") or row.get("task_id") or row.get("run_id") or "")
        row["configured_concurrency"] = str(
            manifest.get("concurrency") or summary.get("configured_concurrency") or ""
        )
    return {
        "label": label,
        "path": path,
        "manifest_path": manifest_path,
        "results_path": results_path,
        "manifest": manifest,
        "summary": summary,
        "results": results,
        "run_results": run_results,
        "step_paths": step_paths,
        "step_rows": step_rows,
        "concurrency": integer_or_none(manifest.get("concurrency") or summary.get("configured_concurrency")),
    }


def locate_step_csvs(batch_path: Path, results: list[dict[str, str]]) -> list[Path]:
    preferred = [
        batch_path / "step-sequence" / "all-steps.csv",
        batch_path / "step-sequence" / "all-tasks-step-sequence.csv",
        batch_path / "step-sequence" / "all-tasks-step-sequence-fixed.csv",
        batch_path / "all-steps.csv",
    ]
    for path in preferred:
        if path.is_file():
            return [path]
    paths: list[Path] = []
    seen: set[Path] = set()
    for row in results:
        raw = row.get("step_sequence_csv")
        if not has_value(raw):
            continue
        path = Path(str(raw)).expanduser()
        if path.is_file() and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def load_batch_run_events(batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for run_id, row in batch["run_results"].items():
        run_dir = Path(str(row.get("run_dir") or "")).expanduser()
        events_path = run_dir / "events.jsonl"
        parsed = read_jsonl(events_path)
        result[run_id] = {
            "run_dir": run_dir,
            "events_path": events_path,
            "events": parsed["events"],
            "invalid_lines": parsed["invalid_lines"],
            "p2_index": build_p2_event_index(parsed["events"]),
        }
    return result


def build_p2_event_index(events: list[dict[str, Any]]) -> dict[str, Any]:
    request_events = [
        event
        for event in events
        if event.get("source") == "vllm_p2"
        and (
            str(event.get("event_type") or "").startswith("vllm.request.")
            or event.get("event_type") == "vllm.kv.block_event"
        )
    ]
    api_to_engines: dict[str, set[str]] = defaultdict(set)
    for event in request_events:
        payload = as_dict(event.get("payload"))
        api_id = text_or_none(payload.get("api_request_id"))
        engine_id = first_text(payload.get("vllm_request_id"), payload.get("request_id"))
        if api_id and engine_id:
            api_to_engines[api_id].add(engine_id)

    candidates: dict[str, set[str]] = defaultdict(set)
    contexts: dict[str, dict[str, Any]] = {}
    for event in request_events:
        payload = as_dict(event.get("payload"))
        api_id = text_or_none(payload.get("api_request_id"))
        engine_id = first_text(payload.get("vllm_request_id"), payload.get("request_id"))
        if engine_id is None and api_id and len(api_to_engines.get(api_id, set())) == 1:
            engine_id = next(iter(api_to_engines[api_id]))
        canonical = first_text(engine_id, payload.get("agentmon_model_request_id"), api_id)
        keys = {
            value
            for value in (
                api_id,
                engine_id,
                text_or_none(payload.get("agentmon_model_request_id")),
                text_or_none(payload.get("agent_request_id")),
            )
            if value is not None
        }
        if api_id:
            for related in api_to_engines.get(api_id, set()):
                candidates[api_id].add(related)
        if canonical:
            for key in keys:
                candidates[key].add(canonical)
            context = contexts.setdefault(
                canonical,
                {
                    "canonical_request_id": canonical,
                    "event_types": [],
                    "event_type_counts": {},
                    "api_request_ids": [],
                    "engine_request_ids": [],
                    "payload": {},
                },
            )
            merge_p2_context(context, event)
    return {"candidates": candidates, "contexts": contexts, "event_count": len(request_events)}


def merge_p2_context(context: dict[str, Any], event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or "")
    payload = as_dict(event.get("payload"))
    if event_type not in context["event_types"]:
        context["event_types"].append(event_type)
    counts = context.setdefault("event_type_counts", {})
    counts[event_type] = int(counts.get(event_type) or 0) + 1
    api_id = text_or_none(payload.get("api_request_id"))
    engine_id = first_text(payload.get("vllm_request_id"), payload.get("request_id"))
    if api_id and api_id not in context.setdefault("api_request_ids", []):
        context["api_request_ids"].append(api_id)
    if engine_id and engine_id not in context.setdefault("engine_request_ids", []):
        context["engine_request_ids"].append(engine_id)
    for key, value in payload.items():
        if value is not None:
            context["payload"][key] = value
    timestamp_fields = {
        "vllm.request.received": "received_ns",
        "vllm.request.queued": "queued_ns",
        "vllm.request.first_scheduled": "first_scheduled_ns",
        "vllm.request.prefill_completed": "prefill_completed_ns",
        "vllm.request.first_token": "first_token_ns",
        "vllm.request.engine_finished": "engine_finished_ns",
        "vllm.request.finished": "finished_ns",
    }
    field = timestamp_fields.get(event_type)
    if field:
        context[field] = integer_or_none(payload.get(field)) or integer_or_none(event.get("monotonic_time_ns"))
        context[f"{field}_clock_domain"] = text_or_none(payload.get("clock_domain"))
        context[f"{field}_host_boot_id"] = text_or_none(payload.get("host_boot_id"))
        context[f"{field}_source_host"] = text_or_none(payload.get("source_host"))


def derive_request_metrics(
    batch: dict[str, Any],
    run_events: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    referenced_p2_contexts: dict[str, set[str]] = defaultdict(set)
    for row in batch["step_rows"]:
        if row.get("item_type") != "model":
            continue
        run_id = str(row.get("run_id") or "")
        p2_index = as_dict(as_dict(run_events.get(run_id)).get("p2_index"))
        candidate_ids = p2_candidates_for_row(row, p2_index)
        p2_evidence_present = bool(p2_index.get("event_count"))
        candidate_count: int | None = len(candidate_ids) if p2_evidence_present else inferred_candidate_count(row)
        context = None
        if len(candidate_ids) == 1:
            context = as_dict(as_dict(p2_index.get("contexts")).get(next(iter(candidate_ids))))
        referenced_p2_contexts[run_id].update(candidate_ids)
        match_status = normalized_match_status(row, candidate_count, evidence_present=p2_evidence_present)
        observed_lifecycle, missing_lifecycle = lifecycle_status_from_context(context)
        reported_lifecycle = text_or_none(row.get("p2_lifecycle_status"))
        lifecycle_status = observed_lifecycle or reported_lifecycle
        audit_row = {
            "batch_label": batch["label"],
            "configured_concurrency": batch["concurrency"],
            "task_id": row.get("task_id"),
            "run_id": run_id,
            "step_id": row.get("step_id"),
            "step_index": integer_or_none(row.get("step_index")),
            "item_index": integer_or_none(row.get("item_index")),
            "model_call_id": first_text(row.get("span_id"), row.get("p2_agentmon_model_request_id")),
            "api_request_id": first_text(row.get("api_request_id"), row.get("response_id")),
            "vllm_request_id": text_or_none(row.get("vllm_request_id")),
            "p2_candidate_count": candidate_count,
            "p2_candidate_ids_json": json_compact(sorted(candidate_ids)),
            "p2_candidate_evidence_source": "raw_p2_events" if p2_evidence_present else "step_csv_projection",
            "p2_match_status": match_status,
            "p2_match_method": row.get("p2_match_method"),
            "p2_match_confidence": row.get("p2_match_confidence"),
            "p2_missing_reason": normalized_missing_reason(row, match_status),
            "lifecycle_status": lifecycle_status,
            "lifecycle_status_source": "raw_p2_events" if observed_lifecycle else "step_csv",
            "lifecycle_reported_status": reported_lifecycle,
            "lifecycle_missing_events": (
                ";".join(missing_lifecycle)
                if observed_lifecycle
                else row.get("p2_lifecycle_missing_events")
            ),
            "lifecycle_status_mismatch": bool(
                observed_lifecycle and reported_lifecycle and observed_lifecycle != reported_lifecycle
            ),
        }
        audit.append(audit_row)
        metric = derive_one_request_metric(batch, row, audit_row, context)
        metrics.append(metric)

    for run_id, data in run_events.items():
        p2_index = as_dict(data.get("p2_index"))
        contexts = as_dict(p2_index.get("contexts"))
        for canonical_id, raw_context in contexts.items():
            if canonical_id in referenced_p2_contexts.get(run_id, set()):
                continue
            context = as_dict(raw_context)
            lifecycle_status, missing_lifecycle = lifecycle_status_from_context(context)
            payload = as_dict(context.get("payload"))
            audit.append(
                {
                    "batch_label": batch["label"],
                    "configured_concurrency": batch["concurrency"],
                    "task_id": as_dict(batch["run_results"].get(run_id)).get("task_id"),
                    "run_id": run_id,
                    "step_id": None,
                    "step_index": None,
                    "item_index": None,
                    "model_call_id": None,
                    "api_request_id": first_text(
                        *(context.get("api_request_ids") or []), payload.get("api_request_id")
                    ),
                    "vllm_request_id": first_text(
                        *(context.get("engine_request_ids") or []), canonical_id
                    ),
                    "p2_candidate_count": 1,
                    "p2_candidate_ids_json": json_compact([canonical_id]),
                    "p2_candidate_evidence_source": "raw_p2_events",
                    "p2_match_status": "unmatched_p2",
                    "p2_match_method": "raw_p2_context_inventory",
                    "p2_match_confidence": "high",
                    "p2_missing_reason": "p2_request_without_step_csv_model",
                    "lifecycle_status": lifecycle_status,
                    "lifecycle_status_source": "raw_p2_events",
                    "lifecycle_reported_status": None,
                    "lifecycle_missing_events": ";".join(missing_lifecycle),
                    "lifecycle_status_mismatch": False,
                }
            )
    return metrics, audit


def p2_candidates_for_row(row: dict[str, str], index: dict[str, Any]) -> set[str]:
    candidates: set[str] = set()
    mapping = as_set_dict(index.get("candidates"))
    for value in (
        row.get("api_request_id"),
        row.get("response_id"),
        row.get("vllm_request_id"),
        row.get("p2_agentmon_model_request_id"),
        row.get("span_id"),
    ):
        key = text_or_none(value)
        if key:
            candidates.update(mapping.get(key, set()))
    return candidates


def inferred_candidate_count(row: dict[str, str]) -> int | None:
    status = str(row.get("p2_match_status") or "")
    if status == "matched":
        return 1
    if status in {"unmatched", "p2_without_trajectory_step"}:
        return 0
    return None


def normalized_match_status(
    row: dict[str, str],
    candidate_count: int | None,
    *,
    evidence_present: bool = False,
) -> str:
    if row.get("step_type") == "model_extra" or row.get("p2_match_status") == "p2_without_trajectory_step":
        return "unmatched_p2"
    if candidate_count is not None and candidate_count > 1:
        return "multiple_candidates"
    if evidence_present and candidate_count == 0:
        return "unmatched_p1"
    if row.get("alignment_quality") == "ordinal_fallback":
        return "ordinal_fallback"
    if row.get("p2_match_status") == "multiple":
        return "multiple_candidates"
    if row.get("p2_match_status") == "matched" or candidate_count == 1:
        return "exact_match"
    return "unmatched_p1"


def lifecycle_status_from_context(context: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    if not context:
        return None, []
    observed = set(context.get("event_types") or [])
    missing = sorted(P2_REQUIRED_LIFECYCLE_EVENTS - observed)
    return ("complete" if not missing else "partial"), missing


def normalized_missing_reason(row: dict[str, str], status: str) -> str | None:
    explicit = text_or_none(row.get("p2_missing_reason"))
    if explicit:
        return explicit
    return {
        "multiple_candidates": "multiple_p2_candidates",
        "unmatched_p1": "no_p2_candidate_for_p1_model",
        "unmatched_p2": "p2_request_without_agent_step",
    }.get(status)


def derive_one_request_metric(
    batch: dict[str, Any],
    row: dict[str, str],
    audit: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    context_payload = as_dict(as_dict(context).get("payload"))
    prompt = first_number(
        row.get("p2_prompt_tokens"), row.get("prompt_tokens"), context_payload.get("prompt_tokens")
    )
    cached = first_number(
        row.get("p2_cached_prompt_tokens"), row.get("cached_tokens"), context_payload.get("cached_prompt_tokens")
    )
    generation = first_number(
        row.get("p2_generation_tokens"), row.get("completion_tokens"), context_payload.get("generation_tokens")
    )
    computed = first_number(row.get("p2_computed_prefill_tokens"), context_payload.get("computed_prefill_tokens"))
    recomputed = first_number(
        row.get("p2_recomputed_prompt_tokens"), context_payload.get("recomputed_prompt_tokens")
    )
    e2e = first_number(row.get("p2_e2e_s"), context_delta_s(context, "received", "finished"))
    queue = first_number(
        row.get("p2_queue_s"),
        context_delta_s(context, "queued", "first_scheduled"),
        context_delta_s(context, "received", "first_scheduled"),
    )
    ttft = first_number(row.get("p2_ttft_s"), context_delta_s(context, "received", "first_token"))
    engine_ttft = first_number(
        row.get("p2_engine_ttft_s"), context_delta_s(context, "queued", "first_token")
    )
    prefill = first_number(
        row.get("p2_prefill_s"), context_delta_s(context, "first_scheduled", "prefill_completed")
    )
    prefill_proxy = first_number(
        row.get("p2_prefill_to_first_token_s"),
        context_delta_s(context, "first_scheduled", "first_token"),
    )
    decode = first_number(row.get("p2_decode_s"), context_delta_s(context, "first_token", "engine_finished"))
    uncached = max(prompt - cached, 0.0) if prompt is not None and cached is not None else None
    total = prompt + generation if prompt is not None and generation is not None else None
    effective_decode = generation - 1 if generation is not None and generation > 1 else None
    tpot = ratio(decode, effective_decode)
    decode_rate = ratio(effective_decode, decode)
    ttft_per_1k = ratio(ttft * 1000.0, prompt) if ttft is not None else None
    ttft_per_1k_uncached = ratio(ttft * 1000.0, uncached) if ttft is not None else None
    prefill_tokens = first_number(computed, uncached)
    prefill_proxy_rate = ratio(prefill_tokens, prefill_proxy)
    lifecycle_residual = subtract_if_present(e2e, queue, prefill_proxy, decode)
    if lifecycle_residual is not None and lifecycle_residual < -1e-6:
        lifecycle_residual = None
    elif lifecycle_residual is not None:
        lifecycle_residual = max(lifecycle_residual, 0.0)
    client_to_server, server_to_client, proxy_quality, proxy_reason = client_server_proxies(row, context)
    missing = []
    if effective_decode is None:
        missing.append("generation_tokens_le_1_or_missing")
    if prefill_proxy_rate is None:
        missing.append("prefill_proxy_or_token_count_missing")
    if proxy_reason:
        missing.append(proxy_reason)
    return {
        **audit,
        "step_type": row.get("step_type"),
        "item_start_mono_ns": integer_or_none(row.get("item_start_mono_ns")),
        "item_end_mono_ns": integer_or_none(row.get("item_end_mono_ns")),
        "clock_domain_id": row.get("clock_domain_id"),
        "host_boot_id": row.get("host_boot_id"),
        "engine_start_mono_ns": integer_or_none(as_dict(context).get("first_scheduled_ns")),
        "engine_end_mono_ns": integer_or_none(
            as_dict(context).get("engine_finished_ns") or as_dict(context).get("finished_ns")
        ),
        "engine_clock_domain_id": text_or_none(
            as_dict(context).get("first_scheduled_ns_clock_domain")
        ),
        "engine_host_boot_id": text_or_none(
            as_dict(context).get("first_scheduled_ns_host_boot_id")
        ),
        "p1_model_span_duration_s": number_or_none(row.get("p1_model_span_duration_s")),
        "p2_e2e_s": e2e,
        "p2_queue_s": queue,
        "p2_ttft_s": ttft,
        "p2_engine_ttft_s": engine_ttft,
        "p2_prefill_s": prefill,
        "p2_prefill_to_first_token_s": prefill_proxy,
        "p2_decode_s": decode,
        "prompt_tokens": integer_if_whole(prompt),
        "cached_prompt_tokens": integer_if_whole(cached),
        "uncached_prompt_tokens": integer_if_whole(uncached),
        "computed_prefill_tokens": integer_if_whole(computed),
        "recomputed_prompt_tokens": integer_if_whole(recomputed),
        "recomputed_token_quality": first_text(
            row.get("p2_recomputed_token_quality"),
            context_payload.get("recomputed_token_quality"),
            "unavailable_not_observed",
        ),
        "generation_tokens": integer_if_whole(generation),
        "total_tokens": integer_if_whole(total),
        "effective_decode_tokens": integer_if_whole(effective_decode),
        "tpot_s": tpot,
        "decode_time_per_token_s": tpot,
        "decode_tokens_per_second": decode_rate,
        "ttft_per_1k_prompt_tokens_s": ttft_per_1k,
        "ttft_per_1k_uncached_prompt_tokens_s": ttft_per_1k_uncached,
        "prefill_proxy_tokens_per_second": prefill_proxy_rate,
        "cache_reuse_rate": ratio(cached, prompt),
        "recomputed_prompt_rate": first_number(
            row.get("p2_recomputed_prompt_rate"), ratio(recomputed, prompt)
        ),
        "lifecycle_residual_s": lifecycle_residual,
        "prefill_metric_quality": "proxy_not_exact_prefill" if prefill_proxy_rate is not None else None,
        "client_to_server_proxy_s": client_to_server,
        "server_to_client_proxy_s": server_to_client,
        "client_server_proxy_quality": proxy_quality,
        "raw_p2_fallback_used": any(
            not has_value(row.get(field)) and value is not None
            for field, value in (
                ("p2_e2e_s", e2e),
                ("p2_queue_s", queue),
                ("p2_ttft_s", ttft),
                ("p2_prefill_s", prefill),
                ("p2_prefill_to_first_token_s", prefill_proxy),
                ("p2_decode_s", decode),
            )
        ),
        "metric_quality": request_metric_quality(row, audit),
        "missing_reason": ";".join(missing) if missing else None,
        "kv_evicted_block_count": number_or_none(row.get("p2_evicted_block_count")),
        "preemption_count": first_number(row.get("p2_preemption_count"), context_payload.get("preemption_count")),
        "queue_depth_on_arrival": first_number(
            row.get("p2_queue_depth_on_arrival"), context_payload.get("queue_depth_on_arrival")
        ),
        "running_requests_on_arrival": first_number(
            row.get("p2_running_requests_on_arrival"), context_payload.get("running_requests_on_arrival")
        ),
        "running_requests_after_schedule": first_number(
            row.get("p2_running_requests_after_schedule"), context_payload.get("running_requests_after_schedule")
        ),
        "waiting_requests_after_schedule": first_number(
            row.get("p2_waiting_requests_after_schedule"), context_payload.get("waiting_requests_after_schedule")
        ),
        "tool_context": None,
        "batch_path": str(batch["path"]),
    }


def context_delta_s(context: dict[str, Any] | None, start: str, end: str) -> float | None:
    if not context:
        return None
    start_ns = integer_or_none(context.get(f"{start}_ns"))
    end_ns = integer_or_none(context.get(f"{end}_ns"))
    if start_ns is None or end_ns is None:
        return None
    start_domain = text_or_none(context.get(f"{start}_ns_clock_domain"))
    end_domain = text_or_none(context.get(f"{end}_ns_clock_domain"))
    start_boot = text_or_none(context.get(f"{start}_ns_host_boot_id"))
    end_boot = text_or_none(context.get(f"{end}_ns_host_boot_id"))
    if not start_domain or start_domain != end_domain:
        return None
    if start_boot and end_boot and start_boot != end_boot:
        return None
    value = ns_delta_seconds(start_ns, end_ns)
    return value if value is not None and value >= 0 else None


def client_server_proxies(
    row: dict[str, str],
    context: dict[str, Any] | None,
) -> tuple[float | None, float | None, str | None, str | None]:
    if not context:
        return None, None, None, "raw_p2_context_missing"
    payload = as_dict(context.get("payload"))
    p1_domain = text_or_none(row.get("clock_domain_id"))
    p1_boot = text_or_none(row.get("host_boot_id"))
    domains = {
        text_or_none(context.get("received_ns_clock_domain")),
        text_or_none(context.get("finished_ns_clock_domain")),
    } - {None}
    boots = {
        text_or_none(context.get("received_ns_host_boot_id")),
        text_or_none(context.get("finished_ns_host_boot_id")),
    } - {None}
    if not p1_domain or not domains or domains != {p1_domain}:
        return None, None, None, "clock_domain_mismatch_or_unverified"
    if p1_boot and boots and boots != {p1_boot}:
        return None, None, None, "host_boot_id_mismatch"
    start = integer_or_none(row.get("item_start_mono_ns"))
    end = integer_or_none(row.get("item_end_mono_ns"))
    received = integer_or_none(context.get("received_ns") or payload.get("received_ns"))
    finished = integer_or_none(context.get("finished_ns") or payload.get("finished_ns"))
    inbound = ns_delta_seconds(start, received)
    outbound = ns_delta_seconds(finished, end)
    if inbound is not None and inbound < 0:
        inbound = None
    if outbound is not None and outbound < 0:
        outbound = None
    return inbound, outbound, "same_host_monotonic_proxy", None


def request_metric_quality(row: dict[str, str], audit: dict[str, Any]) -> str:
    if audit.get("p2_match_status") != "exact_match":
        return "unattributed_or_ambiguous"
    qualities = {
        text_or_none(row.get("p2_decode_quality")),
        text_or_none(row.get("p2_ttft_quality")),
        text_or_none(row.get("p2_e2e_quality")),
    } - {None}
    if "clock_domain_mismatch" in qualities:
        return "clock_domain_mismatch"
    return "derived_from_exact_request" if qualities else "derived_unverified_clock"


def analyze_timeline(
    batch: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    run_intervals: dict[str, dict[str, Any]] = {}
    violations: list[dict[str, Any]] = []
    result_rows = [row for row in batch["results"] if has_value(row.get("run_id"))]
    start_count = sum(integer_or_none(row.get("workload_start_mono_ns")) is not None for row in result_rows)
    end_count = sum(integer_or_none(row.get("workload_end_mono_ns")) is not None for row in result_rows)
    result_run_ids = [str(row.get("run_id")) for row in result_rows]
    for row in batch["results"]:
        run_id = text_or_none(row.get("run_id"))
        if not run_id:
            continue
        start = integer_or_none(row.get("workload_start_mono_ns"))
        end = integer_or_none(row.get("workload_end_mono_ns"))
        source = "batch_results.workload_mono_ns"
        if start is None or end is None:
            bounds = step_run_bounds(batch["step_rows"], run_id)
            start, end = bounds
            source = "step_item_envelope_fallback"
        if start is None or end is None or end < start:
            violations.append(timeline_violation(batch, "invalid_run_interval", run_id=run_id))
            continue
        run_intervals[run_id] = {
            "uid": f"run:{run_id}",
            "interval_type": "run",
            "batch_label": batch["label"],
            "task_id": row.get("task_id"),
            "run_id": run_id,
            "start_mono_ns": start,
            "end_mono_ns": end,
            "duration_s": (end - start) / 1_000_000_000,
            "interval_source": source,
        }

    items: list[dict[str, Any]] = []
    seen_items: set[tuple[str, str, str]] = set()
    for row in batch["step_rows"]:
        item_type = row.get("item_type")
        if item_type not in {"model", "tool"}:
            continue
        run_id = str(row.get("run_id") or "")
        span_id = str(
            row.get("span_id")
            or row.get("tool_call_id")
            or row.get("api_request_id")
            or f"step-{row.get('step_index')}-item-{row.get('item_index')}"
        )
        key = (run_id, str(item_type), span_id)
        if key in seen_items:
            continue
        seen_items.add(key)
        start = integer_or_none(row.get("item_start_mono_ns"))
        end = integer_or_none(row.get("item_end_mono_ns"))
        if start is None or end is None or end < start:
            violations.append(
                timeline_violation(batch, "invalid_item_interval", run_id=run_id, span_id=span_id, item_type=item_type)
            )
            continue
        item = {
            "uid": f"{item_type}:{run_id}:{span_id}",
            "interval_type": item_type,
            "batch_label": batch["label"],
            "task_id": row.get("task_id"),
            "run_id": run_id,
            "step_id": row.get("step_id"),
            "step_index": integer_or_none(row.get("step_index")),
            "span_id": span_id,
            "start_mono_ns": start,
            "end_mono_ns": end,
            "duration_s": (end - start) / 1_000_000_000,
            "clock_domain_id": row.get("clock_domain_id"),
            "host_boot_id": row.get("host_boot_id"),
            "interval_source": "step_sequence.item_mono_ns",
        }
        items.append(item)
        run = run_intervals.get(run_id)
        if run is None:
            violations.append(
                timeline_violation(
                    batch,
                    "item_without_run_interval",
                    run_id=run_id,
                    span_id=span_id,
                    item_type=item_type,
                )
            )
        elif start < run["start_mono_ns"] or end > run["end_mono_ns"]:
            violations.append(
                timeline_violation(
                    batch,
                    "item_outside_run",
                    run_id=run_id,
                    span_id=span_id,
                    item_type=item_type,
                    start_mono_ns=start,
                    end_mono_ns=end,
                )
            )

    model_items = [item for item in items if item["interval_type"] == "model"]
    violations.extend(find_per_run_model_overlaps(batch, model_items))
    sweep = sweep_concurrency(list(run_intervals.values()), model_items)
    violations.extend(sweep["violations"])
    intervals = list(run_intervals.values()) + items
    timeline_rows = sorted(intervals, key=lambda item: (item["start_mono_ns"], item["end_mono_ns"]))
    model_integral = integral_check(model_items)
    tool_integral = integral_check([item for item in items if item["interval_type"] == "tool"])
    configured = batch["concurrency"]
    expected_run_count = first_integer(
        as_dict(batch.get("manifest")).get("task_count"),
        as_dict(batch.get("summary")).get("attempted_count"),
        as_dict(batch.get("summary")).get("task_count"),
    )
    valid_run_count = len(run_intervals)
    completion_offsets = completion_offsets_s(run_intervals)
    completion_percentiles = {
        "t50_s": percentile(completion_offsets, 50),
        "t90_s": percentile(completion_offsets, 90),
        "t95_s": percentile(completion_offsets, 95),
        "t100_s": max(completion_offsets) if completion_offsets else None,
    }
    completion_values = [
        completion_percentiles[name] for name in ("t50_s", "t90_s", "t95_s", "t100_s")
    ]
    completion_ordered = bool(completion_values) and all(
        left is not None and right is not None and left <= right
        for left, right in zip(completion_values, completion_values[1:])
    )
    clock_domains = nonempty_values(batch["step_rows"], "clock_domain_id")
    boot_ids = nonempty_values(batch["step_rows"], "host_boot_id")
    timed_items = [row for row in batch["step_rows"] if row.get("item_type") in {"model", "tool"}]
    checks = [
        timeline_check(batch, "run_intervals_present", bool(run_intervals), {"count": len(run_intervals)}),
        timeline_check(
            batch,
            "result_count_matches_expected",
            expected_run_count is None or len(result_rows) == expected_run_count,
            {"expected": expected_run_count, "result_count": len(result_rows)},
        ),
        timeline_check(
            batch,
            "run_ids_unique",
            len(set(result_run_ids)) == len(result_run_ids),
            {"result_count": len(result_run_ids), "unique_run_id_count": len(set(result_run_ids))},
        ),
        timeline_check(
            batch,
            "started_completed_count_equal",
            start_count == end_count == valid_run_count == len(result_rows),
            {
                "result_count": len(result_rows),
                "started_count": start_count,
                "completed_count": end_count,
                "valid_interval_count": valid_run_count,
            },
        ),
        timeline_check(
            batch,
            "completion_percentiles_ordered",
            completion_ordered,
            completion_percentiles,
        ),
        timeline_check(
            batch,
            "configured_concurrency_not_exceeded",
            configured is None or sweep["max_active_runs"] <= configured,
            {"configured": configured, "observed": sweep["max_active_runs"]},
        ),
        timeline_check(
            batch,
            "active_model_not_above_active_run",
            not any(row["violation_type"] == "active_model_above_active_run" for row in violations),
            {"max_active_model": sweep["max_active_models"], "max_active_run": sweep["max_active_runs"]},
        ),
        timeline_check(
            batch,
            "one_active_model_per_run",
            not any(row["violation_type"] == "overlapping_models_in_run" for row in violations),
        ),
        timeline_check(
            batch,
            "items_contained_by_run",
            not any(
                row["violation_type"] in {"item_outside_run", "item_without_run_interval"}
                for row in violations
            ),
        ),
        timeline_check(batch, "model_integral_error_below_1pct", model_integral["passed"], model_integral),
        timeline_check(batch, "tool_integral_error_below_1pct", tool_integral["passed"], tool_integral),
        timeline_check(
            batch,
            "clock_domain_consistent",
            not timed_items
            or (
                len(clock_domains) == 1
                and all(has_value(row.get("clock_domain_id")) for row in timed_items)
            ),
            {
                "values": sorted(clock_domains),
                "timed_item_count": len(timed_items),
                "missing_count": sum(not has_value(row.get("clock_domain_id")) for row in timed_items),
            },
        ),
        timeline_check(
            batch,
            "host_boot_id_consistent",
            not timed_items
            or (
                len(boot_ids) == 1
                and all(has_value(row.get("host_boot_id")) for row in timed_items)
            ),
            {
                "values": sorted(boot_ids),
                "timed_item_count": len(timed_items),
                "missing_count": sum(not has_value(row.get("host_boot_id")) for row in timed_items),
            },
        ),
    ]
    return timeline_rows, violations, checks


def completion_offsets_s(run_intervals: dict[str, dict[str, Any]]) -> list[float]:
    if not run_intervals:
        return []
    origin = min(int(row["start_mono_ns"]) for row in run_intervals.values())
    return sorted((int(row["end_mono_ns"]) - origin) / 1_000_000_000 for row in run_intervals.values())


def find_per_run_model_overlaps(batch: dict[str, Any], models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in models:
        by_run[str(item["run_id"])].append(item)
    violations: list[dict[str, Any]] = []
    for run_id, rows in by_run.items():
        active: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: (item["start_mono_ns"], item["end_mono_ns"])):
            active = [item for item in active if item["end_mono_ns"] > row["start_mono_ns"]]
            for other in active:
                violations.append(
                    timeline_violation(
                        batch,
                        "overlapping_models_in_run",
                        run_id=run_id,
                        span_id=row.get("span_id"),
                        conflicting_span_id=other.get("span_id"),
                        start_mono_ns=max(row["start_mono_ns"], other["start_mono_ns"]),
                        end_mono_ns=min(row["end_mono_ns"], other["end_mono_ns"]),
                    )
                )
            active.append(row)
    return violations


def sweep_concurrency(runs: list[dict[str, Any]], models: list[dict[str, Any]]) -> dict[str, Any]:
    changes: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"run_start": [], "run_end": [], "model_start": [], "model_end": []}
    )
    for row in runs:
        changes[row["start_mono_ns"]]["run_start"].append(row)
        changes[row["end_mono_ns"]]["run_end"].append(row)
    for row in models:
        changes[row["start_mono_ns"]]["model_start"].append(row)
        changes[row["end_mono_ns"]]["model_end"].append(row)
    active_runs: dict[str, dict[str, Any]] = {}
    active_models: dict[str, dict[str, Any]] = {}
    max_runs = 0
    max_models = 0
    violations: list[dict[str, Any]] = []
    for timestamp in sorted(changes):
        change = changes[timestamp]
        for row in change["model_end"]:
            active_models.pop(row["uid"], None)
        for row in change["run_end"]:
            active_runs.pop(row["uid"], None)
        for row in change["run_start"]:
            active_runs[row["uid"]] = row
        for row in change["model_start"]:
            active_models[row["uid"]] = row
        max_runs = max(max_runs, len(active_runs))
        max_models = max(max_models, len(active_models))
        if len(active_models) > len(active_runs):
            batch = next(iter(active_models.values()), next(iter(active_runs.values()), {"batch_label": "unknown"}))
            violations.append(
                {
                    "batch_label": batch.get("batch_label"),
                    "violation_type": "active_model_above_active_run",
                    "timestamp_mono_ns": timestamp,
                    "active_run_count": len(active_runs),
                    "active_model_count": len(active_models),
                    "active_run_ids_json": json_compact(sorted(row["run_id"] for row in active_runs.values())),
                    "active_model_span_ids_json": json_compact(
                        sorted(str(row.get("span_id")) for row in active_models.values())
                    ),
                }
            )
    return {"max_active_runs": max_runs, "max_active_models": max_models, "violations": violations}


def integral_check(intervals: list[dict[str, Any]]) -> dict[str, Any]:
    interval_sum = sum(float(row["duration_s"]) for row in intervals)
    changes: dict[int, int] = defaultdict(int)
    for row in intervals:
        changes[int(row["start_mono_ns"])] += 1
        changes[int(row["end_mono_ns"])] -= 1
    active = 0
    previous: int | None = None
    integral_ns = 0
    for timestamp in sorted(changes):
        if previous is not None:
            integral_ns += active * (timestamp - previous)
        active += changes[timestamp]
        previous = timestamp
    sweep_sum = integral_ns / 1_000_000_000
    error = abs(interval_sum - sweep_sum)
    relative = error / interval_sum if interval_sum > 0 else (0.0 if sweep_sum == 0 else None)
    return {
        "interval_count": len(intervals),
        "interval_duration_sum_s": interval_sum,
        "sweep_integral_s": sweep_sum,
        "relative_error": relative,
        "passed": relative is not None and relative < 0.01,
    }


def summarize_runs(batch: dict[str, Any], requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in requests:
        requests_by_run[str(row.get("run_id") or "")].append(row)
    tools_by_run: dict[str, list[dict[str, str]]] = defaultdict(list)
    steps_by_run: dict[str, set[str]] = defaultdict(set)
    for row in batch["step_rows"]:
        run_id = str(row.get("run_id") or "")
        if row.get("item_type") == "tool":
            tools_by_run[run_id].append(row)
        if has_value(row.get("step_id")) and row.get("step_type") != "model_extra":
            steps_by_run[run_id].add(str(row["step_id"]))
    summaries: list[dict[str, Any]] = []
    for run_id, result in batch["run_results"].items():
        models = requests_by_run.get(run_id, [])
        tools = tools_by_run.get(run_id, [])
        run_wall = first_number(result.get("workload_wall_s"), first_present(models, "run_wall_s"))
        model_e2e = sum_if_complete(row.get("p2_e2e_s") for row in models)
        if model_e2e is None:
            model_e2e = sum_if_complete(row.get("p1_model_span_duration_s") for row in models)
        queue = sum_if_complete(row.get("p2_queue_s") for row in models)
        prefill_proxy = sum_if_complete(row.get("p2_prefill_to_first_token_s") for row in models)
        decode = sum_if_complete(row.get("p2_decode_s") for row in models)
        prompt = sum_if_complete(row.get("prompt_tokens") for row in models)
        uncached = sum_if_complete(row.get("uncached_prompt_tokens") for row in models)
        generation = sum_if_complete(row.get("generation_tokens") for row in models)
        effective_decode = sum_if_complete(row.get("effective_decode_tokens") for row in models)
        tool_duration = sum_if_complete(
            first_number(row.get("tool_call_duration_s"), row.get("approx_tool_duration_s")) for row in tools
        )
        residual_model = subtract_if_present(model_e2e, queue, prefill_proxy, decode)
        if residual_model is not None and residual_model < -1e-6:
            residual_model = None
        elif residual_model is not None:
            residual_model = max(residual_model, 0.0)
        agent_gap = subtract_if_present(run_wall, model_e2e, tool_duration)
        if agent_gap is not None and agent_gap < -1e-6:
            agent_gap = None
        elif agent_gap is not None:
            agent_gap = max(agent_gap, 0.0)
        command_hashes = [str(row.get("command_hash")) for row in tools if has_value(row.get("command_hash"))]
        repeated_commands = sum(count - 1 for count in value_counts(command_hashes).values() if count > 1)
        evicted = sum_if_any(row.get("kv_evicted_block_count") for row in models)
        preemptions = sum_if_any(row.get("preemption_count") for row in models)
        recomputed = sum_if_complete(row.get("recomputed_prompt_tokens") for row in models)
        summaries.append(
            {
                "batch_label": batch["label"],
                "configured_concurrency": batch["concurrency"],
                "task_id": result.get("task_id"),
                "run_id": run_id,
                "execution_success": parse_bool(result.get("execution_success")),
                "analysis_success": parse_bool(result.get("analysis_pipeline_success")),
                "run_wall_s": run_wall,
                "step_count": len(steps_by_run.get(run_id, set())),
                "model_call_count": len([row for row in models if row.get("p2_match_status") != "unmatched_p2"]),
                "tool_call_count": len(tools),
                "model_e2e_sum_s": model_e2e,
                "queue_sum_s": queue,
                "prefill_proxy_sum_s": prefill_proxy,
                "decode_sum_s": decode,
                "model_residual_sum_s": residual_model,
                "tool_sum_s": tool_duration,
                "agent_gap_s": agent_gap,
                "prompt_tokens": prompt,
                "uncached_prompt_tokens": uncached,
                "generation_tokens": generation,
                "effective_decode_tokens": effective_decode,
                "recomputed_prompt_tokens": recomputed,
                "recomputed_prompt_rate": ratio(recomputed, prompt),
                "kv_evicted_block_count": evicted,
                "preemption_count": preemptions,
                "tool_timeout_count": sum(is_true(row.get("timed_out")) for row in tools),
                "tool_nonzero_return_count": sum(is_nonzero(row.get("returncode")) for row in tools),
                "tool_retry_candidate_count": repeated_commands,
                "analysis_wall_s": number_or_none(result.get("analysis_wall_s")),
            }
        )
    return summaries


def decompose_paired_runs(run_summaries: list[dict[str, Any]], baseline_label: str) -> list[dict[str, Any]]:
    by_label_task = {
        (str(row.get("batch_label")), str(row.get("task_id"))): row
        for row in run_summaries
        if has_value(row.get("batch_label")) and has_value(row.get("task_id"))
    }
    baseline_tasks = {
        task_id for label, task_id in by_label_task if label == baseline_label
    }
    candidate_labels = sorted({label for label, _ in by_label_task if label != baseline_label})
    output: list[dict[str, Any]] = []
    for label in candidate_labels:
        candidate_tasks = {task_id for batch_label, task_id in by_label_task if batch_label == label}
        for task_id in sorted(baseline_tasks | candidate_tasks):
            before = by_label_task.get((baseline_label, task_id))
            candidate = by_label_task.get((label, task_id))
            if before is None or candidate is None:
                output.append(
                    {
                        "baseline_label": baseline_label,
                        "candidate_label": label,
                        "task_id": task_id,
                        "baseline_run_id": before.get("run_id") if before else None,
                        "candidate_run_id": candidate.get("run_id") if candidate else None,
                        "pair_status": "baseline_task_missing" if before is None else "candidate_task_missing",
                        "decomposition_quality": "unavailable",
                    }
                )
                continue
            output.append(decompose_run_pair(before, candidate, baseline_label))
    return output


def decompose_run_pair(before: dict[str, Any], after: dict[str, Any], baseline_label: str) -> dict[str, Any]:
    components = {
        "queue": product_decomposition(
            before.get("model_call_count"), before.get("queue_sum_s"), after.get("model_call_count"), after.get("queue_sum_s")
        ),
        "prompt_proxy": product_decomposition(
            before.get("uncached_prompt_tokens"),
            before.get("prefill_proxy_sum_s"),
            after.get("uncached_prompt_tokens"),
            after.get("prefill_proxy_sum_s"),
        ),
        "decode": product_decomposition(
            before.get("effective_decode_tokens"),
            before.get("decode_sum_s"),
            after.get("effective_decode_tokens"),
            after.get("decode_sum_s"),
        ),
        "model_residual": product_decomposition(
            before.get("model_call_count"),
            before.get("model_residual_sum_s"),
            after.get("model_call_count"),
            after.get("model_residual_sum_s"),
        ),
        "tool": product_decomposition(
            before.get("tool_call_count"), before.get("tool_sum_s"), after.get("tool_call_count"), after.get("tool_sum_s")
        ),
    }
    complete = all(component is not None for component in components.values())
    workload = sum(component["workload_contribution_s"] for component in components.values() if component)
    slowdown = sum(component["unit_rate_contribution_s"] for component in components.values() if component)
    gap_delta = delta(after.get("agent_gap_s"), before.get("agent_gap_s"))
    wall_delta = delta(after.get("run_wall_s"), before.get("run_wall_s"))
    explained = workload + slowdown + gap_delta if complete and gap_delta is not None else None
    residual = wall_delta - explained if wall_delta is not None and explained is not None else None
    row: dict[str, Any] = {
        "baseline_label": baseline_label,
        "candidate_label": after.get("batch_label"),
        "task_id": after.get("task_id"),
        "baseline_run_id": before.get("run_id"),
        "candidate_run_id": after.get("run_id"),
        "pair_status": "paired",
        "run_wall_delta_s": wall_delta,
        "model_call_count_delta": delta(after.get("model_call_count"), before.get("model_call_count")),
        "tool_call_count_delta": delta(after.get("tool_call_count"), before.get("tool_call_count")),
        "prompt_token_delta": delta(after.get("prompt_tokens"), before.get("prompt_tokens")),
        "generation_token_delta": delta(after.get("generation_tokens"), before.get("generation_tokens")),
        "queue_time_delta_s": delta(after.get("queue_sum_s"), before.get("queue_sum_s")),
        "decode_time_delta_s": delta(after.get("decode_sum_s"), before.get("decode_sum_s")),
        "tool_time_delta_s": delta(after.get("tool_sum_s"), before.get("tool_sum_s")),
        "agent_gap_delta_s": gap_delta,
        "workload_amplification_s": workload if complete else None,
        "system_slowdown_s": slowdown if complete else None,
        "workload_amplification_score": ratio(workload, abs(wall_delta)) if complete and wall_delta else None,
        "system_slowdown_score": ratio(slowdown, abs(wall_delta)) if complete and wall_delta else None,
        "unexplained_residual_s": residual,
        "decomposition_quality": "proxy_additive" if complete else "partial_missing_metrics",
    }
    for name, component in components.items():
        row[f"{name}_workload_contribution_s"] = component["workload_contribution_s"] if component else None
        row[f"{name}_unit_rate_contribution_s"] = component["unit_rate_contribution_s"] if component else None
        row[f"{name}_baseline_rate"] = component["baseline_rate"] if component else None
        row[f"{name}_candidate_rate"] = component["candidate_rate"] if component else None
    return row


def product_decomposition(
    baseline_units: Any,
    baseline_duration: Any,
    candidate_units: Any,
    candidate_duration: Any,
) -> dict[str, float] | None:
    b_units = number_or_none(baseline_units)
    b_duration = number_or_none(baseline_duration)
    c_units = number_or_none(candidate_units)
    c_duration = number_or_none(candidate_duration)
    if None in {b_units, b_duration, c_units, c_duration} or b_units <= 0 or c_units <= 0:
        return None
    b_rate = b_duration / b_units
    c_rate = c_duration / c_units
    return {
        "baseline_rate": b_rate,
        "candidate_rate": c_rate,
        "workload_contribution_s": (c_units - b_units) * b_rate,
        "unit_rate_contribution_s": c_units * (c_rate - b_rate),
    }


def pair_model_calls(requests: list[dict[str, Any]], baseline_label: str) -> list[dict[str, Any]]:
    by_batch_task: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in requests:
        if row.get("p2_match_status") == "unmatched_p2":
            continue
        by_batch_task[(str(row.get("batch_label")), str(row.get("task_id")))].append(row)
    for rows in by_batch_task.values():
        rows.sort(key=request_order)
    labels = sorted({key[0] for key in by_batch_task if key[0] != baseline_label})
    tasks = sorted({key[1] for key in by_batch_task if key[0] == baseline_label})
    output: list[dict[str, Any]] = []
    for label in labels:
        for task_id in tasks:
            before = by_batch_task.get((baseline_label, task_id), [])
            after = by_batch_task.get((label, task_id), [])
            for left, right in align_sequences(before, after):
                output.append(
                    {
                        "baseline_label": baseline_label,
                        "candidate_label": label,
                        "task_id": task_id,
                        "baseline_run_id": left.get("run_id") if left else None,
                        "candidate_run_id": right.get("run_id") if right else None,
                        "baseline_step_index": left.get("step_index") if left else None,
                        "candidate_step_index": right.get("step_index") if right else None,
                        "baseline_model_call_id": left.get("model_call_id") if left else None,
                        "candidate_model_call_id": right.get("model_call_id") if right else None,
                        "alignment_status": "matched" if left and right else ("baseline_only" if left else "candidate_only"),
                        "alignment_quality": model_pair_quality(left, right, before, after),
                        "prompt_token_delta": delta(
                            right.get("prompt_tokens") if right else None,
                            left.get("prompt_tokens") if left else None,
                        ),
                        "generation_token_delta": delta(
                            right.get("generation_tokens") if right else None,
                            left.get("generation_tokens") if left else None,
                        ),
                        "queue_delta_s": delta(
                            right.get("p2_queue_s") if right else None,
                            left.get("p2_queue_s") if left else None,
                        ),
                        "decode_delta_s": delta(
                            right.get("p2_decode_s") if right else None,
                            left.get("p2_decode_s") if left else None,
                        ),
                        "tpot_delta_s": delta(
                            right.get("tpot_s") if right else None,
                            left.get("tpot_s") if left else None,
                        ),
                    }
                )
    return output


def analyze_inflight_thresholds(requests: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in requests:
        start = integer_or_none(row.get("engine_start_mono_ns"))
        end = integer_or_none(row.get("engine_end_mono_ns"))
        domain = text_or_none(row.get("engine_clock_domain_id"))
        boot = text_or_none(row.get("engine_host_boot_id")) or "unknown"
        if (
            row.get("p2_match_status") != "exact_match"
            or start is None
            or end is None
            or end < start
            or domain is None
        ):
            continue
        grouped[(str(row.get("batch_label") or ""), domain, boot)].append(row)

    observations: list[dict[str, Any]] = []
    for (label, domain, boot), rows in grouped.items():
        intervals = [
            (
                integer_or_none(row.get("engine_start_mono_ns")),
                integer_or_none(row.get("engine_end_mono_ns")),
                row,
            )
            for row in rows
        ]
        for start, end, row in intervals:
            if start is None or end is None:
                continue
            active = [
                other
                for other_start, other_end, other in intervals
                if other_start is not None and other_end is not None and other_start <= start < other_end
            ]
            uncached_values = [number_or_none(item.get("uncached_prompt_tokens")) for item in active]
            inflight_uncached = (
                sum(value for value in uncached_values if value is not None)
                if any(value is not None for value in uncached_values)
                else None
            )
            generation = number_or_none(row.get("generation_tokens"))
            prompt = number_or_none(row.get("prompt_tokens"))
            total_tokens = (
                prompt + generation if prompt is not None and generation is not None else None
            )
            e2e = number_or_none(row.get("p2_e2e_s"))
            service_time_per_token = ratio(e2e, total_tokens)
            observation = {
                "batch_label": label,
                "task_id": row.get("task_id"),
                "run_id": row.get("run_id"),
                "step_index": row.get("step_index"),
                "model_call_id": row.get("model_call_id"),
                "engine_request_id": row.get("vllm_request_id"),
                "engine_start_mono_ns": start,
                "engine_end_mono_ns": end,
                "clock_domain_id": domain,
                "host_boot_id": boot,
                "inflight_model_requests": len(active),
                "inflight_uncached_tokens": inflight_uncached,
                "scheduler_running_requests": row.get("running_requests_after_schedule"),
                "scheduler_waiting_requests": row.get("waiting_requests_after_schedule"),
                "p2_e2e_s": e2e,
                "tpot_s": row.get("tpot_s"),
                "service_time_per_total_token_s": service_time_per_token,
                "execution_failed": row.get("status") in {"error", "failed"},
                "quality": "derived_same_clock_domain_interval_overlap",
            }
            observations.append(observation)
            row["inflight_model_requests"] = len(active)
            row["inflight_uncached_tokens"] = inflight_uncached

    threshold_rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    labels = sorted({str(row.get("batch_label")) for row in observations})
    for label in labels:
        batch_rows = [row for row in observations if str(row.get("batch_label")) == label]
        counts = sorted(
            {integer_or_none(row.get("inflight_model_requests")) for row in batch_rows} - {None}
        )
        for count in counts:
            samples = [row for row in batch_rows if integer_or_none(row.get("inflight_model_requests")) == count]
            threshold_rows.append(
                {
                    "batch_label": label,
                    "inflight_model_requests": count,
                    "sample_count": len(samples),
                    "p2_e2e_p50_s": percentile_values(samples, "p2_e2e_s", 50),
                    "p2_e2e_p95_s": percentile_values(samples, "p2_e2e_s", 95),
                    "tpot_p50_s": percentile_values(samples, "tpot_s", 50),
                    "service_time_per_total_token_p50_s": percentile_values(
                        samples, "service_time_per_total_token_s", 50
                    ),
                    "failure_rate": ratio(
                        sum(row.get("execution_failed") is True for row in samples), len(samples)
                    ),
                }
            )
        summaries[label] = detect_inflight_change_point(batch_rows)
    return {
        "observations": observations,
        "thresholds": threshold_rows,
        "summary": {
            "schema_version": SCHEMA_VERSION,
            "analysis_type": "agentmon.inflight_change_point",
            "method": "two_segment_median_scan",
            "minimum_relative_slowdown": 0.15,
            "by_batch": summaries,
            "limitations": "derived from overlapping EngineCore request windows; association is not causal",
        },
    }


def detect_inflight_change_point(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    counts = sorted(
        {integer_or_none(row.get("inflight_model_requests")) for row in rows} - {None}
    )
    metric_name = next(
        (
            name
            for name in ("tpot_s", "service_time_per_total_token_s", "p2_e2e_s")
            if sum(number_or_none(row.get(name)) is not None for row in rows) >= 6
        ),
        None,
    )
    if metric_name is None:
        return {
            "status": "unavailable",
            "threshold_inflight_requests": None,
            "missing_reason": "fewer_than_6_valid_latency_samples",
        }
    for threshold in counts[:-1]:
        before = [
            value
            for row in rows
            if integer_or_none(row.get("inflight_model_requests")) <= threshold
            and (value := number_or_none(row.get(metric_name))) is not None
        ]
        after = [
            value
            for row in rows
            if integer_or_none(row.get("inflight_model_requests")) > threshold
            and (value := number_or_none(row.get(metric_name))) is not None
        ]
        if len(before) < 3 or len(after) < 3:
            continue
        before_median = percentile(before, 50)
        after_median = percentile(after, 50)
        if before_median in {None, 0} or after_median is None:
            continue
        effect = after_median / before_median - 1.0
        candidates.append(
            {
                "threshold_inflight_requests": threshold,
                "metric": metric_name,
                "before_sample_count": len(before),
                "after_sample_count": len(after),
                "before_median": before_median,
                "after_median": after_median,
                "relative_slowdown": effect,
            }
        )
    qualifying = [candidate for candidate in candidates if candidate["relative_slowdown"] >= 0.15]
    if not qualifying:
        return {
            "status": "no_change_point_detected" if candidates else "unavailable",
            "threshold_inflight_requests": None,
            "metric": metric_name,
            "candidate_count": len(candidates),
            "missing_reason": None if candidates else "insufficient_samples_on_both_sides",
        }
    best = max(
        qualifying,
        key=lambda candidate: (
            round(candidate["relative_slowdown"], 12),
            min(candidate["before_sample_count"], candidate["after_sample_count"]),
        ),
    )
    total = best["before_sample_count"] + best["after_sample_count"]
    best["status"] = "detected"
    best["confidence"] = (
        "high"
        if total >= 30 and best["relative_slowdown"] >= 0.30
        else "medium"
        if total >= 12
        else "low"
    )
    best["quality"] = "derived_statistical_association"
    return best


def percentile_values(rows: list[dict[str, Any]], field: str, pct: float) -> float | None:
    return percentile(
        [value for row in rows if (value := number_or_none(row.get(field))) is not None],
        pct,
    )


def align_sequences(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    n, m = len(baseline), len(candidate)
    gap = 1.25
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i * gap
        move[i][0] = "up"
    for j in range(1, m + 1):
        dp[0][j] = j * gap
        move[0][j] = "left"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            choices = (
                (dp[i - 1][j - 1] + model_distance(baseline[i - 1], candidate[j - 1]), "diag"),
                (dp[i - 1][j] + gap, "up"),
                (dp[i][j - 1] + gap, "left"),
            )
            dp[i][j], move[i][j] = min(choices, key=lambda item: item[0])
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    i, j = n, m
    while i or j:
        direction = move[i][j]
        if direction == "diag":
            pairs.append((baseline[i - 1], candidate[j - 1]))
            i -= 1
            j -= 1
        elif direction == "up":
            pairs.append((baseline[i - 1], None))
            i -= 1
        else:
            pairs.append((None, candidate[j - 1]))
            j -= 1
    pairs.reverse()
    return pairs


def model_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    distances = []
    for key in ("prompt_tokens", "generation_tokens"):
        a = number_or_none(left.get(key))
        b = number_or_none(right.get(key))
        if a is not None and b is not None:
            distances.append(abs(a - b) / max(abs(a), abs(b), 1.0))
    return statistics.fmean(distances) if distances else 0.75


def model_pair_quality(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
) -> str:
    if left is None or right is None:
        return "unpaired_path_difference"
    if len(baseline) == len(candidate) and request_order(left) == request_order(right):
        return "ordinal_equal_length"
    return "sequence_alignment_from_tokens"


def attach_host_pressure(run_summaries: list[dict[str, Any]], host_rows: list[dict[str, Any]]) -> None:
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in host_rows:
        by_run[str(row.get("run_id") or "")].append(row)
    for summary in run_summaries:
        rows = by_run.get(str(summary.get("run_id") or ""), [])
        fields = {
            "host_cpu_psi_max": "cpu_psi_some_avg10",
            "host_memory_psi_max": "memory_psi_some_avg10",
            "host_io_psi_max": "io_psi_some_avg10",
            "host_load_per_cpu_max": "load1_per_cpu",
            "host_scheduler_latency_max_s": "scheduler_latency_mean_s",
        }
        for target, source in fields.items():
            values = [value for row in rows if (value := number_or_none(row.get(source))) is not None]
            summary[target] = max(values) if values else None
        pressure_signals = (
            number(summary.get("host_cpu_psi_max")) >= 10.0,
            number(summary.get("host_memory_psi_max")) >= 1.0,
            number(summary.get("host_io_psi_max")) >= 10.0,
            number(summary.get("host_load_per_cpu_max")) >= 1.0,
        )
        summary["host_pressure_observed"] = any(pressure_signals) if rows else None


def classify_tails(run_summaries: list[dict[str, Any]], baseline_label: str) -> list[dict[str, Any]]:
    baseline_rows = [row for row in run_summaries if row.get("batch_label") == baseline_label]
    baseline_by_task = {str(row.get("task_id")): row for row in baseline_rows if has_value(row.get("task_id"))}
    thresholds = {
        field: percentile([value for row in baseline_rows if (value := number_or_none(row.get(field))) is not None], 95)
        for field in (
            "run_wall_s",
            "queue_sum_s",
            "prefill_proxy_sum_s",
            "prompt_tokens",
            "generation_tokens",
            "model_call_count",
            "tool_call_count",
            "step_count",
            "model_residual_sum_s",
            "agent_gap_s",
            "analysis_wall_s",
        )
    }
    decode_rates = [
        rate
        for row in baseline_rows
        if (rate := ratio(number_or_none(row.get("decode_sum_s")), number_or_none(row.get("effective_decode_tokens"))))
        is not None
    ]
    decode_rate_threshold = percentile(decode_rates, 95)
    output: list[dict[str, Any]] = []
    for row in run_summaries:
        task_id = str(row.get("task_id") or "")
        paired = baseline_by_task.get(task_id)
        run_wall = number_or_none(row.get("run_wall_s"))
        baseline_wall = number_or_none(paired.get("run_wall_s")) if paired else None
        threshold = thresholds.get("run_wall_s")
        is_tail = bool(
            run_wall is not None
            and (
                (threshold is not None and run_wall > threshold)
                or (baseline_wall is not None and baseline_wall > 0 and run_wall / baseline_wall >= 1.5)
            )
        )
        categories: list[str] = []
        evidence: list[dict[str, Any]] = []
        peers = [
            candidate
            for candidate in run_summaries
            if candidate.get("batch_label") == row.get("batch_label")
        ]

        def add(category: str, field: str, value: Any, limit: Any, baseline_value: Any = None) -> None:
            categories.append(category)
            distribution = [
                number_value
                for candidate in peers
                if (number_value := number_or_none(candidate.get(field))) is not None
            ]
            evidence.append(
                {
                    "category": category,
                    "field": field,
                    "value": value,
                    "threshold": limit,
                    "baseline": baseline_value,
                    "percentile_rank": percentile_rank(distribution, number_or_none(value)),
                }
            )

        if is_tail:
            for category, field in (
                ("queue_tail", "queue_sum_s"),
                ("prefill_proxy_tail", "prefill_proxy_sum_s"),
                ("long_context", "prompt_tokens"),
                ("long_output", "generation_tokens"),
                ("agent_path_expansion", "model_call_count"),
                ("agent_step_expansion", "step_count"),
                ("tool_path_expansion", "tool_call_count"),
                ("model_residual_tail", "model_residual_sum_s"),
                ("agent_orchestration_tail", "agent_gap_s"),
            ):
                value = number_or_none(row.get(field))
                limit = thresholds.get(field)
                base_value = number_or_none(paired.get(field)) if paired else None
                if value is not None and (
                    (limit is not None and value > limit)
                    or (base_value is not None and base_value > 0 and value / base_value >= 1.5)
                ):
                    add(category, field, value, limit, base_value)
            decode_rate = ratio(number_or_none(row.get("decode_sum_s")), number_or_none(row.get("effective_decode_tokens")))
            paired_decode_rate = (
                ratio(number_or_none(paired.get("decode_sum_s")), number_or_none(paired.get("effective_decode_tokens")))
                if paired
                else None
            )
            if decode_rate is not None and (
                (decode_rate_threshold is not None and decode_rate > decode_rate_threshold)
                or (paired_decode_rate is not None and paired_decode_rate > 0 and decode_rate / paired_decode_rate >= 1.5)
            ):
                add("decode_tail", "decode_time_per_effective_token_s", decode_rate, decode_rate_threshold, paired_decode_rate)
            if number(row.get("recomputed_prompt_rate")) >= 0.25:
                add("prompt_recompute", "recomputed_prompt_rate", row.get("recomputed_prompt_rate"), 0.25)
            if number(row.get("kv_evicted_block_count")) > 0:
                add("kv_eviction", "kv_evicted_block_count", row.get("kv_evicted_block_count"), 0)
            if number(row.get("preemption_count")) > 0:
                add("kv_preemption", "preemption_count", row.get("preemption_count"), 0)
            if number(row.get("tool_timeout_count")) > 0:
                add("tool_timeout", "tool_timeout_count", row.get("tool_timeout_count"), 0)
            if number(row.get("tool_retry_candidate_count")) > 0:
                add("tool_retry", "tool_retry_candidate_count", row.get("tool_retry_candidate_count"), 0)
            if row.get("execution_success") is False:
                add("execution_failure", "execution_success", False, True)
            if row.get("host_pressure_observed") is True:
                pressure_field = max(
                    (
                        "host_cpu_psi_max",
                        "host_memory_psi_max",
                        "host_io_psi_max",
                        "host_load_per_cpu_max",
                    ),
                    key=lambda field: number(row.get(field)),
                )
                add("host_pressure", pressure_field, row.get(pressure_field), "host_pressure_threshold")
            analysis_wall = number_or_none(row.get("analysis_wall_s"))
            if analysis_wall is not None and thresholds.get("analysis_wall_s") is not None:
                if analysis_wall > float(thresholds["analysis_wall_s"]):
                    add("pipeline_overhead", "analysis_wall_s", analysis_wall, thresholds["analysis_wall_s"])
            if not categories:
                categories.append("unknown")
        output.append(
            {
                "batch_label": row.get("batch_label"),
                "configured_concurrency": row.get("configured_concurrency"),
                "task_id": row.get("task_id"),
                "run_id": row.get("run_id"),
                "is_tail": is_tail,
                "categories": ";".join(categories),
                "evidence_json": json_compact(evidence),
                "confidence": "high" if is_tail and paired and categories != ["unknown"] else ("medium" if is_tail else "not_applicable"),
                "baseline_run_wall_s": baseline_wall,
                "observed_run_wall_s": run_wall,
                "tail_threshold_s": threshold,
                "prompt_tokens_percentile": percentile_rank(
                    [value for candidate in peers if (value := number_or_none(candidate.get("prompt_tokens"))) is not None],
                    number_or_none(row.get("prompt_tokens")),
                ),
                "uncached_tokens_percentile": percentile_rank(
                    [value for candidate in peers if (value := number_or_none(candidate.get("uncached_prompt_tokens"))) is not None],
                    number_or_none(row.get("uncached_prompt_tokens")),
                ),
                "output_tokens_percentile": percentile_rank(
                    [value for candidate in peers if (value := number_or_none(candidate.get("generation_tokens"))) is not None],
                    number_or_none(row.get("generation_tokens")),
                ),
                "queue_percentile": percentile_rank(
                    [value for candidate in peers if (value := number_or_none(candidate.get("queue_sum_s"))) is not None],
                    number_or_none(row.get("queue_sum_s")),
                ),
                "limitations": "prefill category is proxy; shared resources are not request-attributed",
            }
        )
    return output


def analyze_device_imbalance(batch: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    output: list[dict[str, Any]] = []
    for row in batch["step_rows"]:
        raw = row.get("step_npu_device_metrics_json") or row.get("model_npu_device_metrics_json")
        devices = parse_json_dict(raw)
        if not devices:
            continue
        key = (str(row.get("run_id")), str(row.get("step_id")), json_compact(devices))
        if key in seen:
            continue
        seen.add(key)
        hbm = metric_by_device(devices, "peak_hbm_used_bytes")
        aicore = metric_by_device(devices, "max_aicore_util_pct")
        output.append(
            {
                "batch_label": batch["label"],
                "configured_concurrency": batch["concurrency"],
                "task_id": row.get("task_id"),
                "run_id": row.get("run_id"),
                "step_id": row.get("step_id"),
                "step_index": integer_or_none(row.get("step_index")),
                "device_count": len(devices),
                **imbalance_fields("hbm", hbm),
                **imbalance_fields("aicore", aicore),
                "hbm_metric_quality": metric_coverage_quality(len(hbm), len(devices)),
                "aicore_metric_quality": metric_coverage_quality(len(aicore), len(devices)),
                "hbm_max_device_id": max(hbm, key=hbm.get) if hbm else None,
                "aicore_min_device_id": min(aicore, key=aicore.get) if aicore else None,
                "device_metrics_json": json_compact(devices),
                "rank_id": None,
                "rank_mapping_quality": "unavailable",
                "device_id_semantics": "runtime_device_id_not_tensor_parallel_rank",
                "observation_scope": "shared_service_observation",
                "request_attribution": "not_request_attributed",
            }
        )
    return output


def metric_by_device(devices: dict[str, Any], field: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for device_id, payload in devices.items():
        value = number_or_none(as_dict(payload).get(field))
        if value is not None:
            result[str(device_id)] = value
    return result


def imbalance_fields(prefix: str, values_by_device: dict[str, float]) -> dict[str, Any]:
    values = list(values_by_device.values())
    if not values:
        return {
            f"{prefix}_device_sample_count": 0,
            f"{prefix}_mean": None,
            f"{prefix}_max_minus_min": None,
            f"{prefix}_imbalance_pct": None,
            f"{prefix}_coefficient_of_variation": None,
        }
    mean = statistics.fmean(values)
    spread = max(values) - min(values)
    return {
        f"{prefix}_device_sample_count": len(values),
        f"{prefix}_mean": mean,
        f"{prefix}_max_minus_min": spread,
        f"{prefix}_imbalance_pct": spread / mean * 100.0 if mean else None,
        f"{prefix}_coefficient_of_variation": statistics.pstdev(values) / mean if mean and len(values) > 1 else 0.0,
    }


def summarize_device_imbalance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "schema_version": SCHEMA_VERSION,
        "window_count": len(rows),
        "hbm_imbalance_pct": summarize_numbers(row.get("hbm_imbalance_pct") for row in rows),
        "aicore_imbalance_pct": summarize_numbers(row.get("aicore_imbalance_pct") for row in rows),
        "rank_mapping_quality_counts": value_counts(str(row.get("rank_mapping_quality")) for row in rows),
    }
    labels = sorted({str(row.get("batch_label")) for row in rows if has_value(row.get("batch_label"))})
    by_batch: dict[str, Any] = {}
    for label in labels:
        items = [row for row in rows if str(row.get("batch_label")) == label]
        by_batch[label] = {
            "window_count": len(items),
            "hbm_imbalance_pct": summarize_numbers(row.get("hbm_imbalance_pct") for row in items),
            "aicore_imbalance_pct": summarize_numbers(row.get("aicore_imbalance_pct") for row in items),
            "hbm_quality_counts": value_counts(str(row.get("hbm_metric_quality")) for row in items),
            "aicore_quality_counts": value_counts(str(row.get("aicore_metric_quality")) for row in items),
        }
    summary["by_batch"] = by_batch
    return summary


def expand_device_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        devices = parse_json_dict(row.get("device_metrics_json"))
        for device_id, raw_metrics in sorted(devices.items()):
            metrics = as_dict(raw_metrics)
            output.append(
                {
                    "batch_label": row.get("batch_label"),
                    "configured_concurrency": row.get("configured_concurrency"),
                    "task_id": row.get("task_id"),
                    "run_id": row.get("run_id"),
                    "step_id": row.get("step_id"),
                    "step_index": row.get("step_index"),
                    "device_id": device_id,
                    "rank_id": None,
                    "rank_mapping_quality": "unavailable",
                    "peak_hbm_used_bytes": number_or_none(metrics.get("peak_hbm_used_bytes")),
                    "avg_hbm_used_bytes": number_or_none(metrics.get("avg_hbm_used_bytes")),
                    "max_aicore_util_pct": number_or_none(metrics.get("max_aicore_util_pct")),
                    "avg_aicore_util_pct": number_or_none(metrics.get("avg_aicore_util_pct")),
                    "max_temperature_c": number_or_none(metrics.get("max_temperature_c")),
                    "avg_temperature_c": number_or_none(metrics.get("avg_temperature_c")),
                    "max_power_w": number_or_none(metrics.get("max_power_w")),
                    "avg_power_w": number_or_none(metrics.get("avg_power_w")),
                    "min_aicore_frequency_mhz": number_or_none(metrics.get("min_aicore_frequency_mhz")),
                    "avg_aicore_frequency_mhz": number_or_none(metrics.get("avg_aicore_frequency_mhz")),
                    "max_hbm_bandwidth_bytes_per_s": number_or_none(
                        metrics.get("max_hbm_bandwidth_bytes_per_s")
                    ),
                    "avg_hbm_bandwidth_bytes_per_s": number_or_none(
                        metrics.get("avg_hbm_bandwidth_bytes_per_s")
                    ),
                    "metrics_json": json_compact(metrics),
                    "observation_scope": row.get("observation_scope"),
                    "request_attribution": row.get("request_attribution"),
                }
            )
    return output


def metric_coverage_quality(valid_count: int, expected_count: int) -> str:
    if valid_count <= 0:
        return "unavailable"
    if expected_count > 0 and valid_count == expected_count:
        return "complete"
    return "partial"


def analyze_host_metrics(
    batch: dict[str, Any],
    run_events: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for run_id, data in run_events.items():
        result = batch["run_results"].get(run_id, {})
        samples = [
            event
            for event in data.get("events", [])
            if event.get("source") == "host" and event.get("event_type") == "sample"
        ]
        samples.sort(key=lambda event: integer_or_none(event.get("monotonic_time_ns")) or -1)
        for previous, current in zip(samples, samples[1:]):
            start = integer_or_none(previous.get("monotonic_time_ns"))
            end = integer_or_none(current.get("monotonic_time_ns"))
            dt = ns_delta_seconds(start, end)
            if dt is None or dt <= 0:
                continue
            before = as_dict(previous.get("payload"))
            after = as_dict(current.get("payload"))
            before_stat = as_dict(before.get("proc_stat"))
            after_stat = as_dict(after.get("proc_stat"))
            before_vm = as_dict(before.get("vmstat"))
            after_vm = as_dict(after.get("vmstat"))
            before_disk = aggregate_diskstats(as_dict(before.get("diskstats")))
            after_disk = aggregate_diskstats(as_dict(after.get("diskstats")))
            before_sched = as_dict(before.get("schedstat"))
            after_sched = as_dict(after.get("schedstat"))
            before_numa = as_dict(as_dict(before.get("numa")).get("counters"))
            after_numa = as_dict(as_dict(after.get("numa")).get("counters"))
            cpu_by_core = cpu_utilization_by_core(before_stat, after_stat)
            cpu_iowait_pct = cpu_iowait_percent(before_stat, after_stat)
            meminfo = as_dict(after.get("meminfo"))
            memory_total = number_or_none(meminfo.get("MemTotal"))
            memory_available = number_or_none(meminfo.get("MemAvailable"))
            loadavg = parse_loadavg(after.get("loadavg"))
            pressure = as_dict(after.get("pressure"))
            reset_fields: list[str] = []

            def rate(source_before: dict[str, Any], source_after: dict[str, Any], key: str, scale: float = 1.0) -> float | None:
                value, reset = counter_rate(source_before.get(key), source_after.get(key), dt, scale=scale)
                if reset:
                    reset_fields.append(key)
                return value

            scheduler_delay_ns, scheduler_delay_reset = counter_delta(
                before_sched.get("runqueue_delay_ns"), after_sched.get("runqueue_delay_ns")
            )
            scheduler_timeslices, scheduler_timeslices_reset = counter_delta(
                before_sched.get("timeslices"), after_sched.get("timeslices")
            )
            if scheduler_delay_reset:
                reset_fields.append("schedstat.runqueue_delay_ns")
            if scheduler_timeslices_reset:
                reset_fields.append("schedstat.timeslices")
            disk_read_rate = rate(before_disk, after_disk, "read_bytes") if before_disk and after_disk else None
            disk_write_rate = rate(before_disk, after_disk, "write_bytes") if before_disk and after_disk else None
            output.append(
                {
                    "batch_label": batch["label"],
                    "configured_concurrency": batch["concurrency"],
                    "task_id": result.get("task_id"),
                    "run_id": run_id,
                    "interval_start_mono_ns": start,
                    "interval_end_mono_ns": end,
                    "interval_s": dt,
                    "cpu_core_count": len(cpu_by_core),
                    "cpu_util_pct_mean": statistics.fmean(cpu_by_core.values()) if cpu_by_core else None,
                    "cpu_util_pct_max": max(cpu_by_core.values()) if cpu_by_core else None,
                    "cpu_iowait_pct": cpu_iowait_pct,
                    "cpu_util_pct_by_core_json": json_compact(cpu_by_core),
                    "load1": loadavg.get("load1"),
                    "load5": loadavg.get("load5"),
                    "load15": loadavg.get("load15"),
                    "load1_per_cpu": ratio(loadavg.get("load1"), len(cpu_by_core)),
                    "run_queue": number_or_none(after_stat.get("procs_running")),
                    "blocked_processes": number_or_none(after_stat.get("procs_blocked")),
                    "context_switches_per_s": rate(before_stat, after_stat, "ctxt"),
                    "processes_created_per_s": rate(before_stat, after_stat, "processes"),
                    "minor_faults_per_s": rate(before_vm, after_vm, "pgfault"),
                    "major_faults_per_s": rate(before_vm, after_vm, "pgmajfault"),
                    "swap_in_pages_per_s": rate(before_vm, after_vm, "pswpin"),
                    "swap_out_pages_per_s": rate(before_vm, after_vm, "pswpout"),
                    "paging_read_bytes_per_s": rate(before_vm, after_vm, "pgpgin", scale=1024.0),
                    "paging_write_bytes_per_s": rate(before_vm, after_vm, "pgpgout", scale=1024.0),
                    "scheduler_runqueue_delay_s_per_s": (
                        scheduler_delay_ns / 1_000_000_000 / dt if scheduler_delay_ns is not None else None
                    ),
                    "scheduler_latency_mean_s": (
                        scheduler_delay_ns / 1_000_000_000 / scheduler_timeslices
                        if scheduler_delay_ns is not None and scheduler_timeslices not in {None, 0}
                        else None
                    ),
                    "scheduler_timeslices_per_s": (
                        scheduler_timeslices / dt if scheduler_timeslices is not None else None
                    ),
                    "disk_read_bytes_per_s": disk_read_rate,
                    "disk_write_bytes_per_s": disk_write_rate,
                    "disk_reads_per_s": rate(before_disk, after_disk, "reads_completed") if before_disk and after_disk else None,
                    "disk_writes_per_s": rate(before_disk, after_disk, "writes_completed") if before_disk and after_disk else None,
                    "disk_io_time_ratio": (
                        rate(before_disk, after_disk, "io_time_ms", scale=0.001) if before_disk and after_disk else None
                    ),
                    "numa_local_pages_per_s": rate(before_numa, after_numa, "numa_local") if before_numa and after_numa else None,
                    "numa_remote_pages_per_s": rate(before_numa, after_numa, "numa_other") if before_numa and after_numa else None,
                    "numa_miss_pages_per_s": rate(before_numa, after_numa, "numa_miss") if before_numa and after_numa else None,
                    "memory_total_bytes": memory_total,
                    "memory_available_bytes": memory_available,
                    "memory_used_bytes": (
                        memory_total - memory_available
                        if memory_total is not None and memory_available is not None
                        else None
                    ),
                    "memory_used_ratio": (
                        ratio(memory_total - memory_available, memory_total)
                        if memory_total is not None and memory_available is not None
                        else None
                    ),
                    "cpu_psi_some_avg10": pressure_value(pressure, "cpu", "some", "avg10"),
                    "memory_psi_some_avg10": pressure_value(pressure, "memory", "some", "avg10"),
                    "memory_psi_full_avg10": pressure_value(pressure, "memory", "full", "avg10"),
                    "io_psi_some_avg10": pressure_value(pressure, "io", "some", "avg10"),
                    "io_psi_full_avg10": pressure_value(pressure, "io", "full", "avg10"),
                    "pressure_json": json_compact(pressure),
                    "counter_reset_fields": ";".join(reset_fields) if reset_fields else None,
                    "disk_missing_reason": None if before_disk and after_disk else "proc_diskstats_not_collected",
                    "disk_quality": "whole_device_procfs_may_include_device_mapper_overlap" if before_disk and after_disk else "unavailable",
                    "observation_scope": "shared_host_observation",
                    "request_attribution": "not_request_attributed",
                }
            )
    return output


def parse_loadavg(value: Any) -> dict[str, float]:
    parts = str(value or "").split()
    if len(parts) < 3:
        return {}
    parsed = [number_or_none(item) for item in parts[:3]]
    if any(item is None for item in parsed):
        return {}
    return {"load1": parsed[0], "load5": parsed[1], "load15": parsed[2]}


def pressure_value(pressure: dict[str, Any], resource: str, level: str, field: str) -> float | None:
    return number_or_none(as_dict(as_dict(pressure.get(resource)).get(level)).get(field))


def cpu_utilization_by_core(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float]:
    before_cpus = as_dict(before.get("cpus"))
    after_cpus = as_dict(after.get("cpus"))
    result: dict[str, float] = {}
    for core, after_values in after_cpus.items():
        before_values = before_cpus.get(core)
        if not isinstance(before_values, list) or not isinstance(after_values, list):
            continue
        if len(before_values) < 4 or len(after_values) < 4:
            continue
        deltas = [float(right) - float(left) for left, right in zip(before_values, after_values)]
        if any(value < 0 for value in deltas):
            continue
        total = sum(deltas)
        idle = deltas[3] + (deltas[4] if len(deltas) > 4 else 0.0)
        if total > 0:
            result[str(core)] = max(0.0, min(100.0, (total - idle) / total * 100.0))
    return result


def cpu_iowait_percent(before: dict[str, Any], after: dict[str, Any]) -> float | None:
    left = before.get("cpu_total")
    right = after.get("cpu_total")
    if not isinstance(left, list) or not isinstance(right, list) or len(left) < 5 or len(right) < 5:
        return None
    deltas = [number(right_value) - number(left_value) for left_value, right_value in zip(left, right)]
    if any(value < 0 for value in deltas):
        return None
    total = sum(deltas)
    return deltas[4] / total * 100.0 if total > 0 else None


def aggregate_diskstats(devices: dict[str, Any]) -> dict[str, float]:
    rows = [as_dict(row) for row in devices.values() if as_dict(row).get("is_partition") is not True]
    if not rows:
        return {}
    fields = (
        "read_bytes",
        "write_bytes",
        "reads_completed",
        "writes_completed",
        "io_time_ms",
        "weighted_io_time_ms",
    )
    return {
        field: sum(value for row in rows if (value := number_or_none(row.get(field))) is not None)
        for field in fields
    }


def counter_delta(before: Any, after: Any) -> tuple[float | None, bool]:
    left = number_or_none(before)
    right = number_or_none(after)
    if left is None or right is None:
        return None, False
    if right < left:
        return None, True
    return right - left, False


def counter_rate(before: Any, after: Any, interval_s: float, *, scale: float = 1.0) -> tuple[float | None, bool]:
    left = number_or_none(before)
    right = number_or_none(after)
    if left is None or right is None or interval_s <= 0:
        return None, False
    if right < left:
        return None, True
    return (right - left) * scale / interval_s, False


def build_inventory(
    batch: dict[str, Any],
    run_events: dict[str, dict[str, Any]],
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "batch_label": batch["label"],
        "batch_path": str(batch["path"]),
        "configured_concurrency": batch["concurrency"],
        "manifest_present": batch["manifest_path"].is_file(),
        "results_present": batch["results_path"].is_file(),
        "step_csv_paths": [str(path) for path in batch["step_paths"]],
        "task_result_count": len(batch["results"]),
        "run_count": len(batch["run_results"]),
        "step_row_count": len(batch["step_rows"]),
        "model_request_count": len(requests),
        "tool_row_count": sum(row.get("item_type") == "tool" for row in batch["step_rows"]),
        "run_events_present_count": sum(data.get("events_path").is_file() for data in run_events.values()),
        "invalid_json_lines": sum(int(data.get("invalid_lines") or 0) for data in run_events.values()),
        "p2_request_event_count": sum(
            int(as_dict(data.get("p2_index")).get("event_count") or 0) for data in run_events.values()
        ),
    }


def build_coverage_rows(
    batches: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    host_rows: list[dict[str, Any]],
    device_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fields = {
        "request": (
            requests,
            (
                "p2_candidate_count",
                "p2_e2e_s",
                "p2_queue_s",
                "p2_ttft_s",
                "p2_prefill_s",
                "p2_prefill_to_first_token_s",
                "p2_decode_s",
                "prompt_tokens",
                "cached_prompt_tokens",
                "generation_tokens",
                "tpot_s",
                "client_to_server_proxy_s",
            ),
        ),
        "host_interval": (
            host_rows,
            ("cpu_util_pct_mean", "run_queue", "context_switches_per_s", "memory_available_bytes"),
        ),
        "device_window": (
            device_rows,
            ("hbm_imbalance_pct", "aicore_imbalance_pct"),
        ),
    }
    output: list[dict[str, Any]] = []
    for domain, (rows, names) in fields.items():
        for name in names:
            valid = sum(has_value(row.get(name)) for row in rows)
            output.append(
                {
                    "domain": domain,
                    "field": name,
                    "valid_samples": valid,
                    "total_samples": len(rows),
                    "coverage": valid / len(rows) if rows else None,
                }
            )
    for batch in batches:
        output.append(
            {
                "domain": "batch",
                "field": f"{batch['label']}.step_rows_present",
                "valid_samples": int(bool(batch["step_rows"])),
                "total_samples": 1,
                "coverage": 1.0 if batch["step_rows"] else 0.0,
            }
        )
    return output


def summarize_request_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = (
        "p2_e2e_s",
        "p2_queue_s",
        "p2_ttft_s",
        "p2_prefill_s",
        "p2_prefill_to_first_token_s",
        "p2_decode_s",
        "tpot_s",
        "decode_tokens_per_second",
        "ttft_per_1k_prompt_tokens_s",
        "ttft_per_1k_uncached_prompt_tokens_s",
        "prefill_proxy_tokens_per_second",
        "prompt_tokens",
        "cached_prompt_tokens",
        "generation_tokens",
        "cache_reuse_rate",
        "recomputed_prompt_rate",
        "inflight_model_requests",
        "inflight_uncached_tokens",
        "queue_depth_on_arrival",
        "running_requests_after_schedule",
        "waiting_requests_after_schedule",
    )
    labels = sorted({str(row.get("batch_label")) for row in rows if has_value(row.get("batch_label"))})
    by_batch: dict[str, Any] = {}
    for label in labels:
        items = [row for row in rows if str(row.get("batch_label")) == label]
        by_batch[label] = {
            "request_count": len(items),
            "metrics": {
                name: summarize_numbers(row.get(name) for row in items)
                for name in metric_names
            },
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "by_batch": by_batch,
    }


def summarize_host_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = (
        "cpu_util_pct_mean",
        "cpu_util_pct_max",
        "cpu_iowait_pct",
        "load1_per_cpu",
        "run_queue",
        "blocked_processes",
        "context_switches_per_s",
        "scheduler_runqueue_delay_s_per_s",
        "scheduler_latency_mean_s",
        "scheduler_timeslices_per_s",
        "major_faults_per_s",
        "swap_in_pages_per_s",
        "swap_out_pages_per_s",
        "disk_read_bytes_per_s",
        "disk_write_bytes_per_s",
        "disk_reads_per_s",
        "disk_writes_per_s",
        "disk_io_time_ratio",
        "numa_local_pages_per_s",
        "numa_remote_pages_per_s",
        "numa_miss_pages_per_s",
        "memory_used_ratio",
        "cpu_psi_some_avg10",
        "memory_psi_some_avg10",
        "memory_psi_full_avg10",
        "io_psi_some_avg10",
        "io_psi_full_avg10",
    )
    labels = sorted({str(row.get("batch_label")) for row in rows if has_value(row.get("batch_label"))})
    by_batch: dict[str, Any] = {}
    for label in labels:
        items = [row for row in rows if str(row.get("batch_label")) == label]
        by_batch[label] = {
            "interval_count": len(items),
            "counter_reset_interval_count": sum(has_value(row.get("counter_reset_fields")) for row in items),
            "metrics": {
                name: summarize_numbers(row.get(name) for row in items)
                for name in metric_names
            },
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "by_batch": by_batch,
    }


def summarize_tails(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_batch: dict[str, dict[str, Any]] = {}
    for label in sorted({str(row.get("batch_label")) for row in rows if has_value(row.get("batch_label"))}):
        items = [row for row in rows if str(row.get("batch_label")) == label]
        tails = [row for row in items if row.get("is_tail") is True]
        categories = [
            category
            for row in tails
            for category in str(row.get("categories") or "").split(";")
            if category
        ]
        by_batch[label] = {
            "run_count": len(items),
            "tail_count": len(tails),
            "tail_rate": ratio(len(tails), len(items)),
            "category_counts": value_counts(categories),
        }
    return {"schema_version": SCHEMA_VERSION, "by_batch": by_batch}


def summarize_correlation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_correlation_slice(rows)
    by_batch: dict[str, dict[str, Any]] = {}
    labels = sorted({str(row.get("batch_label")) for row in rows if has_value(row.get("batch_label"))})
    for label in labels:
        by_batch[label] = summarize_correlation_slice(
            [row for row in rows if str(row.get("batch_label")) == label]
        )
    return {"schema_version": SCHEMA_VERSION, **summary, "by_batch": by_batch}


def summarize_correlation_slice(rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected = [row for row in rows if row.get("p2_match_status") != "unmatched_p2"]
    exact = [row for row in expected if row.get("p2_match_status") == "exact_match"]
    lifecycle_evidence = [row for row in expected if row.get("lifecycle_status")]
    lifecycle_complete = [row for row in expected if row.get("lifecycle_status") == "complete"]
    return {
        "expected_p1_model_count": len(expected),
        "exact_match_count": len(exact),
        "exact_match_rate": ratio(len(exact), len(expected)),
        "ordinal_fallback_count": sum(row.get("p2_match_status") == "ordinal_fallback" for row in expected),
        "ambiguous_count": sum(row.get("p2_match_status") == "multiple_candidates" for row in expected),
        "unmatched_p1_count": sum(row.get("p2_match_status") == "unmatched_p1" for row in expected),
        "unmatched_p2_count": sum(row.get("p2_match_status") == "unmatched_p2" for row in rows),
        "lifecycle_expected_count": len(expected),
        "lifecycle_evidence_count": len(lifecycle_evidence),
        "lifecycle_complete_count": len(lifecycle_complete),
        "lifecycle_complete_rate": ratio(len(lifecycle_complete), len(expected)),
        "lifecycle_status_mismatch_count": sum(bool(row.get("lifecycle_status_mismatch")) for row in expected),
        "status_counts": value_counts(str(row.get("p2_match_status")) for row in rows),
    }


def summarize_decomposition(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_candidate[str(row.get("candidate_label"))].append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "candidates": {
            label: {
                "paired_task_count": sum(row.get("pair_status") == "paired" for row in items),
                "missing_baseline_task_count": sum(row.get("pair_status") != "paired" for row in items),
                "run_wall_delta_s": summarize_numbers(row.get("run_wall_delta_s") for row in items),
                "workload_amplification_s": summarize_numbers(row.get("workload_amplification_s") for row in items),
                "system_slowdown_s": summarize_numbers(row.get("system_slowdown_s") for row in items),
                "unexplained_residual_s": summarize_numbers(row.get("unexplained_residual_s") for row in items),
            }
            for label, items in sorted(by_candidate.items())
        },
    }


def build_validation_checks(
    batches: list[dict[str, Any]],
    correlation: dict[str, Any],
    timeline_checks: list[dict[str, Any]],
    *,
    min_p2_match_rate: float | None,
    min_p2_lifecycle_rate: float | None,
) -> dict[str, Any]:
    checks = [
        quality_check("batch_inputs_present", bool(batches), details={"count": len(batches)}),
        quality_check(
            "step_rows_present",
            all(bool(batch["step_rows"]) for batch in batches),
            details={batch["label"]: len(batch["step_rows"]) for batch in batches},
        ),
    ]
    by_batch = as_dict(correlation.get("by_batch"))
    for batch in batches:
        label = str(batch["label"])
        summary = as_dict(by_batch.get(label))
        expected = int(summary.get("expected_p1_model_count") or 0)
        model_row_count = sum(row.get("item_type") == "model" for row in batch["step_rows"])
        checks.extend(
            [
                quality_check(
                    f"{label}.model_rows_present",
                    model_row_count > 0,
                    details={"model_row_count": model_row_count},
                ),
                quality_check(
                    f"{label}.p2_exact_match_rate",
                    expected == 0
                    or (
                        summary.get("exact_match_rate") is not None
                        and (min_p2_match_rate is None or float(summary["exact_match_rate"]) >= min_p2_match_rate)
                    ),
                    details={
                        "expected_p1_model_count": expected,
                        "actual": summary.get("exact_match_rate"),
                        "minimum": min_p2_match_rate,
                    },
                ),
                quality_check(
                    f"{label}.p2_lifecycle_complete_rate",
                    expected == 0
                    or (
                        summary.get("lifecycle_complete_rate") is not None
                        and (min_p2_lifecycle_rate is None or float(summary["lifecycle_complete_rate"]) >= min_p2_lifecycle_rate)
                    ),
                    details={
                        "expected_p1_model_count": expected,
                        "lifecycle_evidence_count": summary.get("lifecycle_evidence_count"),
                        "actual": summary.get("lifecycle_complete_rate"),
                        "minimum": min_p2_lifecycle_rate,
                    },
                ),
                quality_check(
                    f"{label}.p2_lifecycle_status_consistent",
                    int(summary.get("lifecycle_status_mismatch_count") or 0) == 0,
                    details={"mismatch_count": summary.get("lifecycle_status_mismatch_count")},
                ),
                quality_check(
                    f"{label}.no_unmatched_p2_requests",
                    int(summary.get("unmatched_p2_count") or 0) == 0,
                    details={"unmatched_p2_count": summary.get("unmatched_p2_count")},
                    blocking=False,
                ),
            ]
        )
    checks.extend(timeline_checks)
    failures = [check["name"] for check in checks if check.get("blocking") and not check.get("passed")]
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_type": "agentmon.existing_data_analysis",
        "publishability_status": "PUBLISHABLE" if not failures else "UNPUBLISHABLE",
        "blocking_failures": failures,
        "checks": checks,
    }


def build_quality_bundle(
    validation: dict[str, Any],
    coverage: list[dict[str, Any]],
    correlation: dict[str, Any],
) -> dict[str, Any]:
    status = validation["publishability_status"]
    return {
        "metric_definition": {
            "schema_version": SCHEMA_VERSION,
            "observed_or_derived": {
                "p2_*": "observed request lifecycle values copied from step CSV",
                "tpot_s": "derived: decode_s / (generation_tokens - 1)",
                "prefill_proxy_tokens_per_second": "proxy: computed or uncached prompt tokens / first_scheduled-to-first_token",
                "client_to_server_proxy_s": "same-host monotonic proxy; includes client preparation and transport",
                "workload_amplification_s": "product decomposition using baseline per-unit rates",
                "system_slowdown_s": "product decomposition using candidate minus baseline per-unit rates",
                "device_imbalance": "shared service device-window observation, not request attribution",
            },
        },
        "data_quality": {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "coverage": coverage,
            "blocking_failures": validation["blocking_failures"],
        },
        "confidence": {
            "schema_version": SCHEMA_VERSION,
            "p2_exact_match_rate": correlation.get("exact_match_rate"),
            "p2_lifecycle_complete_rate": correlation.get("lifecycle_complete_rate"),
            "p2_by_batch": correlation.get("by_batch"),
            "workload_decomposition": "proxy_additive_when_all_component_metrics_are_present",
            "npu_attribution": "shared_service_observation_not_request_attributed",
        },
        "limitations": {
            "schema_version": SCHEMA_VERSION,
            "limitations": [
                "Existing data cannot recover a true prefill-completed boundary when no event was captured.",
                "Scheduler, NPU, HBM, host, and service KV windows are shared observations.",
                "Device IDs are not treated as tensor-parallel rank IDs.",
                "Legacy runs without /proc/diskstats cannot provide host disk throughput; newer runs expose it when the host permits collection.",
                "Offline decomposition is descriptive and does not prove causality.",
            ],
        },
    }


def write_analysis(analysis: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.chmod(0o700)
    except OSError:
        pass
    write_json(output_dir / "existing_data_inventory.json", analysis["inventory"])
    write_json(
        output_dir / "analysis_manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "analysis_type": analysis["analysis_type"],
            "baseline_label": analysis["baseline_label"],
            "config": analysis["analysis_config"],
            "inputs": [
                {"label": batch["label"], "path": str(batch["path"])}
                for batch in analysis["batches"]
            ],
        },
    )
    write_csv(output_dir / "existing_field_coverage.csv", analysis["coverage"])
    write_text(output_dir / "existing_data_gaps.md", format_data_gaps(analysis))
    write_csv(output_dir / "request_metrics.csv", analysis["request_metrics"])
    write_json(output_dir / "request_metrics_summary.json", analysis["request_metrics_summary"])
    write_csv(output_dir / "p2_correlation_audit.csv", analysis["p2_correlation_audit"])
    write_json(output_dir / "p2_correlation_audit.json", analysis["p2_correlation_audit"])
    write_json(output_dir / "p2_correlation_summary.json", analysis["p2_correlation_summary"])
    write_csv(output_dir / "request_timeline.csv", analysis["request_timeline"])
    write_json(
        output_dir / "timeline_validation.json",
        {
            "schema_version": SCHEMA_VERSION,
            "checks": analysis["timeline_checks"],
            "violation_count": len(analysis["timeline_violations"]),
        },
    )
    write_csv(output_dir / "timeline_violations.csv", analysis["timeline_violations"])
    write_csv(output_dir / "run_summary.csv", analysis["run_summaries"])
    write_csv(output_dir / "paired_task_decomposition.csv", analysis["paired_task_decomposition"])
    write_csv(output_dir / "paired_model_calls.csv", analysis["paired_model_calls"])
    write_csv(output_dir / "inflight_observations.csv", analysis["inflight_observations"])
    write_csv(output_dir / "inflight_threshold_curve.csv", analysis["inflight_thresholds"])
    write_json(output_dir / "inflight_change_point.json", analysis["inflight_summary"])
    write_json(output_dir / "batch_decomposition_summary.json", analysis["batch_decomposition_summary"])
    write_csv(output_dir / "tail_classification.csv", analysis["tail_classification"])
    write_json(output_dir / "tail_summary.json", analysis["tail_summary"])
    write_csv(output_dir / "device_imbalance.csv", analysis["device_imbalance"])
    write_csv(output_dir / "device_metrics_long.csv", analysis["device_metrics_long"])
    write_json(output_dir / "device_imbalance_summary.json", analysis["device_imbalance_summary"])
    write_csv(output_dir / "host_metrics.csv", analysis["host_metrics"])
    write_json(output_dir / "host_metrics_summary.json", analysis["host_metrics_summary"])
    quality = analysis["quality_bundle"]
    for name in ("metric_definition", "data_quality", "confidence", "limitations"):
        write_json(output_dir / f"{name}.json", quality[name])
    write_json(output_dir / "validation_checks.json", analysis["validation_checks"])
    write_text(output_dir / "offline_root_cause_report.md", format_report(analysis))


def format_data_gaps(analysis: dict[str, Any]) -> str:
    lines = ["# Existing Data Gaps", ""]
    for row in analysis["coverage"]:
        coverage = row.get("coverage")
        if coverage is None or coverage < 1.0:
            lines.append(
                f"- `{row['domain']}.{row['field']}`: {row['valid_samples']}/{row['total_samples']} "
                f"({format_percent(coverage)})"
            )
    if len(lines) == 2:
        lines.append("- No incomplete tracked fields.")
    lines.extend(
        [
            "",
            "True prefill completion, communication collectives, rank mapping, and exact request-level NPU attribution "
            "cannot be reconstructed when the original events do not contain them.",
        ]
    )
    return "\n".join(lines) + "\n"


def format_report(analysis: dict[str, Any]) -> str:
    validation = analysis["validation_checks"]
    correlation = analysis["p2_correlation_summary"]
    tail_count = sum(row.get("is_tail") is True for row in analysis["tail_classification"])
    lines = [
        "# AgentMon Existing-Data Root-Cause Report",
        "",
        f"Publication status: **{validation['publishability_status']}**",
        f"Baseline: `{analysis['baseline_label']}`",
        f"Batches: {len(analysis['batches'])}",
        f"Request rows: {len(analysis['request_metrics'])}",
        f"Tail runs: {tail_count}",
        "",
        "## P2 Correlation",
        "",
        f"- Exact match: {correlation['exact_match_count']}/{correlation['expected_p1_model_count']} "
        f"({format_percent(correlation['exact_match_rate'])})",
        f"- Ambiguous: {correlation['ambiguous_count']}",
        f"- Unmatched P1: {correlation['unmatched_p1_count']}",
        f"- Unmatched P2: {correlation['unmatched_p2_count']}",
        f"- Lifecycle complete: {format_percent(correlation['lifecycle_complete_rate'])}",
        "",
        "| Batch | Exact match | Lifecycle complete | Ambiguous | Unmatched P1 | Unmatched P2 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, summary in as_dict(correlation.get("by_batch")).items():
        lines.append(
            f"| {label} | {format_percent(summary.get('exact_match_rate'))} | "
            f"{format_percent(summary.get('lifecycle_complete_rate'))} | "
            f"{summary.get('ambiguous_count', 0)} | {summary.get('unmatched_p1_count', 0)} | "
            f"{summary.get('unmatched_p2_count', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Request Performance",
            "",
            "| Batch | Requests | E2E p50 s | Queue p50 s | TTFT p50 s | TPOT p50 ms | Decode tok/s p50 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for label, summary in as_dict(analysis["request_metrics_summary"].get("by_batch")).items():
        metrics = as_dict(summary.get("metrics"))
        lines.append(
            f"| {label} | {summary.get('request_count', 0)} | "
            f"{format_metric(as_dict(metrics.get('p2_e2e_s')).get('p50'))} | "
            f"{format_metric(as_dict(metrics.get('p2_queue_s')).get('p50'))} | "
            f"{format_metric(as_dict(metrics.get('p2_ttft_s')).get('p50'))} | "
            f"{format_metric(as_dict(metrics.get('tpot_s')).get('p50'), scale=1000.0)} | "
            f"{format_metric(as_dict(metrics.get('decode_tokens_per_second')).get('p50'))} |"
        )
    lines.extend(
        [
            "",
            "## Paired Decomposition",
            "",
            "`workload` is path/token/call amplification at baseline unit cost; `system` is the change in unit cost.",
            "",
            "| Candidate | Paired tasks | Wall delta p50 s | Workload p50 s | System p50 s | Residual p50 s |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for label, summary in as_dict(analysis["batch_decomposition_summary"].get("candidates")).items():
        lines.append(
            f"| {label} | {summary.get('paired_task_count', 0)} | "
            f"{format_metric(as_dict(summary.get('run_wall_delta_s')).get('p50'))} | "
            f"{format_metric(as_dict(summary.get('workload_amplification_s')).get('p50'))} | "
            f"{format_metric(as_dict(summary.get('system_slowdown_s')).get('p50'))} | "
            f"{format_metric(as_dict(summary.get('unexplained_residual_s')).get('p50'))} |"
        )
    lines.extend(["", "## Tail Classification", ""])
    for label, summary in as_dict(analysis["tail_summary"].get("by_batch")).items():
        lines.append(
            f"- `{label}`: {summary.get('tail_count', 0)}/{summary.get('run_count', 0)} "
            f"({format_percent(summary.get('tail_rate'))}); categories="
            f"`{json_compact(summary.get('category_counts', {}))}`"
        )
    lines.extend(["", "## Shared Resource Evidence", ""])
    device_by_batch = as_dict(analysis["device_imbalance_summary"].get("by_batch"))
    host_by_batch = as_dict(analysis["host_metrics_summary"].get("by_batch"))
    for batch in analysis["batches"]:
        label = str(batch["label"])
        device = as_dict(device_by_batch.get(label))
        host = as_dict(host_by_batch.get(label))
        host_metrics = as_dict(host.get("metrics"))
        lines.append(
            f"- `{label}`: HBM imbalance p95="
            f"{format_metric(as_dict(device.get('hbm_imbalance_pct')).get('p95'))}% ; "
            f"AI Core imbalance p95={format_metric(as_dict(device.get('aicore_imbalance_pct')).get('p95'))}% ; "
            f"host CPU p95={format_metric(as_dict(host_metrics.get('cpu_util_pct_mean')).get('p95'))}% ; "
            f"run queue p95={format_metric(as_dict(host_metrics.get('run_queue')).get('p95'))}"
        )
    lines.extend(
        [
            "",
            "## Validation",
        "",
        ]
    )
    for check in validation["checks"]:
        if not check["passed"]:
            severity = "FAIL" if check.get("blocking") else "WARN"
            lines.append(f"- {severity} `{check['name']}`: `{json_compact(check.get('details', {}))}`")
    if not validation["blocking_failures"]:
        lines.append("- All blocking checks passed.")
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "Prefill proxy, shared scheduler/NPU/HBM windows, and additive workload decomposition are diagnostic evidence, "
            "not proof of request-exclusive causality.",
        ]
    )
    return "\n".join(lines) + "\n"


def timeline_check(
    batch: dict[str, Any], name: str, passed: bool, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return quality_check(f"{batch['label']}.{name}", passed, details=details)


def quality_check(
    name: str,
    passed: bool,
    *,
    details: dict[str, Any] | None = None,
    blocking: bool = True,
) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "blocking": blocking, "details": details or {}}


def timeline_violation(batch: dict[str, Any], violation_type: str, **details: Any) -> dict[str, Any]:
    return {"batch_label": batch["label"], "violation_type": violation_type, **details}


def step_run_bounds(rows: list[dict[str, str]], run_id: str) -> tuple[int | None, int | None]:
    starts = [
        value
        for row in rows
        if row.get("run_id") == run_id
        and (value := integer_or_none(row.get("item_start_mono_ns"))) is not None
    ]
    ends = [
        value
        for row in rows
        if row.get("run_id") == run_id
        and (value := integer_or_none(row.get("item_end_mono_ns"))) is not None
    ]
    return (min(starts) if starts else None, max(ends) if ends else None)


def summarize_numbers(values: Iterable[Any]) -> dict[str, Any]:
    cleaned = [value for item in values if (value := number_or_none(item)) is not None]
    return {
        "count": len(cleaned),
        "mean": statistics.fmean(cleaned) if cleaned else None,
        "p50": percentile(cleaned, 50),
        "p95": percentile(cleaned, 95),
        "max": max(cleaned) if cleaned else None,
    }


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


def percentile_rank(values: Iterable[float], value: float | None) -> float | None:
    cleaned = sorted(item for item in values if math.isfinite(item))
    if value is None or not cleaned:
        return None
    below = sum(item < value for item in cleaned)
    equal = sum(item == value for item in cleaned)
    return (below + 0.5 * equal) / len(cleaned) * 100.0


def request_order(row: dict[str, Any]) -> tuple[int, int]:
    return (integer_or_none(row.get("step_index")) or 0, integer_or_none(row.get("item_index")) or 0)


def subtract_if_present(total: Any, *parts: Any) -> float | None:
    total_value = number_or_none(total)
    values = [number_or_none(part) for part in parts]
    if total_value is None or any(value is None for value in values):
        return None
    return total_value - sum(value for value in values if value is not None)


def delta(after: Any, before: Any) -> float | None:
    left = number_or_none(before)
    right = number_or_none(after)
    return right - left if left is not None and right is not None else None


def sum_if_complete(values: Iterable[Any]) -> float | None:
    converted = [number_or_none(value) for value in values]
    if not converted or any(value is None for value in converted):
        return None
    return sum(value for value in converted if value is not None)


def sum_if_any(values: Iterable[Any]) -> float | None:
    converted = [number_or_none(value) for value in values]
    present = [value for value in converted if value is not None]
    return sum(present) if present else None


def first_present(rows: list[dict[str, Any]], field: str) -> Any:
    for row in rows:
        if has_value(row.get(field)):
            return row[field]
    return None


def first_number(*values: Any) -> float | None:
    for value in values:
        converted = number_or_none(value)
        if converted is not None:
            return converted
    return None


def first_integer(*values: Any) -> int | None:
    for value in values:
        converted = integer_or_none(value)
        if converted is not None:
            return converted
    return None


def first_text(*values: Any) -> str | None:
    for value in values:
        converted = text_or_none(value)
        if converted is not None:
            return converted
    return None


def ratio(numerator: Any, denominator: Any) -> float | None:
    left = number_or_none(numerator)
    right = number_or_none(denominator)
    if left is None or right is None or right <= 0:
        return None
    return left / right


def ns_delta_seconds(start: Any, end: Any) -> float | None:
    left = integer_or_none(start)
    right = integer_or_none(end)
    if left is None or right is None:
        return None
    return (right - left) / 1_000_000_000


def number(value: Any) -> float:
    return number_or_none(value) or 0.0


def number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = str(value).strip()
        if text.lower() in MISSING:
            return None
        try:
            result = float(text)
        except ValueError:
            return None
    return result if math.isfinite(result) else None


def integer_or_none(value: Any) -> int | None:
    number_value = number_or_none(value)
    return int(number_value) if number_value is not None else None


def integer_if_whole(value: float | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value.is_integer() else value


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in MISSING else text


def has_value(value: Any) -> bool:
    return text_or_none(value) is not None


def is_true(value: Any) -> bool:
    return parse_bool(value) is True


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = text_or_none(value)
    if text is None:
        return None
    if text.lower() in {"1", "true", "yes", "y"}:
        return True
    if text.lower() in {"0", "false", "no", "n"}:
        return False
    return None


def is_nonzero(value: Any) -> bool:
    converted = number_or_none(value)
    return converted is not None and converted != 0


def nonempty_values(rows: list[dict[str, str]], field: str) -> set[str]:
    return {str(row[field]) for row in rows if has_value(row.get(field))}


def value_counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_set_dict(value: Any) -> dict[str, set[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, set[str]] = {}
    for key, item in value.items():
        if isinstance(item, set):
            result[str(key)] = item
        elif isinstance(item, (list, tuple)):
            result[str(key)] = {str(entry) for entry in item}
    return result


def parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = text_or_none(value)
    if text is None:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def format_percent(value: Any) -> str:
    number_value = number_or_none(value)
    return "N/A" if number_value is None else f"{number_value * 100:.2f}%"


def format_metric(value: Any, *, scale: float = 1.0) -> str:
    number_value = number_or_none(value)
    return "N/A" if number_value is None else f"{number_value * scale:.4f}"


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    invalid = 0
    if not path.is_file():
        return {"events": events, "invalid_lines": invalid}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if isinstance(value, dict):
                events.append(value)
    return {"events": events, "invalid_lines": invalid}


def read_delimited(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if fields:
            writer.writeheader()
            writer.writerows(rows)
    chmod_private(path)


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    chmod_private(path)


def chmod_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
