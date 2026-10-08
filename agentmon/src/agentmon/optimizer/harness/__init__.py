"""Offline optimizer experiment harness helpers."""

from __future__ import annotations

__all__ = [
    "audit_decision_timeline",
    "run_optimizer_experiment_harness",
]


def __getattr__(name: str):
    if name == "audit_decision_timeline":
        from .audit import audit_decision_timeline

        return audit_decision_timeline
    if name == "run_optimizer_experiment_harness":
        from .runner import run_optimizer_experiment_harness

        return run_optimizer_experiment_harness
    raise AttributeError(name)
