from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tomllib
from typing import Any


REGISTRY_DIR = Path(__file__).resolve().parent


def mechanism_registry() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(list(REGISTRY_DIR.glob("*.toml")) + list(REGISTRY_DIR.glob("*.yaml")) + list(REGISTRY_DIR.glob("*.yml"))):
        data = load_definition_file(path)
        name = str(data.get("name") or path.stem)
        normalized = normalize_definition(name, data)
        rows[name] = normalized
    return rows


def mechanism_definition(name: str) -> dict[str, Any] | None:
    definition = mechanism_registry().get(name)
    return deepcopy(definition) if definition else None


def mechanism_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": definition.get("description", ""),
            "allowed_execution_paths": list(definition.get("allowed_execution_paths", ())),
            "activation_event_types": list(definition.get("activation_event_types", ())),
        }
        for name, definition in sorted(mechanism_registry().items())
    ]


def normalize_definition(name: str, data: dict[str, Any]) -> dict[str, Any]:
    coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
    return {
        "name": name,
        "description": str(data.get("description") or ""),
        "allowed_execution_paths": tuple(str(item) for item in as_list(data.get("allowed_execution_paths"))),
        "allowed_config_differences": tuple(str(item) for item in as_list(data.get("allowed_config_differences"))),
        "required_config": dict(data.get("required_config") or {}),
        "activation_event_types": tuple(str(item) for item in as_list(data.get("activation_event_types"))),
        "negative_reason_paths": tuple(str(item) for item in as_list(data.get("negative_reason_paths"))),
        "negative_reasons": tuple(str(item) for item in as_list(data.get("negative_reasons"))),
        "coverage": {
            "min_count": coverage.get("min_count"),
            "min_ratio": coverage.get("min_ratio"),
        },
        "forbidden_conclusions": tuple(str(item) for item in as_list(data.get("forbidden_conclusions"))),
    }


def load_definition_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".toml":
        value = tomllib.loads(text)
    else:
        value = load_yaml_definition(text)
    if not isinstance(value, dict):
        raise ValueError(f"mechanism definition must be an object: {path}")
    return value


def load_yaml_definition(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return parse_simple_yaml(text)
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("mechanism YAML definition must be an object")
    return value


def parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0:
            if line.endswith(":"):
                current_key = strip_quotes(line[:-1].strip())
                root[current_key] = {}
                continue
            key, value = split_yaml_key_value(line)
            root[strip_quotes(key)] = parse_scalar(value)
            current_key = None
            continue
        if current_key is None:
            continue
        if line.startswith("- "):
            if not isinstance(root.get(current_key), list):
                root[current_key] = []
            root[current_key].append(parse_scalar(line[2:].strip()))
            continue
        key, value = split_yaml_key_value(line)
        if not isinstance(root.get(current_key), dict):
            root[current_key] = {}
        root[current_key][strip_quotes(key)] = parse_scalar(value)
    return root


def split_yaml_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        return line, ""
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def parse_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def strip_quotes(value: str) -> str:
    text = value.strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None or value == "":
        return []
    return [value]
