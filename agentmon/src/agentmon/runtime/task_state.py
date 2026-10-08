from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Iterable, Literal

from agentmon.config import AgentMonConfig
from agentmon.contracts import ResourceBudget


TaskPathState = Literal["UNKNOWN", "NORMAL", "LOW_PROGRESS", "STALLED", "RECOVERY"]


@dataclass(frozen=True)
class TaskStatePolicy:
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

    @classmethod
    def from_config(cls, cfg: AgentMonConfig) -> "TaskStatePolicy":
        return cls(
            normal_progress_threshold=cfg.task_state.normal_progress_threshold,
            stalled_progress_threshold=cfg.task_state.stalled_progress_threshold,
            low_progress_patience=cfg.task_state.low_progress_patience,
            repeat_tool_window=cfg.task_state.repeat_tool_window,
            repeated_error_window=cfg.task_state.repeated_error_window,
            high_context_growth_tokens=cfg.task_state.high_context_growth_tokens,
            high_context_growth_ratio=cfg.task_state.high_context_growth_ratio,
            base_progress_score=cfg.task_state.base_progress_score,
            positive_signal_weight=cfg.task_state.positive_signal_weight,
            negative_signal_weight=cfg.task_state.negative_signal_weight,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolResultRecord:
    tool: str | None
    content_hash: str | None
    previous_hash: str | None = None
    novelty_ratio: float | None = None
    structured_summary: dict[str, Any] = field(default_factory=dict)
    blob_uri: str | None = None
    quality: str = "content_hash_only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "content_hash": self.content_hash,
            "previous_hash": self.previous_hash,
            "novelty_ratio": self.novelty_ratio,
            "structured_summary": dict(self.structured_summary),
            "blob_uri": self.blob_uri,
            "quality": self.quality,
        }


@dataclass(frozen=True)
class TaskStateSnapshot:
    identity: dict[str, Any]
    phase: str
    progress: dict[str, Any]
    cost: dict[str, Any]
    budget: dict[str, Any]
    prediction: dict[str, Any]
    context: dict[str, Any]
    engine: dict[str, Any]
    control: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    data_quality: dict[str, Any]
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "result_type": "agentmon.runtime.task_state",
            "identity": dict(self.identity),
            "phase": self.phase,
            "progress": dict(self.progress),
            "cost": dict(self.cost),
            "budget": dict(self.budget),
            "prediction": dict(self.prediction),
            "context": dict(self.context),
            "engine": dict(self.engine),
            "control": dict(self.control),
            "evidence_refs": list(self.evidence_refs),
            "data_quality": dict(self.data_quality),
            "limitations": list(self.limitations),
        }


def build_task_state_from_events(
    events: Iterable[dict[str, Any]],
    *,
    task_id: str | None = None,
    run_id: str | None = None,
    resource_budget: ResourceBudget | dict[str, Any] | None = None,
    policy: TaskStatePolicy | None = None,
    initial_prediction: dict[str, Any] | None = None,
) -> TaskStateSnapshot:
    rows = sorted((row for row in events if isinstance(row, dict)), key=event_sort_key)
    selected_policy = policy or TaskStatePolicy()
    identity = {
        "task_id": task_id or first_event_value(rows, ("task_id", "runtime_task_id")),
        "run_id": run_id or first_event_value(rows, ("run_id", "trace_id")),
        "trajectory_id": first_event_value(rows, ("trajectory_id",)),
        "repository_id": first_event_value(rows, ("repository_id", "repo_id")),
    }
    counters = extract_costs(rows)
    context = extract_context_state(rows, selected_policy)
    prediction = merge_prediction(rows, initial_prediction or {})
    progress = evaluate_progress(rows, counters, context, selected_policy, resource_budget)
    budget = build_budget(counters, resource_budget)
    engine = extract_engine_state(rows)
    control = build_control_advice(progress, context, budget, prediction)
    evidence_refs = collect_evidence_refs(rows)
    limitations = []
    if not rows:
        limitations.append("no_events_available")
    if progress["score"] is None:
        limitations.append("progress_score_unresolved")
    if prediction.get("quality") == "UNKNOWN":
        limitations.append("remaining_work_prediction_unavailable")
    return TaskStateSnapshot(
        identity=identity,
        phase=infer_phase(rows),
        progress=progress,
        cost=counters,
        budget=budget,
        prediction=prediction,
        context=context,
        engine=engine,
        control=control,
        evidence_refs=evidence_refs,
        data_quality={
            "status": "PARTIAL" if limitations else "DERIVED",
            "source": "events.jsonl",
            "event_count": len(rows),
            "policy": selected_policy.to_dict(),
        },
        limitations=limitations,
    )


