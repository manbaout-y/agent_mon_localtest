from __future__ import annotations

from typing import Any


FORMULA_VERSION = "kv_pool_bytes_per_rank.v1"

DTYPE_BYTES = {
    "auto": None,
    "bfloat16": 2,
    "bf16": 2,
    "float16": 2,
    "fp16": 2,
    "float32": 4,
    "fp32": 4,
    "fp8": 1,
    "fp8_e4m3": 1,
    "fp8_e5m2": 1,
}

REQUIRED_POOL_INPUTS = [
    "num_gpu_blocks",
    "block_size",
    "cache_dtype",
    "num_layers_per_rank",
    "num_kv_heads_per_rank",
    "head_dim",
]


def calculate_kv_pool(inputs: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
    normalized = dict(inputs)
    missing = [key for key in REQUIRED_POOL_INPUTS if normalized.get(key) in {None, ""}]
    dtype_bytes = cache_dtype_bytes(normalized.get("cache_dtype"))
    if dtype_bytes is None and "cache_dtype" not in missing:
        missing.append("cache_dtype_bytes")
    result = {
        "formula_version": FORMULA_VERSION,
        "source": source or normalized.get("source"),
        "source_quality": "complete" if not missing else "partial",
        "calculation_inputs": {key: normalized.get(key) for key in REQUIRED_POOL_INPUTS},
        "dtype_bytes": dtype_bytes,
        "missing_inputs": missing,
        "bytes_per_block_per_rank": None,
        "allocated_pool_bytes_per_rank": None,
    }
    if missing:
        return result
    try:
        bytes_per_block = (
            int(normalized["block_size"])
            * int(normalized["num_layers_per_rank"])
            * int(normalized["num_kv_heads_per_rank"])
            * int(normalized["head_dim"])
            * 2
            * int(dtype_bytes)
        )
        result["bytes_per_block_per_rank"] = bytes_per_block
        result["allocated_pool_bytes_per_rank"] = int(normalized["num_gpu_blocks"]) * bytes_per_block
        result["source_quality"] = "complete"
        return result
    except (TypeError, ValueError) as exc:
        result["source_quality"] = "partial"
        result["missing_inputs"] = [*missing, f"invalid_input:{type(exc).__name__}"]
        return result


def cache_dtype_bytes(dtype: Any) -> int | None:
    if dtype is None:
        return None
    text = str(dtype).lower().replace("torch.", "")
    return DTYPE_BYTES.get(text)
