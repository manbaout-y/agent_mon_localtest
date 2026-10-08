from __future__ import annotations

from kvreuse_b5.data.types import StatePair
from .base import (
    CapabilityMissingError,
    CorrectionMethod,
    CorrectionResult,
    EvalContext,
    MethodCapabilities,
    MethodCategory,
)


class InterfaceOnlyMethod(CorrectionMethod):
    category = MethodCategory.UNAVAILABLE

    def __init__(self, name: str, capabilities: MethodCapabilities):
        self.name = name
        self.capabilities = capabilities
        super().__init__()

    def correct(self, pair: StatePair, context: EvalContext) -> CorrectionResult:
        raise CapabilityMissingError(["full_method_implementation"])


PLACEHOLDER_CAPABILITIES = {
    "cache_craft_query": MethodCapabilities(requires_query=True, requires_attention=True),
    "a3": MethodCapabilities(requires_query=True, requires_query_score=True),
    "prophetkv": MethodCapabilities(requires_query=True, requires_query_score=True),
    "qcfuse": MethodCapabilities(requires_query=True, requires_query_score=True),
    "query_aware_selection": MethodCapabilities(requires_query=True, requires_query_score=True),
    "attention_aware_selection": MethodCapabilities(requires_query=True, requires_attention=True),
    "kvlink": MethodCapabilities(requires_training=True, requires_architecture_change=True),
    "kv_packet": MethodCapabilities(requires_training=True, requires_architecture_change=True),
    "c2kv": MethodCapabilities(requires_training=True, requires_architecture_change=True),
    "recache": MethodCapabilities(requires_training=True),
    "fusionrag_runtime": MethodCapabilities(requires_runtime=True),
    "fusionrag": MethodCapabilities(requires_runtime=True),
    "daf_runtime": MethodCapabilities(requires_runtime=True),
    "daf": MethodCapabilities(requires_runtime=True),
    "mepic_runtime": MethodCapabilities(requires_runtime=True),
    "mepic": MethodCapabilities(requires_runtime=True),
    "cachetune_runtime": MethodCapabilities(requires_runtime=True),
    "cachetune": MethodCapabilities(requires_runtime=True),
}
