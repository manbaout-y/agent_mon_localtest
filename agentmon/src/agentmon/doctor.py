from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from agentmon import __version__
from agentmon.capabilities import Capability
from agentmon.config import AgentMonConfig
from agentmon.events import Event
from agentmon.events import safe_json_dumps
from agentmon.paths import output_root
from agentmon.sampling.ascend_smi import parse_npu_smi_text, resolve_npu_smi, run_npu_smi
from agentmon.sampling.host import read_key_value_kb, read_text
from agentmon.sampling.vllm_metrics import probe_metrics
from agentmon.storage.jsonl import JsonlWriter


def run_doctor(cfg: AgentMonConfig, *, output: str | Path | None = None) -> tuple[dict[str, Any], int]:
    raw_dir = None
    if output is not None:
        raw_dir = Path(output).expanduser().parent / "doctor_raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

    capabilities: list[Capability] = []
    capabilities.append(probe_python_and_output(cfg))
    capabilities.append(probe_host(raw_dir))
    capabilities.append(probe_ascend(cfg, raw_dir))
    capabilities.append(probe_vllm(cfg, raw_dir))
    capabilities.append(probe_mini_swe(cfg, raw_dir))
    capabilities.append(probe_p1(cfg, raw_dir))
    capabilities.append(probe_p2(cfg, raw_dir))

    status = summarize_status([cap.status for cap in capabilities])
    report = {
        "schema_version": "1.0",
        "agentmon_version": __version__,
        "command": "doctor",
        "status": status,
        "created_wall_ns": time.time_ns(),
        "host": platform.node(),
        "capabilities": {cap.name: {"status": cap.status, **cap.detail} for cap in capabilities},
    }
    exit_code = 0
    if sys.version_info < (3, 10):
        exit_code = 2
    if report["capabilities"]["python_output"]["status"] in {"permission_denied", "misconfigured"}:
        exit_code = 2

    if output is not None:
        path = Path(output).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return report, exit_code


def probe_python_and_output(cfg: AgentMonConfig) -> Capability:
    detail: dict[str, Any] = {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "required_python": ">=3.10",
        "output_root": str(output_root(cfg)),
    }
    if sys.version_info < (3, 10):
        return Capability("python_output", "unsupported", {**detail, "error": "Python >= 3.10 required"})
    try:
        root = output_root(cfg)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".agentmon_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return Capability("python_output", "supported", detail)
    except PermissionError as exc:
        return Capability("python_output", "permission_denied", {**detail, "error": str(exc)})
    except OSError as exc:
        return Capability("python_output", "misconfigured", {**detail, "error": str(exc)})


def probe_host(raw_dir: Path | None) -> Capability:
    detail: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "node": platform.node(),
        "uname": platform.uname()._asdict(),
        "os_release": read_text("/etc/os-release"),
        "meminfo": read_key_value_kb("/proc/meminfo"),
        "loadavg": read_text("/proc/loadavg"),
        "pressure_available": {
            "cpu": Path("/proc/pressure/cpu").exists(),
            "memory": Path("/proc/pressure/memory").exists(),
            "io": Path("/proc/pressure/io").exists(),
        },
        "ulimit": {
            "nofile": resource.getrlimit(resource.RLIMIT_NOFILE),
            "nproc": resource.getrlimit(resource.RLIMIT_NPROC)
            if hasattr(resource, "RLIMIT_NPROC")
            else None,
        },
    }
    commands = {
        "uname": ["uname", "-a"],
        "df": ["df", "-T"],
        "lsblk": ["lsblk"],
    }
    command_results: dict[str, Any] = {}
    for name, argv in commands.items():
        if shutil.which(argv[0]) is None:
            command_results[name] = {"status": "not_found", "command": argv}
            continue
        raw_path = raw_dir / f"host_{name}.txt" if raw_dir else None
        command_results[name] = run_command(argv, timeout=3, raw_path=raw_path)
    detail["commands"] = command_results
    return Capability("host", "supported", detail)


