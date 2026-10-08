from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
import threading
from typing import Any


MAX_TRACKED_STEPS = 4096


@dataclass(frozen=True)
class ContextSnapshot:
    public: dict[str, Any]
    fingerprints: tuple[str, ...]
    compaction_signals: tuple[str, ...]


class ModelWorkloadTracker:
    """Process-local, content-free context and request accounting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previous_context: ContextSnapshot | None = None
        self._previous_prompt_tokens: int | None = None
        self._request_sequence = 0
        self._step_call_counts: dict[str, int] = {}

    def begin(self, messages: Any, *, step_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        current = capture_context(messages)
        step_key = step_id or "unassigned"
        with self._lock:
            previous = self._previous_context
            self._previous_context = current
            self._request_sequence += 1
            request_sequence = self._request_sequence
            model_call_index = self._step_call_counts.get(step_key, 0) + 1
            self._step_call_counts[step_key] = model_call_index
            while len(self._step_call_counts) > MAX_TRACKED_STEPS:
                self._step_call_counts.pop(next(iter(self._step_call_counts)))
        change = compare_context(previous, current)
        attributes = {
            **current.public,
            **change,
            "model_request_sequence": request_sequence,
            "model_call_index_in_step": model_call_index,
            "agent_semantic_phase": infer_model_semantic_phase(messages),
        }
        return attributes, {"request_sequence": request_sequence}

    def finish(
        self,
        usage: Mapping[str, Any],
        *,
        transport_attempt_count: int | None,
        transport_attempt_observed: bool,
    ) -> dict[str, Any]:
        prompt_tokens = integer_or_none(usage.get("prompt_tokens"))
        with self._lock:
            previous_prompt_tokens = self._previous_prompt_tokens
            if prompt_tokens is not None:
                self._previous_prompt_tokens = prompt_tokens
        if transport_attempt_observed:
            attempt_count = transport_attempt_count or 0
            retry_count = max(0, attempt_count - 1)
            retry_quality = "exact_instrumented_litellm_transport"
        else:
            attempt_count = None
            retry_count = None
            retry_quality = "unavailable_outer_query_only"
        return {
            "context_prompt_tokens": prompt_tokens,
            "context_prompt_tokens_delta": difference_or_none(prompt_tokens, previous_prompt_tokens),
            "transport_attempt_count": attempt_count,
            "retry_count": retry_count,
            "retry_observation_quality": retry_quality,
        }


def capture_context(messages: Any) -> ContextSnapshot:
    if not is_message_sequence(messages):
        public = {
            "message_count": None,
            "input_chars": None,
            "roles": {},
            "context_message_count": None,
            "context_input_chars": None,
            "context_token_estimate": None,
            "context_token_estimate_quality": "unavailable",
            "context_role_chars": {},
            "tool_result_message_count": None,
            "tool_result_chars": None,
            "tool_result_tokens_estimated": None,
            "tool_result_token_quality": "unavailable",
            "assistant_turn_count": None,
            "tool_turn_count": None,
            "agent_path_depth": None,
        }
        return ContextSnapshot(public=public, fingerprints=(), compaction_signals=())

    roles: dict[str, int] = {}
    role_chars: dict[str, int] = {}
    fingerprints: list[str] = []
    signals: set[str] = set()
    input_chars = 0
    tool_result_chars = 0
    tool_result_messages = 0
    for message in messages:
        if isinstance(message, Mapping):
            role = str(message.get("role", "unknown"))
            chars = content_char_count(message.get("content"))
            roles[role] = roles.get(role, 0) + 1
            role_chars[role] = role_chars.get(role, 0) + chars
            input_chars += chars
            if role in {"tool", "observation"}:
                tool_result_messages += 1
                tool_result_chars += chars
            signals.update(compaction_signals(message))
            fingerprints.append(message_fingerprint(message))
        else:
            chars = content_char_count(message)
            input_chars += chars
            fingerprints.append(message_fingerprint(message))
    chars_per_token = token_estimate_chars_per_token()
    public = {
        "message_count": len(messages),
        "input_chars": input_chars,
        "roles": roles,
        "context_message_count": len(messages),
        "context_input_chars": input_chars,
        "context_token_estimate": estimate_tokens(input_chars, chars_per_token),
        "context_token_estimate_quality": f"estimated_chars_div_{chars_per_token:g}",
        "context_role_chars": role_chars,
        "tool_result_message_count": tool_result_messages,
        "tool_result_chars": tool_result_chars,
        "tool_result_tokens_estimated": estimate_tokens(tool_result_chars, chars_per_token),
        "tool_result_token_quality": f"estimated_chars_div_{chars_per_token:g}",
        "assistant_turn_count": roles.get("assistant", 0),
        "tool_turn_count": roles.get("tool", 0) + roles.get("observation", 0),
        "agent_path_depth": roles.get("tool", 0) + roles.get("observation", 0),
    }
    return ContextSnapshot(
        public=public,
        fingerprints=tuple(fingerprints),
        compaction_signals=tuple(sorted(signals)),
    )


def compare_context(previous: ContextSnapshot | None, current: ContextSnapshot) -> dict[str, Any]:
    if previous is None:
        return {
            "context_change_type": "initial",
            "context_message_delta": None,
            "context_input_chars_delta": None,
            "context_token_estimate_delta": None,
            "tool_result_chars_delta": None,
            "tool_result_tokens_estimated_delta": None,
            "context_retained_prefix_messages": 0,
            "context_removed_message_count": 0,
            "context_truncation_detected": False,
            "context_rewrite_detected": False,
            "context_summarization_detected": "summarization" in current.compaction_signals,
            "context_compaction_signals": list(current.compaction_signals),
            "context_change_quality": "exact_message_sequence_shape",
        }

    retained = common_prefix_length(previous.fingerprints, current.fingerprints)
    previous_count = integer_or_none(previous.public.get("context_message_count"))
    current_count = integer_or_none(current.public.get("context_message_count"))
    if previous.fingerprints == current.fingerprints:
        change_type = "stable"
    elif retained == len(previous.fingerprints) and len(current.fingerprints) >= len(previous.fingerprints):
        change_type = "append"
    elif current_count is not None and previous_count is not None and current_count < previous_count:
        change_type = "truncation"
    else:
        change_type = "rewrite"
    truncation = change_type == "truncation" or "truncation" in current.compaction_signals
    rewrite = change_type == "rewrite"
    summarization = "summarization" in current.compaction_signals
    return {
        "context_change_type": change_type,
        "context_message_delta": difference_or_none(current_count, previous_count),
        "context_input_chars_delta": difference_or_none(
            integer_or_none(current.public.get("context_input_chars")),
            integer_or_none(previous.public.get("context_input_chars")),
        ),
        "context_token_estimate_delta": difference_or_none(
            integer_or_none(current.public.get("context_token_estimate")),
            integer_or_none(previous.public.get("context_token_estimate")),
        ),
        "tool_result_chars_delta": difference_or_none(
            integer_or_none(current.public.get("tool_result_chars")),
            integer_or_none(previous.public.get("tool_result_chars")),
        ),
        "tool_result_tokens_estimated_delta": difference_or_none(
            integer_or_none(current.public.get("tool_result_tokens_estimated")),
            integer_or_none(previous.public.get("tool_result_tokens_estimated")),
        ),
        "context_retained_prefix_messages": retained,
        "context_removed_message_count": max(0, len(previous.fingerprints) - retained),
        "context_truncation_detected": truncation,
        "context_rewrite_detected": rewrite,
        "context_summarization_detected": summarization,
        "context_compaction_signals": list(current.compaction_signals),
        "context_change_quality": "exact_message_sequence_shape",
    }


def summarize_agent_step(
    before_messages: Any,
    after_messages: Any,
    *,
    consecutive_format_errors_before: int | None,
    consecutive_format_errors_after: int | None,
    status: str,
) -> dict[str, Any]:
    before_count = len(before_messages) if is_message_sequence(before_messages) else None
    after_count = len(after_messages) if is_message_sequence(after_messages) else None
    added = (
        list(after_messages[before_count:])
        if before_count is not None and after_count is not None and after_count >= before_count
        else []
    )
    role_counts: dict[str, int] = {}
    action_count = 0
    for message in added:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role", "unknown"))
        role_counts[role] = role_counts.get(role, 0) + 1
        action_count += message_action_count(message)
    if status == "submitted" or role_counts.get("exit", 0) > 0:
        semantic_phase = "termination"
    elif action_count > 0 or role_counts.get("tool", 0) > 0:
        semantic_phase = "model_then_tool"
    elif role_counts.get("assistant", 0) > 0:
        semantic_phase = "model_only"
    elif status == "error":
        semantic_phase = "error"
    else:
        semantic_phase = "control_flow"
    format_retry = bool(
        consecutive_format_errors_before is not None and consecutive_format_errors_before > 0
    )
    return {
        "messages_before": before_count,
        "messages_after": after_count,
        "messages_added": difference_or_none(after_count, before_count),
        "added_role_counts": role_counts,
        "step_action_count": action_count,
        "step_semantic_phase": semantic_phase,
        "path_expansion_count": max(0, action_count - 1),
        "path_expansion_observed": action_count > 1,
        "path_expansion_quality": "tool_action_fanout",
        "format_retry_observed": format_retry,
        "consecutive_format_errors_before": consecutive_format_errors_before,
        "consecutive_format_errors_after": consecutive_format_errors_after,
    }


def infer_model_semantic_phase(messages: Any) -> str:
    if not is_message_sequence(messages) or not messages:
        return "initial_reasoning"
    roles = [str(message.get("role", "unknown")) for message in messages if isinstance(message, Mapping)]
    if not roles or "assistant" not in roles:
        return "initial_reasoning"
    if roles[-1] in {"tool", "observation"}:
        return "post_tool_reasoning"
    if roles[-1] == "exit":
        return "post_exit_control_flow"
    return "continued_reasoning"


def message_action_count(message: Mapping[str, Any]) -> int:
    extra = message.get("extra")
    actions = extra.get("actions") if isinstance(extra, Mapping) else None
    tool_calls = message.get("tool_calls")
    counts = [sequence_length(actions), sequence_length(tool_calls)]
    clean = [count for count in counts if count is not None]
    return max(clean) if clean else 0


def compaction_signals(message: Mapping[str, Any]) -> set[str]:
    signals: set[str] = set()
    stack: list[Any] = []
    seen: set[int] = set()
    for key in ("extra", "provider_specific_fields", "metadata"):
        value = message.get(key)
        if isinstance(value, Mapping):
            stack.append(value)
    while stack:
        value = stack.pop()
        if not isinstance(value, Mapping):
            continue
        identity = id(value)
        if identity in seen or len(seen) >= 256:
            continue
        seen.add(identity)
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if "truncat" in normalized or "compact" in normalized:
                signals.add("truncation")
            if "summar" in normalized:
                signals.add("summarization")
            if isinstance(nested, Mapping):
                stack.append(nested)
    return signals


def message_fingerprint(message: Any) -> str:
    if isinstance(message, Mapping):
        tool_calls = message.get("tool_calls")
        tool_call_shapes = []
        if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes, bytearray)):
            for call in tool_calls:
                if not isinstance(call, Mapping):
                    tool_call_shapes.append(type(call).__name__)
                    continue
                function = call.get("function")
                tool_call_shapes.append(
                    {
                        "id": call.get("id"),
                        "type": call.get("type"),
                        "name": function.get("name") if isinstance(function, Mapping) else None,
                    }
                )
        function_call = message.get("function_call")
        shape = {
            "role": message.get("role"),
            "content": message.get("content"),
            "tool_call_id": message.get("tool_call_id"),
            "tool_calls": tool_call_shapes,
            "function_name": (
                function_call.get("name") if isinstance(function_call, Mapping) else None
            ),
        }
    else:
        shape = message
    try:
        encoded = json.dumps(shape, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    except Exception:
        encoded = repr(type(message)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def content_char_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return len(str(value))


def token_estimate_chars_per_token() -> float:
    raw = os.environ.get("AGENTMON_TOOL_RESULT_CHARS_PER_TOKEN", "4")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 4.0
    return value if value > 0 else 4.0


def estimate_tokens(chars: int, chars_per_token: float) -> int:
    return int(math.ceil(chars / chars_per_token)) if chars > 0 else 0


def common_prefix_length(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    count = 0
    for left_item, right_item in zip(left, right):
        if left_item != right_item:
            break
        count += 1
    return count


def difference_or_none(current: int | None, previous: int | None) -> int | None:
    if current is None or previous is None:
        return None
    return current - previous


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sequence_length(value: Any) -> int | None:
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return None
    try:
        return len(value)
    except TypeError:
        return None


def is_message_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
