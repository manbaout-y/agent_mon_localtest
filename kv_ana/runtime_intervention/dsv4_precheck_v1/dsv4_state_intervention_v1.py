"""Fail-closed DeepSeek-V4 compressed-state intervention helper.

This module is imported by the patched runtime only when
``DSV4_STATE_INTERVENTION`` is not ``off``.  It intentionally has no import-time
dependency on torch; the external-artifact path imports torch lazily.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, MutableSequence, Sequence


TRACE_SCHEMA_VERSION = "dsv4-state-intervention-precheck-v1"
SUPPORTED_MODES = {"identity", "zero_selected", "external"}
SUPPORTED_LAYER = 2
SUPPORTED_COMPRESS_RATIO = 4


class InterventionError(RuntimeError):
    """Raised when an in-scope intervention contract is not satisfied."""


def _read_tag(environ: Mapping[str, str]) -> str:
    tag_path = environ.get("DSV4_STATE_INTERVENTION_TAG_FILE") or environ.get("DSV4_KV_TAG_FILE")
    if not tag_path:
        return ""
    path = Path(tag_path)
    if not path.is_file():
        raise InterventionError(f"request tag file does not exist: {path}")
    return path.read_text(encoding="utf-8").strip()


def _read_spec(environ: Mapping[str, str]) -> tuple[dict[str, Any], Path]:
    value = environ.get("DSV4_STATE_INTERVENTION_SPEC", "").strip()
    if not value:
        raise InterventionError("DSV4_STATE_INTERVENTION_SPEC is required for a non-off mode")
    path = Path(value)
    if not path.is_file():
        raise InterventionError(f"intervention spec does not exist: {path}")
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InterventionError(f"cannot read intervention spec {path}: {exc}") from exc
    if not isinstance(spec, dict):
        raise InterventionError("intervention spec must be a JSON object")
    return spec, path


def _extract_layer_id(layer_name: Any) -> int:
    if isinstance(layer_name, int):
        return layer_name
    matches = re.findall(r"(?:^|\.)(?:layers?|h)\.(\d+)(?:\.|$)", str(layer_name))
    if not matches:
        raise InterventionError(f"cannot extract transformer layer from layer_name={layer_name!r}")
    return int(matches[-1])


def _shape(value: Any) -> tuple[int, ...]:
    shape = getattr(value, "shape", None)
    if shape is not None:
        return tuple(int(item) for item in shape)
    result: list[int] = []
    cursor = value
    while isinstance(cursor, (list, tuple)):
        result.append(len(cursor))
        if not cursor:
            break
        cursor = cursor[0]
    return tuple(result)


def _to_list(value: Any) -> Any:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return copy.deepcopy(value)


def _flatten_numbers(value: Any):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_numbers(item)
    else:
        yield float(value)


def _norm(value: Any) -> float:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "float") and hasattr(value, "norm"):
        return float(value.float().norm().item())
    return math.sqrt(sum(item * item for item in _flatten_numbers(value)))


def _select_rows(value: Any, rows: Sequence[int]) -> Any:
    if isinstance(value, (list, tuple)):
        return [copy.deepcopy(value[row]) for row in rows]
    index = value.new_tensor(list(rows)).long()
    return value.index_select(0, index).detach().clone()


def _subtract(left: Any, right: Any) -> Any:
    if not isinstance(left, (list, tuple)):
        return left - right
    if len(left) != len(right):
        raise InterventionError("cannot compute delta norm for unequal nested shapes")
    return [_subtract(a, b) for a, b in zip(left, right)]


def _assign_rows(target: Any, rows: Sequence[int], replacement: Any) -> None:
    if isinstance(target, MutableSequence):
        for index, row in enumerate(rows):
            target[row] = copy.deepcopy(replacement[index])
        return
    index = target.new_tensor(list(rows)).long()
    target.index_copy_(0, index, replacement)


def _zero_rows(target: Any, rows: Sequence[int]) -> None:
    if isinstance(target, MutableSequence):
        for row in rows:
            target[row] = [0 for _ in target[row]]
        return
    index = target.new_tensor(list(rows)).long()
    target.index_fill_(0, index, 0)


def _validate_rows(name: str, rows: Any, row_count: int) -> list[int]:
    if not isinstance(rows, list) or not rows:
        raise InterventionError(f"{name} must be a non-empty JSON integer list")
    if any(isinstance(row, bool) or not isinstance(row, int) for row in rows):
        raise InterventionError(f"{name} must contain integers")
    if len(set(rows)) != len(rows):
        raise InterventionError(f"{name} must not contain duplicates")
    if min(rows) < 0 or max(rows) >= row_count:
        raise InterventionError(f"{name} out of bounds for {row_count} rows: {rows}")
    return rows


def _default_artifact_loader(path: Path) -> Any:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised on the server
        raise InterventionError("external mode requires torch to load the .pt artifact") from exc
    return torch.load(path, map_location="cpu", weights_only=False)


def _append_trace(path_value: str, record: dict[str, Any]) -> None:
    if not path_value:
        raise InterventionError("DSV4_STATE_INTERVENTION_TRACE is required for a non-off mode")
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def _base_record(
    *, mode: str, request_tag: str, layer: int, compress_ratio: int, compressed_kv: Any
) -> dict[str, Any]:
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "timestamp_ns": time.time_ns(),
        "request_tag": request_tag,
        "experiment_class": None,
        "mode": mode,
        "layer": layer,
        "compress_ratio": compress_ratio,
        "tensor_shape": list(_shape(compressed_kv)),
        "dtype": str(getattr(compressed_kv, "dtype", "python")),
        "device": str(getattr(compressed_kv, "device", "python")),
        "source_artifact": None,
        "source_rows_count": 0,
        "target_rows_count": 0,
        "target_rows": [],
        "slot_mapping_match": None,
        "pre_norm": None,
        "post_norm": None,
        "delta_norm": None,
        "applied": False,
        "skip_reason": None,
        "error": None,
    }


def apply_runtime_intervention(
    *,
    compressed_kv: Any,
    slot_mapping: Any,
    layer_name: Any,
    compress_ratio: int,
    mode: str,
    environ: Mapping[str, str] | None = None,
    artifact_loader: Callable[[Path], Any] | None = None,
) -> Any:
    """Apply one explicit C4/layer-2 intervention or fail closed.

    Calls for layers outside the configured target are recorded as out of scope;
    they are expected because the same runtime hook executes at every layer.
    Once a call is in target scope, every configured identity/shape/row check is
    strict and a mismatch raises :class:`InterventionError`.
    """

    env = os.environ if environ is None else environ
    normalized_mode = mode.strip().lower()
    if normalized_mode not in SUPPORTED_MODES:
        raise InterventionError(f"unsupported intervention mode: {mode!r}")

    layer = _extract_layer_id(layer_name)
    request_tag = ""
    record = _base_record(
        mode=normalized_mode,
        request_tag=request_tag,
        layer=layer,
        compress_ratio=int(compress_ratio),
        compressed_kv=compressed_kv,
    )
    trace_path = env.get("DSV4_STATE_INTERVENTION_TRACE", "")

    try:
        request_tag = _read_tag(env)
        record["request_tag"] = request_tag
        if not request_tag:
            record["skip_reason"] = "empty_request_tag"
            _append_trace(trace_path, record)
            return compressed_kv

        spec, _ = _read_spec(env)
        required = {
            "request_tag",
            "experiment_class",
            "mode",
            "target_layer",
            "compress_ratio",
            "source_artifact",
            "source_rows",
            "target_rows",
            "expected_tensor_shape",
            "metadata",
        }
        missing = sorted(required - set(spec))
        if missing:
            raise InterventionError(f"intervention spec missing fields: {missing}")

        record["experiment_class"] = spec["experiment_class"]
        record["source_artifact"] = spec["source_artifact"] or None
        target_layer = int(spec["target_layer"])
        target_ratio = int(spec["compress_ratio"])

        if target_layer != SUPPORTED_LAYER or target_ratio != SUPPORTED_COMPRESS_RATIO:
            raise InterventionError("v1 supports only target_layer=2 and compress_ratio=4")
        if layer != target_layer or int(compress_ratio) != target_ratio:
            record["skip_reason"] = "out_of_scope_layer_or_ratio"
            _append_trace(trace_path, record)
            return compressed_kv

        if spec["request_tag"] != request_tag:
            raise InterventionError(
                f"request tag mismatch: runtime={request_tag!r}, spec={spec['request_tag']!r}"
            )
        if str(spec["mode"]).strip().lower() != normalized_mode:
            raise InterventionError(
                f"mode mismatch: runtime={normalized_mode!r}, spec={spec['mode']!r}"
            )
        if normalized_mode in {"zero_selected", "external"} and spec["experiment_class"] != "CAUSAL_PRECHECK":
            raise InterventionError("mutation modes require experiment_class=CAUSAL_PRECHECK")

        tensor_shape = _shape(compressed_kv)
        expected_shape = tuple(int(item) for item in spec["expected_tensor_shape"])
        if len(tensor_shape) != 2:
            raise InterventionError(f"runtime compressed_kv must be 2-D, got {tensor_shape}")
        if tensor_shape != expected_shape:
            raise InterventionError(
                f"runtime tensor shape mismatch: expected={expected_shape}, actual={tensor_shape}"
            )

        target_rows = _validate_rows("target_rows", spec["target_rows"], tensor_shape[0])
        record["target_rows"] = target_rows
        record["target_rows_count"] = len(target_rows)

        expected_slot_mapping = spec.get("expected_slot_mapping")
        if expected_slot_mapping is not None:
            actual_slot_mapping = _to_list(slot_mapping)
            record["slot_mapping_match"] = actual_slot_mapping == expected_slot_mapping
            if not record["slot_mapping_match"]:
                raise InterventionError("runtime slot_mapping does not exactly match expected_slot_mapping")

        before = _select_rows(compressed_kv, target_rows)
        record["pre_norm"] = _norm(before)

        if normalized_mode == "identity":
            _assign_rows(compressed_kv, target_rows, before)
        elif normalized_mode == "zero_selected":
            _zero_rows(compressed_kv, target_rows)
        else:
            source_value = str(spec["source_artifact"] or "").strip()
            if not source_value:
                raise InterventionError("external mode requires source_artifact")
            source_path = Path(source_value)
            if not source_path.is_file():
                raise InterventionError(f"source artifact does not exist: {source_path}")
            loader = _default_artifact_loader if artifact_loader is None else artifact_loader
            payload = loader(source_path)
            if not isinstance(payload, dict) or "compressed_kv" not in payload:
                raise InterventionError("source artifact must be a dict containing compressed_kv")
            if int(payload.get("layer", -1)) != target_layer:
                raise InterventionError("source artifact layer does not match target_layer")
            if int(payload.get("compress_ratio", -1)) != target_ratio:
                raise InterventionError("source artifact compress_ratio does not match")
            source_tensor = payload["compressed_kv"]
            source_shape = _shape(source_tensor)
            if len(source_shape) != 2 or source_shape != tensor_shape:
                raise InterventionError(
                    f"source tensor shape mismatch: runtime={tensor_shape}, source={source_shape}"
                )
            source_rows = _validate_rows("source_rows", spec["source_rows"], source_shape[0])
            if len(source_rows) != len(target_rows):
                raise InterventionError("source_rows and target_rows must have equal lengths")
            record["source_rows_count"] = len(source_rows)
            replacement = _select_rows(source_tensor, source_rows)
            if hasattr(replacement, "to"):
                replacement = replacement.to(device=compressed_kv.device, dtype=compressed_kv.dtype)
            _assign_rows(compressed_kv, target_rows, replacement)

        after = _select_rows(compressed_kv, target_rows)
        record["post_norm"] = _norm(after)
        record["delta_norm"] = _norm(_subtract(after, before))
        record["applied"] = True
        _append_trace(trace_path, record)
        return compressed_kv
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        try:
            _append_trace(trace_path, record)
        except Exception as trace_exc:
            raise InterventionError(f"{exc}; additionally failed to write trace: {trace_exc}") from exc
        if isinstance(exc, InterventionError):
            raise
        raise InterventionError(str(exc)) from exc
