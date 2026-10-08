from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import ast
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse


class ConfigError(ValueError):
    pass


@dataclass
class GeneralConfig:
    output_root: str = "~/.local/share/agentmon/runs"
    log_level: str = "INFO"
    raw_retention: bool = True
    redact_sensitive: bool = True


@dataclass
class AgentConfig:
    type: str = "mini-swe"
    agent_id: str = ""
    root: str = ""
    trajectory_glob: str = ""
    python: str = ""
    env_unset: list[str] = field(default_factory=lambda: ["PYTHONPATH", "PYTHONHOME"])
    env_set: list[str] = field(
        default_factory=lambda: [
            "PYTHONNOUSERSITE=1",
            "NO_PROXY=127.0.0.1,localhost,::1",
            "no_proxy=127.0.0.1,localhost,::1",
        ]
    )


@dataclass
class VllmConfig:
    metrics_url: str = "http://127.0.0.1:8000/metrics"
    api_base: str = "http://127.0.0.1:8000/v1"
    models_url: str = "http://127.0.0.1:8000/v1/models"
    model_name: str = ""
    process_match: list[str] = field(
        default_factory=lambda: ["vllm", "VLLM::EngineCore", "VLLM::Worker"]
    )
    interval_seconds: float = 1.0
    request_timeout_seconds: float = 2.0
    prefix_caching: str = "unknown"


@dataclass
class ModelConfig:
    path: str = ""
    dtype: str = ""
    max_model_len: int | None = None
    tensor_parallel_size: int | None = None
    max_num_seqs: int | None = None


@dataclass
class ProcConfig:
    interval_seconds: float = 0.5
    read_pss: bool = True
    read_io: bool = True
    read_cmdline: bool = True
    max_pids: int = 4096


@dataclass
class AscendConfig:
    interval_seconds: float = 1.0
    command_timeout_seconds: float = 3.0
    npu_smi: str = "auto"
    device_ids: str = "auto"
    retain_raw: bool = True


@dataclass
class StorageConfig:
    # JSONL is authoritative. Keep the rebuildable SQLite cache opt-in while
    # large concurrent experiments are optimized for write and postprocess I/O.
    sqlite: bool = False
    jsonl: bool = True
    sqlite_wal: bool = True
    flush_interval_seconds: float = 1.0
    queue_max_items: int = 20000


@dataclass
class FinalizationConfig:
    collector_join_timeout_seconds: float = 10.0
    writer_close_timeout_seconds: float = 10.0
    trajectory_wait_timeout_seconds: float = 2.0
    trajectory_poll_interval_seconds: float = 0.05
    trajectory_stable_checks: int = 2
    p2_events_path: str = ""
    p2_import_quality: str = "service_exclusive"
    p2_window_margin_seconds: float = 1.0
    p2_include_window_extras: bool = False
    p2_global_event_policy: str = "auto"


@dataclass
class PrivacyConfig:
    store_prompts: bool = False
    store_tool_output: bool = False
    store_full_command: bool = False
    command_hash_salt_file: str = ""


@dataclass
class RuntimeOptimizationConfig:
    # This master switch preserves telemetry while bypassing scheduling and
    # active control decisions, which makes controlled A/B runs possible.
    enabled: bool = True


@dataclass
class ContextCompressionConfig:
    # A zero window uses model.max_model_len; unresolved windows never trigger.
    # The strategy is still independently switchable for controlled A/B runs.
    enabled: bool = False
    trigger_strategy: str = "token_ratio"
    strategy: str = "model_summary"
    plugin_modules: list[str] = field(default_factory=list)
    trigger_ratio: float = 0.80
    target_ratio: float = 0.20
    window_tokens: int = 0
    estimator_chars_per_token: float = 4.0
    trigger_options_json: str = "{}"
    strategy_options_json: str = '{"min_recent_groups":1}'


@dataclass
class SchedulerConfig:
    enabled: bool = True
    strategy: str = "fixed_concurrency"
    max_running_runs: int = 64
    defer_when_full: bool = True
    workload_prompt_tokens_p90_budget: int | None = None
    workload_generation_tokens_p90_budget: int | None = None
    long_context_token_threshold: int | None = None
    long_context_limit: int | None = None
    abnormal_path_risk_threshold: float | None = None


@dataclass
class OptimizerConfig:
    enabled: bool = True
    mode: str = "advise"
    strategy: str = "static_threshold"
    high_utilization_ratio: float = 0.8
    model_queue_latency_high_s: float = 2.0
    model_kv_high_ratio: float = 0.90
    model_waiting_requests_high: int = 1


