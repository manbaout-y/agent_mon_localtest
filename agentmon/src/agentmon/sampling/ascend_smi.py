from __future__ import annotations

from dataclasses import dataclass
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol

from agentmon.config import AgentMonConfig
from agentmon.sampling.base import PeriodicSampler
from agentmon.storage.event_queue import EventSink


@dataclass
class ParseResult:
    parser: str
    confidence: float
    devices: list[dict[str, object]]
    extra: dict[str, object]


class NpuSmiParser(Protocol):
    name: str

    def detect(self, text: str) -> float: ...

    def parse(self, text: str) -> ParseResult: ...


class UsageKeyValueParser:
    name = "usage_key_value"

    def detect(self, text: str) -> float:
        keys = ["Memory Capacity", "Memory Usage", "Aicore Usage Rate", "HBM"]
        return min(1.0, sum(1 for key in keys if key.lower() in text.lower()) / 2.0)

    def parse(self, text: str) -> ParseResult:
        device: dict[str, object] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = [part.strip() for part in line.split(":", 1)]
            normalized = normalize_key(key)
            parsed = parse_number_with_unit(f"{value} {key}")
            if normalized:
                device[normalized] = parsed
            else:
                device.setdefault("extra", {})[key] = value
        return ParseResult(self.name, self.detect(text), [device] if device else [], {})


class Table910Parser:
    name = "table_910"

    def detect(self, text: str) -> float:
        if "+" in text and "|" in text and "NPU" in text:
            return 0.6
        return 0.0

    def parse(self, text: str) -> ParseResult:
        devices: list[dict[str, object]] = []
        current_npu_id: str | None = None
        for line in text.splitlines():
            if "|" not in line:
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not cells:
                continue
            npu_match = re.match(r"^(\d+)(?:\s+\S+)?$", cells[0])
            if npu_match and len(cells) >= 3 and not cells[0].isdigit():
                current_npu_id = npu_match.group(1)
                continue
            if not cells[0].isdigit():
                continue

            metrics_cell = " ".join(cells[2:]) if len(cells) >= 3 else " ".join(cells)
            device: dict[str, object] = {"device_id": current_npu_id or cells[0], "chip_id": cells[0]}
            util = parse_aicore_util(metrics_cell)
            if util is not None:
                device["aicore_util_pct"] = util
            hbm = parse_last_memory_ratio(metrics_cell)
            if hbm is not None:
                used_mib, total_mib = hbm
                device["hbm_used_bytes"] = int(used_mib * 1024 * 1024)
                device["hbm_total_bytes"] = int(total_mib * 1024 * 1024)
            devices.append(device)
        return ParseResult(self.name, self.detect(text), devices, {})


class ChineseKeyValueParser(UsageKeyValueParser):
    name = "chinese_key_value"

    def detect(self, text: str) -> float:
        return 0.7 if any(key in text for key in ("内存", "温度", "功率", "利用率")) and ":" in text else 0.0


class RawOnlyParser:
    name = "raw_only"

    def detect(self, text: str) -> float:
        return 0.01 if text else 0.0

    def parse(self, text: str) -> ParseResult:
        return ParseResult(self.name, self.detect(text), [], {"raw_parse": "unsupported"})


PARSERS: list[NpuSmiParser] = [
    UsageKeyValueParser(),
    ChineseKeyValueParser(),
    Table910Parser(),
    RawOnlyParser(),
]


class AscendSmiSampler(PeriodicSampler):
    source = "ascend"

    def __init__(self, sink: EventSink, cfg: AgentMonConfig, raw_dir: Path) -> None:
        super().__init__(sink, cfg.ascend.interval_seconds)
        self.cfg = cfg
        self.raw_dir = raw_dir
        self.command = resolve_npu_smi(cfg)
        self._sample_index = 0
        self._consecutive_timeouts = 0

    def sample(self) -> None:
        if self.command is None:
            self.sink.emit(
                "ascend",
                "capability",
                "exact",
                payload={"status": "not_found", "message": "npu-smi not found"},
                critical=True,
            )
            self._stop.set()
            return
        self._sample_index += 1
        raw_path = self.raw_dir / f"npu_smi_{self._sample_index:06d}.txt"
        started = time.monotonic()
        result = run_npu_smi(
            [self.command, "info"],
            timeout=self.cfg.ascend.command_timeout_seconds,
            raw_path=raw_path,
        )
        if result["status"] == "timeout":
            self._consecutive_timeouts += 1
        else:
            self._consecutive_timeouts = 0
        parsed = parse_npu_smi_text(str(result.get("stdout", "")))
        self.sink.emit(
            "ascend",
            "sample",
            "sampled" if parsed.confidence > 0.05 else "estimated",
            payload={
                "command": [self.command, "info"],
                "status": result["status"],
                "returncode": result.get("returncode"),
                "duration_ms": (time.monotonic() - started) * 1000,
                "raw_ref": str(raw_path),
                "parser": parsed.parser,
                "parser_confidence": parsed.confidence,
                "devices": parsed.devices,
                "extra": parsed.extra,
                "stderr_excerpt": result.get("stderr_excerpt"),
            },
        )
        if self._consecutive_timeouts >= 3:
            self.interval_seconds *= 2


