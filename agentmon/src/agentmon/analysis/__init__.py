"""Deterministic AgentMon experiment analysis SOP."""

from __future__ import annotations

__all__ = ["prepare_analysis_bundle", "run_all_analysis"]


def __getattr__(name: str):
    if name in __all__:
        from agentmon.analysis.pipeline import prepare_analysis_bundle, run_all_analysis

        return {
            "prepare_analysis_bundle": prepare_analysis_bundle,
            "run_all_analysis": run_all_analysis,
        }[name]
    raise AttributeError(name)
