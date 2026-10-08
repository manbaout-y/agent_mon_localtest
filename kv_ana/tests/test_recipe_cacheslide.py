from __future__ import annotations

import numpy as np

from kvreuse_b5.providers import SyntheticPartialForwardProvider
from kvreuse_b5.recipes import RecipeExecutionContext
from kvreuse_b5.recipes.cacheslide import CacheSlidePositionModel, CacheSlideRuntimeSpec, CacheSlideWCARecipe


def test_cacheslide_wca_selection_fusion_gating_budget_and_cost():
    base = {"K": np.zeros((4, 4, 1)), "V": np.zeros((4, 4, 1))}
    target = {"K": np.zeros((4, 4, 1)), "V": np.zeros((4, 4, 1))}
    target["K"][:, 3] = 4.0; target["K"][:, 2] = 2.0
    target["V"][:, 3] = 3.0; target["V"][:, 2] = 1.0
    provider = SyntheticPartialForwardProvider(target)
    result = CacheSlideWCARecipe(top_k_ratio=0.5, gating_interval=1, cksim_rule="retire_if_similarity_lte").execute(RecipeExecutionContext({"PARTIAL_FORWARD": provider}, base))
    assert result.trace[0].output_scope["selected"] == [3, 2]
    assert any(entry.step_name == "periodic_cksim_gating" for entry in result.trace)
    assert result.cost.provider_costs["partial_forward_units"] > 0
    assert result.diagnostics["ccpe"] == "contract only"
    assert result.diagnostics["wca"] == "executed"
    assert result.diagnostics["slide"] == "runtime spec only"
    assert result.diagnostics["selected"] != [3, 2]  # explicit CKSim rule rotated selected tokens


def test_cacheslide_components_are_not_rope_shift_aliases():
    assert CacheSlidePositionModel().name == "CCPE"
    assert CacheSlidePositionModel().requires_training is True
    assert CacheSlideRuntimeSpec().name == "SLIDE"
    assert CacheSlideRuntimeSpec().executable_here is False
    assert "rope" not in CacheSlideWCARecipe().spec.recipe_id.lower()


def test_cacheslide_pinned_code_wca_is_not_claimed_executable():
    import pytest
    with pytest.raises(ValueError, match="no auditable complete WCA"):
        CacheSlideWCARecipe("pinned_code")


def test_cacheslide_cksim_inequality_requires_explicit_variant():
    import pytest
    base = {"K": np.zeros((2, 2, 1)), "V": np.zeros((2, 2, 1))}
    with pytest.raises(ValueError, match="cksim_rule must explicitly resolve"):
        CacheSlideWCARecipe().execute(RecipeExecutionContext({"PARTIAL_FORWARD": SyntheticPartialForwardProvider(base)}, base))
