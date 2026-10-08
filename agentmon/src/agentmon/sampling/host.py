from __future__ import annotations

from pathlib import Path

from agentmon.sampling.base import PeriodicSampler
from agentmon.storage.event_queue import EventSink


class HostSampler(PeriodicSampler):
    source = "host"

    def __init__(self, sink: EventSink, interval_seconds: float = 1.0) -> None:
        super().__init__(sink, interval_seconds)

    def sample(self) -> None:
        vmstat = read_key_value_int("/proc/vmstat")
        self.sink.emit(
            "host",
            "sample",
            "sampled",
            payload={
                "proc_stat": read_proc_stat(),
                "loadavg": read_text("/proc/loadavg"),
                "meminfo": read_key_value_kb("/proc/meminfo"),
                "vmstat": vmstat,
                "diskstats": read_diskstats("/proc/diskstats"),
                "schedstat": read_schedstat("/proc/schedstat"),
                "numa": read_numa_metrics(vmstat),
                "pressure": {
                    "cpu": parse_pressure(read_text("/proc/pressure/cpu")),
                    "memory": parse_pressure(read_text("/proc/pressure/memory")),
                    "io": parse_pressure(read_text("/proc/pressure/io")),
                },
            },
        )


def read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None


def read_proc_stat() -> dict[str, object]:
    text = read_text("/proc/stat")
    if text is None:
        return {}
    result: dict[str, object] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu":
            result["cpu_total"] = [int(value) for value in parts[1:]]
        elif parts[0].startswith("cpu") and parts[0][3:].isdigit():
            result.setdefault("cpus", {})[parts[0]] = [int(value) for value in parts[1:]]
        elif parts[0] in {"ctxt", "btime", "processes", "procs_running", "procs_blocked"} and len(parts) > 1:
            result[parts[0]] = int(parts[1])
    return result


def read_key_value_kb(path: str) -> dict[str, int]:
    result: dict[str, int] = {}
    text = read_text(path)
    if text is None:
        return result
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parts = value.strip().split()
        if not parts:
            continue
        try:
            number = int(parts[0])
        except ValueError:
            continue
        result[key] = number * 1024 if len(parts) > 1 and parts[1] == "kB" else number
    return result


def read_key_value_int(path: str) -> dict[str, int]:
    result: dict[str, int] = {}
    text = read_text(path)
    if text is None:
        return result
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                result[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return result


def parse_pressure(text: str | None) -> dict[str, dict[str, float | int]]:
    if text is None:
        return {}
    result: dict[str, dict[str, float | int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        row: dict[str, float | int] = {}
        for item in parts[1:]:
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            try:
                row[key] = int(value) if key == "total" else float(value)
            except ValueError:
                pass
        result[parts[0]] = row
    return result


def read_diskstats(path: str = "/proc/diskstats") -> dict[str, dict[str, object]]:
    text = read_text(path)
    if text is None:
        return {}
    result: dict[str, dict[str, object]] = {}
    names = (
        "reads_completed",
        "reads_merged",
        "sectors_read",
        "read_time_ms",
        "writes_completed",
        "writes_merged",
        "sectors_written",
        "write_time_ms",
        "io_in_progress",
        "io_time_ms",
        "weighted_io_time_ms",
        "discards_completed",
        "discards_merged",
        "sectors_discarded",
        "discard_time_ms",
        "flushes_completed",
        "flush_time_ms",
    )
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        try:
            major = int(parts[0])
            minor = int(parts[1])
            values = [int(value) for value in parts[3:]]
        except ValueError:
            continue
        row = {name: value for name, value in zip(names, values)}
        row.update(
            {
                "major": major,
                "minor": minor,
                "read_bytes": row.get("sectors_read", 0) * 512,
                "write_bytes": row.get("sectors_written", 0) * 512,
                "sector_unit_bytes": 512,
                "is_partition": Path(f"/sys/dev/block/{major}:{minor}/partition").exists(),
                "is_virtual": Path(f"/sys/dev/block/{major}:{minor}").resolve().as_posix().startswith("/sys/devices/virtual/"),
            }
        )
        result[parts[2]] = row
    return result


def read_schedstat(path: str = "/proc/schedstat") -> dict[str, object]:
    text = read_text(path)
    if text is None:
        return {}
    cpus: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("cpu") or not parts[0][3:].isdigit() or len(parts) < 4:
            continue
        try:
            cpus[parts[0]] = {
                "runtime_ns": int(parts[1]),
                "runqueue_delay_ns": int(parts[2]),
                "timeslices": int(parts[3]),
            }
        except ValueError:
            continue
    return {
        "cpus": cpus,
        "runtime_ns": sum(row["runtime_ns"] for row in cpus.values()),
        "runqueue_delay_ns": sum(row["runqueue_delay_ns"] for row in cpus.values()),
        "timeslices": sum(row["timeslices"] for row in cpus.values()),
    }


def read_numa_metrics(vmstat: dict[str, int] | None = None) -> dict[str, object]:
    counters = vmstat or read_key_value_int("/proc/vmstat")
    names = (
        "numa_hit",
        "numa_miss",
        "numa_foreign",
        "numa_interleave",
        "numa_local",
        "numa_other",
    )
    nodes: dict[str, dict[str, int]] = {}
    for path in sorted(Path("/sys/devices/system/node").glob("node*/meminfo")):
        node_values: dict[str, int] = {}
        text = read_text(str(path))
        if text is None:
            continue
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            key = key.split()[-1]
            parts = raw_value.split()
            if not parts:
                continue
            try:
                value = int(parts[0])
            except ValueError:
                continue
            node_values[key] = value * 1024 if len(parts) > 1 and parts[1] == "kB" else value
        nodes[path.parent.name] = node_values
    return {
        "counters": {name: counters.get(name) for name in names},
        "nodes": nodes,
        "quality": "exact_procfs" if any(name in counters for name in names) else "unavailable",
    }
