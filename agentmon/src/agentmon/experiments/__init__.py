"""Experiment planning, evidence, and offline replay helpers."""

from __future__ import annotations

__all__ = [
    "BatchExperimentData",
    "build_evidence_outputs",
    "generate_experiment_contract",
    "load_batch_experiment",
    "load_experiment_request",
    "orchestrate_experiment",
    "run_optimizer_advice_replay",
    "write_generated_contract",
    "write_evidence_outputs",
]


def __getattr__(name: str):
    if name in {
        "BatchExperimentData",
        "build_evidence_outputs",
        "load_batch_experiment",
        "write_evidence_outputs",
    }:
        from .evidence import (
            BatchExperimentData,
            build_evidence_outputs,
            load_batch_experiment,
            write_evidence_outputs,
        )

        return {
            "BatchExperimentData": BatchExperimentData,
            "build_evidence_outputs": build_evidence_outputs,
            "load_batch_experiment": load_batch_experiment,
            "write_evidence_outputs": write_evidence_outputs,
        }[name]
    if name == "run_optimizer_advice_replay":
        from .replay import run_optimizer_advice_replay

        return run_optimizer_advice_replay
    if name == "orchestrate_experiment":
        from .orchestration import orchestrate_experiment

        return orchestrate_experiment
    if name in {"generate_experiment_contract", "load_experiment_request", "write_generated_contract"}:
        from .request import generate_experiment_contract, load_experiment_request, write_generated_contract

        return {
            "generate_experiment_contract": generate_experiment_contract,
            "load_experiment_request": load_experiment_request,
            "write_generated_contract": write_generated_contract,
        }[name]
    raise AttributeError(name)