def fingerprint_tool_result(
    *,
    tool: str | None,
    content: str | bytes | None,
    previous_content_hash: str | None = None,
    blob_uri: str | None = None,
) -> ToolResultRecord:
    if content is None:
        return ToolResultRecord(
            tool=tool,
            content_hash=None,
            previous_hash=previous_content_hash,
            novelty_ratio=None,
            blob_uri=blob_uri,
            quality="content_unavailable",
        )
    raw = content if isinstance(content, bytes) else content.encode("utf-8", errors="replace")
    digest = hashlib.sha256(raw).hexdigest()
    novelty_ratio = 0.0 if previous_content_hash == digest else 1.0
    summary = {
        "byte_count": len(raw),
        "line_count": len(raw.splitlines()),
    }
    return ToolResultRecord(
        tool=tool,
        content_hash=digest,
        previous_hash=previous_content_hash,
        novelty_ratio=novelty_ratio,
        structured_summary=summary,
        blob_uri=blob_uri,
    )


def evaluate_progress(
    events: list[dict[str, Any]],
    cost: dict[str, Any],
    context: dict[str, Any],
    policy: TaskStatePolicy,
    resource_budget: ResourceBudget | dict[str, Any] | None,
) -> dict[str, Any]:
    if not events:
        return {
            "state": "UNKNOWN",
            "score": None,
            "positive_signals": [],
            "negative_signals": [],
            "quality": "unavailable",
        }
    positive = collect_positive_signals(events)
    negative = collect_negative_signals(events, context, policy)
    score = policy.base_progress_score
    score += len(positive) * policy.positive_signal_weight
    score -= len(negative) * policy.negative_signal_weight
    score = min(1.0, max(0.0, score))
    hard_budget_exceeded = budget_exceeded(cost, resource_budget)
    if hard_budget_exceeded:
        state: TaskPathState = "STALLED"
    elif score <= policy.stalled_progress_threshold:
        state = "STALLED"
    elif score < policy.normal_progress_threshold:
        state = "LOW_PROGRESS"
    else:
        state = "NORMAL"
    return {
        "state": state,
        "score": score,
        "positive_signals": positive,
        "negative_signals": negative,
        "hard_budget_exceeded": hard_budget_exceeded,
        "quality": "derived_from_canonical_events",
    }


def extract_costs(events: list[dict[str, Any]]) -> dict[str, Any]:
    step_count = 0
    model_request_count = 0
    model_request_error_count = 0
    tool_call_count = 0
    prompt_tokens = 0
    completion_tokens = 0
    prompt_known = False
    completion_known = False
    tool_output_bytes = 0
    tool_output_known = False
    first_ns: int | None = None
    last_ns: int | None = None
    for event in events:
        event_type = str(event.get("event_type") or "")
        payload = as_dict(event.get("payload"))
        when = event_sort_key(event)
        if when is not None:
            first_ns = when if first_ns is None else min(first_ns, when)
            last_ns = when if last_ns is None else max(last_ns, when)
        if event_type == "agent_step.end":
            step_count += 1
        elif event_type == "model_request.start":
            model_request_count += 1
        elif event_type == "model_request.error":
            model_request_error_count += 1
        elif event_type == "tool_call.end":
            tool_call_count += 1
        if event_type in {"model_request.end", "model_request.error"}:
            usage = as_dict(payload.get("usage"))
            prompt = first_int(payload.get("prompt_tokens"), usage.get("prompt_tokens"), payload.get("context_prompt_tokens"))
            completion = first_int(
                payload.get("completion_tokens"),
                payload.get("generation_tokens"),
                usage.get("completion_tokens"),
            )
            if prompt is not None:
                prompt_known = True
                prompt_tokens += prompt
            if completion is not None:
                completion_known = True
                completion_tokens += completion
        if event_type in {"tool_call.end", "tool_call.error"}:
            output_bytes = first_int(
                payload.get("output_bytes"),
                payload.get("stdout_bytes"),
                payload.get("stderr_bytes"),
                payload.get("tool_output_bytes"),
            )
            if output_bytes is not None:
                tool_output_known = True
                tool_output_bytes += output_bytes
    return {
        "steps": step_count,
        "model_requests": model_request_count,
        "model_request_errors": model_request_error_count,
        "prompt_tokens": prompt_tokens if prompt_known else None,
        "generation_tokens": completion_tokens if completion_known else None,
        "tool_calls": tool_call_count,
        "tool_output_bytes": tool_output_bytes if tool_output_known else None,
        "wall_time_ns": (last_ns - first_ns) if first_ns is not None and last_ns is not None else None,
        "quality": "derived_from_events",
    }