def probe_ascend(cfg: AgentMonConfig, raw_dir: Path | None) -> Capability:
    npu_smi = resolve_npu_smi(cfg)
    detail: dict[str, Any] = {"npu_smi": npu_smi}
    if npu_smi is None:
        return Capability("ascend", "not_found", detail)

    commands = {
        "info_l": [npu_smi, "info", "-l"],
        "info_m": [npu_smi, "info", "-m"],
        "info": [npu_smi, "info"],
    }
    statuses: list[str] = []
    results: dict[str, Any] = {}
    for name, argv in commands.items():
        raw_path = raw_dir / f"ascend_{name}.txt" if raw_dir else None
        result = run_npu_smi(argv, timeout=cfg.ascend.command_timeout_seconds, raw_path=raw_path)
        if result.get("stdout"):
            parsed = parse_npu_smi_text(str(result["stdout"]))
            result["parser"] = parsed.parser
            result["parser_confidence"] = parsed.confidence
            result["devices"] = parsed.devices
        result["raw_ref"] = str(raw_path) if raw_path else None
        statuses.append(str(result["status"]))
        results[name] = compact_command_result(result)
    detail["commands"] = results
    status = "supported" if any(item == "supported" for item in statuses) else statuses[0]
    return Capability("ascend", status, detail)


def probe_vllm(cfg: AgentMonConfig, raw_dir: Path | None) -> Capability:
    raw_path = raw_dir / "vllm_metrics.prom" if raw_dir else None
    detail = probe_metrics(
        cfg.vllm.metrics_url,
        timeout=cfg.vllm.request_timeout_seconds,
        raw_path=raw_path,
    )
    detail["metrics_url"] = cfg.vllm.metrics_url
    detail["models_url"] = cfg.vllm.models_url
    detail["models_probe"] = probe_http_endpoint(
        cfg.vllm.models_url,
        timeout=cfg.vllm.request_timeout_seconds,
        raw_path=raw_dir / "vllm_models.json" if raw_dir else None,
    )
    detail["prefix_caching_config"] = cfg.vllm.prefix_caching
    return Capability("vllm", str(detail.get("status", "unknown")), detail)


def probe_mini_swe(cfg: AgentMonConfig, raw_dir: Path | None) -> Capability:
    detail: dict[str, Any] = {
        "agent_type": cfg.agent.type,
        "root": cfg.agent.root,
        "python": cfg.agent.python,
        "trajectory_glob": cfg.agent.trajectory_glob,
    }
    root = Path(cfg.agent.root).expanduser() if cfg.agent.root else None
    python = Path(cfg.agent.python).expanduser() if cfg.agent.python else None
    if root is not None:
        detail["root_exists"] = root.exists()
    if python is not None:
        detail["python_exists"] = python.exists()
        if python.exists():
            raw_path = raw_dir / "mini_swe_python.txt" if raw_dir else None
            detail["python_probe"] = run_command(
                [str(python), "-c", "import sys; print(sys.version)"],
                timeout=3,
                raw_path=raw_path,
            )
    status = "supported"
    if python is not None and not python.exists():
        status = "not_found"
    elif root is not None and not root.exists():
        status = "partial"
    return Capability("mini_swe", status, detail)


