"""Evaluate supplied aligned states; never generate a correction or infer runtime row mapping."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from zipfile import BadZipFile

import numpy as np

from kvreuse_b5.data.types import StatePair
from kvreuse_b5.metrics.residual import compute_residual_metrics


def _rows(values, length, name):
    if not isinstance(values, (list, tuple)) or not values or any(type(v) is not int or not 0 <= v < length for v in values):
        raise ValueError(f"{name}: explicit in-bounds row indices required")
    if len(set(values)) != len(values):
        raise ValueError(f"{name}: duplicate rows are not a one-to-one mapping")
    return values


def _positions(values, length, name):
    result = np.asarray(values)
    if result.ndim != 1 or len(result) != length or result.dtype.kind not in "iuf" or not np.isfinite(result).all():
        raise ValueError(f"{name}: finite numeric canonical positions required for every mapped row")
    if len(np.unique(result)) != length:
        raise ValueError(f"{name}: duplicate canonical positions are not a one-to-one mapping")
    return result


def compare_aligned_arrays(a, b, corrected, *, mapping: dict, identity: dict,
                           representation_a: str, representation_b: str, representation_corrected: str,
                           source: str) -> dict:
    if not representation_a or len({representation_a, representation_b, representation_corrected}) != 1:
        raise ValueError("states must explicitly share representation semantics")
    arrays = [np.asarray(value) for value in (a, b, corrected)]
    if any(value.ndim != 2 or value.dtype.kind != "f" or not np.isfinite(value).all() for value in arrays):
        raise ValueError("comparison accepts finite floating [T,F]; quantized/index states need a separate provider")
    if source not in {"provided", "runtime", "synthetic"} or not mapping.get("evidence_ref"):
        raise ValueError("source and frozen mapping evidence_ref required")
    ra = _rows(mapping["row_a"], len(arrays[0]), "row_a")
    rb = _rows(mapping["row_b"], len(arrays[1]), "row_b")
    rc = _rows(mapping["row_corrected"], len(arrays[2]), "row_corrected")
    pa = _positions(mapping["positions_a"], len(ra), "positions_a")
    pb = _positions(mapping["positions_b"], len(rb), "positions_b")
    mapping_verified = "positions_corrected" in mapping
    if not mapping_verified and source != "synthetic":
        raise ValueError("positions_corrected must explicitly establish corrected-state coordinates")
    # Preserve older synthetic fixtures, but never present their missing
    # corrected-coordinate declaration as verified runtime alignment.
    pc = _positions(mapping.get("positions_corrected", mapping["positions_b"]), len(rc), "positions_corrected")
    if len(ra) != len(rb) or len(ra) != len(rc) or not np.array_equal(pa, pb) or not np.array_equal(pa, pc):
        raise ValueError("row lengths and explicit canonical positions must agree")
    if not mapping.get("positions_semantics"):
        raise ValueError("positions_semantics must be explicit")
    pair = StatePair(**identity, a=arrays[0][ra], b=arrays[1][rb], positions=np.asarray(pa),
                     metadata={"mapping_evidence": mapping["evidence_ref"]})
    selected = arrays[2][rc]
    if selected.shape != pair.a.shape:
        raise ValueError("corrected feature dimensions do not match")
    metrics = asdict(compute_residual_metrics(pair.a, pair.b, selected))
    if not np.isfinite(metrics["energy_explained"]):
        metrics["energy_explained"] = None
    return {**identity, **metrics, "source": source, "representation": representation_a,
            "mapping_evidence": mapping["evidence_ref"], "positions_semantics": mapping["positions_semantics"],
            "canonical_mapping_verified": mapping_verified,
            "mapping_status": "EXPLICIT_COORDINATES_VERIFIED" if mapping_verified else
                              "LEGACY_SYNTHETIC_CORRECTED_COORDINATES_UNVERIFIED",
            "units": pair.total_units, "feature_dim": pair.feature_dim,
            "target_b_usage": "EVALUATION_ONLY", "functional_safety": "NOT_EVALUATED",
            "correction_source": "EXTERNALLY_SUPPLIED_NOT_GENERATED_BY_THIS_TOOL"}


def compare_npz(case: dict, base: Path) -> dict:
    """Portable CPU interchange; existing real PT adapters remain unchanged."""
    path = base / case["artifact"]
    try:
        archive = np.load(path, allow_pickle=False)
        if not isinstance(archive, np.lib.npyio.NpzFile):
            raise ValueError("state artifact must be an NPZ archive")
        with archive:
            a, b, corrected = [archive[case["keys"][k]] for k in ("a", "b", "corrected")]
    except (BadZipFile, OSError, ValueError, KeyError, EOFError) as exc:
        raise ValueError(f"cannot read state artifact {path}: {exc}") from exc
    return compare_aligned_arrays(a, b, corrected, mapping=case["mapping"], identity=case["identity"],
                                  representation_a=case["representations"]["a"],
                                  representation_b=case["representations"]["b"],
                                  representation_corrected=case["representations"]["corrected"], source=case["source"])


def dependency_impact(nodes: list[dict], changed_state_ids: list[str]) -> dict:
    """Propagate invalidation through explicit producer-consumer edges, not model guesses."""
    graph = {row["state_id"]: set(row["depends_on"]) for row in nodes}
    if len(graph) != len(nodes):
        raise ValueError("duplicate dependency node")
    missing = (set(changed_state_ids) | set().union(set(), *graph.values())) - set(graph)
    if missing:
        raise ValueError(f"missing dependency nodes: {sorted(missing)}")
    resolved, pending = set(), dict(graph)
    while pending:
        ready = {sid for sid, deps in pending.items() if deps <= resolved}
        if not ready:
            raise ValueError("cyclic dependency graph")
        resolved |= ready
        pending = {sid: deps for sid, deps in pending.items() if sid not in ready}
    affected = set(changed_state_ids)
    while True:
        expanded = affected | {sid for sid, deps in graph.items() if deps & affected}
        if expanded == affected:
            break
        affected = expanded
    return {"changed": sorted(set(changed_state_ids)), "potentially_invalidated": sorted(affected),
            "unaffected_by_declared_edges": sorted(set(graph) - affected),
            "semantics": "conservative dependency reachability; unaffected does not certify safe KV reuse"}
