#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kvreuse_b5.adapters import QwenRawKVAdapter  # noqa: E402
from kvreuse_b5.observability import ObservabilityWriter, TRACE_SCHEMA_VERSION, TraceCollector  # noqa: E402
from kvreuse_b5.observability.ablations import AgentKVShiftForensicAblation  # noqa: E402
from kvreuse_b5.pipeline.providers import OfflineFullTargetPartialForwardProvider, OfflineFullTargetProbeProvider  # noqa: E402
from kvreuse_b5.recipes import RecipeExecutionContext  # noqa: E402
from kvreuse_b5.recipes.agentkvshift import AgentKVShiftRecipe  # noqa: E402
from kvreuse_b5.recipes.cacheslide import CacheSlideWCARecipe  # noqa: E402
from kvreuse_b5.regression.math import shared_residual_sse  # noqa: E402


def _allow_explicit_target(recipe):
    recipe.spec = replace(
        recipe.spec,
        capabilities=replace(recipe.spec.capabilities, allow_oracle_providers=True, allow_full_target_providers=True),
    )
    return recipe


def _stack(pairs, field):
    return np.stack([getattr(pair, field) for pair in sorted(pairs, key=lambda item: item.layer_id)], axis=0).astype(np.float64)


def _context(collector, states, targets, pairs, args):
    return RecipeExecutionContext(
        {}, states,
        {
            "observability_collector": collector,
            "observability_targets": targets,
            "model_family": args.model_family,
            "condition": args.condition,
            "pair_id": args.pair_id,
            "layer_ids": [pair.layer_id for pair in sorted(pairs["K"], key=lambda item: item.layer_id)],
        },
    )


