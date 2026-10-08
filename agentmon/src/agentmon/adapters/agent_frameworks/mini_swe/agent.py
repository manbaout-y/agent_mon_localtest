from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from .event_sink import AdapterEventSink, get_sink, mini_swe_submitted_control_flow_payload
from .ids import new_step_id
from .workload import capture_context, message_action_count, summarize_agent_step


class InstrumentedAgentMixin:
    """Mixin that instruments mini-SWE Agent methods while preserving super() behavior."""

    agentmon_sink: AdapterEventSink
    agentmon_current_step_id: str | None

    def _agentmon_init(self) -> None:
        if not hasattr(self, "agentmon_sink"):
            self.agentmon_sink = get_sink()
            self.agentmon_current_step_id = None

    def run(self, *args: Any, **kwargs: Any) -> Any:
        self._agentmon_init()
        run_id = self.agentmon_sink.context.run_id
        with self.agentmon_sink.span(
            span_id=self.agentmon_sink.context.root_span_id or f"run:{run_id}",
            span_type="run",
            name="run",
            attributes={"run_id": run_id, "n_calls": _safe_getattr(self, "n_calls")},
            source="mini_swe",
        ) as span:
            result = super().run(*args, **kwargs)  # type: ignore[misc]
            span["end_payload"] = {
                "run_id": run_id,
                "n_calls": _safe_getattr(self, "n_calls"),
                "exit_status": _safe_getattr(self, "exit_status"),
            }
        return result

    def step(self, *args: Any, **kwargs: Any) -> Any:
        self._agentmon_init()
        step_id = new_step_id()
        before_messages = _copy_messages(_safe_getattr(self, "messages"))
        before_context = _safe_context_public(before_messages, self.agentmon_sink)
        format_errors_before = _integer_or_none(_safe_getattr(self, "n_consecutive_format_errors"))
        previous_step_id = self.agentmon_current_step_id
        self.agentmon_current_step_id = step_id
        self.agentmon_sink.context = self.agentmon_sink.context.child(step_id=step_id)
        try:
            with self.agentmon_sink.span(
                span_id=step_id,
                span_type="agent_step",
                name="agent_step",
                attributes={
                    "step_id": step_id,
                    "n_calls": _safe_getattr(self, "n_calls"),
                    "messages_before": before_context.get("context_message_count"),
                    "agent_path_depth_before": before_context.get("agent_path_depth"),
                    "consecutive_format_errors_before": format_errors_before,
                },
                source="mini_swe",
            ) as span:
                try:
                    result = super().step(*args, **kwargs)  # type: ignore[misc]
                except BaseException as exc:
                    control_flow_payload = mini_swe_submitted_control_flow_payload(exc)
                    status = "submitted" if control_flow_payload is not None else "error"
                    workload = _safe_step_workload(
                        before_messages,
                        _copy_messages(_safe_getattr(self, "messages")),
                        consecutive_format_errors_before=format_errors_before,
                        consecutive_format_errors_after=_integer_or_none(
                            _safe_getattr(self, "n_consecutive_format_errors")
                        ),
                        status=status,
                        sink=self.agentmon_sink,
                    )
                    if control_flow_payload is not None:
                        span["end_payload"] = {
                            "step_id": step_id,
                            "n_calls": _safe_getattr(self, "n_calls"),
                            **workload,
                            **control_flow_payload,
                        }
                    else:
                        span["end_payload"] = {
                            "step_id": step_id,
                            "n_calls": _safe_getattr(self, "n_calls"),
                            **workload,
                        }
                    raise
                span["end_payload"] = {
                    "step_id": step_id,
                    "n_calls": _safe_getattr(self, "n_calls"),
                    **_safe_step_workload(
                        before_messages,
                        _copy_messages(_safe_getattr(self, "messages")),
                        consecutive_format_errors_before=format_errors_before,
                        consecutive_format_errors_after=_integer_or_none(
                            _safe_getattr(self, "n_consecutive_format_errors")
                        ),
                        status="success",
                        sink=self.agentmon_sink,
                    ),
                }
        finally:
            self.agentmon_current_step_id = previous_step_id
            self.agentmon_sink.context = self.agentmon_sink.context.child(step_id=previous_step_id)
        return result

    def query(self, *args: Any, **kwargs: Any) -> Any:
        self._agentmon_init()
        # DefaultAgent.query() in mini-SWE 2.4.3 accepts no instrumentation
        # kwargs. Step linkage is carried through the shared sink context.
        return super().query(*args, **kwargs)  # type: ignore[misc]

    def execute_actions(self, *args: Any, **kwargs: Any) -> Any:
        self._agentmon_init()
        message = args[0] if args else kwargs.get("message")
        try:
            action_count = message_action_count(message) if isinstance(message, Mapping) else None
            action_types = _action_types(message)
        except Exception as exc:
            action_count = None
            action_types = []
            _safe_emit(
                self.agentmon_sink,
                "mini_swe.adapter.error",
                {"where": "agent.execute_actions.observe", "error_type": type(exc).__name__},
            )
        start_mono_ns = time.monotonic_ns()
        _safe_emit(
            self.agentmon_sink,
            "agent.action_batch.start",
            {
                "step_id": self.agentmon_current_step_id,
                "action_count": action_count,
                "action_types": action_types,
                "start_mono_ns": start_mono_ns,
            },
        )
        try:
            result = super().execute_actions(*args, **kwargs)  # type: ignore[misc]
        except BaseException as exc:
            end_mono_ns = time.monotonic_ns()
            _safe_emit(
                self.agentmon_sink,
                "agent.action_batch.error",
                {
                    "step_id": self.agentmon_current_step_id,
                    "action_count": action_count,
                    "action_types": action_types,
                    "end_mono_ns": end_mono_ns,
                    "duration_ns": end_mono_ns - start_mono_ns,
                    "error_type": type(exc).__name__,
                    "status": "error",
                },
            )
            raise
        end_mono_ns = time.monotonic_ns()
        _safe_emit(
            self.agentmon_sink,
            "agent.action_batch.end",
            {
                "step_id": self.agentmon_current_step_id,
                "action_count": action_count,
                "action_types": action_types,
                "observation_message_count": len(result) if isinstance(result, Sequence) else None,
                "end_mono_ns": end_mono_ns,
                "duration_ns": end_mono_ns - start_mono_ns,
                "status": "success",
            },
        )
        return result

    def serialize(self, *args: Any, **kwargs: Any) -> Any:
        self._agentmon_init()
        data = super().serialize(*args, **kwargs)  # type: ignore[misc]
        if isinstance(data, dict):
            info = data.setdefault("info", {})
            if isinstance(info, dict):
                info["agentmon"] = {
                    "schema_version": "1.0",
                    "run_id": self.agentmon_sink.context.run_id,
                    "events_path": str(self.agentmon_sink.context.events_path)
                    if self.agentmon_sink.context.events_path
                    else None,
                }
        return data


