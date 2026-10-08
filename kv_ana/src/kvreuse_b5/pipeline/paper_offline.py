from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Sequence

import numpy as np

from kvreuse_b5.pipeline.providers import OfflineFullTargetPartialForwardProvider, OfflineFullTargetProbeProvider
from kvreuse_b5.observability import TraceCollector
from kvreuse_b5.pipeline.real_data import RealStateSource
from kvreuse_b5.recipes import RecipeCapabilities, RecipeExecutionContext
from kvreuse_b5.recipes.agentkvshift import AgentKVShiftRecipe
from kvreuse_b5.recipes.cacheslide import CacheSlideWCARecipe
from kvreuse_b5.regression.manifest import ManifestSample


def _stack(pairs: Sequence[Any], field: str) -> np.ndarray:
    shapes = {getattr(pair, field).shape for pair in pairs}
    if len(shapes) != 1:
        raise ValueError(f"paper component requires identical per-layer shape, got {sorted(shapes)}")
    return np.stack([getattr(pair, field) for pair in pairs], axis=0).astype(np.float64)


def _allow_explicit_target_provider(recipe: Any) -> None:
    capabilities = replace(
        recipe.spec.capabilities,
        allow_oracle_providers=True,
        allow_full_target_providers=True,
    )
    recipe.spec = replace(recipe.spec, capabilities=capabilities)


class _MetricAccumulator:
    def __init__(self) -> None:
        self.baseline = 0.0
        self.corrected = 0.0
        self.before_dot = 0.0
        self.before_a2 = 0.0
        self.before_b2 = 0.0
        self.after_dot = 0.0
        self.after_a2 = 0.0
        self.after_b2 = 0.0
        self.pairs: set[int] = set()
        self.observed = 0.0
        self.total = 0

    def add(self, pair_id: int, a: np.ndarray, b: np.ndarray, corrected: np.ndarray, observed: float) -> None:
        av = a.astype(np.float64).ravel(); bv = b.astype(np.float64).ravel(); cv = corrected.astype(np.float64).ravel()
        self.baseline += float(np.sum((bv - av) ** 2)); self.corrected += float(np.sum((bv - cv) ** 2))
        self.before_dot += float(av @ bv); self.before_a2 += float(av @ av); self.before_b2 += float(bv @ bv)
        self.after_dot += float(cv @ bv); self.after_a2 += float(cv @ cv); self.after_b2 += float(bv @ bv)
        self.pairs.add(int(pair_id)); self.observed += float(observed); self.total += int(np.prod(a.shape[:-1]))

    @staticmethod
    def _cos(dot: float, left: float, right: float) -> float | None:
        denom = np.sqrt(left * right)
        return dot / denom if denom > 0 else None

    def row(self, **values: Any) -> dict[str, Any]:
        return {
            **values,
            "scope": "heldout16",
            "pair_count": len(self.pairs),
            "baseline_sse": self.baseline,
            "corrected_sse": self.corrected,
            "residual_energy_explained": 1.0 - self.corrected / self.baseline if self.baseline > 0 else None,
            "before_cosine": self._cos(self.before_dot, self.before_a2, self.before_b2),
            "after_cosine": self._cos(self.after_dot, self.after_a2, self.after_b2),
            "probe_fraction": self.observed / self.total if self.total else None,
            "repair_fraction": None,
        }


