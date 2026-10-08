from __future__ import annotations

from pathlib import Path
import math
import os
import re
from typing import Any, Sequence


VISIBLE_DEVICE_ENV_KEYS = ("ASCEND_RT_VISIBLE_DEVICES", "ASCEND_VISIBLE_DEVICES")


def summarize_ascend_devices(
    ascend_report: dict[str, Any],
    *,
    target_devices: Sequence[str] | None = None,
    target_device_source: str = "unavailable",
    visible_devices_raw: Any = None,
    mapping_quality: str | None = None,
) -> dict[str, Any]:
    devices = as_dict(ascend_report.get("devices"))
    collected_devices = sorted(devices, key=device_sort_key)
    host_peak_hbm = peak_device_metric(devices, "peak_hbm_used_bytes")
    host_max_aicore = peak_device_metric(devices, "max_aicore_util_pct")
    normalized_targets = normalize_device_ids(target_devices)
    if normalized_targets:
        target_rows = {device_id: devices[device_id] for device_id in normalized_targets if device_id in devices}
        missing_target_devices = [device_id for device_id in normalized_targets if device_id not in devices]
        target_peak_hbm = peak_device_metric(target_rows, "peak_hbm_used_bytes")
        target_max_aicore = peak_device_metric(target_rows, "max_aicore_util_pct")
        if not target_rows:
            target_status = "unavailable"
        elif missing_target_devices or target_peak_hbm is None or target_max_aicore is None:
            target_status = "partial"
        else:
            target_status = "complete"
    else:
        missing_target_devices = []
        target_peak_hbm = None
        target_max_aicore = None
        target_status = "unavailable"
    return {
        "collection_scope": "host",
        "collected_devices": collected_devices,
        "visible_devices_raw": visible_devices_raw,
        "target_devices": normalized_targets,
        "target_device_source": target_device_source if normalized_targets else "unavailable",
        "target_device_mapping_quality": mapping_quality or ("unavailable" if not normalized_targets else target_device_source),
        "target_attribution_status": target_status,
        "missing_target_devices": missing_target_devices,
        "host_peak_hbm_used_bytes": host_peak_hbm,
        "host_max_aicore_util_pct": host_max_aicore,
        "target_peak_hbm_used_bytes": target_peak_hbm,
        "target_max_aicore_util_pct": target_max_aicore,
        "hbm_quality": ascend_report.get("hbm_quality"),
        "aicore_quality": ascend_report.get("aicore_quality"),
        "parser_confidence": ascend_report.get("parser_confidence"),
    }


def resolve_ascend_target_devices(
    *,
    explicit_devices: Any = None,
    p2_events: Sequence[dict[str, Any]] | None = None,
    proc_root: str | Path = "/proc",
) -> dict[str, Any]:
    explicit = parse_device_list(explicit_devices)
    if explicit is not None:
        return target_resolution(explicit, source="explicit", raw=explicit_devices, mapping_quality="explicit")

    metadata = target_devices_from_p2_metadata(p2_events or [])
    if metadata is not None:
        return metadata

    environ = target_devices_from_proc_environ(p2_events or [], proc_root=proc_root)
    if environ is not None:
        return environ

    return target_resolution(None, source="unavailable", raw=None, mapping_quality="unavailable")


