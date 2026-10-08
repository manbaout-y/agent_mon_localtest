from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from string import Formatter
from typing import Any

def read_tasks(path: Path, *, task_id_column: str) -> list[dict[str, str]]:
    path = path.expanduser()
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        delimiter = "\t" if path.suffix in {".tsv", ".tab"} else ","
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=delimiter))
    tasks: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"task row {index} is not an object")
        task = {str(key): "" if value is None else str(value) for key, value in row.items()}
        task_id = task.get(task_id_column) or task.get("id") or task.get("instance_id")
        if not task_id:
            raise ValueError(f"task row {index} has no {task_id_column!r}, id, or instance_id")
        task["task_id"] = sanitize_task_id(task_id)
        tasks.append(task)
    return tasks


def read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in path.expanduser().read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            env[key] = value
    return env


def batch_context(task: dict[str, str], batch_root: Path) -> dict[str, str]:
    return {
        **task,
        "batch_root": str(batch_root),
        "task_id": task["task_id"],
    }


def render_template(template: str, values: dict[str, Any]) -> str:
    missing = [field for _, field, _, _ in Formatter().parse(template) if field and field.split(".", 1)[0] not in values]
    if missing:
        raise KeyError(f"missing template fields: {', '.join(sorted(set(missing)))}")
    return template.format_map({key: str(value) for key, value in values.items()})


def write_json_private(path: Path, payload: dict[str, Any]) -> None:
    make_private_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def sanitize_task_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "task"


def make_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def string_or_none(value: Any) -> str | None:
    if is_blank_value(value):
        return None
    return str(value)


def is_blank_value(value: Any) -> bool:
    return value is None or value == ""


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