def run_real_paper_components(
    source: RealStateSource, manifest: Sequence[ManifestSample], *, max_pairs: int | None = None,
    observability_collector: TraceCollector | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    test = [sample for sample in manifest if sample.split == "test"]
    if max_pairs is not None:
        test = test[:max_pairs]
    accumulators: dict[tuple[str, str, str, str, str], _MetricAccumulator] = {}
    costs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for model in ("Qwen3-8B", "Qwen3.5-27B"):
        for condition in ("PREFIX_ONLY", "POSITION_ONLY"):
            for sample in test:
                grouped = source.load_qwen_kv(model, condition, sample.pair_id, split="test")
                states = {kind: _stack(grouped[kind], "a") for kind in ("K", "V")}
                targets = {kind: _stack(grouped[kind], "b") for kind in ("K", "V")}
                trace_metadata = {
                    "observability_collector": observability_collector,
                    "model_family": model,
                    "condition": condition,
                    "pair_id": sample.pair_id,
                    "layer_ids": [pair.layer_id for pair in grouped["K"]],
                    "observability_targets": targets,
                    "request_id": None,
                    "agent_run_id": None,
                    "agent_step_id": None,
                    "model_request_id": None,
                    "reuse_request_id": None,
                    "chunk_id": None,
                }

                try:
                    agent = AgentKVShiftRecipe("full_exact", probe_ratio=0.20)
                    _allow_explicit_target_provider(agent)
                    provider = OfflineFullTargetProbeProvider(targets)
                    result = agent.execute(RecipeExecutionContext({"PARTIAL_FORWARD": provider}, states, trace_metadata))
                    observed = result.cost.provider_costs.get("full_exact_fresh_units", 0.0)
                    for kind in ("K", "V"):
                        key = ("AgentKVShift", "paper_spec/full_exact", model, condition, kind)
                        accumulators.setdefault(key, _MetricAccumulator()).add(sample.pair_id, states[kind], targets[kind], result.states[kind], observed / 2.0)
                    costs.append({"paper": "AgentKVShift", "variant": "paper_spec/full_exact", "model": model, "condition": condition, "pair_id": sample.pair_id, "observed_probe_units": observed, "exact_repaired_units": 0, "basis_rank": None, "anchor_count": None, "estimated_offline_compute_class": "full checkpoint layer + sparse exact probes"})
                except Exception as exc:  # method-level isolation; adapter failures occur before this block
                    errors.append({"paper": "AgentKVShift", "variant": "paper_spec/full_exact", "model": model, "condition": condition, "pair_id": sample.pair_id, "status": "METHOD_BLOCKED", "error": f"{type(exc).__name__}: {exc}"})

                for rule in ("retire_if_similarity_gte", "retire_if_similarity_lte"):
                    try:
                        slide = CacheSlideWCARecipe(top_k_ratio=0.26, gating_interval=4, cksim_rule=rule)
                        _allow_explicit_target_provider(slide)
                        slide_provider = OfflineFullTargetPartialForwardProvider(targets)
                        result = slide.execute(RecipeExecutionContext({"PARTIAL_FORWARD": slide_provider}, states, trace_metadata))
                        observed = result.cost.provider_costs.get("full_exact_fresh_units", 0.0)
                        for kind in ("K", "V"):
                            key = ("CacheSlide", f"WCA/{rule}", model, condition, kind)
                            accumulators.setdefault(key, _MetricAccumulator()).add(sample.pair_id, states[kind], targets[kind], result.states[kind], observed / 2.0)
                        costs.append({"paper": "CacheSlide", "variant": f"WCA/{rule}", "model": model, "condition": condition, "pair_id": sample.pair_id, "observed_probe_units": observed, "exact_repaired_units": result.cost.updated_units, "basis_rank": None, "anchor_count": None, "estimated_offline_compute_class": "full layer1 + selected fresh WCA states"})
                    except Exception as exc:  # one ambiguity variant must not block the other
                        errors.append({"paper": "CacheSlide", "variant": f"WCA/{rule}", "model": model, "condition": condition, "pair_id": sample.pair_id, "status": "METHOD_BLOCKED", "error": f"{type(exc).__name__}: {exc}"})
    rows = []
    for (paper, variant, model, condition, state), accumulator in sorted(accumulators.items()):
        rows.append(accumulator.row(method=paper if paper != "CacheSlide" else "CacheSlide_WCA", paper=paper, variant=variant, fidelity="F1_ALGORITHMIC_REPRODUCTION_COMPONENT", component="full_exact fresh-probe correction" if paper == "AgentKVShift" else "WCA", model=model, condition=condition, state=state, oracle_target_usage="oracle_probe/full_exact_fresh_probe" if paper == "AgentKVShift" else "oracle_probe/full_exact_fresh_recompute", train_target_usage="none", runtime_required=True, notes="offline component only; not a full serving-system reproduction"))
    return rows, costs, errors
