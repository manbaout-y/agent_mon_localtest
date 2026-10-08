from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agentmon.pipeline.model import StageSpec as AnalysisStageSpec


@dataclass(frozen=True)
class AnalysisStageResult:
    status: str = "PASS"
    output_files: tuple[str, ...] = field(default_factory=tuple)
    row_counts: dict[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    cache_hit: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisStageContext:
    bundle_root: Path
    cfg: Any
    state: dict[str, Any] = field(default_factory=dict)


class AnalysisStage(Protocol):
    spec: AnalysisStageSpec

    def run(self, context: AnalysisStageContext) -> AnalysisStageResult:
        ...


STAGE_SPECS: tuple[AnalysisStageSpec, ...] = (
    AnalysisStageSpec("preflight", "preparation"),
    AnalysisStageSpec("input_inventory", "preparation"),
    AnalysisStageSpec("baseline_check", "preparation", dependencies=("input_inventory",)),
    AnalysisStageSpec("resume_check", "preparation", dependencies=("input_inventory", "baseline_check")),
    AnalysisStageSpec("discovery", "normalization", dependencies=("input_inventory", "resume_check")),
    AnalysisStageSpec("manifest", "normalization", dependencies=("discovery",)),
    AnalysisStageSpec("inventory", "normalization", dependencies=("discovery",)),
    AnalysisStageSpec("contract", "normalization", dependencies=("discovery", "inventory")),
    AnalysisStageSpec("schema_resolution", "normalization", dependencies=("discovery",)),
    AnalysisStageSpec("semantics_export", "normalization", dependencies=("schema_resolution",)),
    AnalysisStageSpec("build_p2_index", "normalization", dependencies=("discovery",)),
    AnalysisStageSpec("normalize_runs", "normalization", dependencies=("discovery", "build_p2_index", "schema_resolution")),
    AnalysisStageSpec("normalize_requests", "normalization", dependencies=("normalize_runs",)),
    AnalysisStageSpec("normalize_scheduler", "normalization", dependencies=("normalize_runs",)),
    AnalysisStageSpec("normalize_resources", "normalization", dependencies=("normalize_runs",)),
    AnalysisStageSpec("p1_p2_audit", "normalization", dependencies=("normalize_requests", "build_p2_index")),
    AnalysisStageSpec("validation", "validation", dependencies=("normalize_requests", "normalize_scheduler", "normalize_resources", "p1_p2_audit")),
    AnalysisStageSpec("throughput_analysis", "analysis", dependencies=("validation",)),
    AnalysisStageSpec("concurrency_analysis", "analysis", dependencies=("validation",)),
    AnalysisStageSpec("lifecycle_analysis", "analysis", dependencies=("validation",)),
    AnalysisStageSpec("workload_analysis", "analysis", dependencies=("validation",)),
    AnalysisStageSpec("tail_analysis", "analysis", dependencies=("validation",)),
    AnalysisStageSpec("pipeline_analysis", "analysis", dependencies=("validation",)),
    AnalysisStageSpec("scheduler_analysis", "analysis", dependencies=("validation",)),
    AnalysisStageSpec("resource_analysis", "analysis", dependencies=("validation",)),
    AnalysisStageSpec("evidence_build", "packaging", dependencies=("throughput_analysis", "concurrency_analysis", "lifecycle_analysis", "workload_analysis", "tail_analysis", "pipeline_analysis", "scheduler_analysis", "resource_analysis")),
    AnalysisStageSpec("optimizer_harness_build", "packaging", dependencies=("validation",)),
    AnalysisStageSpec("handoff_build", "packaging", dependencies=("evidence_build",)),
    AnalysisStageSpec("model_pack_build", "packaging", dependencies=("evidence_build",)),
    AnalysisStageSpec("report_prompt_build", "packaging", dependencies=("evidence_build", "handoff_build", "model_pack_build")),
    AnalysisStageSpec("artifact_validation", "validation", dependencies=("evidence_build", "optimizer_harness_build", "handoff_build", "model_pack_build", "report_prompt_build")),
    AnalysisStageSpec("final_summary", "finalization", cacheable=False, dependencies=("artifact_validation",)),
)

STAGE_NAMES = tuple(spec.name for spec in STAGE_SPECS)


def stage_spec(stage_name: str) -> AnalysisStageSpec:
    specs = {spec.name: spec for spec in STAGE_SPECS}
    try:
        return specs[stage_name]
    except KeyError as exc:
        raise ValueError(f"unknown analysis stage: {stage_name}") from exc


def stage_dag_nodes() -> list[dict[str, Any]]:
    return [
        {
            "stage_name": spec.name,
            "group": spec.group,
            "cacheable": spec.cacheable,
            "inputs": list(spec.inputs),
            "outputs": list(spec.outputs),
            "dependencies": list(spec.dependencies),
            "required_capabilities": list(spec.required_capabilities),
        }
        for spec in STAGE_SPECS
    ]


def stage_dag_edges() -> list[dict[str, str]]:
    return [
        {"from": dependency, "to": spec.name}
        for spec in STAGE_SPECS
        for dependency in spec.dependencies
    ]
