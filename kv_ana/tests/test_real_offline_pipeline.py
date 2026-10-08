from __future__ import annotations

from pathlib import Path

import pytest

from kvreuse_b5.pipeline.b5 import validate_target_usage
from kvreuse_b5.pipeline.capability_audit import audit_paper_capabilities
from kvreuse_b5.pipeline.capsule import render_capsule, write_bounded_capsule
from kvreuse_b5.pipeline.provider_plan import build_provider_plan
from kvreuse_b5.recipes import RecipeCapabilityMissingError, RecipeExecutionContext
from kvreuse_b5.recipes.cacheblend import CacheBlendRecipe


ROOT = Path(__file__).resolve().parents[1]


def test_paper_capability_audit_is_20_unique_and_conservative() -> None:
    rows = audit_paper_capabilities(ROOT)
    assert len(rows) == 20
    assert len({row["paper_id"] for row in rows}) == 20
    by_id = {row["paper_id"]: row for row in rows}
    assert by_id["14"]["classification"] == "OFFLINE_COMPONENT_EXECUTABLE"
    assert by_id["13"]["classification"] == "OFFLINE_COMPONENT_EXECUTABLE"
    assert by_id["11"]["classification"] == "BLOCKED_SOURCE"
    assert not any(row["classification"] == "OFFLINE_EXECUTABLE" for row in rows)


def test_missing_provider_fails_without_surrogate() -> None:
    recipe = CacheBlendRecipe("paper_spec")
    with pytest.raises(RecipeCapabilityMissingError, match="CAPABILITY_MISSING"):
        recipe.execute(RecipeExecutionContext({}, {"K": [[[0.0]]], "V": [[[0.0]]]}))


def test_target_leakage_audit_rejects_deployment_visible_target() -> None:
    with pytest.raises(ValueError, match="target leakage"):
        validate_target_usage([{"method": "bad", "method_class": "DEPLOYMENT-VISIBLE", "oracle_target_usage": "full_target_oracle"}])
    validate_target_usage([{"method": "boundary", "method_class": "DEPLOYMENT-VISIBLE", "oracle_target_usage": "none"}])


def test_provider_plan_contains_future_functional_signals() -> None:
    plan = build_provider_plan(audit_paper_capabilities(ROOT))
    names = {item["signal"] for item in plan["providers"]}
    assert {"partial_forward_state", "query_signal", "attention_signal", "logits", "generation_outputs"} <= names


def test_capsule_enforces_utf8_byte_limit(tmp_path: Path) -> None:
    audit = audit_paper_capabilities(ROOT)
    plan = build_provider_plan(audit)
    text = render_capsule(
        regression=[{"status": "NUMERIC_WARN", "experiment": "B2", "model": "q", "condition": "p", "state": "K", "historical_value": 0.1, "new_value": 0.2, "abs_diff": 0.1}],
        audit=audit,
        tournament=[{"method": "方法", "variant": "x", "model": "q", "condition": "p", "state": "K", "scope": "test", "residual_energy_explained": 0.5, "oracle_target_usage": "none"}],
        provider_plan=plan,
        output_files=[f"very-long-file-{i}.csv" for i in range(1000)],
    )
    path = tmp_path / "capsule.txt"
    size = write_bounded_capsule(path, text, 2048)
    assert size <= 2048
    path.read_text(encoding="utf-8")

