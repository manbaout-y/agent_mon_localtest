from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Sequence

from agentmon.contracts import AdapterDescriptor, ResourceTelemetryProvider, TelemetryCapability
from agentmon.paths import chmod_private_file, make_private_dir, open_private_text, write_private_text


DEFAULT_PROCESS_MATCH = ("vllm",)
DEFAULT_NPU_SMI_COMMAND = ("npu-smi", "info")


class NpuProcessMappingProvider:
    name = "npu_process_mapping"

    def __init__(
        self,
        *,
        env: dict[str, str] | None = None,
        output_dir: str | Path | None = None,
        model_path: str | None = None,
        process_match: Sequence[str] = DEFAULT_PROCESS_MATCH,
        proc_root: str | Path = "/proc",
        npu_smi_command: Sequence[str] = DEFAULT_NPU_SMI_COMMAND,
    ) -> None:
        self.env = env or {}
        self.output_dir = Path(output_dir).expanduser() if output_dir is not None else None
        self.model_path = model_path
        self.process_match = tuple(process_match)
        self.proc_root = Path(proc_root)
        self.npu_smi_command = tuple(npu_smi_command)

    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            name=self.name,
            domain="resource",
            namespace="agentmon.sampling.npu_mapping",
            capabilities=(
                TelemetryCapability(
                    name="process_to_npu_mapping",
                    quality="derived",
                    required_fields=("pid", "visible_devices"),
                    optional_fields=("rank", "local_rank", "logical_device", "physical_device"),
                ),
                TelemetryCapability(
                    name="npu_smi_process_evidence",
                    quality="heuristic",
                    required_fields=("pid",),
                    optional_fields=("physical_device", "raw"),
                ),
            ),
        )

    def snapshot(self) -> dict[str, Any]:
        return collect_npu_process_mapping(
            env=self.env,
            output_dir=self.output_dir,
            model_path=self.model_path,
            process_match=self.process_match,
            proc_root=self.proc_root,
            npu_smi_command=self.npu_smi_command,
        )


def provider(**kwargs: Any) -> ResourceTelemetryProvider:
    return NpuProcessMappingProvider(**kwargs)


def collect_npu_process_mapping(
    *,
    env: dict[str, str],
    output_dir: str | Path | None = None,
    model_path: str | None = None,
    process_match: Sequence[str] = DEFAULT_PROCESS_MATCH,
    proc_root: str | Path = "/proc",
    npu_smi_command: Sequence[str] = DEFAULT_NPU_SMI_COMMAND,
    command_timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    started = time.monotonic()
    output_root = Path(output_dir).expanduser() if output_dir is not None else None
    visible_devices = visible_npu_devices(env)
    process_rows = discover_service_process_rows(
        env=env,
        model_path=model_path,
        process_match=process_match,
        proc_root=proc_root,
    )
    npu_smi_rows = collect_npu_smi_process_rows(
        command=npu_smi_command,
        timeout_seconds=command_timeout_seconds,
    )
    npu_pids = {
        str(row.get("pid"))
        for row in npu_smi_rows
        if row.get("pid") is not None and row.get("pid") != ""
    }
    evidence_level = classify_mapping_evidence(process_rows, npu_pids, visible_devices)
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "status": evidence_status(evidence_level),
        "evidence_level": evidence_level,
        "visible_devices": visible_devices,
        "process_match": [term for term in process_match if term],
        "processes": process_rows,
        "npu_smi_processes": npu_smi_rows,
        "collection_wall_s": time.monotonic() - started,
        "artifacts": {},
    }
    if output_root is not None:
        mapping_path = output_root / "npu-process-mapping.json"
        evidence_path = output_root / "npu-mapping-evidence.csv"
        result["artifacts"] = {
            "npu_process_mapping_json": str(mapping_path),
            "npu_mapping_evidence_csv": str(evidence_path),
        }
        write_json_private(mapping_path, result)
        write_mapping_evidence_csv(evidence_path, result)
    return result


def visible_npu_devices(env: dict[str, str]) -> str | None:
    return first_env(env, "ASCEND_RT_VISIBLE_DEVICES", "ASCEND_VISIBLE_DEVICES")


def classify_mapping_evidence(
    process_rows: list[dict[str, Any]],
    npu_pids: set[str],
    visible_devices: str | None,
) -> str:
    if process_rows and npu_pids and any(str(row.get("pid")) in npu_pids for row in process_rows):
        return "LEVEL_1"
    if process_rows and any(
        row.get("rank") is not None or row.get("local_rank") is not None
        for row in process_rows
    ):
        return "LEVEL_2"
    if visible_devices or process_rows:
        return "LEVEL_3"
    return "NONE"


def evidence_status(evidence_level: str) -> str:
    if evidence_level in {"LEVEL_1", "LEVEL_2"}:
        return "PASS"
    if evidence_level == "LEVEL_3":
        return "PARTIAL"
    return "FAIL"


