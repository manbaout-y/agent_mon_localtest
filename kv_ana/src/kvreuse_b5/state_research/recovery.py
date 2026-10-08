"""Evidence-gated recovery planning. No repair algorithm and no model execution."""
from __future__ import annotations

import math
import json
from statistics import median


COST_SCOPE = ("model_family", "runtime_version", "device_class", "state_kind", "size_bytes",
              "dtype", "layout", "context_tokens", "batch_size", "measurement_protocol")
PATHS = {"direct_reuse", "load", "replay", "index_rebuild", "repair", "full_recompute"}
RUNTIME_SCOPE = ("condition", "layer_id", "compress_ratio", "operation_unit", "unit_count")


def scope_key(scope: dict):
    values = tuple(scope[k] for k in COST_SCOPE)
    if type(scope["size_bytes"]) is not int or scope["size_bytes"] < 0:
        raise ValueError("size_bytes must be nonnegative integer")
    if any(type(scope[k]) is not int or scope[k] <= 0 for k in ("context_tokens", "batch_size")):
        raise ValueError("context_tokens and batch_size must be positive integers")
    if any(v is None or v == "" for v in values):
        raise ValueError("cost scope must be explicit; no wildcard lookup")
    # Scope extensions (layer, compression ratio, transfer tier, etc.) must not
    # disappear from the comparison key. JSON also makes dict ordering irrelevant.
    return json.dumps(scope, sort_keys=True, separators=(",", ":"), allow_nan=False)


