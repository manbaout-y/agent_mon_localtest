from __future__ import annotations

from pathlib import Path
from typing import Any


def load_torch_payload(path: Path) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency
        raise RuntimeError("real-state adapters require the optional torch dependency") from exc

    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        payload = torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:  # older torch releases
        payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a dict payload")
    return payload


def require_keys(mapping: dict[str, Any], keys: tuple[str, ...], *, where: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValueError(f"{where}: missing required keys {missing}")
