from __future__ import annotations

import json

import numpy as np
import pytest

from kvreuse_b5.providers import ProviderCapabilities, SyntheticPartialForwardProvider
from kvreuse_b5.recipes import RecipeCapabilityMissingError, RecipeExecutionContext, default_recipe_registry
from kvreuse_b5.recipes.cacheblend import CacheBlendRecipe, cacheblend_spec
from kvreuse_b5.recipes.contract import canonical_hash


def test_all20_declarative_recipe_specs_load_and_are_unique():
    registry = default_recipe_registry()
    specs = registry.list_recipes()
    assert len(specs) == 20
    assert len({spec.paper_id for spec in specs}) == 20
    assert len({spec.recipe_id for spec in specs}) == 20


def test_recipe_hash_is_deterministic_and_serialization_order_independent():
    assert canonical_hash({"a": 1, "b": {"x": 2, "y": 3}}) == canonical_hash({"b": {"y": 3, "x": 2}, "a": 1})
    assert cacheblend_spec("paper_spec", recompute_ratio=0.2).recipe_variant_hash == cacheblend_spec("paper_spec", recompute_ratio=0.2).recipe_variant_hash


def test_missing_provider_fails_without_surrogate():
    recipe = CacheBlendRecipe()
    with pytest.raises(RecipeCapabilityMissingError, match="CAPABILITY_MISSING.*PARTIAL_FORWARD"):
        recipe.execute(RecipeExecutionContext({}, {"K": np.zeros((2, 3, 1)), "V": np.zeros((2, 3, 1))}))


def test_forbidden_oracle_provider_is_rejected():
    class OracleProvider(SyntheticPartialForwardProvider):
        capabilities = ProviderCapabilities("PARTIAL_FORWARD", exact=True, oracle=True, uses_full_target=True, source="test_oracle")

    states = {"K": np.zeros((2, 3, 1)), "V": np.zeros((2, 3, 1))}
    with pytest.raises(RecipeCapabilityMissingError, match="oracle_provider|uses_full_target"):
        CacheBlendRecipe().execute(RecipeExecutionContext({"PARTIAL_FORWARD": OracleProvider(states)}, states))


def test_all_core_steps_have_resolved_evidence_refs_and_variant_hashes_differ():
    registry = default_recipe_registry()
    for spec in registry.list_executable():
        known = set(spec.evidence_ids)
        for step in spec.offline_steps + spec.online_steps:
            if step.step_type == "paper_mechanism": assert step.evidence_refs
            assert set(step.evidence_refs) <= known
    assert registry.get("kvcomm/paper_spec").recipe_variant_hash != registry.get("kvcomm/pinned_code").recipe_variant_hash
    assert registry.get("cacheblend/paper_spec").recipe_variant_hash != registry.get("cacheblend/pinned_code").recipe_variant_hash


def test_skeleton_files_are_canonical_json_yaml():
    registry = default_recipe_registry()
    for spec in registry.list_recipes():
        assert len(spec.paper_spec_source_hash) == 64
        json.dumps(spec.__dict__, sort_keys=True)