def target_devices_from_p2_metadata(events: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(list(events)):
        if event.get("source") != "vllm_p2" or event.get("event_type") != "vllm.adapter.capability":
            continue
        payload = as_dict(event.get("payload"))
        candidates = [payload, as_dict(payload.get("details"))]
        for item in candidates:
            for key in ("visible_devices", "visible_device_ids"):
                devices = parse_device_list(item.get(key))
                if devices is not None:
                    return target_resolution(
                        devices,
                        source="service_metadata",
                        raw=item.get(key),
                        mapping_quality="service_metadata",
                    )
            for env_key in VISIBLE_DEVICE_ENV_KEYS:
                devices = parse_device_list(item.get(env_key))
                if devices is not None:
                    return target_resolution(
                        devices,
                        source="service_metadata",
                        raw=item.get(env_key),
                        mapping_quality="service_metadata",
                    )
    return None


def target_devices_from_proc_environ(
    events: Sequence[dict[str, Any]],
    *,
    proc_root: str | Path = "/proc",
) -> dict[str, Any] | None:
    seen: set[int] = set()
    for event in reversed(list(events)):
        payload = as_dict(event.get("payload"))
        pid = integer_or_none(payload.get("source_pid"))
        starttime = integer_or_none(payload.get("source_starttime_ticks"))
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        env = read_proc_environ(pid, expected_starttime_ticks=starttime, proc_root=proc_root)
        if not env:
            continue
        for env_key in VISIBLE_DEVICE_ENV_KEYS:
            devices = parse_device_list(env.get(env_key))
            if devices is not None:
                return target_resolution(
                    devices,
                    source="proc_environ",
                    raw=env.get(env_key),
                    mapping_quality="proc_environ",
                )
    return None


def read_proc_environ(
    pid: int,
    *,
    expected_starttime_ticks: int | None,
    proc_root: str | Path = "/proc",
) -> dict[str, str] | None:
    root = Path(proc_root)
    stat_path = root / str(pid) / "stat"
    environ_path = root / str(pid) / "environ"
    try:
        if expected_starttime_ticks is not None:
            actual_starttime = proc_starttime_ticks(stat_path.read_text(encoding="utf-8", errors="replace"))
            if actual_starttime != expected_starttime_ticks:
                return None
        raw = environ_path.read_bytes()
    except OSError:
        return None
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        env[key.decode("utf-8", errors="replace")] = value.decode("utf-8", errors="replace")
    return env


def proc_starttime_ticks(stat_text: str) -> int | None:
    end = stat_text.rfind(")")
    if end == -1:
        return None
    fields = stat_text[end + 2 :].split()
    if len(fields) < 20:
        return None
    return integer_or_none(fields[19])


def parse_device_list(value: Any) -> list[str] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        if value.strip().lower() in {"all", "none", "void", "null"}:
            return None
        parts = [part.strip() for part in re.split(r"[,\s]+", value) if part.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        return None
    result: list[str] = []
    for part in parts:
        if part in {"-1", "NoDevFiles"}:
            return None
        if not re.fullmatch(r"\d+", part):
            continue
        if part not in result:
            result.append(part)
    return result or None


def normalize_device_ids(devices: Sequence[str] | None) -> list[str]:
    if not devices:
        return []
    result: list[str] = []
    for device in devices:
        value = str(device).strip()
        if value and value not in result:
            result.append(value)
    return result


def target_resolution(
    devices: Sequence[str] | None,
    *,
    source: str,
    raw: Any,
    mapping_quality: str,
) -> dict[str, Any]:
    return {
        "devices": normalize_device_ids(devices),
        "source": source,
        "raw": raw,
        "mapping_quality": mapping_quality,
    }


def peak_device_metric(devices: dict[str, Any], key: str) -> float | int | None:
    values: list[float | int] = []
    for device in devices.values():
        if not isinstance(device, dict):
            continue
        value = numeric_or_none(device.get(key))
        if value is not None:
            values.append(value)
    return max(values) if values else None


def visible_devices_from_env(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = env if env is not None else os.environ
    for key in VISIBLE_DEVICE_ENV_KEYS:
        devices = parse_device_list(env.get(key))
        if devices is not None:
            return {"visible_devices": devices, "visible_devices_source": key}
    return {"visible_devices": None, "visible_devices_source": None}


def device_sort_key(value: str) -> tuple[int, Any]:
    if str(value).isdigit():
        return (0, int(value))
    return (1, str(value))


def numeric_or_none(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    return None


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
