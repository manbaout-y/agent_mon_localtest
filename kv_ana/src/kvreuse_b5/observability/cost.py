from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import KVTraceEvent


@dataclass
class CostAccountingRecord:
    run_id: str
    method: str
    method_config_hash: str
    model_family: str
    condition: str
    state_kind: str
    pair_id: int | str
    selected_units: int = 0
    observed_units: int = 0
    probe_units: int = 0
    recomputed_units_additive: int = 0
    corrected_units: int = 0
    replaced_units: int = 0
    total_available_units: int = 0
    total_positions: int = 0
    total_layer_position_units: int = 0
    _recomputed_layer_positions: set[tuple[int, int | float]] = field(default_factory=set, repr=False)
    _recomputed_positions: set[int | float] = field(default_factory=set, repr=False)
    _selected_positions: set[int | float] = field(default_factory=set, repr=False)
    _available_layer_units: dict[int, int] = field(default_factory=dict, repr=False)

    @property
    def recomputed_units_unique_layer_position(self) -> int:
        return len(self._recomputed_layer_positions)

    @property
    def unique_positions(self) -> int:
        return len(self._recomputed_positions)

    @property
    def selected_unique_positions(self) -> int:
        return len(self._selected_positions)

    def add(self, event: KVTraceEvent) -> None:
        self.selected_units += event.selected_units
        if event.action.value == "select" and event.positions is not None:
            self._selected_positions.update(event.positions)
        self.observed_units += event.observed_units
        if event.action.value == "probe":
            self.probe_units += event.observed_units
        self.recomputed_units_additive += event.recomputed_units
        self.corrected_units += event.corrected_units
        self.replaced_units += event.replaced_units
        available = int(event.metadata.get("total_available_units", 0))
        positions = int(event.metadata.get("total_positions", 0))
        layer_positions = int(event.metadata.get("total_layer_position_units", available))
        self.total_positions = max(self.total_positions, positions)
        if event.metadata.get("available_scope") == "layer":
            self._available_layer_units[event.layer_id] = max(self._available_layer_units.get(event.layer_id, 0), available)
            self.total_available_units = sum(self._available_layer_units.values())
            self.total_layer_position_units = self.total_available_units
        else:
            self.total_available_units = max(self.total_available_units, available)
            self.total_layer_position_units = max(self.total_layer_position_units, layer_positions)
        if event.recomputed_units and event.positions is not None:
            self._recomputed_layer_positions.update((event.layer_id, position) for position in event.positions)
            self._recomputed_positions.update(event.positions)

    @staticmethod
    def _fraction(value: int, total: int) -> float | None:
        return value / total if total > 0 else None

    def to_row(self) -> dict[str, Any]:
        total = self.total_available_units
        layer_total = self.total_layer_position_units or total
        position_total = self.total_positions
        return {
            "run_id": self.run_id,
            "method": self.method,
            "method_config_hash": self.method_config_hash,
            "model_family": self.model_family,
            "condition": self.condition,
            "state_kind": self.state_kind,
            "pair_id": self.pair_id,
            "selected_units": self.selected_units,
            "observed_units": self.observed_units,
            "probe_units": self.probe_units,
            "recomputed_units_additive": self.recomputed_units_additive,
            "recomputed_units_unique_layer_position": self.recomputed_units_unique_layer_position,
            "unique_positions": self.unique_positions,
            "selected_unique_positions": self.selected_unique_positions,
            "corrected_units": self.corrected_units,
            "replaced_units": self.replaced_units,
            "total_available_units": total,
            "total_positions": position_total,
            "total_layer_position_units": layer_total,
            "observed_layer_position_units": self.observed_units,
            "recomputed_layer_position_units_additive": self.recomputed_units_additive,
            "recomputed_layer_position_units_unique": self.recomputed_units_unique_layer_position,
            "corrected_layer_position_units": self.corrected_units,
            "selected_fraction": self._fraction(self.selected_units, total),
            "probe_fraction": self._fraction(self.probe_units, total),
            "recomputed_fraction_additive": self._fraction(self.recomputed_units_additive, total),
            "recomputed_fraction_unique": self._fraction(self.recomputed_units_unique_layer_position, total),
            "corrected_fraction": self._fraction(self.corrected_units, total),
            "selected_position_fraction": self._fraction(self.selected_unique_positions, position_total),
            "recomputed_layer_position_fraction_additive": self._fraction(self.recomputed_units_additive, layer_total),
            "recomputed_layer_position_fraction_unique": self._fraction(self.recomputed_units_unique_layer_position, layer_total),
            "corrected_layer_position_fraction": self._fraction(self.corrected_units, layer_total),
            "legacy_fraction_semantics": "selected_fraction/probe_fraction/recomputed_fraction_*/corrected_fraction deprecated when numerator granularity is ambiguous",
            "cost_semantics": "positions use total_positions; layer-position work uses total_layer_position_units; additive!=unique_recompute!=latency",
        }
