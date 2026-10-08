from __future__ import annotations

import numpy as np
import pytest

from kvreuse_b5.providers import ApproximateContextFreshProbeProvider, FullExactFreshProbeProvider
from kvreuse_b5.recipes import RecipeExecutionContext
from kvreuse_b5.recipes.agentkvshift import AgentKVShiftRecipe


def _fixture():
    base = {"K": np.zeros((3, 4, 2)), "V": np.zeros((3, 4, 2))}
    target = {"K": np.ones((3, 4, 2)), "V": 2 * np.ones((3, 4, 2))}
    target["K"][0, 3] += 1.0
    target["V"][0, 0] += 2.0
    return base, target


def test_agentkvshift_requires_explicit_probe_semantics():
    base, target = _fixture()
    with pytest.raises(ValueError, match="explicitly"):
        AgentKVShiftRecipe().execute(RecipeExecutionContext({"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base))


def test_agentkvshift_probe_selection_weighting_and_exact_probe_preservation():
    base, target = _fixture()
    recipe = AgentKVShiftRecipe("full_exact", probe_ratio=0.25)
    result = recipe.execute(RecipeExecutionContext({"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base))
    assert result.diagnostics["probe_positions"]["K"] == [3]
    assert result.diagnostics["probe_positions"]["V"] == [0]
    for layer in (1, 2):
        np.testing.assert_array_equal(result.states["K"][layer, 3], target["K"][layer, 3])
        np.testing.assert_array_equal(result.states["V"][layer, 0], target["V"][layer, 0])
    assert result.trace[0].step_name == "checkpoint_divergence_and_probe_selection"


def test_agentkvshift_exact_and_approximate_providers_are_explicit_variants():
    base, target = _fixture()
    exact = AgentKVShiftRecipe("full_exact").execute(RecipeExecutionContext({"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base))
    approx = AgentKVShiftRecipe("approximate_context").execute(RecipeExecutionContext({"PARTIAL_FORWARD": ApproximateContextFreshProbeProvider(target, 0.25)}, base))
    assert exact.diagnostics["probe_semantics"] == "full_exact"
    assert approx.diagnostics["probe_semantics"] == "approximate_context"
    assert not np.allclose(exact.states["K"][1], approx.states["K"][1])
