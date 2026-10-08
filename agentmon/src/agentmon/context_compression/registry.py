from __future__ import annotations

from collections.abc import Callable, Mapping
import importlib
import json
import threading
from typing import Any, Protocol

from agentmon.config import AgentMonConfig
from agentmon.strategy import StrategyRegistry

from .strategies import (
    ModelSummaryContextCompression,
    RecentGroupsContextCompression,
    TokenRatioContextCompressionTrigger,
)
from .types import (
    ContextCompressionRequest,
    ContextCompressionResult,
    ContextCompressionTriggerResult,
    ContextMeasurement,
)


class ContextCompressionStrategy(Protocol):
    name: str

    def measure(
        self,
        context: Any,
        metadata: Mapping[str, Any],
    ) -> ContextMeasurement:
        ...

    def compress(
        self,
        request: ContextCompressionRequest,
    ) -> ContextCompressionResult:
        ...


class ContextCompressionTrigger(Protocol):
    name: str

    def evaluate(
        self,
        request: ContextCompressionRequest,
    ) -> ContextCompressionTriggerResult:
        ...


ContextCompressionFactory = Callable[
    [AgentMonConfig, Mapping[str, Any]],
    ContextCompressionStrategy,
]
ContextCompressionTriggerFactory = Callable[
    [AgentMonConfig, Mapping[str, Any]],
    ContextCompressionTrigger,
]

CONTEXT_COMPRESSION_TRIGGERS: StrategyRegistry[ContextCompressionTrigger] = (
    StrategyRegistry("context_compression_trigger")
)
CONTEXT_COMPRESSION_TRIGGERS.register(
    TokenRatioContextCompressionTrigger.name,
    TokenRatioContextCompressionTrigger,
    description=(
        "Trigger when measured context tokens reach the configured ratio of "
        "the model context window."
    ),
)

CONTEXT_COMPRESSION_STRATEGIES: StrategyRegistry[ContextCompressionStrategy] = (
    StrategyRegistry("context_compression")
)
CONTEXT_COMPRESSION_STRATEGIES.register(
    ModelSummaryContextCompression.name,
    ModelSummaryContextCompression,
    description=(
        "Replace older adapter-declared context groups with a model-generated "
        "continuation summary while retaining required and recent groups."
    ),
)
CONTEXT_COMPRESSION_STRATEGIES.register(
    RecentGroupsContextCompression.name,
    RecentGroupsContextCompression,
    description=(
        "Keep adapter-declared instruction groups and the newest complete "
        "conversation groups within the target token budget."
    ),
)

_PLUGIN_LOCK = threading.Lock()
_LOADED_PLUGIN_MODULES: set[str] = set()


def build_context_compression_trigger(
    cfg: AgentMonConfig,
) -> ContextCompressionTrigger:
    load_context_compression_plugins(cfg.context_compression.plugin_modules)
    options = json.loads(cfg.context_compression.trigger_options_json)
    return CONTEXT_COMPRESSION_TRIGGERS.create(
        cfg.context_compression.trigger_strategy,
        cfg,
        options,
    )


def build_context_compression_strategy(
    cfg: AgentMonConfig,
) -> ContextCompressionStrategy:
    load_context_compression_plugins(cfg.context_compression.plugin_modules)
    options = json.loads(cfg.context_compression.strategy_options_json)
    return CONTEXT_COMPRESSION_STRATEGIES.create(
        cfg.context_compression.strategy,
        cfg,
        options,
    )


def load_context_compression_plugins(modules: list[str]) -> None:
    with _PLUGIN_LOCK:
        for raw_name in modules:
            name = str(raw_name).strip()
            if not name or name in _LOADED_PLUGIN_MODULES:
                continue
            importlib.import_module(name)
            _LOADED_PLUGIN_MODULES.add(name)


def register_context_compression_strategy(
    name: str,
    factory: ContextCompressionFactory,
    *,
    description: str = "",
    replace: bool = False,
) -> None:
    CONTEXT_COMPRESSION_STRATEGIES.register(
        name,
        factory,
        description=description,
        replace=replace,
    )


def register_context_compression_trigger(
    name: str,
    factory: ContextCompressionTriggerFactory,
    *,
    description: str = "",
    replace: bool = False,
) -> None:
    CONTEXT_COMPRESSION_TRIGGERS.register(
        name,
        factory,
        description=description,
        replace=replace,
    )


def unregister_context_compression_strategy(name: str) -> None:
    CONTEXT_COMPRESSION_STRATEGIES.unregister(name)


def unregister_context_compression_trigger(name: str) -> None:
    CONTEXT_COMPRESSION_TRIGGERS.unregister(name)


def context_compression_strategy_catalog() -> list[dict[str, str]]:
    return CONTEXT_COMPRESSION_STRATEGIES.catalog()


def context_compression_trigger_catalog() -> list[dict[str, str]]:
    return CONTEXT_COMPRESSION_TRIGGERS.catalog()
