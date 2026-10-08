from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shlex
import signal
import subprocess
import threading
import re
from typing import Any, Mapping, Sequence

try:
    import resource
except ImportError:  # pragma: no cover - resource is available on the target Linux host.
    resource = None  # type: ignore[assignment]

from agentmon.sampling.procfs import (
    descendants_and_pgid,
    list_proc_stats,
    read_proc_io,
    read_proc_stat,
    read_smaps_rollup,
)
from agentmon.security import command_hash

from .context import AgentMonContext
from .event_sink import AdapterEventSink, get_sink
from .ids import new_tool_call_id


class InstrumentedEnvironment:
    """Decorator for an existing mini-SWE environment object."""

    def __init__(
        self,
        inner_environment: Any,
        sink: AdapterEventSink | None = None,
        context: AgentMonContext | None = None,
    ) -> None:
        self.inner_environment = inner_environment
        self.sink = sink or get_sink()
        self.context = context or self.sink.context

    def execute(self, action: Any, *args: Any, **kwargs: Any) -> Any:
        tool_call_id = new_tool_call_id()
        metadata = action_metadata(action)
        context = self.sink.context.child(step_id=self.sink.context.step_id or self.context.step_id)
        payload = {
            "tool_call_id": tool_call_id,
            "step_id": context.step_id,
            **metadata,
        }
        with self.sink.span(
            span_id=tool_call_id,
            span_type="tool_call",
            name=metadata.get("tool_name") or "environment.execute",
            attributes=payload,
            source="mini_swe",
        ) as span:
            result = self.inner_environment.execute(action, *args, **kwargs)
            span["end_payload"] = {
                "result_summary": result_summary(result),
                "attribution_quality": "unavailable_inner_environment",
            }
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner_environment, name)


@dataclass
class LocalExecutionResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    command: list[str]
    pid: int | None
    pgid: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "command": self.command,
            "pid": self.pid,
            "pgid": self.pgid,
        }


class ToolProcessObserver:
    """Sample one tool process group without depending on the P0 sample cadence."""

    def __init__(self, pid: int, pgid: int | None) -> None:
        self.pid = pid
        self.pgid = pgid
        self.interval_seconds = tool_sample_interval_seconds()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._samples: list[dict[str, Any]] = []
        self._io_by_identity: dict[tuple[int, int], list[tuple[int | None, int | None]]] = {}
        self._error_count = 0

    def start(self) -> None:
        self._sample()
        if self.interval_seconds <= 0:
            return
        self._thread = threading.Thread(target=self._run, name=f"agentmon-tool-{self.pid}", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 3))
        return self.summary()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def _sample(self) -> None:
        try:
            stats = list_proc_stats()
            pids = descendants_and_pgid(stats, self.pid, self.pgid)
            rss_total = 0
            pss_total = 0
            pss_process_count = 0
            for pid in pids:
                stat = stats.get(pid)
                if stat is None:
                    continue
                rss_total += int(stat.get("rss_bytes") or 0)
                smaps = read_smaps_rollup(pid)
                if smaps.get("Pss") is not None:
                    pss_total += int(smaps["Pss"])
                    pss_process_count += 1
                io_data = read_proc_io(pid)
                identity = (pid, int(stat.get("starttime_ticks") or 0))
                self._io_by_identity.setdefault(identity, []).append(
                    (io_data.get("read_bytes"), io_data.get("write_bytes"))
                )
            with self._lock:
                self._samples.append(
                    {
                        "process_count": len(pids),
                        "rss_bytes": rss_total if pids else None,
                        "pss_bytes": pss_total if pids and pss_process_count == len(pids) else None,
                        "pss_process_count": pss_process_count,
                    }
                )
        except Exception:
            with self._lock:
                self._error_count += 1

    def summary(self) -> dict[str, Any]:
        with self._lock:
            samples = list(self._samples)
            io_rows = {identity: list(values) for identity, values in self._io_by_identity.items()}
            error_count = self._error_count
        read_delta, read_found = io_delta(io_rows, 0)
        write_delta, write_found = io_delta(io_rows, 1)
        rss_values = [int(row["rss_bytes"]) for row in samples if row.get("rss_bytes") is not None]
        pss_values = [int(row["pss_bytes"]) for row in samples if row.get("pss_bytes") is not None]
        return {
            "tool_resource_sample_count": len(samples),
            "tool_resource_sample_interval_s": self.interval_seconds,
            "tool_resource_sample_error_count": error_count,
            "tool_resource_quality": "sampled_process_group" if rss_values else "unavailable",
            "tool_peak_rss_bytes": max(rss_values) if rss_values else None,
            "tool_peak_pss_bytes": max(pss_values) if pss_values else None,
            "tool_pss_quality": "exact" if pss_values else "unavailable",
            "tool_read_bytes_delta": read_delta if read_found else None,
            "tool_write_bytes_delta": write_delta if write_found else None,
        }


