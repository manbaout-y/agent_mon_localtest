from __future__ import annotations

from typing import Any

from agentmon.adapters.inference.vllm_018.p2_index import is_p2_request_or_keyed_event_type
from agentmon.step_sequence.utils import (
    as_dict,
    count_values,
    first_value,
    integer_or_none,
    max_present,
    mean_present,
    min_present,
    ns_delta,
    ns_to_s,
    number_or_none,
    ratio_or_none,
    sort_time,
    sum_numeric,
    sum_present,
)
from agentmon.step_sequence.utils import *  # noqa: F403 - shared internal resource helpers

def build_resource_context(
    events: list[dict[str, Any]],
    p2_events: list[dict[str, Any]],
    *,
    ascend_devices: str | None,
) -> dict[str, Any]:
    explicit_devices = parse_device_ids(ascend_devices)
    inferred_devices = infer_p2_visible_devices(p2_events)
    return {
        "proc_events": sorted(
            [event for event in events if event.get("source") == "proc" and event.get("event_type") == "sample"],
            key=lambda event: integer_or_none(event.get("monotonic_time_ns")) or -1,
        ),
        "ascend_events": sorted(
            [event for event in events if event.get("source") == "ascend" and event.get("event_type") == "sample"],
            key=lambda event: integer_or_none(event.get("monotonic_time_ns")) or -1,
        ),
        "scheduler_events": sorted(
            [
                event
                for event in p2_events
                if event.get("source") == "vllm_p2"
                and event.get("event_type")
                in {
                    "vllm.scheduler.snapshot",
                    "vllm.scheduler.iteration.scheduled",
                    "vllm.scheduler.iteration.completed",
                }
            ],
            key=lambda event: integer_or_none(event.get("monotonic_time_ns")) or -1,
        ),
        "ascend_target_devices": explicit_devices or inferred_devices,
        "ascend_target_device_source": "cli" if explicit_devices else ("p2_visible_devices" if inferred_devices else "unavailable"),
        "npu_observation_scope": "shared_service_observation",
        "npu_request_attribution": "not_request_attributed",
    }


def scheduler_window_summary(
    scheduler_events: list[dict[str, Any]],
    start_ns: Any,
    end_ns: Any,
) -> dict[str, Any]:
    samples = events_in_window(scheduler_events, start_ns, end_ns)
    fields = ("running_requests", "waiting_requests", "scheduled_requests", "scheduled_tokens")
    result: dict[str, Any] = {
        "quality": "sampled_window" if samples else "missing",
        "sample_count": len(samples),
        "observation_scope": "shared_service_observation",
        "request_attribution": "not_request_attributed",
    }
    for field in fields:
        values = [
            value
            for event in samples
            if (value := scheduler_field_value(as_dict(event.get("payload")), field)) is not None
        ]
        result[f"{field}_max"] = max(values) if values else None
        result[f"{field}_mean"] = sum(values) / len(values) if values else None
    scheduled_iterations = [
        as_dict(event.get("payload"))
        for event in samples
        if event.get("event_type") == "vllm.scheduler.iteration.scheduled"
    ]
    completed_iterations = [
        as_dict(event.get("payload"))
        for event in samples
        if event.get("event_type") == "vllm.scheduler.iteration.completed"
    ]
    result.update(
        {
            "iteration_scheduled_count": len(scheduled_iterations),
            "iteration_completed_count": len(completed_iterations),
            "mixed_prefill_decode_iteration_count": sum(
                1 for payload in scheduled_iterations if payload.get("mixed_prefill_decode_batch") is True
            ),
            "continuous_batching_iteration_count": sum(
                1 for payload in scheduled_iterations if payload.get("continuous_batching_observed") is True
            ),
            "prefill_request_total": sum_present(
                payload.get("prefill_request_count") for payload in scheduled_iterations
            ),
            "decode_request_total": sum_present(
                payload.get("decode_request_count") for payload in scheduled_iterations
            ),
            "batch_request_count_max": max_present(
                payload.get("batch_request_count") for payload in scheduled_iterations
            ),
            "request_attribution": "request_ids_listed_not_resource_exclusive"
            if scheduled_iterations
            else "not_request_attributed",
        }
    )
    return result


def scheduler_field_value(payload: dict[str, Any], field: str) -> float | None:
    aliases = {
        "running_requests": ("running_requests", "running_requests_after_schedule"),
        "waiting_requests": ("waiting_requests", "waiting_requests_after_schedule"),
        "scheduled_requests": ("scheduled_requests", "batch_request_count"),
        "scheduled_tokens": ("scheduled_tokens",),
    }
    for key in aliases.get(field, (field,)):
        value = number_or_none(payload.get(key))
        if value is not None:
            return value
    return None


