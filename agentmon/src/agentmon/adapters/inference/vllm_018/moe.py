from __future__ import annotations

import base64
import hashlib
import math
import os
from typing import Any
import zlib


MOE_MODE_ENV = "AGENTMON_VLLM_P2_MOE"
MOE_MODE_ENV_ALIAS = "AGENTMON_VLLM_P2_MOE_ROUTING"
MOE_CHUNK_TOKENS_ENV = "AGENTMON_VLLM_P2_MOE_CHUNK_TOKENS"
MOE_MODES = {"off", "summary", "trace"}
DEFAULT_MOE_CHUNK_TOKENS = 256
MAX_MOE_CHUNK_TOKENS = 16384


def moe_mode_from_env(env: dict[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    raw = values.get(MOE_MODE_ENV, values.get(MOE_MODE_ENV_ALIAS, "off"))
    normalized = str(raw or "off").strip().lower()
    return normalized if normalized in MOE_MODES else "off"


def moe_chunk_tokens_from_env(env: dict[str, str] | None = None) -> int:
    values = os.environ if env is None else env
    raw = values.get(MOE_CHUNK_TOKENS_ENV, str(DEFAULT_MOE_CHUNK_TOKENS))
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = DEFAULT_MOE_CHUNK_TOKENS
    return max(1, min(parsed, MAX_MOE_CHUNK_TOKENS))


def routed_expert_events(
    output: Any,
    *,
    mode: str,
    chunk_tokens: int,
    prompt_tokens: int | None,
    configured_expert_count: int | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    if mode not in {"summary", "trace"}:
        return []
    completions = list(getattr(output, "outputs", None) or [])
    if not completions and getattr(output, "routed_experts", None) is not None:
        completions = [output]
    if not completions:
        return [
            (
                "vllm.moe.routing.summary",
                unresolved_summary(
                    mode=mode,
                    completion_index=None,
                    reason="completion_outputs_unavailable",
                ),
            )
        ]

    events: list[tuple[str, dict[str, Any]]] = []
    for completion_index, completion in enumerate(completions):
        routed = getattr(completion, "routed_experts", None)
        if routed is None and isinstance(completion, dict):
            routed = completion.get("routed_experts")
        if routed is None:
            events.append(
                (
                    "vllm.moe.routing.summary",
                    unresolved_summary(
                        mode=mode,
                        completion_index=completion_index,
                        reason="routed_experts_unavailable",
                    ),
                )
            )
            continue
        try:
            array = as_int32_route_array(routed)
        except Exception as exc:
            events.append(
                (
                    "vllm.moe.routing.summary",
                    unresolved_summary(
                        mode=mode,
                        completion_index=completion_index,
                        reason=f"routed_experts_unreadable:{type(exc).__name__}",
                    ),
                )
            )
            continue
        expected_tokens = expected_routed_token_count(output, completion, prompt_tokens)
        summary = summarize_route_array(
            array,
            mode=mode,
            completion_index=completion_index,
            expected_tokens=expected_tokens,
            configured_expert_count=configured_expert_count,
        )
        chunks = (
            encode_route_chunks(
                array,
                completion_index=completion_index,
                chunk_tokens=chunk_tokens,
            )
            if mode == "trace" and summary["status"] != "UNRESOLVED"
            else []
        )
        summary["trace_chunk_count"] = len(chunks)
        events.append(("vllm.moe.routing.summary", summary))
        events.extend(("vllm.moe.routing.chunk", payload) for payload in chunks)
    return events


def as_int32_route_array(value: Any) -> Any:
    import numpy as np

    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    array = np.asarray(value)
    if array.ndim != 3:
        raise ValueError(f"expected 3 dimensions, got {array.ndim}")
    return np.ascontiguousarray(array, dtype=np.dtype("<i4"))


def summarize_route_array(
    array: Any,
    *,
    mode: str,
    completion_index: int,
    expected_tokens: int | None,
    configured_expert_count: int | None,
) -> dict[str, Any]:
    import numpy as np

    token_count, layer_count, top_k = (int(value) for value in array.shape)
    nonnegative = array[array >= 0]
    inferred_expert_count = int(nonnegative.max()) + 1 if nonnegative.size else None
    expert_count_mismatch = bool(
        configured_expert_count is not None
        and inferred_expert_count is not None
        and inferred_expert_count > configured_expert_count
    )
    expert_count = (
        max(configured_expert_count, inferred_expert_count)
        if configured_expert_count is not None and inferred_expert_count is not None
        else configured_expert_count or inferred_expert_count
    )
    expert_count_quality = (
        "model_config_inconsistent_with_observed_id_range"
        if expert_count_mismatch
        else "model_config"
        if configured_expert_count is not None
        else "inferred_from_observed_id_range"
        if inferred_expert_count is not None
        else "UNKNOWN"
    )
    limitations = [
        "logical_router_ids_only",
        "physical_rank_mapping_requires_contemporaneous_expert_map",
        "per_expert_kernel_time_not_observed",
    ]
    if configured_expert_count is None:
        limitations.append("expert_count_not_discovered_from_model_config")
    if expert_count_mismatch:
        limitations.append("observed_expert_id_exceeds_configured_expert_domain")
    if bool(np.any(array < 0)):
        limitations.append("negative_or_padding_expert_ids_excluded_from_balance_metrics")

    layer_summaries: list[dict[str, Any]] = []
    for layer_index in range(layer_count):
        values = array[:, layer_index, :].reshape(-1)
        values = values[values >= 0]
        layer_summaries.append(
            layer_balance_summary(
                values,
                layer_index=layer_index,
                expert_count=expert_count,
            )
        )

    alignment = (
        "UNKNOWN"
        if expected_tokens is None
        else "MATCH"
        if expected_tokens == token_count
        else "MISMATCH"
    )
    if alignment == "MISMATCH":
        limitations.append("routed_token_count_does_not_match_request_token_accounting")
    elif alignment == "UNKNOWN":
        limitations.append("request_token_alignment_unresolved")
    status = (
        "COMPLETE"
        if alignment == "MATCH"
        and configured_expert_count is not None
        and not expert_count_mismatch
        else "PARTIAL"
    )
    valid_layers = [row for row in layer_summaries if row["assignment_count"] > 0]
    return {
        "schema_version": "1.0",
        "status": status,
        "mode": mode,
        "completion_index": completion_index,
        "shape": [token_count, layer_count, top_k],
        "dtype": "int32_le",
        "routed_token_count": token_count,
        "expected_routed_token_count": expected_tokens,
        "token_alignment": alignment,
        "layer_count": layer_count,
        "top_k": top_k,
        "assignment_count": int(nonnegative.size),
        "expert_count": expert_count,
        "expert_count_quality": expert_count_quality,
        "unique_logical_expert_count": int(np.unique(nonnegative).size) if nonnegative.size else 0,
        "max_expert_share": max_or_none(row["max_expert_share"] for row in valid_layers),
        "min_normalized_entropy": min_or_none(
            row["normalized_entropy"] for row in valid_layers
        ),
        "max_load_coefficient_of_variation": max_or_none(
            row["load_coefficient_of_variation"] for row in valid_layers
        ),
        "layers": layer_summaries,
        "trace_chunk_count": 0,
        "quality": "exact_logical_router_topk_ids",
        "evidence_source": "vllm.CompletionOutput.routed_experts",
        "limitations": limitations,
        "prohibited_conclusions": [
            "do_not_infer_physical_expert_or_rank_without_expert_map",
            "do_not_infer_expert_execution_time_from_route_ids",
        ],
    }


def layer_balance_summary(
    values: Any,
    *,
    layer_index: int,
    expert_count: int | None,
) -> dict[str, Any]:
    import numpy as np

    assignment_count = int(values.size)
    if assignment_count == 0 or expert_count is None or expert_count <= 0:
        return {
            "layer_index": layer_index,
            "assignment_count": assignment_count,
            "unique_expert_count": 0,
            "top_expert_id": None,
            "max_expert_share": None,
            "normalized_entropy": None,
            "load_coefficient_of_variation": None,
            "status": "UNRESOLVED",
        }
    counts = np.bincount(values.astype(np.int64), minlength=expert_count)
    probabilities = counts[counts > 0] / assignment_count
    entropy = float(-np.sum(probabilities * np.log(probabilities)))
    normalized_entropy = (
        entropy / math.log(counts.size) if counts.size > 1 else 1.0
    )
    mean = float(np.mean(counts))
    coefficient = float(np.std(counts) / mean) if mean > 0 else None
    return {
        "layer_index": layer_index,
        "assignment_count": assignment_count,
        "unique_expert_count": int(np.count_nonzero(counts)),
        "top_expert_id": int(np.argmax(counts)),
        "max_expert_share": float(np.max(counts) / assignment_count),
        "normalized_entropy": normalized_entropy,
        "load_coefficient_of_variation": coefficient,
        "status": "COMPLETE",
    }


def encode_route_chunks(
    array: Any,
    *,
    completion_index: int,
    chunk_tokens: int,
) -> list[dict[str, Any]]:
    token_count = int(array.shape[0])
    if token_count == 0:
        return []
    chunk_count = math.ceil(token_count / chunk_tokens)
    chunks: list[dict[str, Any]] = []
    for chunk_index, token_start in enumerate(range(0, token_count, chunk_tokens)):
        token_end = min(token_count, token_start + chunk_tokens)
        chunk = array[token_start:token_end]
        raw = chunk.tobytes(order="C")
        compressed = zlib.compress(raw)
        chunks.append(
            {
                "schema_version": "1.0",
                "status": "COMPLETE",
                "mode": "trace",
                "completion_index": completion_index,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "token_start": token_start,
                "token_end": token_end,
                "shape": [int(value) for value in chunk.shape],
                "dtype": "int32_le",
                "encoding": "base64+zlib",
                "raw_byte_count": len(raw),
                "compressed_byte_count": len(compressed),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "data": base64.b64encode(compressed).decode("ascii"),
                "quality": "lossless_exact_logical_router_topk_ids",
            }
        )
    return chunks


def decode_route_chunk(payload: dict[str, Any]) -> Any:
    import numpy as np

    if payload.get("encoding") != "base64+zlib" or payload.get("dtype") != "int32_le":
        raise ValueError("unsupported routed-expert chunk encoding")
    compressed = base64.b64decode(str(payload["data"]).encode("ascii"))
    raw = zlib.decompress(compressed)
    if hashlib.sha256(raw).hexdigest() != payload.get("raw_sha256"):
        raise ValueError("routed-expert chunk checksum mismatch")
    shape = tuple(int(value) for value in payload.get("shape") or [])
    if len(shape) != 3:
        raise ValueError("routed-expert chunk shape must have three dimensions")
    return np.frombuffer(raw, dtype=np.dtype("<i4")).reshape(shape)


def expected_routed_token_count(
    output: Any,
    completion: Any,
    prompt_tokens: int | None,
) -> int | None:
    prompt = prompt_tokens
    if prompt is None:
        prompt_ids = getattr(output, "prompt_token_ids", None)
        try:
            prompt = len(prompt_ids) if prompt_ids is not None else None
        except TypeError:
            prompt = None
    token_ids = (
        completion.get("token_ids")
        if isinstance(completion, dict)
        else getattr(completion, "token_ids", None)
    )
    try:
        generated = len(token_ids) if token_ids is not None else None
    except TypeError:
        generated = None
    if prompt is None or generated is None:
        return None
    return max(0, int(prompt) + int(generated) - 1)


def unresolved_summary(
    *,
    mode: str,
    completion_index: int | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "UNRESOLVED",
        "mode": mode,
        "completion_index": completion_index,
        "shape": None,
        "routed_token_count": None,
        "expected_routed_token_count": None,
        "token_alignment": "UNKNOWN",
        "layer_count": None,
        "top_k": None,
        "assignment_count": None,
        "expert_count": None,
        "trace_chunk_count": 0,
        "quality": "unavailable",
        "evidence_source": "vllm.CompletionOutput.routed_experts",
        "limitations": [reason],
        "prohibited_conclusions": [
            "do_not_infer_routing_when_routed_experts_is_unavailable"
        ],
    }


def max_or_none(values: Any) -> float | None:
    known = [float(value) for value in values if value is not None]
    return max(known) if known else None


def min_or_none(values: Any) -> float | None:
    known = [float(value) for value in values if value is not None]
    return min(known) if known else None
