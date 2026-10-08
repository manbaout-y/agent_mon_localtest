from __future__ import annotations

from pathlib import Path
from typing import Any

from kvreuse_b5.recipes import default_recipe_registry


AVAILABLE_OFFLINE = {
    "STATEPAIR_A_B",
    "FULL_EXACT_FRESH_PROBE_QWEN",
    "FULL_EXACT_WCA_STATE_QWEN",
    "POSITION_OFFSETS_QWEN",
    "STRICT_MAPPING_DEEPSEEK_C4",
}


def audit_paper_capabilities(project_root: str | Path) -> list[dict[str, Any]]:
    project_root = Path(project_root)
    registry = default_recipe_registry()
    rows = []
    for spec in registry.list_recipes():
        requirements = set(spec.execution_requirements)
        missing = set(spec.required_providers)
        classification = "RUNTIME_REQUIRED"
        component = ""
        can_run = False
        models = ""
        target_usage = "none"
        leakage = "none if blocked; provider contract required"
        reason = "requires unavailable runtime/provider signals"
        max_fidelity = spec.max_fidelity
        evidence_path = project_root / spec.paper_spec_path
        if not evidence_path.is_file():
            classification = "BLOCKED_SOURCE"
            reason = f"frozen paper spec missing: {spec.paper_spec_path}"
        elif spec.paper_id == "11":
            classification = "BLOCKED_SOURCE"
            reason = "local DAF source omits recipe-critical state update/layer/position semantics"
        elif spec.paper_id == "14":
            classification = "OFFLINE_COMPONENT_EXECUTABLE"
            component = "full_exact fresh-probe AgentKVShift interpretation"
            can_run = True
            models = "Qwen3-8B|Qwen3.5-27B"
            target_usage = "oracle_probe/full_exact_fresh_probe"
            leakage = "B restricted to checkpoint/probe provider; approximate-context variant remains blocked"
            missing = set()
            reason = "saved Qwen B strictly supplies the explicit full_exact probe interpretation only"
        elif spec.paper_id == "13":
            classification = "OFFLINE_COMPONENT_EXECUTABLE"
            component = "WCA"
            can_run = True
            models = "Qwen3-8B|Qwen3.5-27B"
            target_usage = "oracle_probe/full_exact_fresh_recompute"
            leakage = "B use explicit in WCA recompute provider; CCPE/SLIDE not claimed"
            missing = set()
            reason = "WCA component is defined over full layer-1 and selected later fresh states; CCPE training and SLIDE runtime remain blocked"
        elif "TRAINING" in requirements or "MODEL_MODIFICATION" in requirements:
            classification = "TRAINING_REQUIRED"
            reason = "paper recipe requires training and/or model modification"
        elif spec.paper_id == "18":
            classification = "UNSUPPORTED_STATE"
            reason = "Irminsul requires MLA-native cKV; saved DeepSeek C4 is not proven equivalent"
        rows.append(
            {
                "paper_id": spec.paper_id,
                "paper_name": spec.paper,
                "variant": spec.variant,
                "max_fidelity": max_fidelity,
                "classification": classification,
                "component": component,
                "execution_requirements": "|".join(spec.execution_requirements),
                "required_providers": "|".join(spec.required_providers),
                "available_providers": "|".join(sorted(AVAILABLE_OFFLINE)),
                "missing_providers": "|".join(sorted(missing)),
                "real_models_supported": models,
                "can_run_now": can_run,
                "reason": reason,
                "source_evidence": str(evidence_path),
                "target_B_usage": target_usage,
                "leakage_risk": leakage,
            }
        )
    if len(rows) != 20 or len({row["paper_id"] for row in rows}) != 20:
        raise ValueError("paper capability audit did not produce 20 unique papers")
    return rows
