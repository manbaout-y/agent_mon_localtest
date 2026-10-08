from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


SCHEMA_REGISTRY_VERSION = "1.0"
DATA_LEVELS = {
    "experiment",
    "trial",
    "batch",
    "worker",
    "run",
    "step",
    "span",
    "request",
    "request_scheduler_iteration",
    "scheduler_iteration",
    "device_sample",
    "host_sample",
    "process_sample",
    "event",
}


@dataclass(frozen=True)
class MetricDefinition:
    canonical_name: str
    aliases: tuple[str, ...]
    source_event_types: tuple[str, ...]
    data_level: str
    unit: str
    clock_domain: str
    definition: str
    required_fields: tuple[str, ...]
    nullable: bool
    aggregation_rules: tuple[str, ...]
    time_weighted: bool
    summable: bool
    quantile_allowed: bool
    normalization: str
    validation_rules: tuple[str, ...]
    source_code_reference: str
    version_range: str
    deprecation_status: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricDefinition":
        level = str(data["data_level"])
        if level not in DATA_LEVELS:
            raise ValueError(f"unsupported metric data_level: {level}")
        return cls(
            canonical_name=str(data["canonical_name"]),
            aliases=tuple(str(item) for item in data.get("aliases", [])),
            source_event_types=tuple(str(item) for item in data.get("source_event_types", [])),
            data_level=level,
            unit=str(data.get("unit") or "UNKNOWN"),
            clock_domain=str(data.get("clock_domain") or "UNKNOWN"),
            definition=str(data.get("definition") or ""),
            required_fields=tuple(str(item) for item in data.get("required_fields", [])),
            nullable=bool(data.get("nullable", True)),
            aggregation_rules=tuple(str(item) for item in data.get("aggregation_rules", [])),
            time_weighted=bool(data.get("time_weighted", False)),
            summable=bool(data.get("summable", False)),
            quantile_allowed=bool(data.get("quantile_allowed", False)),
            normalization=str(data.get("normalization") or "none"),
            validation_rules=tuple(str(item) for item in data.get("validation_rules", [])),
            source_code_reference=str(data.get("source_code_reference") or "UNKNOWN"),
            version_range=str(data.get("version_range") or "all"),
            deprecation_status=str(data.get("deprecation_status") or "active"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "source_event_types": list(self.source_event_types),
            "data_level": self.data_level,
            "unit": self.unit,
            "clock_domain": self.clock_domain,
            "definition": self.definition,
            "required_fields": list(self.required_fields),
            "nullable": self.nullable,
            "aggregation_rules": list(self.aggregation_rules),
            "time_weighted": self.time_weighted,
            "summable": self.summable,
            "quantile_allowed": self.quantile_allowed,
            "normalization": self.normalization,
            "validation_rules": list(self.validation_rules),
            "source_code_reference": self.source_code_reference,
            "version_range": self.version_range,
            "deprecation_status": self.deprecation_status,
        }


def load_schema_profile(profile: str = "agentmon_default") -> dict[str, Any]:
    filename = profile if profile.endswith(".yaml") else f"{profile}.yaml"
    text = (Path(__file__).resolve().parent / "schemas" / filename).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("schema profile must be a JSON/YAML object")
    return data


def load_metric_definitions(profile: str = "agentmon_default") -> dict[str, MetricDefinition]:
    raw = load_schema_profile(profile)
    metrics = raw.get("metrics", [])
    if not isinstance(metrics, list):
        raise ValueError("schema profile metrics must be a list")
    definitions = [MetricDefinition.from_dict(item) for item in metrics if isinstance(item, dict)]
    return {item.canonical_name: item for item in definitions}


def metric_dictionary_markdown(profile: str = "agentmon_default") -> str:
    definitions = load_metric_definitions(profile)
    lines = [
        "# Metric Dictionary",
        "",
        "| Metric | Level | Unit | Clock | Aggregation | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for metric in sorted(definitions.values(), key=lambda item: item.canonical_name):
        aggregation = ", ".join(metric.aggregation_rules) or "none"
        notes = metric.definition.replace("|", "/")
        lines.append(
            f"| `{metric.canonical_name}` | {metric.data_level} | {metric.unit} | "
            f"{metric.clock_domain} | {aggregation} | {notes} |"
        )
    return "\n".join(lines) + "\n"