class InstrumentedLocalEnvironment:
    """Standalone local environment using Popen so tool PID/PGID are observable."""

    def __init__(
        self,
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        sink: AdapterEventSink | None = None,
        context: AgentMonContext | None = None,
        output_limit_bytes: int | None = None,
    ) -> None:
        self.cwd = str(cwd) if cwd is not None else os.getcwd()
        self.env = dict(env) if env is not None else None
        self.timeout = timeout
        self.sink = sink or get_sink()
        self.context = context or self.sink.context
        self.output_limit_bytes = output_limit_bytes

    def execute(self, action: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        timeout = kwargs.pop("timeout", self.timeout)
        cwd = str(kwargs.pop("cwd", self.cwd))
        env = kwargs.pop("env", self.env)
        argv = action_to_argv(action)
        tool_call_id = new_tool_call_id()
        metadata = action_metadata(action, argv=argv)
        context = self.sink.context.child(step_id=self.sink.context.step_id or self.context.step_id)
        payload = {
            "tool_call_id": tool_call_id,
            "step_id": context.step_id,
            **metadata,
            "cwd": cwd,
        }
        process: subprocess.Popen[str] | None = None
        pid: int | None = None
        pgid: int | None = None
        starttime_ticks: int | None = None
        timed_out = False
        termination_signal: int | None = None
        stdout_text = ""
        stderr_text = ""
        returncode = 70
        observer: ToolProcessObserver | None = None
        tool_resources: dict[str, Any] = {}
        child_usage_before = child_rusage_snapshot()
        with self.sink.span(
            span_id=tool_call_id,
            span_type="tool_call",
            name=metadata.get("tool_name") or "local.execute",
            attributes=payload,
            source="mini_swe",
        ) as span:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
            pid = process.pid
            try:
                pgid = os.getpgid(pid)
            except ProcessLookupError:
                pgid = pid
            stat = read_proc_stat(pid)
            if stat is not None:
                starttime_ticks = int(stat["starttime_ticks"])
            span["end_payload"] = {
                "pid": pid,
                "pgid": pgid,
                "pid_starttime_ticks": starttime_ticks,
                "attribution_quality": "exact_pid_boundary",
            }
            self.sink.emit(
                "tool_call.process_start",
                source="mini_swe",
                payload={
                    **payload,
                    "pid": pid,
                    "pgid": pgid,
                    "pid_starttime_ticks": starttime_ticks,
                },
            )
            try:
                try:
                    observer = ToolProcessObserver(pid, pgid)
                    observer.start()
                except Exception:
                    observer = None
                    tool_resources = unavailable_tool_resources(error_count=1)
                try:
                    stdout_text, stderr_text = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    termination_signal = int(signal.SIGTERM)
                    terminate_process_group(pgid, signal.SIGTERM)
                    try:
                        stdout_text, stderr_text = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        termination_signal = int(signal.SIGKILL)
                        terminate_process_group(pgid, signal.SIGKILL)
                        stdout_text, stderr_text = process.communicate(timeout=5)
            finally:
                if observer is not None:
                    try:
                        tool_resources = observer.stop()
                    except Exception:
                        tool_resources = unavailable_tool_resources(error_count=1)
            returncode = process.returncode if process.returncode is not None else 70
            stdout_text = truncate_text(stdout_text, self.output_limit_bytes)
            stderr_text = truncate_text(stderr_text, self.output_limit_bytes)
            child_usage = child_rusage_delta(child_usage_before, child_rusage_snapshot())
            span["end_payload"] = {
                "status": "success" if returncode == 0 and not timed_out else "failed",
                "tool_status": "timeout" if timed_out else ("success" if returncode == 0 else "nonzero_return"),
                "returncode": returncode,
                "timed_out": timed_out,
                "timeout_limit_s": timeout,
                "termination_signal": termination_signal,
                "exception_type": "TimeoutExpired" if timed_out else None,
                "stdout_bytes": len(stdout_text.encode("utf-8", errors="replace")),
                "stderr_bytes": len(stderr_text.encode("utf-8", errors="replace")),
                **tool_result_observation(
                    stdout_text,
                    stderr_text,
                    argv,
                    returncode=returncode,
                ),
                "pid": pid,
                "pgid": pgid,
                "pid_starttime_ticks": starttime_ticks,
                "attribution_quality": "exact_pid_boundary" if pid is not None else "unavailable",
                **child_usage,
                **tool_resources,
            }
        return LocalExecutionResult(
            stdout=stdout_text,
            stderr=stderr_text,
            returncode=returncode,
            timed_out=timed_out,
            command=argv,
            pid=pid,
            pgid=pgid,
        ).to_dict()


class MiniSweLocalExecutionMixin:
    """Mixin for minisweagent.environments.local.LocalEnvironment."""

    def _agentmon_sink(self) -> AdapterEventSink:
        sink = getattr(self, "agentmon_sink", None)
        if sink is None:
            sink = get_sink()
            self.agentmon_sink = sink
        return sink

    def execute(
        self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None
    ) -> dict[str, Any]:
        sink = self._agentmon_sink()
        command = str(action.get("command", ""))
        cwd = str(cwd or action.get("cwd") or self.config.cwd or os.getcwd())
        timeout = timeout or action.get("timeout") or self.config.timeout
        argv = [command]
        tool_call_id = new_tool_call_id()
        metadata = action_metadata({"command": command}, argv=argv)
        payload = {
            "tool_call_id": tool_call_id,
            "step_id": sink.context.step_id,
            **metadata,
            "cwd": cwd,
        }
        process: subprocess.Popen[str] | None = None
        pid: int | None = None
        pgid: int | None = None
        starttime_ticks: int | None = None
        stdout_text = ""
        stderr_text = ""
        returncode = 70
        exception_info = ""
        timed_out = False
        termination_signal: int | None = None
        observer: ToolProcessObserver | None = None
        tool_resources: dict[str, Any] = {}
        child_usage_before = child_rusage_snapshot()
        try:
            with sink.span(
                span_id=tool_call_id,
                span_type="tool_call",
                name=metadata.get("tool_name") or "local.execute",
                attributes=payload,
                source="mini_swe",
            ) as span:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=cwd,
                    env=os.environ | getattr(self.config, "env", {}),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    start_new_session=True,
                )
                pid = process.pid
                try:
                    pgid = os.getpgid(pid)
                except ProcessLookupError:
                    pgid = pid
                stat = read_proc_stat(pid)
                if stat is not None:
                    starttime_ticks = int(stat["starttime_ticks"])
                span["end_payload"] = {
                    "pid": pid,
                    "pgid": pgid,
                    "pid_starttime_ticks": starttime_ticks,
                    "attribution_quality": "exact_pid_boundary",
                }
                sink.emit(
                    "tool_call.process_start",
                    source="mini_swe",
                    payload={
                        **payload,
                        "pid": pid,
                        "pgid": pgid,
                        "pid_starttime_ticks": starttime_ticks,
                    },
                )
                try:
                    try:
                        observer = ToolProcessObserver(pid, pgid)
                        observer.start()
                    except Exception:
                        observer = None
                        tool_resources = unavailable_tool_resources(error_count=1)
                    try:
                        stdout_text, _ = process.communicate(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        termination_signal = int(signal.SIGKILL)
                        terminate_process_group(pgid, signal.SIGKILL)
                        stdout_text, _ = process.communicate()
                        exception_info = (
                            f"An error occurred while executing the command: "
                            f"Command '{command}' timed out after {timeout} seconds"
                        )
                finally:
                    if observer is not None:
                        try:
                            tool_resources = observer.stop()
                        except Exception:
                            tool_resources = unavailable_tool_resources(error_count=1)
                returncode = process.returncode if process.returncode is not None else -1
                output = stdout_text + stderr_text
                child_usage = child_rusage_delta(child_usage_before, child_rusage_snapshot())
                result = {
                    "output": output,
                    "returncode": -1 if timed_out else returncode,
                    "exception_info": exception_info,
                }
                span["end_payload"] = {
                    "status": "success" if result["returncode"] == 0 else "failed",
                    "tool_status": (
                        "timeout"
                        if timed_out
                        else "success"
                        if result["returncode"] == 0
                        else "nonzero_return"
                    ),
                    "returncode": result["returncode"],
                    "timed_out": timed_out,
                    "timeout_limit_s": timeout,
                    "termination_signal": termination_signal,
                    "exception_type": "TimeoutExpired" if timed_out else None,
                    "output_bytes": len(output.encode("utf-8", errors="replace")),
                    **tool_result_observation(
                        output,
                        "",
                        argv,
                        returncode=result["returncode"],
                    ),
                    "pid": pid,
                    "pgid": pgid,
                    "pid_starttime_ticks": starttime_ticks,
                    "attribution_quality": "exact_pid_boundary" if pid is not None else "unavailable",
                    **child_usage,
                    **tool_resources,
                }
                checker = getattr(self, "_check_finished", None)
                if callable(checker):
                    checker(result)
        except Exception as exc:
            exception_info = f"{type(exc).__name__}: {exc}"
            raise
        return result


def child_rusage_snapshot() -> dict[str, float] | None:
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "user_time_s": float(usage.ru_utime),
        "system_time_s": float(usage.ru_stime),
        "minor_faults": float(usage.ru_minflt),
        "major_faults": float(usage.ru_majflt),
        "input_blocks": float(usage.ru_inblock),
        "output_blocks": float(usage.ru_oublock),
        "voluntary_context_switches": float(usage.ru_nvcsw),
        "involuntary_context_switches": float(usage.ru_nivcsw),
    }


