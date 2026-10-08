from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping


FORENSIC_FIELDS = (
    "method",
    "variant",
    "model",
    "condition",
    "state_kind",
    "pair_id",
    "selector_degenerate",
    "selected_position_fraction",
    "recomputed_layer_position_fraction",
    "correction_only_recovery",
    "replacement_incremental_recovery",
    "total_recovery",
    "unique_positions",
    "unique_layer_position_units",
    "additive_recompute_units",
)


def _fraction(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def build_forensic_rows(
    summary_events: Iterable[Mapping[str, Any]],
    cost_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    events_by_key: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for event in summary_events:
        key = (
            str(event["method"]),
            str(event["method_config_hash"]),
            str(event["model_family"]),
            str(event["condition"]),
            str(event["state_kind"]),
            str(event["pair_id"]),
        )
        events_by_key[key].append(event)

    rows: list[dict[str, Any]] = []
    for cost in cost_rows:
        key = (
            str(cost["method"]),
            str(cost["method_config_hash"]),
            str(cost["model_family"]),
            str(cost["condition"]),
            str(cost["state_kind"]),
            str(cost["pair_id"]),
        )
        events = events_by_key.get(key, [])
        selector_events = [event for event in events if event["action"] == "select"]
        selector_degenerate = any(
            bool(event.get("metadata", {}).get("selector_degenerate"))
            for event in selector_events
        )
        variant = next(
            (
                str(event.get("metadata", {}).get("forensic_variant") or event.get("metadata", {}).get("variant"))
                for event in events
                if event.get("metadata", {}).get("forensic_variant") or event.get("metadata", {}).get("variant")
            ),
            "",
        )

        layer_checkpoints: dict[int, tuple[float, float, float]] = {}
        for event in events:
            metadata = event.get("metadata", {})
            before = metadata.get("sse_before_layer_repair")
            after_correction = metadata.get("sse_after_nonprobe_correction")
            after_replacement = metadata.get("sse_after_probe_replacement")
            if before is not None and after_correction is not None and after_replacement is not None:
                layer_checkpoints[int(event["layer_id"])] = (
                    float(before), float(after_correction), float(after_replacement)
                )
        denominator = sum(value[0] for value in layer_checkpoints.values())
        correction_gain = sum(value[0] - value[1] for value in layer_checkpoints.values())
        replacement_gain = sum(value[1] - value[2] for value in layer_checkpoints.values())
        total_gain = sum(value[0] - value[2] for value in layer_checkpoints.values())

        rows.append({
            "method": cost["method"],
            "variant": variant,
            "model": cost["model_family"],
            "condition": cost["condition"],
            "state_kind": cost["state_kind"],
            "pair_id": cost["pair_id"],
            "selector_degenerate": selector_degenerate,
            "selected_position_fraction": cost.get("selected_position_fraction"),
            "recomputed_layer_position_fraction": cost.get("recomputed_layer_position_fraction_unique"),
            "correction_only_recovery": _fraction(correction_gain, denominator),
            "replacement_incremental_recovery": _fraction(replacement_gain, denominator),
            "total_recovery": _fraction(total_gain, denominator),
            "unique_positions": cost.get("unique_positions", 0),
            "unique_layer_position_units": cost.get("recomputed_layer_position_units_unique", 0),
            "additive_recompute_units": cost.get("recomputed_layer_position_units_additive", 0),
        })
    return rows
