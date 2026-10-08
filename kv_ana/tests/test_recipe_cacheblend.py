from __future__ import annotations

import numpy as np

from kvreuse_b5.providers import SyntheticPartialForwardProvider
from kvreuse_b5.recipes import RecipeExecutionContext
from kvreuse_b5.recipes.cacheblend import CacheBlendRecipe


def test_cacheblend_runs_layerwise_scatter_and_propagates_updated_state():
    base = {"K": np.zeros((3, 5, 1)), "V": np.zeros((3, 5, 1))}
    target = {"K": np.zeros((3, 5, 1)), "V": np.zeros((3, 5, 1))}
    target["K"][:, 3:] = 4.0; target["V"][:, 3:] = 2.0
    provider = SyntheticPartialForwardProvider(target)
    result = CacheBlendRecipe("paper_spec", candidate_ratio=0.6, recompute_ratio=0.4).execute(RecipeExecutionContext({"PARTIAL_FORWARD": provider}, base))
    np.testing.assert_array_equal(result.states["K"][0], target["K"][0])
    recompute_calls = [call for call in provider.calls if call["op"] == "recompute"]
    assert [call["layer"] for call in recompute_calls] == [0, 1, 2]
    np.testing.assert_array_equal(recompute_calls[1]["current_snapshot"]["K"][0], target["K"][0])
    assert result.trace[0].step_name == "layer1_candidate_discovery"
    assert result.trace[1].step_name == "layerwise_selective_recompute"
    assert result.trace[2].step_name == "cache_scatter_and_hidden_propagation"


def test_cacheblend_never_reads_target_outside_provider():
    base = {"K": np.zeros((2, 4, 1)), "V": np.zeros((2, 4, 1))}
    target = {"K": np.ones((2, 4, 1)), "V": np.ones((2, 4, 1))}
    provider = SyntheticPartialForwardProvider(target)
    result = CacheBlendRecipe(recompute_ratio=0.25).execute(RecipeExecutionContext({"PARTIAL_FORWARD": provider}, base))
    assert result.status.value == "EXECUTED"
    assert provider.capabilities.uses_full_target is False
    assert all(entry.provider_invoked in {None, "PARTIAL_FORWARD"} for entry in result.trace)