def child_rusage_delta(
    before: dict[str, float] | None,
    after: dict[str, float] | None,
) -> dict[str, Any]:
    if before is None or after is None:
        return {
            "cpu_time_s": None,
            "cpu_user_time_s": None,
            "cpu_system_time_s": None,
            "cpu_time_quality": "unavailable",
        }
    deltas = {key: max(0.0, after[key] - before[key]) for key in before}
    return {
        "cpu_time_s": deltas["user_time_s"] + deltas["system_time_s"],
        "cpu_user_time_s": deltas["user_time_s"],
        "cpu_system_time_s": deltas["system_time_s"],
        "cpu_time_quality": "rusage_children_delta",
        "minor_faults_delta": int(deltas["minor_faults"]),
        "major_faults_delta": int(deltas["major_faults"]),
        "input_blocks_delta": int(deltas["input_blocks"]),
        "output_blocks_delta": int(deltas["output_blocks"]),
        "voluntary_context_switches_delta": int(deltas["voluntary_context_switches"]),
        "involuntary_context_switches_delta": int(deltas["involuntary_context_switches"]),
    }


def tool_sample_interval_seconds() -> float:
    raw = os.environ.get("AGENTMON_TOOL_SAMPLE_INTERVAL_SECONDS", "0.1")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.1