def proc_window_summary(
    proc_events: list[dict[str, Any]],
    start_ns: Any,
    end_ns: Any,
    *,
    role: str | None = None,
    pid: int | None = None,
    pgid: int | None = None,
) -> dict[str, Any]:
    samples = events_in_window(proc_events, start_ns, end_ns)
    if not samples:
        return {"quality": "missing", "sample_count": 0}

    sample_rows: list[dict[str, Any]] = []
    by_identity: dict[tuple[int, int | None], list[dict[str, Any]]] = {}
    clk_tck = None
    for event in samples:
        payload = as_dict(event.get("payload"))
        aggregate = as_dict(payload.get("aggregate"))
        clk_tck = integer_or_none(aggregate.get("clk_tck")) or clk_tck
        processes = []
        for process in payload.get("processes") or []:
            if not isinstance(process, dict) or not proc_process_matches(process, role=role, pid=pid, pgid=pgid):
                continue
            processes.append(process)
            proc_pid = integer_or_none(process.get("pid"))
            if proc_pid is not None:
                identity = (proc_pid, integer_or_none(process.get("starttime_ticks")))
                process_row = dict(process)
                process_row["_sample_mono_ns"] = integer_or_none(event.get("monotonic_time_ns"))
                by_identity.setdefault(identity, []).append(process_row)

        rss = sum_numeric(process.get("rss_bytes") for process in processes)
        pss = sum_numeric(process.get("pss_bytes") for process in processes)
        private_bytes = sum_numeric(process.get("private_bytes") for process in processes)
        sample_rows.append(
            {
                "monotonic_time_ns": integer_or_none(event.get("monotonic_time_ns")),
                "process_count": len(processes),
                "rss_bytes": rss,
                "pss_bytes": pss,
                "private_bytes": private_bytes,
            }
        )

    clk = clk_tck or 100
    cpu_ticks = 0
    read_delta = 0
    write_delta = 0
    read_found = False
    write_found = False
    for rows in by_identity.values():
        rows.sort(key=lambda row: integer_or_none(row.get("_sample_mono_ns")) or -1)
        first = rows[0]
        last = rows[-1]
        cpu_ticks += max(
            0,
            (integer_or_none(last.get("utime_ticks")) or 0)
            + (integer_or_none(last.get("stime_ticks")) or 0)
            - (integer_or_none(first.get("utime_ticks")) or 0)
            - (integer_or_none(first.get("stime_ticks")) or 0),
        )
        read_start = integer_or_none(first.get("read_bytes"))
        read_end = integer_or_none(last.get("read_bytes"))
        if read_start is not None and read_end is not None:
            read_delta += max(0, read_end - read_start)
            read_found = True
        write_start = integer_or_none(first.get("write_bytes"))
        write_end = integer_or_none(last.get("write_bytes"))
        if write_start is not None and write_end is not None:
            write_delta += max(0, write_end - write_start)
            write_found = True

    cpu_time_s = cpu_ticks / clk if clk > 0 else None
    window_s = window_duration_s(start_ns, end_ns, samples)
    cpu_wall_ratio = ratio_or_none(cpu_time_s, window_s)
    process_count_max = max_present(row.get("process_count") for row in sample_rows)
    process_count_last = sample_rows[-1].get("process_count") if sample_rows else None
    return {
        "quality": "sampled_window" if len(samples) >= 2 else "single_sample",
        "sample_count": len(samples),
        "process_count_max": integer_or_none(process_count_max),
        "process_count_last": process_count_last,
        "cpu_time_s": cpu_time_s,
        "cpu_wall_ratio": cpu_wall_ratio,
        "avg_cpu_percent": cpu_wall_ratio * 100 if cpu_wall_ratio is not None else None,
        "peak_rss_bytes": max_present(row.get("rss_bytes") for row in sample_rows),
        "avg_rss_bytes": mean_present(row.get("rss_bytes") for row in sample_rows),
        "peak_pss_bytes": max_present(row.get("pss_bytes") for row in sample_rows),
        "avg_pss_bytes": mean_present(row.get("pss_bytes") for row in sample_rows),
        "peak_private_bytes": max_present(row.get("private_bytes") for row in sample_rows),
        "avg_private_bytes": mean_present(row.get("private_bytes") for row in sample_rows),
        "read_bytes_delta": read_delta if read_found else None,
        "write_bytes_delta": write_delta if write_found else None,
    }