def build_parser():
    parser = argparse.ArgumentParser(description="CPU-only real-state KVReuse observability smoke")
    parser.add_argument("--q8-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pair-id", type=int, default=221)
    parser.add_argument("--condition", choices=("PREFIX_ONLY", "POSITION_ONLY"), default="PREFIX_ONLY")
    parser.add_argument("--model-family", choices=("Qwen3-8B",), default="Qwen3-8B")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    loaded = QwenRawKVAdapter(args.q8_root, args.model_family).load_pair(args.pair_id, args.condition, split="test")
    pairs = {kind: [pair for pair in loaded if pair.state_kind == kind] for kind in ("K", "V")}
    states = {kind: _stack(pairs[kind], "a") for kind in ("K", "V")}
    targets = {kind: _stack(pairs[kind], "b") for kind in ("K", "V")}
    historical_reference = {
        (kind, layer): shared_residual_sse(targets[kind][layer] - states[kind][layer])
        for kind in ("K", "V") for layer in range(states[kind].shape[0])
    }

    agent_off = _allow_explicit_target(AgentKVShiftRecipe("full_exact", probe_ratio=0.20))
    agent_off_result = agent_off.execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": OfflineFullTargetProbeProvider(targets)}, states
    ))
    slide_off = _allow_explicit_target(CacheSlideWCARecipe(top_k_ratio=0.26, gating_interval=4, cksim_rule="retire_if_similarity_lte"))
    slide_off_result = slide_off.execute(RecipeExecutionContext(
        {"PARTIAL_FORWARD": OfflineFullTargetPartialForwardProvider(targets)}, states
    ))

    collector = TraceCollector("detail", trace_dir=Path(args.output) / "observability")
    agent_on = _allow_explicit_target(AgentKVShiftRecipe("full_exact", probe_ratio=0.20))
    agent_context = _context(collector, states, targets, pairs, args)
    agent_context.providers = {"PARTIAL_FORWARD": OfflineFullTargetProbeProvider(targets)}
    agent_on_result = agent_on.execute(agent_context)
    probe_only = _allow_explicit_target(AgentKVShiftForensicAblation(
        "probe_only", "full_exact", probe_ratio=0.20,
    ))
    probe_context = _context(collector, states, targets, pairs, args)
    probe_context.providers = {"PARTIAL_FORWARD": OfflineFullTargetProbeProvider(targets)}
    probe_result = probe_only.execute(probe_context)
    shift_only = _allow_explicit_target(AgentKVShiftForensicAblation(
        "shift_only", "full_exact", probe_ratio=0.20,
    ))
    shift_context = _context(collector, states, targets, pairs, args)
    shift_context.providers = {"PARTIAL_FORWARD": OfflineFullTargetProbeProvider(targets)}
    shift_result = shift_only.execute(shift_context)

    slide_on = _allow_explicit_target(CacheSlideWCARecipe(top_k_ratio=0.26, gating_interval=4, cksim_rule="retire_if_similarity_lte"))
    slide_context = _context(collector, states, targets, pairs, args)
    slide_context.providers = {"PARTIAL_FORWARD": OfflineFullTargetPartialForwardProvider(targets)}
    slide_on_result = slide_on.execute(slide_context)
    slide_gte = _allow_explicit_target(CacheSlideWCARecipe(top_k_ratio=0.26, gating_interval=4, cksim_rule="retire_if_similarity_gte"))
    slide_gte_context = _context(collector, states, targets, pairs, args)
    slide_gte_context.providers = {"PARTIAL_FORWARD": OfflineFullTargetPartialForwardProvider(targets)}
    slide_gte.execute(slide_gte_context)
    manifest = ObservabilityWriter(collector.trace_dir).write(collector)

    numeric_unchanged = all(
        np.array_equal(off.states[kind], on.states[kind])
        for off, on in ((agent_off_result, agent_on_result), (slide_off_result, slide_on_result))
        for kind in ("K", "V")
    )
    details = collector.detail_events()
    probe_match = True
    for kind in ("K", "V"):
        probe_rows = [row for row in details if row["method"] == "agentkvshift/paper_spec" and row["state_kind"] == kind and row["action"] == "probe"]
        probe_match = probe_match and bool(probe_rows) and all(
            row["positions"] == agent_on_result.diagnostics["probe_positions"][kind] for row in probe_rows
        )
    agent_costs = [row for row in collector.cost_rows() if row["method"] == "agentkvshift/paper_spec"]
    agent_cost_ok = bool(agent_costs) and all(row["recomputed_units_additive"] >= row["recomputed_units_unique_layer_position"] and row["replaced_units"] > 0 for row in agent_costs)
    official_selects = [
        row for row in collector.summary_events()
        if row["method"] == "agentkvshift/paper_spec" and row["action"] == "select" and row["layer_id"] == 0
    ]
    selector_diagnostics_ok = bool(official_selects) and all(
        all(name in row["metadata"] for name in (
            "selector_score_min", "selector_score_max", "selector_score_mean",
            "selector_nonzero_count", "selector_zero_count", "selector_unique_value_count",
            "selector_tie_count", "selector_all_equal", "selector_degenerate", "selected_count",
            "selected_position_fraction", "weight_min", "weight_max", "weight_mean",
            "weight_nonzero_count", "weight_zero_count",
        ))
        for row in official_selects
    )
    attribution_rows = [
        row for row in details
        if row["method"] == "agentkvshift/paper_spec" and row["action"] == "replace"
    ]
    attribution_ok = bool(attribution_rows)
    for row in attribution_rows:
        metadata = row["metadata"]
        before = metadata.get("sse_before_layer_repair")
        corrected = metadata.get("sse_after_nonprobe_correction")
        replaced = metadata.get("sse_after_probe_replacement")
        attribution_ok = attribution_ok and all(value is not None for value in (before, corrected, replaced))
        if before is not None and before > 0:
            attribution_ok = attribution_ok and np.isclose(
                metadata["correction_only_recovery"] + metadata["replacement_incremental_recovery"],
                metadata["total_layer_recovery"],
            )
    probe_actions = [row for row in details if row["method"] == "agentkvshift/forensic_probe_only" and row["layer_id"] > 0]
    shift_actions = [row for row in details if row["method"] == "agentkvshift/forensic_shift_only" and row["layer_id"] > 0]
    probe_ablation_ok = (
        probe_result.diagnostics.get("experiment_class") == "FORENSIC_ABLATION"
        and any(row["action"] == "replace" for row in probe_actions)
        and not any(row["action"] == "correct" for row in probe_actions)
    )
    shift_ablation_ok = (
        shift_result.diagnostics.get("experiment_class") == "FORENSIC_ABLATION"
        and any(row["action"] == "correct" for row in shift_actions)
        and not any(row["action"] == "replace" for row in shift_actions)
    )
    lte_details = [
        row for row in details
        if row["method"].startswith("cacheslide/") and row["metadata"].get("canonical_forensic_variant")
    ]
    slide_actions = {row["action"] for row in lte_details}
    slide_trace_ok = {"select", "recompute", "correct", "retire", "replace"} <= slide_actions
    summary_rows = collector.summary_events()
    forbidden_position_keys = {"retire_positions", "selected_before", "selected_after", "newly_selected_positions", "selector_score_by_position", "cksim_by_position"}
    summary_clean = all(
        row["positions"] is None
        and "detail" not in row["metadata"]
        and not (forbidden_position_keys & set(row["metadata"]))
        and not any(isinstance(value, list) for value in row["metadata"].values())
        for row in summary_rows
    )
    expected_positions = states["K"].shape[1]
    expected_layer_units = states["K"].shape[0] * expected_positions
    denominator_ok = bool(agent_costs) and all(
        row["total_positions"] == expected_positions
        and row["total_layer_position_units"] == expected_layer_units
        and np.isclose(row["selected_position_fraction"], row["selected_unique_positions"] / expected_positions)
        and np.isclose(
            row["recomputed_layer_position_fraction_unique"],
            row["recomputed_layer_position_units_unique"] / expected_layer_units,
        )
        for row in agent_costs
    )
    hash_unchanged = (
        agent_off.spec.recipe_variant_hash == agent_on.spec.recipe_variant_hash
        and slide_off.spec.recipe_variant_hash == slide_on.spec.recipe_variant_hash
    )
    historical_ok = all(
        historical_reference[(kind, layer)] == shared_residual_sse(targets[kind][layer] - states[kind][layer])
        for kind in ("K", "V") for layer in range(states[kind].shape[0])
    )

    print(f"TRACE_SCHEMA_VERSION={TRACE_SCHEMA_VERSION}")
    print(f"SUMMARY_EVENT_COUNT={manifest['summary_event_count']}")
    print(f"DETAIL_EVENT_COUNT={manifest['detail_event_count']}")
    print(f"AGENT_PROBE_POSITIONS_MATCH={'PASS' if probe_match else 'FAIL'}")
    print(f"AGENT_COST_ACCOUNTING={'PASS' if agent_cost_ok else 'FAIL'}")
    print(f"AGENT_SELECTOR_DIAGNOSTICS={'PASS' if selector_diagnostics_ok else 'FAIL'}")
    print(f"AGENT_CORRECTION_ATTRIBUTION={'PASS' if attribution_ok else 'FAIL'}")
    print(f"AGENT_PROBE_ONLY_ABLATION={'PASS' if probe_ablation_ok else 'FAIL'}")
    print(f"AGENT_SHIFT_ONLY_ABLATION={'PASS' if shift_ablation_ok else 'FAIL'}")
    print(f"CACHESLIDE_LTE_TRANSITION_TRACE={'PASS' if slide_trace_ok else 'FAIL'}")
    print(f"SUMMARY_HAS_NO_POSITION_ARRAYS={'PASS' if summary_clean else 'FAIL'}")
    print(f"COST_DENOMINATOR_SEMANTICS={'PASS' if denominator_ok else 'FAIL'}")
    print(f"NUMERIC_OUTPUT_UNCHANGED={'PASS' if numeric_unchanged else 'FAIL'}")
    print(f"METHOD_HASH_UNCHANGED={'PASS' if hash_unchanged else 'FAIL'}")
    print(f"HISTORICAL_REGRESSION={'PASS' if historical_ok else 'FAIL'}")
    selector_k = next((row for row in official_selects if row["state_kind"] == "K"), official_selects[0] if official_selects else None)
    if selector_k is not None:
        print(f"SELECTOR_DEGENERATE={str(bool(selector_k['metadata']['selector_degenerate'])).lower()}")
        print(f"SELECTOR_NONZERO_COUNT={selector_k['metadata']['selector_nonzero_count']}")
        print(f"SELECTED_POSITIONS_COUNT={selector_k['metadata']['selected_count']}")
    for path in sorted(collector.trace_dir.iterdir()):
        print(f"OBSERVABILITY_FILE={path} SIZE={path.stat().st_size}")
    checks = (
        probe_match, agent_cost_ok, selector_diagnostics_ok, attribution_ok,
        probe_ablation_ok, shift_ablation_ok, slide_trace_ok, summary_clean,
        denominator_ok, numeric_unchanged, hash_unchanged, historical_ok,
    )
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
