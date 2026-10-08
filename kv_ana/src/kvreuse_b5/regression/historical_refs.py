from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_historical_references(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != "historical-regression-v1":
        raise ValueError("unsupported historical reference schema")
    return data