def operation_key(variant=None, parameters=None):
    if variant is not None and (not isinstance(variant, str) or not variant):
        raise ValueError("operation variant must be a nonempty string")
    if parameters is not None and not isinstance(parameters, dict):
        raise ValueError("operation parameters must be a dictionary")
    return json.dumps({"variant": variant, "parameters": parameters or {}},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def missing_runtime_scope(scope):
    """None explicitly denotes a non-applicable layer/ratio; omission is unknown."""
    missing = [name for name in RUNTIME_SCOPE if name not in scope]
    if "condition" in scope and (not isinstance(scope["condition"], str) or not scope["condition"]):
        raise ValueError("condition must be explicit")
    for name in ("layer_id", "compress_ratio"):
        value = scope.get(name)
        if value is not None and (type(value) is not int or value < (0 if name == "layer_id" else 1)):
            raise ValueError(f"{name} must be an explicit integer or null when not applicable")
    if "operation_unit" in scope and (not isinstance(scope["operation_unit"], str) or not scope["operation_unit"]):
        raise ValueError("operation_unit must name the measured scope")
    if "unit_count" in scope and (type(scope["unit_count"]) is not int or scope["unit_count"] <= 0):
        raise ValueError("unit_count must be a positive integer")
    return missing


class CostProfiles:
    """Exact-scope median, fitted only from calibration/train trials. No extrapolation."""

    def __init__(self, trials: list[dict]):
        self.groups = {}
        self.fit_sample_ids = set()
        seen = set()
        for trial in trials:
            if trial["trial_id"] in seen:
                raise ValueError("duplicate cost trial_id")
            seen.add(trial["trial_id"])
            if trial["split"] not in {"train", "calibration"}:
                raise ValueError("test cost trials cannot fit a profile")
            if trial["source"] not in {"runtime", "synthetic"} or trial["operation"] not in PATHS:
                raise ValueError("unsupported cost source/operation")
            duration = trial["operation_duration_ns"]
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                raise ValueError("cost duration must be finite and nonnegative")
            scope = scope_key(trial["scope"])
            group = scope, trial["operation"], trial["source"], operation_key(trial.get("variant"), trial.get("parameters"))
            self.groups.setdefault(group, []).append(dict(trial))
            self.fit_sample_ids.add(trial["sample_id"])

    def lookup(self, scope: dict, operation: str, source: str, *, variant=None, parameters=None):
        # Legacy synthetic fixtures remain useful, but incomplete runtime cost
        # descriptions cannot authorize a measured plan.
        if source == "runtime" and (missing_runtime_scope(scope) or not variant):
            return None
        rows = self.groups.get((scope_key(scope), operation, source, operation_key(variant, parameters)), [])
        if not rows:
            return None
        return {"median_operation_duration_ns": median(r["operation_duration_ns"] for r in rows),
                "trial_count": len(rows), "trial_ids": [r["trial_id"] for r in rows],
                "variant": variant, "parameters": parameters or {}, "scope": dict(scope),
                "source": source, "semantics": "isolated operation cost; not request latency"}


def plan_recovery(cases: list[dict], profiles: CostProfiles, capabilities: set[str]) -> list[dict]:
    """Candidates are supplied by model semantics/evidence, never inferred from text equality."""
    results = []
    ids = set()
    for case in cases:
        if case["case_id"] in ids:
            raise ValueError("duplicate recovery case_id")
        ids.add(case["case_id"])
        if case["sample_id"] in profiles.fit_sample_ids:
            raise ValueError("recovery evaluation sample contaminated cost-profile fit")
        source = case["source"]
        if source not in {"runtime", "synthetic"}:
            raise ValueError("recovery case must declare runtime or synthetic evidence")
        scope_key(case["scope"])
        if case.get("uses_target_b", False):
            raise ValueError("target B may evaluate a supplied state, never authorize a recovery decision")
        runtime_scope_missing = missing_runtime_scope(case["scope"]) if source == "runtime" else []
        candidates = []
        for supplied in case["candidates"]:
            operation = supplied["operation"]
            if operation not in PATHS:
                raise ValueError("unknown recovery operation")
            variant, parameters = supplied.get("variant"), supplied.get("parameters", {})
            operation_key(variant, parameters)
            if not isinstance(supplied.get("required_capabilities", []), list) or any(
                    not isinstance(value, str) or not value for value in supplied.get("required_capabilities", [])):
                raise ValueError("required_capabilities must be a list of capability names")
            candidate = {"operation": operation, "variant": variant, "parameters": parameters,
                         "missing_capabilities": [], "cost": None}
            if supplied.get("uses_target_b", False):
                raise ValueError("target B may evaluate a supplied state, never authorize a recovery decision")
            refs = supplied.get("evidence_refs", [])
            if supplied.get("validity") != "VALIDATED" or not isinstance(refs, list) or not refs or any(
                    not isinstance(ref, str) or not ref for ref in refs):
                candidate["status"] = "BLOCKED_VALIDITY_EVIDENCE"
            elif supplied.get("evidence_source") != source:
                candidate["status"] = "BLOCKED_EVIDENCE_SOURCE"
            elif source == "runtime" and (runtime_scope_missing or not variant
                                           or supplied.get("uses_target_b") is not False
                                           or "required_capabilities" not in supplied):
                candidate["status"] = "BLOCKED_INPUT_CONTRACT"
                candidate["missing_contract_fields"] = runtime_scope_missing + [
                    field for field, present in (("variant", bool(variant)),
                                                 ("uses_target_b=false", supplied.get("uses_target_b") is False),
                                                 ("required_capabilities", "required_capabilities" in supplied))
                    if not present]
            elif not set(supplied.get("required_capabilities", [])) <= capabilities:
                candidate["status"] = "CAPABILITY_MISSING"
                candidate["missing_capabilities"] = sorted(set(supplied["required_capabilities"]) - capabilities)
            else:
                candidate["cost"] = profiles.lookup(case["scope"], operation, source,
                                                     variant=variant, parameters=parameters)
                candidate["status"] = "COSTED" if candidate["cost"] else "COST_MISSING"
            candidate["evidence_refs"] = supplied.get("evidence_refs", [])
            candidates.append(candidate)
        costed = [c for c in candidates if c["status"] == "COSTED"]
        preferred = min(costed, key=lambda c: (c["cost"]["median_operation_duration_ns"], c["operation"])) if costed else None
        unknown_cost = any(c["status"] == "COST_MISSING" for c in candidates)
        incomplete_evidence = any(c["status"].startswith("BLOCKED_") for c in candidates)
        status = "BLOCKED" if not preferred else "PARTIAL_COST_COVERAGE" if unknown_cost else "PLAN_READY"
        if preferred and source == "runtime" and incomplete_evidence and not unknown_cost:
            status = "PARTIAL_CANDIDATE_COVERAGE"
        results.append({"case_id": case["case_id"], "sample_id": case["sample_id"], "source": source,
                        "selected_path": preferred["operation"] if preferred else None,
                        "selected_variant": preferred["variant"] if preferred else None,
                        "selected_parameters": preferred["parameters"] if preferred else None,
                        "status": status, "candidate_evidence_complete": not incomplete_evidence,
                        "runtime_execution_ready": False,
                        "comparison_scope": "only evidence-valid and capability-available candidates; no global optimality claim",
                        "candidates": candidates, "execution_status": "NOT_EXECUTED",
                        "functional_safety": "REQUIRES_MODEL_VALIDATION"})
    return results