def unavailable_tool_resources(*, error_count: int) -> dict[str, Any]:
    return {
        "tool_resource_sample_count": 0,
        "tool_resource_sample_interval_s": tool_sample_interval_seconds(),
        "tool_resource_sample_error_count": error_count,
        "tool_resource_quality": "unavailable",
        "tool_peak_rss_bytes": None,
        "tool_peak_pss_bytes": None,
        "tool_pss_quality": "unavailable",
        "tool_read_bytes_delta": None,
        "tool_write_bytes_delta": None,
    }


def io_delta(
    rows: dict[tuple[int, int], list[tuple[int | None, int | None]]],
    index: int,
) -> tuple[int, bool]:
    total = 0
    found = False
    for values in rows.values():
        present = [value[index] for value in values if value[index] is not None]
        if len(present) < 2:
            continue
        total += max(0, int(present[-1]) - int(present[0]))
        found = True
    return total, found


def action_to_argv(action: Any) -> list[str]:
    if isinstance(action, str):
        return ["/bin/bash", "-lc", action]
    if isinstance(action, Sequence) and not isinstance(action, (bytes, bytearray)):
        return [str(item) for item in action]
    if isinstance(action, Mapping):
        command = action.get("command") or action.get("cmd") or action.get("action")
        if isinstance(command, Sequence) and not isinstance(command, (str, bytes, bytearray)):
            return [str(item) for item in command]
        if command is not None:
            return ["/bin/bash", "-lc", str(command)]
    return ["/bin/bash", "-lc", str(action)]


