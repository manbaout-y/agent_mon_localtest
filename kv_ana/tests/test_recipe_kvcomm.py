from __future__ import annotations

import numpy as np

from kvreuse_b5.providers import SyntheticAnchor, SyntheticAnchorStateProvider
from kvreuse_b5.recipes import RecipeExecutionContext
from kvreuse_b5.recipes.kvcomm import KVCOMMRecipe


def _anchors():
    zero = np.zeros((2, 3, 1))
    return [
        SyntheticAnchor("near", {"K": zero.copy(), "V": zero.copy()}, {"K": np.ones_like(zero), "V": 2 * np.ones_like(zero)}, 3),
        SyntheticAnchor("far", {"K": 10 * np.ones_like(zero), "V": 10 * np.ones_like(zero)}, {"K": 8 * np.ones_like(zero), "V": 9 * np.ones_like(zero)}, 3),
    ]


def test_kvcomm_selects_anchor_and_keeps_kv_separate():
    base = {"K": np.zeros((2, 3, 1)), "V": np.zeros((2, 3, 1))}
    provider = SyntheticAnchorStateProvider(_anchors())
    result = KVCOMMRecipe("paper_spec", temperature=0.01, entropy_threshold=2.0).execute(RecipeExecutionContext({"ONLINE_ANCHOR_STATE": provider}, base))
    np.testing.assert_allclose(result.states["K"], 1.0, atol=1e-9)
    np.testing.assert_allclose(result.states["V"], 2.0, atol=1e-9)
    assert [entry.step_name for entry in result.trace] == ["anchor_eligibility", "kv_separate_anchor_weights", "offset_application", "fallback_or_anchor_update"]


def test_kvcomm_pinned_code_preserves_layer0_and_hash_differs():
    base = {"K": np.zeros((2, 3, 1)), "V": np.zeros((2, 3, 1))}
    provider = SyntheticAnchorStateProvider(_anchors())
    paper = KVCOMMRecipe("paper_spec", temperature=0.01, entropy_threshold=2.0)
    code = KVCOMMRecipe("pinned_code", temperature=0.01, entropy_threshold=2.0)
    result = code.execute(RecipeExecutionContext({"ONLINE_ANCHOR_STATE": provider}, base))
    np.testing.assert_array_equal(result.states["K"][0], 0.0)
    np.testing.assert_allclose(result.states["K"][1], 1.0)
    assert paper.spec.recipe_variant_hash != code.spec.recipe_variant_hash


def test_kvcomm_fallback_updates_anchor_pool():
    base = {"K": np.zeros((2, 3, 1)), "V": np.zeros((2, 3, 1))}
    provider = SyntheticAnchorStateProvider([])
    fresh = {"K": np.ones((2, 3, 1)), "V": 2 * np.ones((2, 3, 1))}
    result = KVCOMMRecipe().execute(RecipeExecutionContext({"ONLINE_ANCHOR_STATE": provider}, base, {"fallback_fresh_states": fresh, "anchor_id": "new"}))
    assert result.diagnostics["fallback"] is True
    assert result.diagnostics["anchor_update"] == "new"
    assert len(provider.updates) == 1
