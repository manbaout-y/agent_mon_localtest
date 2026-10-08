from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from agentmon.analysis.io import read_json, write_json


DEFAULT_ANALYSIS_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class IntegrityThresholds:
    max_invalid_json_lines: int = 0
    max_duplicate_event_ids: int = 0
    max_sequence_regressions: int = 0


@dataclass(frozen=True)
class ValidationThresholds:
    little_law_pass_relative_error: float = 0.10
    little_law_partial_relative_error: float = 0.20
    pipeline_pass_relative_error: float = 0.02
    pipeline_partial_relative_error: float = 0.05
    time_conservation_pass_relative_error: float = 0.05
    lifecycle_pass_relative_error: float = 0.05
    min_p2_match_rate: float | None = None
    min_p2_lifecycle_rate: float | None = None
    min_resource_sample_count: int = 2


@dataclass(frozen=True)
class AnalysisWindows:
    steady_state_warmup_fraction: float = 0.10
    steady_state_cooldown_fraction: float = 0.10
    effective_capacity_threshold_ratio: float = 0.80
    effective_capacity_min_duration_s: float = 30.0
    tail_fractions: tuple[float, ...] = (0.20, 0.10, 0.05)


@dataclass(frozen=True)
class StatisticsConfig:
    bootstrap_iterations: int = 1000
    confidence_level: float = 0.95


@dataclass(frozen=True)
class OutputConfig:
    format: str = "parquet"
    figures: bool = True
    max_rows_per_model_pack_table: int = 5000
    fast_smoke: bool = False
    include_evidence: bool = True
    include_model_pack: bool = True
    include_handoff: bool = True
    include_module_packs: bool = True
    include_claim_registry: bool = True
    omit_normalized_tables: tuple[str, ...] = ("event_inventory",)


@dataclass(frozen=True)
class RuntimeConfig:
    jobs: int = 1
    temp_dir: str | None = None
    keep_temp: bool = False
    memory_limit_bytes: int | None = None
    min_free_bytes: int | None = None
    status_file: str | None = None
    progress_interval_seconds: float = 30.0
    timeout_seconds: float | None = None
    full_jsonl_integrity: bool = False
    full_file_checksums: bool = False


@dataclass(frozen=True)
class BaselineConfig:
    expected_runs: int | None = None
    expected_request_attempts: int | None = None
    expected_p2_requests: int | None = None
    allow_baseline_mismatch: bool = False


@dataclass(frozen=True)
class ProfileDetectionConfig:
    auto_min_confidence: float = 0.50
    mixed_schema_policy: str = "per_run"
    definitions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineMetricConfig:
    plateau_fraction: float = 0.10
    minimum_state_duration_s: float = 30.0
    waiting_positive_threshold: float = 0.0
    sensitivity_time_slices_s: tuple[float, ...] = (1.0, 5.0, 10.0, 30.0)
    sensitivity_waiting_thresholds: tuple[float, ...] = (0.0, 1.0, 2.0)
    sensitivity_plateau_fractions: tuple[float, ...] = (0.05, 0.10, 0.20)
    sensitivity_minimum_durations_s: tuple[float, ...] = (10.0, 30.0, 60.0)
    token_budget_near_ratio: float = 0.90
    long_context_signal_fields: tuple[str, ...] = ("active_prompt_tokens", "max_prompt_tokens", "kv_usage_ratio")
    long_context_signal_threshold: float | None = None


@dataclass(frozen=True)
class AnalysisConfig:
    schema_version: str = DEFAULT_ANALYSIS_SCHEMA_VERSION
    profile: str = "auto"
    integrity: IntegrityThresholds = field(default_factory=IntegrityThresholds)
    validation: ValidationThresholds = field(default_factory=ValidationThresholds)
    windows: AnalysisWindows = field(default_factory=AnalysisWindows)
    statistics: StatisticsConfig = field(default_factory=StatisticsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    baseline: BaselineConfig = field(default_factory=BaselineConfig)
    profile_detection: ProfileDetectionConfig = field(default_factory=ProfileDetectionConfig)
    engine_metrics: EngineMetricConfig = field(default_factory=EngineMetricConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_analysis_config() -> AnalysisConfig:
    return AnalysisConfig()


def load_analysis_config(path: str | Path | None = None, *, overrides: dict[str, Any] | None = None) -> AnalysisConfig:
    data = default_analysis_config().to_dict()
    if path:
        loaded = read_config_file(Path(path).expanduser())
        deep_update(data, loaded)
    if overrides:
        deep_update(data, overrides)
    return config_from_dict(data)


def read_config_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = parse_minimal_yaml(text)
    if not isinstance(value, dict):
        raise ValueError("analysis config must be a mapping")
    return value


def write_resolved_config(path: str | Path, cfg: AnalysisConfig) -> None:
    write_json(path, cfg.to_dict())


def config_from_dict(data: dict[str, Any]) -> AnalysisConfig:
    return AnalysisConfig(
        schema_version=str(data.get("schema_version") or DEFAULT_ANALYSIS_SCHEMA_VERSION),
        profile=str(data.get("profile") or "auto"),
        integrity=IntegrityThresholds(**filtered(data.get("integrity"), IntegrityThresholds)),
        validation=ValidationThresholds(**filtered(data.get("validation"), ValidationThresholds)),
        windows=AnalysisWindows(**filtered(data.get("windows"), AnalysisWindows)),
        statistics=StatisticsConfig(**filtered(data.get("statistics"), StatisticsConfig)),
        output=OutputConfig(**filtered(data.get("output"), OutputConfig)),
        runtime=RuntimeConfig(**filtered(data.get("runtime"), RuntimeConfig)),
        baseline=BaselineConfig(**filtered(data.get("baseline"), BaselineConfig)),
        profile_detection=ProfileDetectionConfig(**filtered(data.get("profile_detection"), ProfileDetectionConfig)),
        engine_metrics=EngineMetricConfig(**filtered(data.get("engine_metrics"), EngineMetricConfig)),
    )


def filtered(value: Any, cls: type) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    allowed = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    return {key: raw[key] for key in raw if key in allowed}


def deep_update(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value


def parse_minimal_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, result)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if not raw_value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = parse_yaml_scalar(raw_value)
    return result


def parse_yaml_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value.strip('"').strip("'")