def action_metadata(action: Any, *, argv: list[str] | None = None) -> dict[str, Any]:
    command = argv or action_to_argv(action)
    executable, argc = command_shape(command)
    return {
        "tool_name": executable,
        "executable": executable,
        "command_hash": command_hash(command),
        "command_argc": argc,
        "action_type": type(action).__name__,
    }


def command_shape(command: list[str]) -> tuple[str | None, int]:
    if not command:
        return None, 0
    if len(command) == 1:
        try:
            parts = shlex.split(command[0])
        except ValueError:
            parts = command[0].split()
        if not parts:
            return None, 0
        return Path(parts[0]).name, len(parts)
    if len(command) >= 3 and command[1] in {"-c", "-lc"}:
        try:
            parts = shlex.split(command[2])
        except ValueError:
            parts = command[2].split()
        if parts:
            return Path(parts[0]).name, len(parts)
    return Path(command[0]).name, len(command)


def result_summary(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        stdout = str(result.get("stdout") or result.get("output") or "")
        stderr = str(result.get("stderr") or "")
        return {
            "returncode": result.get("returncode"),
            "timed_out": result.get("timed_out") or result.get("timeout"),
            "stdout_bytes": _bytes_len(stdout),
            "stderr_bytes": _bytes_len(stderr),
            **tool_result_observation(
                stdout,
                stderr,
                [],
                returncode=result.get("returncode"),
            ),
        }
    return {"result_type": type(result).__name__, "result_bytes": _bytes_len(result)}


def tool_result_observation(
    stdout: str,
    stderr: str,
    argv: Sequence[str],
    *,
    returncode: Any,
) -> dict[str, Any]:
    output = stdout + stderr
    category = command_category(argv)
    test_counts = test_result_counts(output) if category == "test" else {}
    diff_observed = category == "code_diff"
    return {
        "tool_category": category,
        "stdout_sha256": text_sha256(stdout),
        "stderr_sha256": text_sha256(stderr),
        "output_sha256": text_sha256(output),
        "error_fingerprint": (
            text_sha256(stderr or output)
            if integer_or_none(returncode) not in {None, 0}
            else None
        ),
        "code_diff_observed": diff_observed,
        "code_diff_sha256": text_sha256(output) if diff_observed else None,
        "code_diff_bytes": _bytes_len(output) if diff_observed else None,
        "code_diff_changed_file_count": (
            len(re.findall(r"(?m)^diff --git ", output)) if diff_observed else None
        ),
        **test_counts,
    }


def command_category(argv: Sequence[str]) -> str:
    try:
        text = " ".join(str(item) for item in argv)
        parts = shlex.split(text)
    except ValueError:
        parts = [str(item) for item in argv]
    normalized = [Path(item).name.lower() for item in parts]
    if (
        any(item in {"pytest", "unittest", "jest", "vitest"} for item in normalized)
        or ("go" in normalized and "test" in normalized)
        or ("cargo" in normalized and "test" in normalized)
    ):
        return "test"
    if normalized and normalized[0] == "git" and "diff" in normalized[1:]:
        return "code_diff"
    return "general"


def test_result_counts(output: str) -> dict[str, int | None]:
    aliases = {
        "test_passed_count": "passed",
        "test_failed_count": "failed",
        "test_error_count": "error",
        "test_skipped_count": "skipped",
        "test_xfailed_count": "xfailed",
        "test_xpassed_count": "xpassed",
    }
    result: dict[str, int | None] = {}
    for field, label in aliases.items():
        matches = re.findall(rf"(?<!\d)(\d+)\s+{label}\b", output, flags=re.IGNORECASE)
        result[field] = int(matches[-1]) if matches else None
    return result


def text_sha256(value: str) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def terminate_process_group(pgid: int | None, signum: int) -> None:
    if pgid is None:
        return
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return
    try:
        os.waitpid(-pgid, os.WNOHANG)
    except OSError:
        pass


def truncate_text(text: str, limit: int | None) -> str:
    if limit is None:
        return text
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="replace")


def _bytes_len(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return len(value)
    return len(str(value).encode("utf-8", errors="replace"))


try:
    from minisweagent.environments.local import LocalEnvironment
except Exception:

    class InstrumentedMiniSweLocalEnvironment(MiniSweLocalExecutionMixin):  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("minisweagent.environments.local.LocalEnvironment is not importable")

else:

    class InstrumentedMiniSweLocalEnvironment(MiniSweLocalExecutionMixin, LocalEnvironment):  # type: ignore[no-redef]
        pass