def ascend_window_summary(
    ascend_events: list[dict[str, Any]],
    start_ns: Any,
    end_ns: Any,
    *,
    target_devices: set[str] | None,
) -> dict[str, Any]:
    samples = events_in_window(ascend_events, start_ns, end_ns)
    if not samples:
        return {"quality": "missing", "sample_count": 0, "target_attribution_status": "unavailable"}

    host_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    parser_confidences: list[float] = []
    collected_devices: set[str] = set()
    per_device_samples: dict[str, list[dict[str, Any]]] = {}
    for event in samples:
        payload = as_dict(event.get("payload"))
        confidence = number_or_none(payload.get("parser_confidence"))
        if confidence is not None:
            parser_confidences.append(float(confidence))
        devices = [device for device in payload.get("devices") or [] if isinstance(device, dict)]
        for device in devices:
            device_id = device_identity(device)
            if device_id is not None:
                collected_devices.add(device_id)
                per_device_samples.setdefault(device_id, []).append(device)
        host_rows.append(device_sample_summary(devices))
        selected = [device for device in devices if target_devices and device_identity(device) in target_devices]
        target_rows.append(device_sample_summary(selected))

    has_target = bool(target_devices)
    target_has_metrics = any(row.get("hbm_used_bytes") is not None or row.get("aicore_util_pct") is not None for row in target_rows)
    return {
        "quality": "sampled_window" if len(samples) >= 2 else "single_sample",
        "sample_count": len(samples),
        "parser_confidence_mean": sum(parser_confidences) / len(parser_confidences) if parser_confidences else None,
        "collected_devices": sorted(collected_devices),
        "target_devices": sorted(target_devices) if target_devices else None,
        "target_attribution_status": "complete" if has_target and target_has_metrics else "unavailable",
        "observation_scope": "shared_service_observation",
        "request_attribution": "not_request_attributed",
        "device_rank_mapping_quality": "unavailable",
        "device_metrics": summarize_per_device_samples(per_device_samples, target_devices),
        "target_peak_hbm_used_bytes": max_present(row.get("hbm_used_bytes") for row in target_rows) if has_target else None,
        "target_avg_hbm_used_bytes": mean_present(row.get("hbm_used_bytes") for row in target_rows) if has_target else None,
        "target_max_aicore_util_pct": max_present(row.get("aicore_util_pct") for row in target_rows) if has_target else None,
        "target_avg_aicore_util_pct": mean_present(row.get("aicore_util_pct") for row in target_rows) if has_target else None,
        "target_max_temperature_c": max_present(row.get("temperature_c") for row in target_rows) if has_target else None,
        "target_avg_power_w": mean_present(row.get("power_w") for row in target_rows) if has_target else None,
        "target_min_aicore_frequency_mhz": min_present(row.get("aicore_frequency_mhz") for row in target_rows) if has_target else None,
        "target_max_hbm_bandwidth_bytes_per_s": max_present(row.get("hbm_bandwidth_bytes_per_s") for row in target_rows) if has_target else None,
        "host_peak_hbm_used_bytes": max_present(row.get("hbm_used_bytes") for row in host_rows),
        "host_avg_hbm_used_bytes": mean_present(row.get("hbm_used_bytes") for row in host_rows),
        "host_max_aicore_util_pct": max_present(row.get("aicore_util_pct") for row in host_rows),
        "host_avg_aicore_util_pct": mean_present(row.get("aicore_util_pct") for row in host_rows),
        "host_max_temperature_c": max_present(row.get("temperature_c") for row in host_rows),
        "host_avg_power_w": mean_present(row.get("power_w") for row in host_rows),
        "host_min_aicore_frequency_mhz": min_present(row.get("aicore_frequency_mhz") for row in host_rows),
        "host_max_hbm_bandwidth_bytes_per_s": max_present(row.get("hbm_bandwidth_bytes_per_s") for row in host_rows),
    }


