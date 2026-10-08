from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable


@dataclass(frozen=True)
class MetricSample:
    name: str
    labels: dict[str, str]
    value: float
    metric_type: str = "untyped"


_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[+-]?(?:NaN|Inf|Infinity|[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?|[0-9]*\.[0-9]+(?:[eE][+-]?[0-9]+)?))"
    r"(?:\s+\d+)?$"
)


def parse_prometheus_text(text: str) -> list[MetricSample]:
    types: dict[str, str] = {}
    samples: list[MetricSample] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line.split(maxsplit=3)
            if len(parts) >= 4 and parts[1] == "TYPE":
                types[parts[2]] = parts[3]
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        labels = parse_labels(match.group("labels") or "")
        samples.append(
            MetricSample(
                name=name,
                labels=labels,
                value=parse_prometheus_float(match.group("value")),
                metric_type=_metric_type_for(name, types),
            )
        )
    return samples


def parse_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    if not raw:
        return labels
    pos = 0
    length = len(raw)
    while pos < length:
        while pos < length and raw[pos] in " ,":
            pos += 1
        start = pos
        while pos < length and raw[pos] != "=":
            pos += 1
        if pos >= length:
            break
        key = raw[start:pos].strip()
        pos += 1
        if pos >= length or raw[pos] != '"':
            break
        pos += 1
        value_chars: list[str] = []
        escaped = False
        while pos < length:
            char = raw[pos]
            pos += 1
            if escaped:
                if char == "n":
                    value_chars.append("\n")
                elif char in {'"', "\\", "\n"}:
                    value_chars.append(char)
                else:
                    value_chars.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                break
            value_chars.append(char)
        labels[key] = "".join(value_chars)
        while pos < length and raw[pos] not in ",":
            pos += 1
        if pos < length and raw[pos] == ",":
            pos += 1
    return labels


def parse_prometheus_float(raw: str) -> float:
    lowered = raw.lower()
    if lowered in {"nan", "+nan", "-nan"}:
        return math.nan
    if lowered in {"inf", "+inf", "infinity", "+infinity"}:
        return math.inf
    if lowered in {"-inf", "-infinity"}:
        return -math.inf
    return float(raw)


def _metric_type_for(name: str, types: dict[str, str]) -> str:
    if name in types:
        return types[name]
    for suffix in ("_bucket", "_sum", "_count"):
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            if base in types:
                return types[base]
    return "untyped"


def samples_to_json(samples: Iterable[MetricSample], *, limit: int | None = None) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for index, sample in enumerate(samples):
        if limit is not None and index >= limit:
            break
        output.append(
            {
                "name": sample.name,
                "labels": sample.labels,
                "value": sample.value,
                "metric_type": sample.metric_type,
            }
        )
    return output


SEMANTIC_METRICS = {
    "prompt_tokens_total": ["vllm:prompt_tokens", "vllm:prompt_tokens_total"],
    "generation_tokens_total": ["vllm:generation_tokens", "vllm:generation_tokens_total"],
    "request_success_total": ["vllm:request_success_total", "vllm:num_requests_total"],
    "preemptions_total": ["vllm:num_preemptions_total", "vllm:preemptions_total"],
    "running_requests": ["vllm:num_requests_running", "vllm:running_requests"],
    "waiting_requests": ["vllm:num_requests_waiting", "vllm:waiting_requests"],
    "kv_cache_usage_ratio": ["vllm:gpu_cache_usage_perc", "vllm:kv_cache_usage_perc"],
}


def match_semantic_metrics(names: Iterable[str]) -> dict[str, str]:
    available = set(names)
    matched: dict[str, str] = {}
    for semantic, candidates in SEMANTIC_METRICS.items():
        for candidate in candidates:
            if candidate in available:
                matched[semantic] = candidate
                break
    return matched
