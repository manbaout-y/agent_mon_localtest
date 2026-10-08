from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence
import json


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExperimentConfig:
    methods: Sequence[Mapping[str, Any]]
    experiment: Mapping[str, Any]
    output_dir: str
    raw: Mapping[str, Any]

    @property
    def config_hash(self) -> str:
        return canonical_hash(self.raw)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExperimentConfig":
        if "methods" not in value or not isinstance(value["methods"], list):
            raise ValueError("config.methods must be a list")
        experiment = dict(value.get("experiment", {"split": "predefined"}))
        if experiment.get("split", "predefined") != "predefined":
            raise ValueError("first version supports only predefined train/test splits")
        return cls(value["methods"], experiment, str(value.get("output_dir", "outputs/run")), dict(value))


def load_config(path: str | Path) -> ExperimentConfig:
    text = Path(path).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError("non-JSON YAML requires optional dependency PyYAML; JSON is valid YAML") from exc
        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("config root must be a mapping")
    return ExperimentConfig.from_mapping(value)