def summarize_per_device_samples(
    samples: dict[str, list[dict[str, Any]]],
    target_devices: set[str] | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for device_id, rows in sorted(samples.items()):
        hbm = [number for row in rows if (number := number_or_none(row.get("hbm_used_bytes"))) is not None]
        util = [number for row in rows if (number := number_or_none(row.get("aicore_util_pct"))) is not None]
        temperature = [number for row in rows if (number := number_or_none(row.get("temperature_c"))) is not None]
        power = [number for row in rows if (number := number_or_none(row.get("power_w"))) is not None]
        frequency = [
            number
            for row in rows
            if (number := number_or_none(first_value(row.get("aicore_frequency_mhz"), row.get("frequency_mhz"))))
            is not None
        ]
        bandwidth = [
            number
            for row in rows
            if (number := number_or_none(row.get("hbm_bandwidth_bytes_per_s"))) is not None
        ]
        result[device_id] = {
            "is_target_device": device_id in target_devices if target_devices else None,
            "sample_count": len(rows),
            "peak_hbm_used_bytes": max(hbm) if hbm else None,
            "avg_hbm_used_bytes": sum(hbm) / len(hbm) if hbm else None,
            "max_aicore_util_pct": max(util) if util else None,
            "avg_aicore_util_pct": sum(util) / len(util) if util else None,
            "max_temperature_c": max(temperature) if temperature else None,
            "avg_temperature_c": sum(temperature) / len(temperature) if temperature else None,
            "max_power_w": max(power) if power else None,
            "avg_power_w": sum(power) / len(power) if power else None,
            "min_aicore_frequency_mhz": min(frequency) if frequency else None,
            "avg_aicore_frequency_mhz": sum(frequency) / len(frequency) if frequency else None,
            "max_hbm_bandwidth_bytes_per_s": max(bandwidth) if bandwidth else None,
            "avg_hbm_bandwidth_bytes_per_s": sum(bandwidth) / len(bandwidth) if bandwidth else None,
            "rank": None,
            "rank_mapping_quality": "unavailable",
        }
    return result


def events_in_window(events: list[dict[str, Any]], start_ns: Any, end_ns: Any) -> list[dict[str, Any]]:
    start = integer_or_none(start_ns)
    end = integer_or_none(end_ns)
    selected: list[dict[str, Any]] = []
    for event in events:
        mono = integer_or_none(event.get("monotonic_time_ns"))
        if mono is None:
            continue
        if start is not None and mono < start:
            continue
        if end is not None and mono > end:
            continue
        selected.append(event)
    return selected


def proc_process_matches(
    process: dict[str, Any],
    *,
    role: str | None,
    pid: int | None,
    pgid: int | None,
) -> bool:
    if pid is not None and integer_or_none(process.get("pid")) == pid:
        return True
    if pgid is not None and integer_or_none(process.get("pgid")) == pgid:
        return True
    if pid is not None or pgid is not None:
        return False
    return role is None or process.get("role") == role


def device_sample_summary(devices: list[dict[str, Any]]) -> dict[str, Any]:
    hbm_values = [number_or_none(device.get("hbm_used_bytes")) for device in devices]
    hbm_clean = [value for value in hbm_values if value is not None]
    util_values = [number_or_none(device.get("aicore_util_pct")) for device in devices]
    util_clean = [value for value in util_values if value is not None]
    temperatures = [number_or_none(device.get("temperature_c")) for device in devices]
    temperature_clean = [value for value in temperatures if value is not None]
    powers = [number_or_none(device.get("power_w")) for device in devices]
    power_clean = [value for value in powers if value is not None]
    frequencies = [
        number_or_none(first_value(device.get("aicore_frequency_mhz"), device.get("frequency_mhz")))
        for device in devices
    ]
    frequency_clean = [value for value in frequencies if value is not None]
    bandwidths = [number_or_none(device.get("hbm_bandwidth_bytes_per_s")) for device in devices]
    bandwidth_clean = [value for value in bandwidths if value is not None]
    return {
        "hbm_used_bytes": sum(hbm_clean) if hbm_clean else None,
        "aicore_util_pct": max(util_clean) if util_clean else None,
        "temperature_c": max(temperature_clean) if temperature_clean else None,
        "power_w": sum(power_clean) if power_clean else None,
        "aicore_frequency_mhz": min(frequency_clean) if frequency_clean else None,
        "hbm_bandwidth_bytes_per_s": sum(bandwidth_clean) if bandwidth_clean else None,
    }


def device_identity(device: dict[str, Any]) -> str | None:
    for key in ("device_id", "npu_id", "id", "chip_id"):
        value = device.get(key)
        if value not in {None, ""}:
            return str(value)
    return None


def parse_device_ids(value: str | None) -> set[str] | None:
    if not value:
        return None
    devices = {part.strip() for part in value.split(",") if part.strip()}
    return devices or None


def infer_p2_visible_devices(events: list[dict[str, Any]]) -> set[str] | None:
    for event in events:
        if event.get("source") != "vllm_p2" or event.get("event_type") != "vllm.adapter.capability":
            continue
        payload = as_dict(event.get("payload"))
        for key in ("visible_devices", "target_devices", "device_ids", "ascend_devices"):
            devices = normalize_device_list(payload.get(key))
            if devices:
                return devices
    return None


def normalize_device_list(value: Any) -> set[str] | None:
    if isinstance(value, str):
        return parse_device_ids(value)
    if isinstance(value, (list, tuple)):
        devices = {str(item) for item in value if item not in {None, ""}}
        return devices or None
    return None


def window_duration_s(start_ns: Any, end_ns: Any, events: list[dict[str, Any]]) -> float | None:
    explicit = ns_to_s(ns_delta(start_ns, end_ns))
    if explicit is not None and explicit > 0:
        return explicit
    times = [integer_or_none(event.get("monotonic_time_ns")) for event in events]
    clean = [time_ns for time_ns in times if time_ns is not None]
    if len(clean) >= 2:
        return ns_to_s(max(clean) - min(clean))
    return None
