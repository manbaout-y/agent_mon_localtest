from __future__ import annotations

import gzip
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from agentmon.analysis.prometheus import match_semantic_metrics, parse_prometheus_text, samples_to_json
from agentmon.config import AgentMonConfig
from agentmon.sampling.base import PeriodicSampler
from agentmon.storage.event_queue import EventSink


MAX_METRICS_BYTES = 16 * 1024 * 1024


class VllmMetricsSampler(PeriodicSampler):
    source = "vllm"

    def __init__(self, sink: EventSink, cfg: AgentMonConfig) -> None:
        super().__init__(sink, cfg.vllm.interval_seconds)
        self.cfg = cfg

    def sample(self) -> None:
        started = time.monotonic()
        try:
            text, status, byte_count = fetch_metrics(
                self.cfg.vllm.metrics_url,
                timeout=self.cfg.vllm.request_timeout_seconds,
            )
            samples = parse_prometheus_text(text)
            names = sorted({sample.name for sample in samples})
            self.sink.emit(
                "vllm",
                "sample",
                "sampled",
                payload={
                    "status": status,
                    "bytes": byte_count,
                    "scrape_duration_ms": (time.monotonic() - started) * 1000,
                    "metric_count": len(samples),
                    "metric_names": names,
                    "semantic_matches": match_semantic_metrics(names),
                    "samples": samples_to_json(samples),
                },
            )
        except Exception as exc:
            self.sink.emit(
                "vllm",
                "error",
                "sampled",
                payload={
                    "status": "unavailable",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "scrape_duration_ms": (time.monotonic() - started) * 1000,
                },
                critical=True,
            )


def fetch_metrics(url: str, *, timeout: float, raw_path: str | Path | None = None) -> tuple[str, int, int]:
    request = Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "agentmon/0.1"})
    with urlopen(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200))
        raw = response.read(MAX_METRICS_BYTES + 1)
        if len(raw) > MAX_METRICS_BYTES:
            raise ValueError("metrics response exceeded 16 MiB")
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    if raw_path is not None:
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_path).write_bytes(raw)
    return raw.decode("utf-8", errors="replace"), status, len(raw)


def probe_metrics(url: str, *, timeout: float, raw_path: str | Path | None = None) -> dict[str, object]:
    started = time.monotonic()
    try:
        text, status, byte_count = fetch_metrics(url, timeout=timeout, raw_path=raw_path)
        samples = parse_prometheus_text(text)
        names = sorted({sample.name for sample in samples})
        return {
            "status": "supported" if status < 400 else "partial",
            "http_status": status,
            "latency_ms": (time.monotonic() - started) * 1000,
            "bytes": byte_count,
            "metric_count": len(samples),
            "metric_names": names,
            "semantic_matches": match_semantic_metrics(names),
            "raw_ref": str(raw_path) if raw_path else None,
        }
    except URLError as exc:
        return {
            "status": "unsupported",
            "latency_ms": (time.monotonic() - started) * 1000,
            "error": str(exc),
            "raw_ref": str(raw_path) if raw_path else None,
        }
    except Exception as exc:
        return {
            "status": "partial",
            "latency_ms": (time.monotonic() - started) * 1000,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "raw_ref": str(raw_path) if raw_path else None,
        }