def extract_context_state(events: list[dict[str, Any]], policy: TaskStatePolicy) -> dict[str, Any]:
    latest_tokens: int | None = None
    latest_token_delta: int | None = None
    latest_input_chars: int | None = None
    latest_tool_tokens: int | None = None
    latest_change_type: str | None = None
    latest_prefix_messages: int | None = None
    max_tokens: int | None = None
    for event in events:
        payload = as_dict(event.get("payload"))
        tokens = first_int(payload.get("context_prompt_tokens"), payload.get("context_token_estimate"))
        if tokens is not None:
            latest_tokens = tokens
            max_tokens = tokens if max_tokens is None else max(max_tokens, tokens)
        delta = first_int(payload.get("context_prompt_tokens_delta"), payload.get("context_token_estimate_delta"))
        if delta is not None:
            latest_token_delta = delta
        input_chars = first_int(payload.get("context_input_chars"), payload.get("input_chars"))
        if input_chars is not None:
            latest_input_chars = input_chars
        tool_tokens = first_int(payload.get("tool_result_tokens_estimated"))
        if tool_tokens is not None:
            latest_tool_tokens = tool_tokens
        if payload.get("context_change_type") not in {None, ""}:
            latest_change_type = str(payload["context_change_type"])
        prefix = first_int(payload.get("context_retained_prefix_messages"))
        if prefix is not None:
            latest_prefix_messages = prefix
    high_growth = context_growth_is_high(latest_tokens, latest_token_delta, policy)
    actions: list[str] = []
    if high_growth:
        actions.append("compact_dynamic_context")
    if latest_change_type == "rewrite":
        actions.append("rebuild_stable_prefix_layout")
    if latest_tool_tokens is not None and latest_tokens is not None and latest_tokens > 0:
        if latest_tool_tokens / latest_tokens >= 0.5:
            actions.append("deduplicate_or_externalize_tool_results")
    return {
        "active_tokens": latest_tokens,
        "max_active_tokens": max_tokens,
        "active_input_chars": latest_input_chars,
        "tool_result_tokens_estimated": latest_tool_tokens,
        "latest_token_delta": latest_token_delta,
        "context_change_type": latest_change_type,
        "retained_prefix_messages": latest_prefix_messages,
        "high_growth": high_growth,
        "recommended_actions": actions,
        "state_version": stable_state_version(events),
        "quality": "derived_context_shape" if events else "UNKNOWN",
    }


def merge_prediction(events: list[dict[str, Any]], initial: dict[str, Any]) -> dict[str, Any]:
    prediction = {
        "remaining_model_requests_p50": None,
        "remaining_model_requests_p90": None,
        "remaining_prompt_tokens_p50": None,
        "remaining_prompt_tokens_p90": None,
        "remaining_generation_tokens_p50": None,
        "remaining_generation_tokens_p90": None,
        "remaining_tool_time_p50_s": None,
        "remaining_tool_time_p90_s": None,
        "remaining_task_time_p50_s": None,
        "remaining_task_time_p90_s": None,
        "success_probability": None,
        "abnormal_path_probability": None,
        "quality": "UNKNOWN",
    }
    for key, value in initial.items():
        if key in prediction:
            prediction[key] = value
            prediction["quality"] = "provided_by_run_spec"
    for event in events:
        payload = as_dict(event.get("payload"))
        for key in tuple(prediction):
            if key == "quality":
                continue
            if payload.get(key) is not None:
                prediction[key] = payload.get(key)
                prediction["quality"] = "latest_event_payload"
    return prediction


def build_budget(
    cost: dict[str, Any],
    resource_budget: ResourceBudget | dict[str, Any] | None,
) -> dict[str, Any]:
    raw = resource_budget.to_dict() if isinstance(resource_budget, ResourceBudget) else dict(resource_budget or {})
    remaining = {
        "remaining_steps": None,
        "remaining_model_requests": subtract_optional(raw.get("max_model_requests"), cost.get("model_requests")),
        "remaining_prompt_tokens": subtract_optional(raw.get("max_prompt_tokens"), cost.get("prompt_tokens")),
        "remaining_generation_tokens": subtract_optional(raw.get("max_completion_tokens"), cost.get("generation_tokens")),
        "remaining_tool_calls": subtract_optional(raw.get("max_tool_calls"), cost.get("tool_calls")),
        "remaining_wall_time_s": subtract_optional(raw.get("max_wall_time_s"), ns_to_s(cost.get("wall_time_ns"))),
    }
    return {
        "configured": raw,
        **remaining,
        "quality": "configured_budget" if raw else "UNKNOWN",
    }


