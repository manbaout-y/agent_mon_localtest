"""Offline algorithms for studying non-prefix KV/state reuse."""

__version__ = "2.0.1"

from .data.types import StatePair
from .execution.runner import ExperimentRunner
from .methods.registry import MethodRegistry, default_registry

__all__ = ["StatePair", "ExperimentRunner", "MethodRegistry", "default_registry"]
