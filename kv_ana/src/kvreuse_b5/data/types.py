from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Dict, Union
import warnings

import numpy as np

if TYPE_CHECKING:
    import torch

    TensorLike = Union["torch.Tensor", np.ndarray]
else:
    TensorLike = Any


def as_numpy(value: Any, *, dtype: np.dtype | None = None) -> np.ndarray:
    """Convert CPU torch/NumPy/array-like input without making torch mandatory."""
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    array = np.asarray(value, dtype=dtype)
    return array


def stable_matmul(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Work around spurious Accelerate warnings in some macOS NumPy 2.0 builds."""
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.matmul(left, right)


@dataclass(frozen=True)
class StatePair:
    pair_id: int | str
    split: str
    model_family: str
    condition: str
    state_kind: str
    layer_id: int
    a: TensorLike
    b: TensorLike
    positions: TensorLike
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.split not in {"train", "test"}:
            raise ValueError(f"pair {self.pair_id}: split must be train/test, got {self.split!r}")
        a = as_numpy(self.a)
        b = as_numpy(self.b)
        positions = as_numpy(self.positions)
        if a.ndim != 2 or b.ndim != 2:
            raise ValueError(f"pair {self.pair_id}/layer {self.layer_id}: A and B must be [T,F]")
        if a.shape != b.shape:
            raise ValueError(
                f"pair {self.pair_id}/layer {self.layer_id}: A/B shape mismatch {a.shape} != {b.shape}"
            )
        if positions.ndim != 1 or len(positions) != a.shape[0]:
            raise ValueError(f"pair {self.pair_id}/layer {self.layer_id}: positions must be [T]")
        if not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError(f"pair {self.pair_id}/layer {self.layer_id}: A/B contains NaN or Inf")
        if not np.isfinite(positions).all():
            raise ValueError(f"pair {self.pair_id}/layer {self.layer_id}: positions contains NaN or Inf")
        object.__setattr__(self, "a", a)
        object.__setattr__(self, "b", b)
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def residual(self) -> np.ndarray:
        return self.b - self.a

    @property
    def total_units(self) -> int:
        return int(self.a.shape[0])

    @property
    def feature_dim(self) -> int:
        return int(self.a.shape[1])

    @property
    def fit_key(self) -> tuple[str, str, str, int]:
        return (self.model_family, self.condition, self.state_kind, self.layer_id)

    @property
    def family_key(self) -> tuple[str, str, str]:
        return (self.model_family, self.condition, self.state_kind)

    @property
    def state_identity(self) -> tuple[str, str, str, int | str, int]:
        """Uniquely identify one model/condition/state/layer tensor pair."""
        return (self.model_family, self.condition, self.state_kind, self.pair_id, self.layer_id)

    @property
    def sample_identity(self) -> tuple[str, str, str, int | str]:
        """Identify the logical cross-layer sample within one state experiment."""
        return (self.model_family, self.condition, self.state_kind, self.pair_id)

    @property
    def dataset_group_identity(self) -> tuple[Any, ...]:
        """Explicit grouping boundary; never inferred across state or condition."""
        namespace = self.metadata.get("dataset_group_id", self.pair_id)
        return (self.model_family, self.condition, self.state_kind, namespace)

    def with_a(self, a: Any) -> "StatePair":
        return replace(self, a=a)