def extract_engine_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {
        "running_requests": None,
        "waiting_requests": None,
        "kv_usage_ratio": None,
        "queue_latency_p95_s": None,
        "quality": "UNKNOWN",
    }
    for event in events:
        payload = as_dict(event.get("payload"))
        for key in ("running_requests", "waiting_requests", "kv_usage_ratio", "queue_latency_p95_s"):
            if payload.get(key) is not None:
                latest[key] = payload.get(key)
                latest["quality"] = "latest_event_payload"
    return latest


def build_control_advice(
    progress: dict[str, Any],
    context: dict[str, Any],
    budget: dict[str, Any],
    prediction: dict[str, Any],
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    state = str(progress.get("state") or "UNKNOWN")
    for action in context.get("recommended_actions") or []:
        actions.append(
            {
                "action_type": action,
                "target_type": "context",
                "mode": "advise",
                "reason": "context_lifecycle_signal",
            }
        )
    if state in {"LOW_PROGRESS", "STALLED"}:
        actions.append(
            {
                "action_type": "replan_or_review",
                "target_type": "run",
                "mode": "advise",
                "reason": "low_progress_state",
            }
        )
    if state == "STALLED":
        actions.append(
            {
                "action_type": "defer_or_degrade_future_model_requests",
                "target_type": "model_request_gate",
                "mode": "advise",
                "reason": "stalled_task_state",
            }
        )
    if progress.get("hard_budget_exceeded"):
        actions.append(
            {
                "action_type": "stop_after_policy_check",
                "target_type": "run",
                "mode": "advise",
                "reason": "hard_budget_exceeded",
            }
        )
    if optional_float(prediction.get("abnormal_path_probability")) is not None:
        actions.append(
            {
                "action_type": "apply_abnormal_path_priority_policy",
                "target_type": "scheduler",
                "mode": "advise",
                "reason": "abnormal_path_prediction_available",
            }
        )
    return {
        "state": state,
        "intervention_level": intervention_level(actions, state),
        "recommended_actions": actions,
        "quality": "advisory_only",
    }


def collect_positive_signals(events: list[dict[str, Any]]) -> list[str]:
    signals: list[str] = []
    for event in events:
        payload = as_dict(event.get("payload"))
        for key, signal in (
            ("test_delta", "test_delta_positive"),
            ("passed_tests_delta", "passed_tests_delta_positive"),
            ("resolved_failures_delta", "resolved_failures_delta_positive"),
            ("code_diff_delta", "code_diff_delta_positive"),
            ("diff_delta", "diff_delta_positive"),
            ("subgoal_delta", "subgoal_delta_positive"),
            ("new_evidence_count", "new_evidence_observed"),
        ):
            value = optional_float(payload.get(key))
            if value is not None and value > 0 and signal not in signals:
                signals.append(signal)
    return signals


def collect_negative_signals(
    events: list[dict[str, Any]],
    context: dict[str, Any],
    policy: TaskStatePolicy,
) -> list[str]:
    signals: list[str] = []
    repeated_tool = max_consecutive_tool_signature(events)
    if repeated_tool >= policy.repeat_tool_window:
        signals.append("repeat_tool_window_reached")
    repeated_error = max_consecutive_error_signature(events)
    if repeated_error >= policy.repeated_error_window:
        signals.append("repeated_error_window_reached")
    if context.get("high_growth"):
        signals.append("context_growth_high")
    token_waste = latest_bool_payload(events, "token_waste_observed")
    if token_waste:
        signals.append("token_waste_observed")
    return signals


def max_consecutive_tool_signature(events: list[dict[str, Any]]) -> int:
    best = 0
    current = 0
    previous: str | None = None
    for event in events:
        if not str(event.get("event_type") or "").startswith("tool_call."):
            continue
        signature = tool_signature(as_dict(event.get("payload")))
        if signature is None:
            continue
        current = current + 1 if signature == previous else 1
        previous = signature
        best = max(best, current)
    return best


def max_consecutive_error_signature(events: list[dict[str, Any]]) -> int:
    best = 0
    current = 0
    previous: str | None = None
    for event in events:
        payload = as_dict(event.get("payload"))
        signature = string_or_none(
            payload.get("error_signature")
            or payload.get("exception_fingerprint")
            or payload.get("error_fingerprint")
        )
        if signature is None:
            continue
        current = current + 1 if signature == previous else 1
        previous = signature
        best = max(best, current)
    return best


def tool_signature(payload: dict[str, Any]) -> str | None:
    for key in ("command_signature", "command_hash", "command_digest", "executable", "tool_name", "name"):
        value = string_or_none(payload.get(key))
        if value is not None:
            return value
    return None


def context_growth_is_high(tokens: int | None, delta: int | None, policy: TaskStatePolicy) -> bool:
    if delta is None or delta <= 0:
        return False
    if delta >= policy.high_context_growth_tokens:
        return True
    if tokens is None or tokens <= 0:
        return False
    return delta / tokens >= policy.high_context_growth_ratio


def budget_exceeded(
    cost: dict[str, Any],
    resource_budget: ResourceBudget | dict[str, Any] | None,
) -> bool:
    raw = resource_budget.to_dict() if isinstance(resource_budget, ResourceBudget) else dict(resource_budget or {})
    checks = (
        ("max_model_requests", "model_requests"),
        ("max_prompt_tokens", "prompt_tokens"),
        ("max_completion_tokens", "generation_tokens"),
        ("max_tool_calls", "tool_calls"),
    )
    for budget_key, cost_key in checks:
        budget_value = optional_float(raw.get(budget_key))
        cost_value = optional_float(cost.get(cost_key))
        if budget_value is not None and cost_value is not None and cost_value >= budget_value:
            return True
    wall_budget = optional_float(raw.get("max_wall_time_s"))
    wall_cost = ns_to_s(cost.get("wall_time_ns"))
    return wall_budget is not None and wall_cost is not None and wall_cost >= wall_budget


def infer_phase(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        payload = as_dict(event.get("payload"))
        for key in ("task_phase", "agent_phase", "agent_semantic_phase", "step_semantic_phase"):
            if payload.get(key) not in {None, ""}:
                return str(payload[key])
        event_type = str(event.get("event_type") or "")
        if event_type.startswith("model_request."):
            return "model"
        if event_type.startswith("tool_call."):
            return "tool"
        if event_type.startswith("agent_step."):
            return "agent"
    return "UNKNOWN"


def collect_evidence_refs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for event in events[-32:]:
        event_id = event.get("event_id")
        event_type = event.get("event_type")
        if event_id is None and event_type is None:
            continue
        refs.append({"event_id": event_id, "event_type": event_type})
    return refs


def stable_state_version(events: list[dict[str, Any]]) -> str | None:
    if not events:
        return None
    digest = hashlib.sha256()
    for event in events[-128:]:
        payload = as_dict(event.get("payload"))
        shape = {
            "event_type": event.get("event_type"),
            "span_id": payload.get("span_id"),
            "step_id": payload.get("step_id"),
            "sequence": payload.get("sequence"),
            "context_token_estimate": payload.get("context_token_estimate"),
            "context_prompt_tokens": payload.get("context_prompt_tokens"),
        }
        digest.update(json.dumps(shape, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
    return digest.hexdigest()[:16]


def event_sort_key(event: dict[str, Any]) -> int:
    payload = as_dict(event.get("payload"))
    return first_int(
        event.get("mono_ns"),
        event.get("wall_time_ns"),
        payload.get("end_mono_ns"),
        payload.get("start_mono_ns"),
        payload.get("mono_ns"),
        payload.get("timestamp_ns"),
    ) or 0


def first_event_value(events: list[dict[str, Any]], keys: tuple[str, ...]) -> str | None:
    for event in events:
        payload = as_dict(event.get("payload"))
        for source in (event, payload):
            for key in keys:
                value = string_or_none(source.get(key))
                if value is not None:
                    return value
    return None


def latest_bool_payload(events: list[dict[str, Any]], key: str) -> bool:
    for event in reversed(events):
        payload = as_dict(event.get("payload"))
        if isinstance(payload.get(key), bool):
            return bool(payload[key])
    return False


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool) or value in {None, ""}:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def subtract_optional(limit: Any, used: Any) -> float | int | None:
    limit_value = optional_float(limit)
    used_value = optional_float(used)
    if limit_value is None or used_value is None:
        return None
    remaining = limit_value - used_value
    if isinstance(limit, int) and isinstance(used, int):
        return int(remaining)
    return remaining


def ns_to_s(value: Any) -> float | None:
    raw = optional_float(value)
    return None if raw is None else raw / 1_000_000_000


def string_or_none(value: Any) -> str | None:
    return None if value in {None, ""} else str(value)


def intervention_level(actions: list[dict[str, Any]], state: str) -> str:
    if not actions:
        return "I0_RECORD"
    if state == "STALLED":
        return "I2_REPLAN_OR_DEGRADE_ADVISED"
    if any(action.get("target_type") == "context" for action in actions):
        return "I1_CONTEXT_CLEANUP_ADVISED"
    return "I0_RECORD"
