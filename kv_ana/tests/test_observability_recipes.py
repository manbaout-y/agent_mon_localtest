from __future__ import annotations

import numpy as np

from kvreuse_b5.observability import TraceCollector
from kvreuse_b5.observability.ablations import AgentKVShiftForensicAblation
from kvreuse_b5.providers import FullExactFreshProbeProvider, SyntheticPartialForwardProvider
from kvreuse_b5.recipes import RecipeExecutionContext
from kvreuse_b5.recipes.agentkvshift import AgentKVShiftRecipe
from kvreuse_b5.recipes.cacheslide import CacheSlideWCARecipe


def _metadata(collector, targets, *, model="q8_like", condition="PREFIX_ONLY", pair_id="p"):
    return {
        "observability_collector": collector,
        "observability_targets": targets,
        "model_family": model,
        "condition": condition,
        "pair_id": pair_id,
        "layer_ids": [0, 1, 2, 3],
    }


def _agent_fixture():
    base = {"K": np.zeros((3, 4, 2)), "V": np.zeros((3, 4, 2))}
    target = {"K": np.ones((3, 4, 2)), "V": 2 * np.ones((3, 4, 2))}
    target["K"][0, 3] += 1.0
    target["V"][0, 0] += 2.0
    return base, target


def test_agent_trace_matches_probe_output_costs_and_does_not_change_numbers() -> None:
    base, target = _agent_fixture()
    recipe_off = AgentKVShiftRecipe("full_exact", probe_ratio=0.25)
    method_hash = recipe_off.spec.recipe_variant_hash
    off = recipe_off.execute(RecipeExecutionContext({"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base))

    collector = TraceCollector("detail", run_id="agent-run")
    recipe_on = AgentKVShiftRecipe("full_exact", probe_ratio=0.25)
    on = recipe_on.execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base,
        _metadata(collector, target),
    ))
    for kind in ("K", "V"):
        np.testing.assert_array_equal(on.states[kind], off.states[kind])
    assert recipe_on.spec.recipe_variant_hash == method_hash
    details = collector.detail_events()
    for kind in ("K", "V"):
        expected = on.diagnostics["probe_positions"][kind]
        probes = [row for row in details if row["state_kind"] == kind and row["action"] == "probe"]
        assert probes and all(row["positions"] == expected for row in probes)
    costs = {row["state_kind"]: row for row in collector.cost_rows()}
    assert costs["K"]["replaced_units"] == 6
    assert costs["V"]["replaced_units"] == 6
    assert costs["K"]["corrected_units"] == 6
    assert costs["V"]["corrected_units"] == 6
    assert costs["K"]["recomputed_units_additive"] == 6
    assert costs["K"]["recomputed_units_unique_layer_position"] == 6

    attribution = [
        row for row in details
        if row["method"] == "agentkvshift/paper_spec" and row["action"] == "replace" and row["layer_id"] > 0
    ]
    assert attribution
    for row in attribution:
        metadata = row["metadata"]
        before = metadata["sse_before_layer_repair"]
        after_correction = metadata["sse_after_nonprobe_correction"]
        after_replacement = metadata["sse_after_probe_replacement"]
        assert row["baseline_sse"] == after_correction
        assert row["after_sse"] == after_replacement
        assert metadata["total_layer_recovery"] == (before - after_replacement) / before


def test_official_recipe_hashes_match_observability_v1_bundle() -> None:
    assert AgentKVShiftRecipe("full_exact", probe_ratio=0.20).spec.recipe_variant_hash == (
        "4e2efa2c2551f972a4c7606ce9965595066dcdf12ef43911787e4104f6559065"
    )
    assert CacheSlideWCARecipe(
        top_k_ratio=0.26,
        gating_interval=4,
        cksim_rule="retire_if_similarity_lte",
    ).spec.recipe_variant_hash == "467d20bb370de1cfd917985b481c5e45472caf4f6f5e62c36eadb89d826cd54a"


def test_agent_all_zero_selector_is_diagnosed_without_changing_stable_selection() -> None:
    base = {"K": np.zeros((3, 4, 1)), "V": np.zeros((3, 4, 1))}
    target = {"K": np.ones((3, 4, 1)), "V": 2 * np.ones((3, 4, 1))}
    target["K"][0] = 0.0
    target["V"][0] = 0.0
    collector = TraceCollector("detail", run_id="degenerate")
    recipe = AgentKVShiftRecipe("full_exact", probe_ratio=0.5)
    result = recipe.execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)},
        base,
        _metadata(collector, target),
    ))
    assert result.diagnostics["probe_positions"] == {"K": [0, 1], "V": [0, 1]}
    selects = [row for row in collector.summary_events() if row["action"] == "select"]
    assert selects and all(row["metadata"]["selector_degenerate"] for row in selects)
    assert all(row["metadata"]["selector_nonzero_count"] == 0 for row in selects)
    assert all(row["metadata"]["selector_zero_count"] == 4 for row in selects)
    assert all(row["metadata"]["selector_tie_count"] == 4 for row in selects)
    assert all(row["metadata"]["selected_count"] == 2 for row in selects)