@dataclass
class TaskStateConfig:
    enabled: bool = True
    normal_progress_threshold: float = 0.55
    stalled_progress_threshold: float = 0.25
    low_progress_patience: int = 3
    repeat_tool_window: int = 3
    repeated_error_window: int = 3
    high_context_growth_tokens: int = 2048
    high_context_growth_ratio: float = 0.25
    base_progress_score: float = 0.50
    positive_signal_weight: float = 0.15
    negative_signal_weight: float = 0.15


@dataclass
class ControlConfig:
    enabled: bool = True
    mode: str = "advise"


@dataclass
class ModelGateConfig:
    enabled: bool = True
    service_id: str = "runtime.default"
    max_inflight_requests: int = 64
    moderate_pressure_target: int = 32
    high_pressure_target: int = 16
    min_inflight_requests: int = 1


@dataclass
class ToolPoolConfig:
    enabled: bool = True
    default_max_concurrent: int = 8
    cpu_heavy_max_concurrent: int = 4
    io_heavy_max_concurrent: int = 2
    high_priority_max_concurrent: int = 2
    nice_increment: int = 0
    memory_limit_bytes: int | None = None
    cpu_affinity_cpus: list[str] = field(default_factory=list)
    cpu_heavy_executables: list[str] = field(
        default_factory=lambda: ["pytest", "python", "gcc", "g++", "make", "ninja"]
    )
    io_heavy_executables: list[str] = field(
        default_factory=lambda: ["tar", "unzip", "zip", "rsync", "cp"]
    )


