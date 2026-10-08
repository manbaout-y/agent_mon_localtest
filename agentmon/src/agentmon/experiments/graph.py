from __future__ import annotations

from typing import Any

from .evidence import BatchExperimentData, build_evidence_graph


def build_optimizer_evidence_graph(data: BatchExperimentData) -> dict[str, Any]:
    return build_evidence_graph(data)
