from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable, Iterable

from agentmon.config import AgentMonConfig
from agentmon.sampling.base import PeriodicSampler
from agentmon.storage.event_queue import EventSink


PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
CLK_TCK = os.sysconf("SC_CLK_TCK")


@dataclass
class ProcIdentity:
    pid: int
    starttime_ticks: int


def parse_proc_stat_line(line: str) -> dict[str, int | str]:
    open_paren = line.find("(")
    close_paren = line.rfind(")")
    if open_paren < 0 or close_paren < open_paren:
        raise ValueError("invalid /proc stat line")
    pid = int(line[:open_paren].strip())
    comm = line[open_paren + 1 : close_paren]
    parts = line[close_paren + 2 :].split()
    if len(parts) < 22:
        raise ValueError("truncated /proc stat line")
    return {
        "pid": pid,
        "comm": comm,
        "state": parts[0],
        "ppid": int(parts[1]),
        "pgrp": int(parts[2]),
        "minflt": int(parts[7]),
        "majflt": int(parts[9]),
        "utime": int(parts[11]),
        "stime": int(parts[12]),
        "num_threads": int(parts[17]),
        "starttime_ticks": int(parts[19]),
        "rss_bytes": int(parts[21]) * PAGE_SIZE,
    }


def read_proc_stat(pid: int) -> dict[str, int | str] | None:
    try:
        return parse_proc_stat_line(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace"))
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
        return None


def read_key_value_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
    return data


def read_proc_io(pid: int) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in read_key_value_file(Path(f"/proc/{pid}/io")).items():
        try:
            result[key] = int(value)
        except ValueError:
            continue
    return result


def read_proc_schedstat(pid: int) -> dict[str, int]:
    try:
        parts = Path(f"/proc/{pid}/schedstat").read_text(encoding="utf-8", errors="replace").split()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return {}
    if len(parts) < 3:
        return {}
    try:
        return {
            "scheduler_runtime_ns": int(parts[0]),
            "runqueue_delay_ns": int(parts[1]),
            "scheduler_timeslices": int(parts[2]),
        }
    except ValueError:
        return {}


def read_smaps_rollup(pid: int) -> dict[str, int]:
    result: dict[str, int] = {}
    data = read_key_value_file(Path(f"/proc/{pid}/smaps_rollup"))
    for key in ("Pss", "Private_Clean", "Private_Dirty", "SwapPss"):
        if key in data:
            try:
                result[key] = int(data[key].split()[0]) * 1024
            except (ValueError, IndexError):
                pass
    return result


def read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def list_proc_stats() -> dict[int, dict[str, int | str]]:
    stats: dict[int, dict[str, int | str]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        stat = read_proc_stat(int(entry.name))
        if stat is not None:
            stats[int(entry.name)] = stat
    return stats


def descendants_and_pgid(stats: dict[int, dict[str, int | str]], root_pid: int, pgid: int | None) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, stat in stats.items():
        children.setdefault(int(stat["ppid"]), []).append(pid)
    result: set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in result:
            continue
        if pid in stats:
            result.add(pid)
        stack.extend(children.get(pid, []))
    if pgid is not None:
        for pid, stat in stats.items():
            if int(stat["pgrp"]) == pgid:
                result.add(pid)
    return result


class ProcSampler(PeriodicSampler):
    source = "proc"

    def __init__(
        self,
        sink: EventSink,
        cfg: AgentMonConfig,
        *,
        child_pid_getter: Callable[[], int | None],
        child_pgid_getter: Callable[[], int | None],
    ) -> None:
        super().__init__(sink, cfg.proc.interval_seconds)
        self.cfg = cfg
        self._child_pid_getter = child_pid_getter
        self._child_pgid_getter = child_pgid_getter

    def sample(self) -> None:
        stats = list_proc_stats()
        child_pid = self._child_pid_getter()
        child_pgid = self._child_pgid_getter()
        agent_pids = descendants_and_pgid(stats, child_pid, child_pgid) if child_pid else set()
        vllm_pids = self._match_vllm_pids(stats, exclude=agent_pids | {os.getpid()})
        process_samples = [
            self._sample_pid(pid, role="agent" if pid in agent_pids else "vllm")
            for pid in sorted(agent_pids | vllm_pids)
        ]
        process_samples = [sample for sample in process_samples if sample is not None]
        aggregate = {
            "agent": aggregate_processes([p for p in process_samples if p["role"] == "agent"]),
            "vllm": aggregate_processes([p for p in process_samples if p["role"] == "vllm"]),
            "clk_tck": CLK_TCK,
        }
        self.sink.emit(
            "proc",
            "sample",
            "sampled",
            payload={"processes": process_samples, "aggregate": aggregate},
        )

    def _sample_pid(self, pid: int, *, role: str) -> dict[str, object] | None:
        stat = read_proc_stat(pid)
        if stat is None:
            return None
        status = read_key_value_file(Path(f"/proc/{pid}/status"))
        io_data = read_proc_io(pid) if self.cfg.proc.read_io else {}
        schedstat = read_proc_schedstat(pid)
        smaps = read_smaps_rollup(pid) if self.cfg.proc.read_pss else {}
        fd_count = None
        try:
            fd_count = len(list(Path(f"/proc/{pid}/fd").iterdir()))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
        return {
            "pid": pid,
            "role": role,
            "starttime_ticks": stat["starttime_ticks"],
            "ppid": stat["ppid"],
            "pgid": stat["pgrp"],
            "state": stat["state"],
            "comm": stat["comm"],
            "utime_ticks": stat["utime"],
            "stime_ticks": stat["stime"],
            "minor_faults": stat["minflt"],
            "major_faults": stat["majflt"],
            "threads": int(status.get("Threads", stat["num_threads"]).split()[0]),
            "rss_bytes": stat["rss_bytes"],
            "pss_bytes": smaps.get("Pss"),
            "private_bytes": smaps.get("Private_Clean", 0) + smaps.get("Private_Dirty", 0)
            if smaps
            else None,
            "read_bytes": io_data.get("read_bytes"),
            "write_bytes": io_data.get("write_bytes"),
            **schedstat,
            "fd_count": fd_count,
        }

    def _match_vllm_pids(self, stats: dict[int, dict[str, int | str]], exclude: set[int]) -> set[int]:
        matches: set[int] = set()
        needles = [item.lower() for item in self.cfg.vllm.process_match]
        for pid, stat in stats.items():
            if pid in exclude:
                continue
            cmdline = read_cmdline(pid) if self.cfg.proc.read_cmdline else ""
            haystack = f"{stat.get('comm', '')} {cmdline}".lower()
            if any(needle and needle in haystack for needle in needles):
                matches.add(pid)
        return matches


def aggregate_processes(samples: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(samples)
    return {
        "process_count": len(rows),
        "rss_bytes": sum(int(row.get("rss_bytes") or 0) for row in rows),
        "pss_bytes": sum(int(row.get("pss_bytes") or 0) for row in rows if row.get("pss_bytes") is not None),
        "pss_quality": "exact" if all(row.get("pss_bytes") is not None for row in rows) else "rss_fallback",
        "cpu_ticks": sum(int(row.get("utime_ticks") or 0) + int(row.get("stime_ticks") or 0) for row in rows),
        "read_bytes": sum(int(row.get("read_bytes") or 0) for row in rows),
        "write_bytes": sum(int(row.get("write_bytes") or 0) for row in rows),
        "scheduler_runtime_ns": sum(int(row.get("scheduler_runtime_ns") or 0) for row in rows),
        "runqueue_delay_ns": sum(int(row.get("runqueue_delay_ns") or 0) for row in rows),
        "scheduler_timeslices": sum(int(row.get("scheduler_timeslices") or 0) for row in rows),
    }