def resolve_npu_smi(cfg: AgentMonConfig) -> str | None:
    if cfg.ascend.npu_smi and cfg.ascend.npu_smi != "auto":
        return cfg.ascend.npu_smi
    return shutil.which("npu-smi")


def run_npu_smi(argv: list[str], *, timeout: float, raw_path: Path | None = None) -> dict[str, object]:
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
            env={**os.environ, "LC_ALL": "C"},
        )
        stdout = completed.stdout
        stderr = completed.stderr
        status = "supported" if completed.returncode == 0 else "partial"
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
        status = "timeout"
        returncode = None
    except FileNotFoundError:
        stdout = ""
        stderr = "npu-smi not found"
        status = "not_found"
        returncode = None

    if raw_path is not None:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            f"$ {' '.join(argv)}\nstatus={status} returncode={returncode}\n\nSTDOUT\n{stdout}\n\nSTDERR\n{stderr}",
            encoding="utf-8",
        )
    return {
        "status": status,
        "returncode": returncode,
        "stdout": stdout,
        "stderr_excerpt": stderr[-2000:],
    }


def parse_npu_smi_text(text: str) -> ParseResult:
    parser = max(PARSERS, key=lambda item: item.detect(text))
    return parser.parse(text)


def normalize_key(key: str) -> str | None:
    lowered = key.lower()
    mapping = {
        "memory capacity": "hbm_total_bytes",
        "memory-capacity": "hbm_total_bytes",
        "hbm total": "hbm_total_bytes",
        "hbm-total": "hbm_total_bytes",
        "hbm usage": "hbm_used_bytes",
        "hbm-usage": "hbm_used_bytes",
        "memory used": "hbm_used_bytes",
        "memory-used": "hbm_used_bytes",
        "aicore usage rate": "aicore_util_pct",
        "aicore-usage-rate": "aicore_util_pct",
        "ai core": "aicore_util_pct",
        "aicpu usage rate": "aicpu_util_pct",
        "temperature": "temperature_c",
        "power": "power_w",
        "aicore frequency": "aicore_frequency_mhz",
        "ai core frequency": "aicore_frequency_mhz",
        "frequency": "frequency_mhz",
        "hbm bandwidth": "hbm_bandwidth_bytes_per_s",
        "memory bandwidth": "hbm_bandwidth_bytes_per_s",
        "health": "health",
    }
    for needle, normalized in mapping.items():
        if needle in lowered:
            return normalized
    if "内存" in key and ("总" in key or "容量" in key):
        return "hbm_total_bytes"
    if "内存" in key and ("用" in key or "已" in key):
        return "hbm_used_bytes"
    if "memory usage" in lowered and "rate" not in lowered:
        return "hbm_used_bytes"
    if "温度" in key:
        return "temperature_c"
    if "功率" in key:
        return "power_w"
    if "利用率" in key:
        return "aicore_util_pct"
    return None


def parse_number_with_unit(value: str) -> object:
    match = re.search(r"(-?\d+(?:\.\d+)?)", value)
    if not match:
        return value
    number = float(match.group(1))
    lowered = value.lower()
    if "%" in lowered:
        return number
    if "/s" in lowered and ("gib" in lowered or "gb" in lowered):
        return number * 1024**3
    if "/s" in lowered and ("mib" in lowered or "mb" in lowered):
        return number * 1024**2
    if "mhz" in lowered:
        return number
    if "ghz" in lowered:
        return number * 1000
    if "gib" in lowered or "gb" in lowered:
        return int(number * 1024**3)
    if "mib" in lowered or "mb" in lowered:
        return int(number * 1024**2)
    if "kib" in lowered or "kb" in lowered:
        return int(number * 1024)
    if "w" in lowered:
        return number
    if "c" in lowered or "℃" in value:
        return number
    return int(number) if number.is_integer() else number


def parse_aicore_util(text: str) -> float | None:
    explicit = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if explicit:
        return float(explicit.group(1))
    first_ratio = re.search(r"\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?", text)
    prefix = text[: first_ratio.start()] if first_ratio else text
    values = re.findall(r"\d+(?:\.\d+)?", prefix)
    if not values:
        return None
    return float(values[0])


def parse_last_memory_ratio(text: str) -> tuple[float, float] | None:
    ratios = [
        (float(match.group(1)), float(match.group(2)))
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*(?:MB|MiB|GB|GiB)?", text, re.I)
    ]
    ratios = [(used, total) for used, total in ratios if total > 0]
    if not ratios:
        return None
    return ratios[-1]