def probe_p1(cfg: AgentMonConfig, raw_dir: Path | None) -> Capability:
    detail: dict[str, Any] = {
        "required_adapter_pythonpath": expected_agentmon_pythonpath(cfg),
        "compatibility_scope": "static",
    }
    python = Path(cfg.agent.python).expanduser() if cfg.agent.python else None
    if python is None or not python.exists():
        return Capability("p1", "not_found", {**detail, "error": "agent.python is not configured or not found"})

    mini_bin = python.parent / "mini"
    if not mini_bin.exists():
        mini_bin = python.parent / "mini-swe-agent"
    if mini_bin.exists():
        help_result = run_command(
            [str(mini_bin), "--help"],
            timeout=5,
            raw_path=raw_dir / "p1_mini_help.txt" if raw_dir else None,
        )
        help_text = (help_result.get("stdout_excerpt") or "") + (help_result.get("stderr_excerpt") or "")
        detail["cli_class_options"] = {
            "status": "supported"
            if all(option in help_text for option in ("agent-class", "model-class", "environment-class"))
            else "unsupported",
            "command": [str(mini_bin), "--help"],
            "raw_ref": help_result.get("raw_ref"),
        }
    else:
        detail["cli_class_options"] = {"status": "not_found", "path_checked": str(mini_bin)}

    child_env = build_p1_probe_env(cfg)
    import_script = """
from agentmon.adapters.agent_frameworks.mini_swe.agent import InstrumentedInteractiveAgent
from agentmon.adapters.agent_frameworks.mini_swe.model import InstrumentedLitellmModel
from agentmon.adapters.agent_frameworks.mini_swe.environment import InstrumentedMiniSweLocalEnvironment
print("P1_IMPORT_OK")
print(InstrumentedInteractiveAgent)
print(InstrumentedLitellmModel)
print(InstrumentedMiniSweLocalEnvironment)
""".strip()
    import_result = run_command(
        [str(python), "-c", import_script],
        timeout=5,
        raw_path=raw_dir / "p1_adapter_import.txt" if raw_dir else None,
        env=child_env,
    )
    detail["adapter_import"] = {
        "status": "supported" if import_result.get("returncode") == 0 else "unsupported",
        "raw_ref": import_result.get("raw_ref"),
        "stderr_excerpt": import_result.get("stderr_excerpt"),
        "stdout_excerpt": import_result.get("stdout_excerpt"),
    }

    compat_script = """
import inspect
from agentmon.adapters.agent_frameworks.mini_swe.agent import InstrumentedInteractiveAgent
from agentmon.adapters.agent_frameworks.mini_swe.model import InstrumentedLitellmModel
from agentmon.adapters.agent_frameworks.mini_swe.environment import InstrumentedMiniSweLocalEnvironment
for cls in (InstrumentedInteractiveAgent, InstrumentedLitellmModel, InstrumentedMiniSweLocalEnvironment):
    print(cls)
    print(cls.__mro__)
    print(inspect.signature(cls.__init__))
""".strip()
    compat_result = run_command(
        [str(python), "-c", compat_script],
        timeout=5,
        raw_path=raw_dir / "p1_adapter_compatibility.txt" if raw_dir else None,
        env=child_env,
    )
    detail["adapter_compatibility"] = {
        "status": "supported" if compat_result.get("returncode") == 0 else "unsupported",
        "raw_ref": compat_result.get("raw_ref"),
        "stderr_excerpt": compat_result.get("stderr_excerpt"),
        "stdout_excerpt": compat_result.get("stdout_excerpt"),
    }

    detail["events_path_writable"] = probe_p1_events_path_writable(cfg)
    detail["child_pythonpath_clean"] = probe_child_pythonpath(child_env)
    statuses = [
        detail["cli_class_options"]["status"],
        detail["adapter_import"]["status"],
        detail["adapter_compatibility"]["status"],
        detail["events_path_writable"]["status"],
        detail["child_pythonpath_clean"]["status"],
    ]
    status = "supported" if all(item == "supported" for item in statuses) else "partial"
    return Capability("p1", status, detail)


def probe_p2(cfg: AgentMonConfig, raw_dir: Path | None) -> Capability:
    detail: dict[str, Any] = {
        "target_vllm_version": "0.18.0",
        "runtime_hook_loaded_by_default": False,
        "scope": "contract_and_discovery_only",
    }
    try:
        from agentmon.adapters.inference.vllm_018.events import VllmP2EventSink
        from agentmon.adapters.inference.vllm_018.version import detected_vllm_version, is_supported_vllm_version

        event = VllmP2EventSink(run_id="doctor-p2-probe").adapter_capability(status="dry_run")
        detected = detected_vllm_version()
        detail["adapter_contract_import"] = "supported"
        detail["dry_run_event_type"] = event.event_type
        detail["detected_vllm_version"] = detected
        detail["vllm_version_supported"] = is_supported_vllm_version(detected)
    except Exception as exc:
        return Capability("p2", "unsupported", {**detail, "error": str(exc), "error_type": type(exc).__name__})

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "agentmon_p2_discovery.py"
    detail["discovery_script"] = {"path": str(script_path), "exists": script_path.exists()}
    if detail["vllm_version_supported"]:
        status = "supported"
    elif detail["detected_vllm_version"] is None:
        status = "not_found"
    else:
        status = "partial"
    return Capability("p2", status, detail)


