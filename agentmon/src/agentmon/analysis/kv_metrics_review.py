"""Offline review of one service-level snapshot; never a request/KV trace."""
from __future__ import annotations

import math
from agentmon.analysis.prometheus import parse_prometheus_text


COUNTERS = ("prefix_cache_queries_total", "prefix_cache_hits_total",
            "external_prefix_cache_queries_total", "external_prefix_cache_hits_total",
            "num_preemptions_total")
GAUGES = ("num_requests_running", "num_requests_waiting", "kv_cache_usage_perc")


def review_metrics(text: str, *, model: str, engine: str, historical_block_size: int = 128,
                   max_model_len: int = 262144) -> dict:
    """Select exact model/engine series; absent/ambiguous/nonfinite remain unknown.

    Config labels are preserved strings, not interpreted as physical allocation.
    Ratios below are arithmetic only; counter units and windows require source.
    """
    if max_model_len <= 0 or historical_block_size <= 0:
        raise ValueError("reference sizes must be positive")
    parsed = parse_prometheus_text(text)
    samples = [s for s in parsed
               if s.labels.get("model_name") == model and s.labels.get("engine") == engine]
    issues = []

    def one(name):
        candidates = samples
        if name == "cache_config_info":
            # Current exporter omits model_name on config info; preserve engine-only scope.
            candidates = [s for s in parsed if s.labels.get("engine") == engine
                          and s.labels.get("model_name") in (None, model)]
        rows = [s for s in candidates if s.name == "vllm:" + name]
        if len(rows) != 1 or not math.isfinite(rows[0].value) or rows[0].value < 0:
            issues.append({"metric": name, "status": "UNRESOLVED", "matching_series": len(rows)})
            return None
        return rows[0]

    values = {}
    for name in COUNTERS + GAUGES:
        sample = one(name)
        values[name] = sample.value if sample else None
    sample = one("cache_config_info")
    config = dict(sample.labels) if sample and sample.value == 1 else {}
    if sample and sample.value != 1:
        issues.append({"metric": "cache_config_info", "status": "UNRESOLVED", "reason": "info_value_not_one"})

    def positive_integer(key):
        raw = config.get(key, "")
        return int(raw) if raw.isdigit() and int(raw) > 0 else None

    block_size, blocks, capacity = (positive_integer(k) for k in
                                    ("block_size", "num_gpu_blocks", "kv_cache_size_tokens"))
    queries, hits = values[COUNTERS[0]], values[COUNTERS[1]]
    ratio = hits / queries if queries and hits is not None else None
    product = blocks * block_size if blocks and block_size else None
    return {
        "schema_version": "agentmon-kv-metrics-review-v1",
        "scope": {"model_name": model, "engine": engine},
        "evidence_level": "SERVICE_SNAPSHOT_ONLY", "values": values,
        "cache_config_labels": config, "issues": issues,
        "cache_config_scope": "ENGINE_ONLY" if config and "model_name" not in config else "MODEL_ENGINE" if config else "UNRESOLVED",
        "derived": {"prefix_counter_ratio": ratio, "counter_unit": "UNRESOLVED",
                    "counter_window_and_reset": "UNRESOLVED",
                    "num_gpu_blocks_times_block_size": product,
                    "capacity_matches_simple_product": product == capacity if product and capacity else None,
                    "capacity_div_reference_max_model_len": capacity / max_model_len if capacity else None,
                    "reference_max_model_len": max_model_len,
                    "actual_max_num_seqs": "UNRESOLVED"},
        "block_size_investigation": {"historical": historical_block_size, "metric_label": block_size,
                                     "discrepancy_observed": block_size != historical_block_size if block_size else None,
                                     "resolution": "UNRESOLVED", "physical_layout": "NOT_CAPTURED"},
        "request_binding": "NOT_CAPTURED", "request_kv_events": "NOT_CAPTURED",
        "tensor_capture": "NOT_CAPTURED", "kv_offload_observed": "NOT_PROVEN",
    }