T = TypeVar("T", bound=type)


def make_instrumented_agent_class(base_class: T) -> T:
    name = f"Instrumented{base_class.__name__}"
    return type(name, (InstrumentedAgentMixin, base_class), {"__module__": __name__})  # type: ignore[return-value]


def _safe_getattr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _copy_messages(messages: Any) -> Any:
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes, bytearray)):
        return list(messages)
    return messages


def _integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _action_types(message: Any) -> list[str]:
    if not isinstance(message, Mapping):
        return []
    extra = message.get("extra")
    actions = extra.get("actions") if isinstance(extra, Mapping) else None
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, bytearray)):
        return []
    types = []
    for action in actions:
        if isinstance(action, Mapping):
            value = action.get("type") or action.get("name")
            function = action.get("function")
            if value in {None, ""} and isinstance(function, Mapping):
                value = function.get("name")
            types.append(str(value or "unknown"))
        else:
            types.append(type(action).__name__)
    return types


def _safe_emit(sink: AdapterEventSink, event_type: str, payload: dict[str, Any]) -> None:
    try:
        sink.emit(event_type, payload=payload)
    except Exception:
        pass


def _safe_context_public(messages: Any, sink: AdapterEventSink) -> dict[str, Any]:
    try:
        return capture_context(messages).public
    except Exception as exc:
        _safe_emit(
            sink,
            "mini_swe.adapter.error",
            {"where": "agent.context.observe", "error_type": type(exc).__name__},
        )
        return {"context_message_count": None, "agent_path_depth": None}


def _safe_step_workload(
    before_messages: Any,
    after_messages: Any,
    *,
    consecutive_format_errors_before: int | None,
    consecutive_format_errors_after: int | None,
    status: str,
    sink: AdapterEventSink,
) -> dict[str, Any]:
    try:
        return summarize_agent_step(
            before_messages,
            after_messages,
            consecutive_format_errors_before=consecutive_format_errors_before,
            consecutive_format_errors_after=consecutive_format_errors_after,
            status=status,
        )
    except Exception as exc:
        _safe_emit(
            sink,
            "mini_swe.adapter.error",
            {"where": "agent.step_workload.observe", "error_type": type(exc).__name__},
        )
        return {"step_semantic_phase": "unavailable", "workload_observation_quality": "partial"}


try:
    from minisweagent.agents.default import DefaultAgent
except Exception:

    class InstrumentedDefaultAgent(InstrumentedAgentMixin):  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("minisweagent.agents.default.DefaultAgent is not importable")

else:

    class InstrumentedDefaultAgent(InstrumentedAgentMixin, DefaultAgent):  # type: ignore[no-redef]
        pass


try:
    from minisweagent.agents.interactive import InteractiveAgent
except Exception:

    class InstrumentedInteractiveAgent(InstrumentedAgentMixin):  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("minisweagent.agents.interactive.InteractiveAgent is not importable")

else:

    class InstrumentedInteractiveAgent(InstrumentedAgentMixin, InteractiveAgent):  # type: ignore[no-redef]
        pass