def run_command(
    argv: list[str],
    *,
    timeout: float,
    raw_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**(env or os.environ), "LC_ALL": "C"},
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
        status = "supported" if returncode == 0 else "partial"
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
        returncode = None
        status = "timeout"
    except FileNotFoundError:
        stdout = ""
        stderr = "command not found"
        returncode = None
        status = "not_found"
    if raw_path is not None:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            f"$ {' '.join(argv)}\nstatus={status} returncode={returncode}\n\nSTDOUT\n{stdout}\n\nSTDERR\n{stderr}",
            encoding="utf-8",
        )
    return {
        "status": status,
        "command": argv,
        "returncode": returncode,
        "duration_ms": (time.monotonic() - started) * 1000,
        "stdout_excerpt": stdout[:4000],
        "stderr_excerpt": stderr[-2000:],
        "raw_ref": str(raw_path) if raw_path else None,
    }


def build_p1_probe_env(cfg: AgentMonConfig) -> dict[str, str]:
    env = os.environ.copy()
    for name in cfg.agent.env_unset:
        env.pop(name, None)
    for entry in cfg.agent.env_set:
        key, value = entry.split("=", 1)
        env[key] = value
    env["PYTHONNOUSERSITE"] = "1"
    env["AGENTMON_RUN_ID"] = "doctor-p1-probe"
    env["AGENTMON_RUN_DIR"] = str(output_root(cfg) / ".doctor-p1-probe")
    env["AGENTMON_EVENTS_PATH"] = str(output_root(cfg) / ".doctor-p1-probe" / "events.jsonl")
    env["AGENTMON_ROOT_SPAN_ID"] = "run:doctor-p1-probe"
    return env


def expected_agentmon_pythonpath(cfg: AgentMonConfig) -> str | None:
    for entry in cfg.agent.env_set:
        if entry.startswith("PYTHONPATH="):
            return entry.split("=", 1)[1]
    return None


def probe_p1_events_path_writable(cfg: AgentMonConfig) -> dict[str, Any]:
    probe_dir = output_root(cfg) / ".doctor-p1-probe"
    probe_path = probe_dir / "events.jsonl"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        with JsonlWriter(probe_path) as writer:
            writer.write_event(
                Event(
                    run_id="doctor-p1-probe",
                    source="mini_swe",
                    event_type="capability",
                    quality="exact",
                    payload={"probe": "p1.events_path_writable"},
                )
            )
        probe_path.unlink(missing_ok=True)
        probe_dir.rmdir()
        return {"status": "supported", "path": str(probe_path)}
    except PermissionError as exc:
        return {"status": "permission_denied", "path": str(probe_path), "error": str(exc)}
    except OSError as exc:
        return {"status": "partial", "path": str(probe_path), "error": str(exc)}


def probe_child_pythonpath(env: dict[str, str]) -> dict[str, Any]:
    pythonpath = env.get("PYTHONPATH", "")
    suspicious = [part for part in pythonpath.split(os.pathsep) if "Ascend" in part or "cann" in part.lower()]
    return {
        "status": "supported" if pythonpath and not suspicious else "partial",
        "pythonpath": pythonpath,
        "suspicious_entries": suspicious,
        "pythonhome_present": "PYTHONHOME" in env,
        "pythonnousersite": env.get("PYTHONNOUSERSITE"),
    }


def probe_http_endpoint(url: str, *, timeout: float, raw_path: Path | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "agentmon/0.1"})
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("HTTP response exceeded 1 MiB")
        if raw_path is not None:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(body)
        return {
            "status": "supported" if status < 400 else "partial",
            "http_status": status,
            "bytes": len(body),
            "duration_ms": (time.monotonic() - started) * 1000,
            "raw_ref": str(raw_path) if raw_path else None,
        }
    except URLError as exc:
        return {
            "status": "unsupported",
            "duration_ms": (time.monotonic() - started) * 1000,
            "error": str(exc),
            "raw_ref": str(raw_path) if raw_path else None,
        }
    except Exception as exc:
        return {
            "status": "partial",
            "duration_ms": (time.monotonic() - started) * 1000,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "raw_ref": str(raw_path) if raw_path else None,
        }


def compact_command_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = dict(result)
    if "stdout" in compact:
        compact["stdout_excerpt"] = str(compact.pop("stdout"))[:4000]
    return compact


def summarize_status(statuses: list[str]) -> str:
    if any(status in {"permission_denied", "misconfigured"} for status in statuses):
        return "partial"
    if any(status in {"partial", "timeout", "not_found", "unsupported", "unknown"} for status in statuses):
        return "partial"
    return "supported"
