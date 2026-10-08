from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np

from kvreuse_b5.providers import RecipeProvider


class FidelityLevel(str, Enum):
    F0_EXACT_REPRODUCTION = "F0_EXACT_REPRODUCTION"
    F1_ALGORITHMIC_REPRODUCTION = "F1_ALGORITHMIC_REPRODUCTION"
    F2_MECHANISM_BASELINE = "F2_MECHANISM_BASELINE"
    F3_INTERFACE_ONLY = "F3_INTERFACE_ONLY"


class RecipeExecutionStatus(str, Enum):
    EXECUTED = "EXECUTED"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    BLOCKED = "BLOCKED"


class RecipeCapabilityMissingError(RuntimeError):
    def __init__(self, missing: Sequence[str]):
        self.missing = tuple(sorted(set(missing)))
        super().__init__("CAPABILITY_MISSING: " + ", ".join(self.missing))


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RecipeCapabilities:
    required: tuple[str, ...] = ()
    allow_oracle_providers: bool = False
    allow_full_target_providers: bool = False


@dataclass(frozen=True)
class RecipeVariant:
    variant_id: str
    source: str
    difference_level: str
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecipeStep:
    name: str
    step_type: str
    provider: str | None = None
    evidence_refs: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.step_type == "paper_mechanism" and not self.evidence_refs:
            raise ValueError(f"core recipe step {self.name!r} has no evidence refs")


@dataclass(frozen=True)
class PaperRecipeSpec:
    recipe_id: str
    paper_id: str
    variant_id: str
    fidelity_level: FidelityLevel
    paper_spec_path: str
    paper_spec_source_hash: str
    capabilities: RecipeCapabilities
    required_providers: tuple[str, ...]
    offline_steps: tuple[RecipeStep, ...]
    online_steps: tuple[RecipeStep, ...]
    selection_granularity: str
    budget_semantics: str
    position_semantics: str
    state_update_semantics: str
    evidence_ids: tuple[str, ...]
    unresolved_fields: tuple[str, ...] = ()
    runtime_dependencies: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    execution_status: str = "EXECUTABLE_COMPONENT"

    def __post_init__(self) -> None:
        known = set(self.evidence_ids)
        unresolved = [ref for step in self.offline_steps + self.online_steps for ref in step.evidence_refs if ref not in known]
        if unresolved:
            raise ValueError(f"unresolved evidence refs for {self.recipe_id}: {sorted(set(unresolved))}")

    def canonical_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["fidelity_level"] = self.fidelity_level.value
        return value

    @property
    def recipe_spec_hash(self) -> str:
        return canonical_hash({"paper_spec_source_hash": self.paper_spec_source_hash, "recipe": self.canonical_dict()})

    @property
    def recipe_variant_hash(self) -> str:
        return canonical_hash({"recipe_id": self.recipe_id, "variant": self.variant_id, "parameters": self.parameters, "steps": [asdict(s) for s in self.online_steps], "providers": self.required_providers, "source": self.paper_spec_source_hash})


@dataclass(frozen=True)
class DeclarativeRecipeSpec:
    recipe_id: str
    paper_id: str
    paper: str
    variant: str
    spec_status: str
    execution_requirements: tuple[str, ...]
    max_fidelity: str
    required_providers: tuple[str, ...]
    implementable_now: bool
    blocking_fields: tuple[str, ...]
    paper_spec_path: str
    paper_spec_source_hash: str

    @property
    def recipe_spec_hash(self) -> str:
        return canonical_hash(asdict(self))


@dataclass
class RecipeCostRecord:
    provider_costs: dict[str, float] = field(default_factory=dict)
    selected_units: int = 0
    updated_units: int = 0

    def add(self, costs: Mapping[str, float]) -> None:
        for key, value in costs.items():
            self.provider_costs[key] = self.provider_costs.get(key, 0.0) + float(value)


@dataclass
class RecipeTraceEntry:
    step_index: int
    step_name: str
    provider_invoked: str | None
    input_scope: Mapping[str, Any]
    output_scope: Mapping[str, Any]
    selected_units: int
    state_updated: bool
    cost_added: Mapping[str, float]


RecipeExecutionTrace = list[RecipeTraceEntry]


@dataclass
class RecipeExecutionContext:
    providers: dict[str, RecipeProvider]
    states: dict[str, np.ndarray]
    metadata: dict[str, Any] = field(default_factory=dict)

    def copy_states(self) -> dict[str, np.ndarray]:
        return {k: np.asarray(v, dtype=float).copy() for k, v in self.states.items()}


@dataclass
class RecipeResult:
    recipe_id: str
    status: RecipeExecutionStatus
    states: dict[str, np.ndarray]
    trace: RecipeExecutionTrace
    cost: RecipeCostRecord
    diagnostics: dict[str, Any] = field(default_factory=dict)


class ExecutableRecipe(ABC):
    def __init__(self, spec: PaperRecipeSpec):
        self.spec = spec

    def validate_context(self, context: RecipeExecutionContext) -> None:
        missing = [name for name in self.spec.required_providers if name not in context.providers]
        if missing:
            raise RecipeCapabilityMissingError(missing)
        forbidden = []
        for name in self.spec.required_providers:
            caps = context.providers[name].capabilities
            if caps.capability != name:
                forbidden.append(f"{name}:provider_declares_{caps.capability}")
            if caps.oracle and not self.spec.capabilities.allow_oracle_providers:
                forbidden.append(f"{name}:oracle_provider")
            if caps.uses_full_target and not self.spec.capabilities.allow_full_target_providers:
                forbidden.append(f"{name}:uses_full_target")
        if forbidden:
            raise RecipeCapabilityMissingError(forbidden)

    @abstractmethod
    def execute(self, context: RecipeExecutionContext) -> RecipeResult:
        raise NotImplementedError


def trace_entry(index: int, step: RecipeStep, *, input_scope: Mapping[str, Any], output_scope: Mapping[str, Any], selected_units: int = 0, state_updated: bool = False, cost: Mapping[str, float] | None = None) -> RecipeTraceEntry:
    return RecipeTraceEntry(index, step.name, step.provider, dict(input_scope), dict(output_scope), int(selected_units), bool(state_updated), dict(cost or {}))