@dataclass
class AgentMonConfig:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    vllm: VllmConfig = field(default_factory=VllmConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    proc: ProcConfig = field(default_factory=ProcConfig)
    ascend: AscendConfig = field(default_factory=AscendConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    finalization: FinalizationConfig = field(default_factory=FinalizationConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    runtime_optimization: RuntimeOptimizationConfig = field(
        default_factory=RuntimeOptimizationConfig
    )
    context_compression: ContextCompressionConfig = field(
        default_factory=ContextCompressionConfig
    )
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    task_state: TaskStateConfig = field(default_factory=TaskStateConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    model_gate: ModelGateConfig = field(default_factory=ModelGateConfig)
    tool_pool: ToolPoolConfig = field(default_factory=ToolPoolConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SECTION_TYPES = {
    "general": GeneralConfig,
    "agent": AgentConfig,
    "vllm": VllmConfig,
    "model": ModelConfig,
    "proc": ProcConfig,
    "ascend": AscendConfig,
    "storage": StorageConfig,
    "finalization": FinalizationConfig,
    "privacy": PrivacyConfig,
    "runtime_optimization": RuntimeOptimizationConfig,
    "context_compression": ContextCompressionConfig,
    "scheduler": SchedulerConfig,
    "optimizer": OptimizerConfig,
    "task_state": TaskStateConfig,
    "control": ControlConfig,
    "model_gate": ModelGateConfig,
    "tool_pool": ToolPoolConfig,
}


def load_config(
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    allow_unknown_config: bool = False,
) -> AgentMonConfig:
    env_map = os.environ if env is None else env
    data = AgentMonConfig().to_dict()

    selected_config = _select_config_path(config_path, env_map)
    if selected_config is not None:
        file_data = _load_toml_file(selected_config)
        _merge_nested(data, file_data, allow_unknown_config=allow_unknown_config)

    _merge_nested(data, _env_overrides(env_map, data), allow_unknown_config=False)

    if cli_overrides:
        _merge_nested(
            data,
            _expand_dotted_overrides(cli_overrides),
            allow_unknown_config=False,
        )

    data = _expand_env_vars(data, env_map)
    cfg = _from_dict(data)
    validate_config(cfg)
    return cfg


def validate_config(cfg: AgentMonConfig) -> None:
    for name, value in (
        ("vllm.interval_seconds", cfg.vllm.interval_seconds),
        ("vllm.request_timeout_seconds", cfg.vllm.request_timeout_seconds),
        ("proc.interval_seconds", cfg.proc.interval_seconds),
        ("ascend.interval_seconds", cfg.ascend.interval_seconds),
        ("ascend.command_timeout_seconds", cfg.ascend.command_timeout_seconds),
        ("storage.flush_interval_seconds", cfg.storage.flush_interval_seconds),
        ("finalization.collector_join_timeout_seconds", cfg.finalization.collector_join_timeout_seconds),
        ("finalization.writer_close_timeout_seconds", cfg.finalization.writer_close_timeout_seconds),
        ("finalization.trajectory_wait_timeout_seconds", cfg.finalization.trajectory_wait_timeout_seconds),
        ("finalization.trajectory_poll_interval_seconds", cfg.finalization.trajectory_poll_interval_seconds),
        ("finalization.p2_window_margin_seconds", cfg.finalization.p2_window_margin_seconds),
    ):
        if value <= 0:
            raise ConfigError(f"{name} must be > 0")

    if cfg.storage.queue_max_items <= 0:
        raise ConfigError("storage.queue_max_items must be > 0")
    if cfg.finalization.trajectory_stable_checks < 1:
        raise ConfigError("finalization.trajectory_stable_checks must be >= 1")
    if cfg.finalization.p2_import_quality not in {"direct", "service_exclusive", "service_stream", "heuristic"}:
        raise ConfigError("finalization.p2_import_quality must be a supported P2 quality label")
    if cfg.finalization.p2_global_event_policy not in {"auto", "exclude", "copy"}:
        raise ConfigError("finalization.p2_global_event_policy must be auto, exclude, or copy")

    if not cfg.context_compression.trigger_strategy.strip():
        raise ConfigError("context_compression.trigger_strategy must not be empty")
    if not cfg.context_compression.strategy.strip():
        raise ConfigError("context_compression.strategy must not be empty")
    if not 0.0 < cfg.context_compression.trigger_ratio <= 1.0:
        raise ConfigError("context_compression.trigger_ratio must be > 0 and <= 1")
    if not 0.0 < cfg.context_compression.target_ratio < cfg.context_compression.trigger_ratio:
        raise ConfigError(
            "context_compression.target_ratio must be > 0 and less than trigger_ratio"
        )
    if cfg.context_compression.window_tokens < 0:
        raise ConfigError("context_compression.window_tokens must be >= 0")
    if cfg.context_compression.estimator_chars_per_token <= 0:
        raise ConfigError("context_compression.estimator_chars_per_token must be > 0")
    if any(not str(module).strip() for module in cfg.context_compression.plugin_modules):
        raise ConfigError("context_compression.plugin_modules entries must not be empty")
    for name, raw_options in (
        ("trigger_options_json", cfg.context_compression.trigger_options_json),
        ("strategy_options_json", cfg.context_compression.strategy_options_json),
    ):
        try:
            options = json.loads(raw_options)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"context_compression.{name} must be valid JSON"
            ) from exc
        if not isinstance(options, dict):
            raise ConfigError(
                f"context_compression.{name} must decode to an object"
            )

    if cfg.scheduler.max_running_runs < 0:
        raise ConfigError("scheduler.max_running_runs must be >= 0")
    if not cfg.scheduler.strategy.strip():
        raise ConfigError("scheduler.strategy must not be empty")
    for name, value in (
        ("scheduler.workload_prompt_tokens_p90_budget", cfg.scheduler.workload_prompt_tokens_p90_budget),
        (
            "scheduler.workload_generation_tokens_p90_budget",
            cfg.scheduler.workload_generation_tokens_p90_budget,
        ),
        ("scheduler.long_context_token_threshold", cfg.scheduler.long_context_token_threshold),
        ("scheduler.long_context_limit", cfg.scheduler.long_context_limit),
    ):
        if value is not None and int(value) < 0:
            raise ConfigError(f"{name} must be >= 0")
    if cfg.scheduler.abnormal_path_risk_threshold is not None:
        if not 0.0 <= float(cfg.scheduler.abnormal_path_risk_threshold) <= 1.0:
            raise ConfigError("scheduler.abnormal_path_risk_threshold must be >= 0 and <= 1")
    if cfg.optimizer.mode not in {"advise", "enforce"}:
        raise ConfigError("optimizer.mode must be advise or enforce")
    if not cfg.optimizer.strategy.strip():
        raise ConfigError("optimizer.strategy must not be empty")
    if not 0.0 < cfg.optimizer.high_utilization_ratio <= 1.0:
        raise ConfigError("optimizer.high_utilization_ratio must be > 0 and <= 1")
    if cfg.optimizer.model_queue_latency_high_s < 0:
        raise ConfigError("optimizer.model_queue_latency_high_s must be >= 0")
    if not 0.0 <= cfg.optimizer.model_kv_high_ratio <= 1.0:
        raise ConfigError("optimizer.model_kv_high_ratio must be >= 0 and <= 1")
    if cfg.optimizer.model_waiting_requests_high < 0:
        raise ConfigError("optimizer.model_waiting_requests_high must be >= 0")
    if not 0.0 <= cfg.task_state.stalled_progress_threshold <= cfg.task_state.normal_progress_threshold <= 1.0:
        raise ConfigError(
            "task_state thresholds must satisfy 0 <= stalled_progress_threshold <= normal_progress_threshold <= 1"
        )
    if cfg.task_state.low_progress_patience < 1:
        raise ConfigError("task_state.low_progress_patience must be >= 1")
    if cfg.task_state.repeat_tool_window < 1:
        raise ConfigError("task_state.repeat_tool_window must be >= 1")
    if cfg.task_state.repeated_error_window < 1:
        raise ConfigError("task_state.repeated_error_window must be >= 1")
    if cfg.task_state.high_context_growth_tokens < 0:
        raise ConfigError("task_state.high_context_growth_tokens must be >= 0")
    if cfg.task_state.high_context_growth_ratio < 0:
        raise ConfigError("task_state.high_context_growth_ratio must be >= 0")
    if not 0.0 <= cfg.task_state.base_progress_score <= 1.0:
        raise ConfigError("task_state.base_progress_score must be >= 0 and <= 1")
    if cfg.task_state.positive_signal_weight < 0 or cfg.task_state.negative_signal_weight < 0:
        raise ConfigError("task_state signal weights must be >= 0")
    if cfg.control.mode not in {"advise", "enforce"}:
        raise ConfigError("control.mode must be advise or enforce")
    for name, value in (
        ("model_gate.max_inflight_requests", cfg.model_gate.max_inflight_requests),
        ("model_gate.moderate_pressure_target", cfg.model_gate.moderate_pressure_target),
        ("model_gate.high_pressure_target", cfg.model_gate.high_pressure_target),
        ("model_gate.min_inflight_requests", cfg.model_gate.min_inflight_requests),
        ("tool_pool.default_max_concurrent", cfg.tool_pool.default_max_concurrent),
        ("tool_pool.cpu_heavy_max_concurrent", cfg.tool_pool.cpu_heavy_max_concurrent),
        ("tool_pool.io_heavy_max_concurrent", cfg.tool_pool.io_heavy_max_concurrent),
        ("tool_pool.high_priority_max_concurrent", cfg.tool_pool.high_priority_max_concurrent),
        ("tool_pool.nice_increment", cfg.tool_pool.nice_increment),
    ):
        if value < 0:
            raise ConfigError(f"{name} must be >= 0")
    if cfg.tool_pool.nice_increment > 19:
        raise ConfigError("tool_pool.nice_increment must be <= 19")
    if cfg.tool_pool.memory_limit_bytes is not None:
        try:
            cfg.tool_pool.memory_limit_bytes = int(cfg.tool_pool.memory_limit_bytes)
        except (TypeError, ValueError) as exc:
            raise ConfigError("tool_pool.memory_limit_bytes must be an integer") from exc
        if cfg.tool_pool.memory_limit_bytes < 0:
            raise ConfigError("tool_pool.memory_limit_bytes must be >= 0")
    if cfg.model_gate.min_inflight_requests > cfg.model_gate.max_inflight_requests:
        raise ConfigError("model_gate.min_inflight_requests must be <= max_inflight_requests")

    if cfg.vllm.prefix_caching not in {"enabled", "disabled", "unknown"}:
        raise ConfigError("vllm.prefix_caching must be enabled, disabled, or unknown")

    for name, url in (
        ("vllm.metrics_url", cfg.vllm.metrics_url),
        ("vllm.api_base", cfg.vllm.api_base),
        ("vllm.models_url", cfg.vllm.models_url),
    ):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ConfigError(f"{name} must be an HTTP URL")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError(f"{name} must point to localhost in P0")

    for name in cfg.agent.env_unset:
        if not name or "=" in name:
            raise ConfigError("agent.env_unset entries must be environment variable names")
    for entry in cfg.agent.env_set:
        if "=" not in entry or entry.startswith("="):
            raise ConfigError("agent.env_set entries must be KEY=VALUE strings")


def _select_config_path(
    config_path: str | Path | None, env: Mapping[str, str]
) -> Path | None:
    if config_path:
        path = Path(config_path).expanduser()
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        return path

    env_path = env.get("AGENTMON_CONFIG")
    if env_path:
        path = Path(env_path).expanduser()
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        return path

    for candidate in (
        Path.cwd() / "agentmon.toml",
        Path.home() / ".config" / "agentmon" / "config.toml",
    ):
        if candidate.exists():
            return candidate
    return None


def _load_toml_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:
        return _parse_basic_toml(text)

    try:
        return tomllib.loads(text)
    except Exception as exc:  # pragma: no cover - exact tomllib error type is versioned.
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc


def _parse_basic_toml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line_number = index + 1
        raw_line = lines[index]
        index += 1
        line = _strip_toml_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise ConfigError(f"empty section at line {line_number}")
            current = result.setdefault(section, {})
            continue
        if current is None or "=" not in line:
            raise ConfigError(f"invalid TOML line {line_number}: {raw_line!r}")
        key, raw_value = line.split("=", 1)
        raw_value = raw_value.strip()
        while _toml_bracket_delta(raw_value) > 0 and index < len(lines):
            raw_value += "\n" + _strip_toml_comment(lines[index]).strip()
            index += 1
        current[key.strip()] = _parse_basic_toml_value(raw_value.strip())
    return result


def _strip_toml_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None:
            return line[:index]
    return line


def _parse_basic_toml_value(raw: str) -> Any:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        return ast.literal_eval(raw)
    if raw in {"true", "false"}:
        return raw == "true"
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_basic_toml_value(item.strip()) for item in _split_array(inner) if item.strip()]
    try:
        if any(marker in raw for marker in (".", "e", "E")):
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _split_array(inner: str) -> list[str]:
    items: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(inner):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "," and quote is None:
            items.append(inner[start:index])
            start = index + 1
    items.append(inner[start:])
    return items


def _toml_bracket_delta(raw: str) -> int:
    delta = 0
    quote: str | None = None
    escaped = False
    for char in raw:
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if quote is not None:
            continue
        if char == "[":
            delta += 1
        elif char == "]":
            delta -= 1
    return delta


def _merge_nested(
    base: dict[str, Any],
    updates: Mapping[str, Any],
    *,
    allow_unknown_config: bool,
) -> None:
    for section, section_updates in updates.items():
        if section not in SECTION_TYPES:
            if allow_unknown_config:
                continue
            raise ConfigError(f"unknown config section: {section}")
        if not isinstance(section_updates, Mapping):
            raise ConfigError(f"config section {section} must be a table")
        known_keys = {field.name for field in fields(SECTION_TYPES[section])}
        for key, value in section_updates.items():
            if key not in known_keys:
                if allow_unknown_config:
                    continue
                raise ConfigError(f"unknown config key: {section}.{key}")
            current = base[section][key]
            base[section][key] = _coerce_value(value, current)


def _env_overrides(
    env: Mapping[str, str], current_data: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    for section, section_data in current_data.items():
        for key, current in section_data.items():
            env_name = f"AGENTMON_{section}_{key}".upper()
            if env_name in env:
                overrides.setdefault(section, {})[key] = _coerce_value(env[env_name], current)
    return overrides


def _expand_dotted_overrides(overrides: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    nested: dict[str, dict[str, Any]] = {}
    for dotted_key, value in overrides.items():
        if "." not in dotted_key:
            raise ConfigError(f"CLI override must be section.key: {dotted_key}")
        section, key = dotted_key.split(".", 1)
        nested.setdefault(section, {})[key] = value
    return nested


def _coerce_value(value: Any, current: Any) -> Any:
    if current is None:
        return value
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        raise ConfigError(f"expected boolean value, got {value!r}")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, list):
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        raise ConfigError(f"expected list value, got {value!r}")
    if isinstance(current, str):
        return str(value)
    return value


_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env_vars(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda match: env.get(match.group(1), match.group(0)), value)
    if isinstance(value, list):
        return [_expand_env_vars(item, env) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env_vars(item, env) for key, item in value.items()}
    return value


def _from_dict(data: Mapping[str, Mapping[str, Any]]) -> AgentMonConfig:
    return AgentMonConfig(
        general=GeneralConfig(**dict(data["general"])),
        agent=AgentConfig(**dict(data["agent"])),
        vllm=VllmConfig(**dict(data["vllm"])),
        model=ModelConfig(**dict(data["model"])),
        proc=ProcConfig(**dict(data["proc"])),
        ascend=AscendConfig(**dict(data["ascend"])),
        storage=StorageConfig(**dict(data["storage"])),
        finalization=FinalizationConfig(**dict(data["finalization"])),
        privacy=PrivacyConfig(**dict(data["privacy"])),
        runtime_optimization=RuntimeOptimizationConfig(
            **dict(data["runtime_optimization"])
        ),
        context_compression=ContextCompressionConfig(
            **dict(data["context_compression"])
        ),
        scheduler=SchedulerConfig(**dict(data["scheduler"])),
        optimizer=OptimizerConfig(**dict(data["optimizer"])),
        task_state=TaskStateConfig(**dict(data["task_state"])),
        control=ControlConfig(**dict(data["control"])),
        model_gate=ModelGateConfig(**dict(data["model_gate"])),
        tool_pool=ToolPoolConfig(**dict(data["tool_pool"])),
    )
