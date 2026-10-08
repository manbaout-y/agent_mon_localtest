from __future__ import annotations

import json
from pathlib import Path
from typing import Any

def read_jsonl(path: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    invalid_lines = 0
    if not path.exists():
        return {"events": events, "invalid_lines": 0}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if isinstance(item, dict):
            events.append(item)
    return {"events": events, "invalid_lines": invalid_lines}


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def read_optional_json(path: Path) -> dict[str, Any]:
    try:
        value = read_json_file(path)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
