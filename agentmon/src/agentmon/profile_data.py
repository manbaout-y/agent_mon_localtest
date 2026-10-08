from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_analysis_profile_document(name: str = "default") -> dict[str, Any]:
    filename = name if name.endswith(".yaml") else f"{name}.yaml"
    path = Path(__file__).resolve().parent / "analysis" / "profiles" / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"analysis profile must be an object: {path}")
    return data


def profile_quality_gate(name: str, *, profile: str = "default") -> float | None:
    value = load_analysis_profile_document(profile).get("quality_gates", {}).get(name)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