def test_agent_correction_norm_is_applied_norm_not_estimator_norm() -> None:
    base = {"K": np.zeros((2, 4, 1)), "V": np.zeros((2, 4, 1))}
    target = {"K": np.ones((2, 4, 1)), "V": np.ones((2, 4, 1))}
    target["K"][0] = 0.0
    target["V"][0] = 0.0
    collector = TraceCollector("detail", run_id="norm")
    AgentKVShiftRecipe("full_exact", probe_ratio=0.25).execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base, _metadata(collector, target)
    ))
    corrections = [row for row in collector.detail_events() if row["action"] == "correct"]
    assert corrections
    assert all(row["estimated_residual_mean_norm"] > 0 for row in corrections)
    assert all(row["applied_correction_norm"] == 0 for row in corrections)
    assert all(row["correction_norm"] == row["applied_correction_norm"] for row in corrections)


def test_agent_forensic_ablations_are_separate_and_have_documented_actions() -> None:
    base, target = _agent_fixture()
    official = AgentKVShiftRecipe("full_exact", probe_ratio=0.25)
    official_hash = official.spec.recipe_variant_hash

    probe_collector = TraceCollector("detail", run_id="probe-only")
    probe = AgentKVShiftForensicAblation("probe_only", "full_exact", probe_ratio=0.25)
    probe_result = probe.execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base,
        _metadata(probe_collector, target),
    ))
    probe_later = [row for row in probe_collector.detail_events() if row["layer_id"] > 0]
    assert not any(row["action"] == "correct" for row in probe_later)
    assert any(row["action"] == "replace" for row in probe_later)

    shift_collector = TraceCollector("detail", run_id="shift-only")
    shift = AgentKVShiftForensicAblation("shift_only", "full_exact", probe_ratio=0.25)
    shift_result = shift.execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base,
        _metadata(shift_collector, target),
    ))
    shift_later = [row for row in shift_collector.detail_events() if row["layer_id"] > 0]
    assert any(row["action"] == "correct" for row in shift_later)
    assert not any(row["action"] == "replace" for row in shift_later)
    assert probe_result.diagnostics["experiment_class"] == "FORENSIC_ABLATION"
    assert shift_result.diagnostics["experiment_class"] == "FORENSIC_ABLATION"
    assert probe.spec.recipe_variant_hash != shift.spec.recipe_variant_hash
    assert official.spec.recipe_variant_hash == official_hash


def test_forensic_summary_attributes_agent_recovery_and_work() -> None:
    base, target = _agent_fixture()
    collector = TraceCollector("detail", run_id="forensic-summary")
    AgentKVShiftRecipe("full_exact", probe_ratio=0.25).execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": FullExactFreshProbeProvider(target)}, base,
        _metadata(collector, target),
    ))
    rows = collector.forensic_rows()
    assert len(rows) == 2
    for row in rows:
        assert row["method"] == "agentkvshift/paper_spec"
        assert row["selected_position_fraction"] == 0.25
        assert row["unique_layer_position_units"] == 6
        assert row["additive_recompute_units"] == 6
        assert row["correction_only_recovery"] is not None
        assert row["replacement_incremental_recovery"] is not None
        assert row["total_recovery"] is not None
        assert np.isclose(
            row["correction_only_recovery"] + row["replacement_incremental_recovery"],
            row["total_recovery"],
        )


def test_cacheslide_transition_trace_and_q35_prefix_v_forensic_label() -> None:
    base = {"K": np.zeros((4, 4, 1)), "V": np.zeros((4, 4, 1))}
    target = {"K": np.zeros((4, 4, 1)), "V": np.zeros((4, 4, 1))}
    target["K"][:, 3] = 4.0; target["K"][:, 2] = 2.0
    target["V"][:, 3] = 3.0; target["V"][:, 2] = 1.0
    collector = TraceCollector("detail", run_id="slide-run")
    recipe = CacheSlideWCARecipe(top_k_ratio=0.5, gating_interval=1, cksim_rule="retire_if_similarity_lte")
    result = recipe.execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": SyntheticPartialForwardProvider(target)}, base,
        _metadata(collector, target, model="Qwen3.5-27B", condition="PREFIX_ONLY", pair_id="forensic-v"),
    ))
    details = collector.detail_events()
    actions = {row["action"] for row in details}
    assert {"select", "recompute", "correct", "retire", "replace"} <= actions
    retire = [row for row in details if row["action"] == "retire"]
    assert retire and all("selected_before" in row["metadata"]["detail"] and "selected_after" in row["metadata"]["detail"] for row in retire)
    replacement_select = [row for row in details if row["action"] == "select" and row["metadata"].get("selection_phase") == "gating_replacement"]
    assert replacement_select and all("newly_selected_positions" in row["metadata"]["detail"] for row in replacement_select)
    assert all(row["metadata"]["canonical_forensic_variant"] for row in details)
    assert all(not row["metadata"]["sensitivity_variant"] for row in details)
    summary = collector.summary_events()
    assert all("detail" not in row["metadata"] for row in summary)
    assert all(not any(isinstance(value, list) for value in row["metadata"].values()) for row in summary)
    costs = {row["state_kind"]: row for row in collector.cost_rows()}
    assert costs["V"]["model_family"] == "Qwen3.5-27B"
    assert costs["V"]["recomputed_units_additive"] == 10
    assert costs["V"]["recomputed_units_unique_layer_position"] == 10
    assert {row["variant"] for row in collector.forensic_rows()} == {
        "wca_retire_if_similarity_lte"
    }
    assert result.diagnostics["wca"] == "executed"
