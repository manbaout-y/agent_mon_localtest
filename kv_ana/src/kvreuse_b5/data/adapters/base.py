from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..types import StatePair


class StatePairAdapter(ABC):
    """Boundary for future Qwen/DeepSeek-specific readers."""

    @abstractmethod
    def iter_pairs(self) -> Iterable[StatePair]:
        raise NotImplementedError