def discover_service_process_rows(
    *,
    env: dict[str, str],
    model_path: str | None = None,
    process_match: Sequence[str] = DEFAULT_PROCESS_MATCH,
    proc_root: str | Path = "/proc",
) -> list[dict[str, Any]]:
    root = Path(proc_root)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    model_path_text = str(Path(model_path).expanduser()) if model_path else ""
    match_terms = tuple(term.lower() for term in process_match if term)
    for proc_dir in root.iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            cmdline = (
                (proc_dir / "cmdline")
                .read_bytes()
                .decode("utf-8", errors="replace")
                .replace("\x00", " ")
                .strip()
            )
        except OSError:
            continue
        cmdline_lower = cmdline.lower()
        matches_process = any(term in cmdline_lower for term in match_terms)
        matches_model = bool(model_path_text and model_path_text in cmdline)
        if not matches_process and not matches_model:
            continue
        environ = read_proc_environ(proc_dir / "environ")
        rows.append(
            {
                "pid": proc_dir.name,
                "cmdline": cmdline,
                "rank": first_env(environ, "RANK", "OMPI_COMM_WORLD_RANK"),
                "local_rank": first_env(environ, "LOCAL_RANK", "OMPI_COMM_WORLD_LOCAL_RANK"),
                "world_size": first_env(environ, "WORLD_SIZE", "OMPI_COMM_WORLD_SIZE"),
                "logical_device": first_env(environ, "ASCEND_DEVICE_ID", "DEVICE_ID", "LOCAL_DEVICE_ID"),
                "visible_devices": first_env(environ, "ASCEND_RT_VISIBLE_DEVICES", "ASCEND_VISIBLE_DEVICES")
                or visible_npu_devices(env),
                "environ_keys": sorted(
                    key
                    for key in environ
                    if "ASCEND" in key or "NPU" in key or key in {"RANK", "LOCAL_RANK", "WORLD_SIZE"}
                ),
            }
        )
    rows.sort(key=lambda row: int(str(row["pid"])))
    return rows


def discover_vllm_process_rows(
    *,
    env: dict[str, str],
    model_path: str | None = None,
    proc_root: str | Path = "/proc",
) -> list[dict[str, Any]]:
    return discover_service_process_rows(
        env=env,
        model_path=model_path,
        process_match=DEFAULT_PROCESS_MATCH,
        proc_root=proc_root,
    )


def read_proc_environ(path: Path) -> dict[str, str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        env[key.decode("utf-8", errors="replace")] = value.decode("utf-8", errors="replace")
    return env


def first_env(env: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = env.get(key)
        if value is not None and value != "":
            return value
    return None


def collect_npu_smi_process_rows(
    *,
    command: Sequence[str] = DEFAULT_NPU_SMI_COMMAND,
    timeout_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_npu_smi_process_rows(completed.stdout)


def parse_npu_smi_process_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not re.search(r"\b\d+\b", line):
            continue
        pid_match = re.search(r"\bpid\s*[:=]?\s*(\d+)\b", line, flags=re.IGNORECASE)
        if pid_match:
            pid = pid_match.group(1)
        else:
            numbers = re.findall(r"\b\d+\b", line)
            pid = numbers[-1] if numbers else None
        if pid is None:
            continue
        device_match = re.search(r"(?:NPU|Device|device)\s*[:=]?\s*(\d+)", line)
        rows.append(
            {
                "pid": pid,
                "physical_device": device_match.group(1) if device_match else None,
                "raw": line.strip(),
            }
        )
    return rows


def write_mapping_evidence_csv(path: Path, mapping: dict[str, Any]) -> None:
    make_private_dir(path.parent)
    fields = [
        "evidence_level",
        "pid",
        "rank",
        "local_rank",
        "world_size",
        "logical_device",
        "visible_devices",
        "physical_device",
        "source",
    ]
    smi_by_pid = {
        str(row.get("pid")): row
        for row in mapping.get("npu_smi_processes", [])
        if isinstance(row, dict) and row.get("pid") is not None and row.get("pid") != ""
    }
    with open_private_text(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        wrote = False
        for proc in mapping.get("processes", []):
            if not isinstance(proc, dict):
                continue
            smi = smi_by_pid.get(str(proc.get("pid")), {})
            writer.writerow(
                {
                    "evidence_level": mapping.get("evidence_level"),
                    "pid": proc.get("pid"),
                    "rank": proc.get("rank"),
                    "local_rank": proc.get("local_rank"),
                    "world_size": proc.get("world_size"),
                    "logical_device": proc.get("logical_device"),
                    "visible_devices": proc.get("visible_devices"),
                    "physical_device": smi.get("physical_device"),
                    "source": "proc_environ+npu_smi" if smi else "proc_environ",
                }
            )
            wrote = True
        if not wrote:
            writer.writerow(
                {
                    "evidence_level": mapping.get("evidence_level"),
                    "visible_devices": mapping.get("visible_devices"),
                    "source": "environment_only" if mapping.get("visible_devices") else "unavailable",
                }
            )
    chmod_private_file(path)


def write_json_private(path: Path, payload: dict[str, Any]) -> None:
    write_private_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    chmod_private_file(path)
