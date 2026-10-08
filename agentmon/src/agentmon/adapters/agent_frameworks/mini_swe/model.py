from __future__ import annotations

from collections.abc import Mapping
from copy import copy
import inspect
import json
import threading
import time
from typing import Any

from agentmon.config import AgentMonConfig, load_config
from agentmon.context_compression import ContextSummaryRequest, ContextSummaryResult

from .context import AgentMonContext
from .context_compression import MiniSweContextCompressor
from .event_sink import AdapterEventSink, get_sink
from .ids import new_request_id
from .workload import ModelWorkloadTracker, capture_context


class InstrumentedModel:
    """Decorator for mini-SWE/LiteLLM model objects."""

    def __init__(
        self,
        inner_model: Any,
        sink: AdapterEventSink | None = None,
        context: AgentMonContext | None = None,
        cfg: AgentMonConfig | None = None,
        context_compressor: MiniSweContextCompressor | None = None,
    ) -> None:
        self.inner_model = inner_model
        self.sink = sink or get_sink()
        self.context = context or self.sink.context
        self._agentmon_workload_tracker = ModelWorkloadTracker()
        self._agentmon_context_compressor = context_compressor or MiniSweContextCompressor(
            cfg or load_adapter_config(self.sink)
        )

    def query(self, messages: Any, **kwargs: Any) -> Any:
        request_id = new_request_id()
        step_id = kwargs.pop("agentmon_step_id", None) or self.sink.context.step_id or self.context.step_id
        context = self.sink.context.child(step_id=step_id)
        compression = safe_prepare_context(
            self._agentmon_context_compressor,
            messages,
            sink=self.sink,
            summary_provider=build_model_summary_provider(
                target=self.inner_model.query,
                base_kwargs=kwargs,
                sink=self.sink,
                run_id=context.run_id,
                step_id=step_id,
                parent_model_call_id=request_id,
                model_name=_get_model_name(self.inner_model, kwargs),
                agent_id=context.agent_id,
            ),
        )
        request_messages = compression.context if compression is not None else messages
        workload_attributes = safe_begin_workload(
            self._agentmon_workload_tracker,
            request_messages,
            step_id=step_id,
            sink=self.sink,
        )
        attributes = dict(workload_attributes)
        attributes.update(compression_telemetry(compression))
        attributes.update(
            {
                "model_call_id": request_id,
                "client_request_id": request_id,
                "agentmon_model_request_id": request_id,
                "request_id": request_id,
                "step_id": step_id,
                "model_name": _get_model_name(self.inner_model, kwargs),
                "headers_injected": inject_request_headers(
                    kwargs,
                    context.run_id,
                    request_id,
                    agent_id=context.agent_id,
                    target=getattr(self.inner_model, "query", None),
                ),
            }
        )
        emit_context_compression_decision(
            self.sink,
            compression,
            request_id=request_id,
            step_id=step_id,
        )
        with self.sink.span(
            span_id=request_id,
            span_type="model_request",
            name="model_request",
            attributes=attributes,
            source="mini_swe",
        ) as span:
            try:
                response = self.inner_model.query(request_messages, **kwargs)
            except BaseException:
                span["end_payload"] = safe_finish_workload(
                    self._agentmon_workload_tracker,
                    {},
                    transport_attempt_count=None,
                    transport_attempt_observed=False,
                    sink=self.sink,
                )
                raise
            usage = extract_usage(response)
            span["end_payload"] = {
                "usage": usage,
                **extract_response_identifiers(response),
                **safe_finish_workload(
                    self._agentmon_workload_tracker,
                    usage,
                    transport_attempt_count=None,
                    transport_attempt_observed=False,
                    sink=self.sink,
                ),
            }
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner_model, name)


class InstrumentedModelMixin:
    """Mixin for mini-SWE model classes loaded directly via --model-class."""

    def _agentmon_sink(self) -> AdapterEventSink:
        sink = getattr(self, "agentmon_sink", None)
        if sink is None:
            sink = get_sink()
            self.agentmon_sink = sink
        return sink

    def _agentmon_tracker(self) -> ModelWorkloadTracker:
        tracker = getattr(self, "agentmon_workload_tracker", None)
        if tracker is None:
            tracker = ModelWorkloadTracker()
            self.agentmon_workload_tracker = tracker
        return tracker

    def _agentmon_transport_context(self) -> threading.local:
        context = getattr(self, "agentmon_transport_context", None)
        if context is None:
            context = threading.local()
            self.agentmon_transport_context = context
        return context

    def _agentmon_compressor(self) -> MiniSweContextCompressor:
        compressor = getattr(self, "agentmon_context_compressor", None)
        if compressor is None:
            sink = self._agentmon_sink()
            compressor = MiniSweContextCompressor(load_adapter_config(sink))
            self.agentmon_context_compressor = compressor
        return compressor

    def query(self, messages: Any, **kwargs: Any) -> Any:
        sink = self._agentmon_sink()
        parent_query = getattr(super(), "query")
        request_id = new_request_id()
        step_id = kwargs.pop("agentmon_step_id", None) or sink.context.step_id
        tracker = self._agentmon_tracker()
        compression = safe_prepare_context(
            self._agentmon_compressor(),
            messages,
            sink=sink,
            summary_provider=build_model_summary_provider(
                target=parent_query,
                base_kwargs=kwargs,
                sink=sink,
                run_id=sink.context.run_id,
                step_id=step_id,
                parent_model_call_id=request_id,
                model_name=_get_model_name(self, kwargs),
                agent_id=sink.context.agent_id,
            ),
        )
        request_messages = compression.context if compression is not None else messages
        workload_attributes = safe_begin_workload(
            tracker,
            request_messages,
            step_id=step_id,
            sink=sink,
        )
        attributes = dict(workload_attributes)
        attributes.update(compression_telemetry(compression))
        attributes.update(
            {
                "model_call_id": request_id,
                "client_request_id": request_id,
                "agentmon_model_request_id": request_id,
                "request_id": request_id,
                "step_id": step_id,
                "model_name": _get_model_name(self, kwargs),
                "headers_injected": inject_request_headers(
                    kwargs,
                    sink.context.run_id,
                    request_id,
                    agent_id=sink.context.agent_id,
                    target=parent_query,
                ),
            }
        )
        emit_context_compression_decision(
            sink,
            compression,
            request_id=request_id,
            step_id=step_id,
        )
        with sink.span(
            span_id=request_id,
            span_type="model_request",
            name="model_request",
            attributes=attributes,
            source="mini_swe",
        ) as span:
            transport_context = self._agentmon_transport_context()
            previous_active = getattr(transport_context, "active", None)
            active = {
                "request_id": request_id,
                "step_id": step_id,
                "attempt_count": 0,
                "attempt_observed": False,
            }
            transport_context.active = active
            try:
                try:
                    response = parent_query(request_messages, **kwargs)
                except BaseException:
                    span["end_payload"] = safe_finish_workload(
                        tracker,
                        {},
                        transport_attempt_count=active["attempt_count"],
                        transport_attempt_observed=active["attempt_observed"],
                        sink=sink,
                    )
                    raise
                usage = extract_usage(response)
                span["end_payload"] = {
                    "usage": usage,
                    **extract_response_identifiers(response),
                    **safe_finish_workload(
                        tracker,
                        usage,
                        transport_attempt_count=active["attempt_count"],
                        transport_attempt_observed=active["attempt_observed"],
                        sink=sink,
                    ),
                }
            finally:
                transport_context.active = previous_active
        return response

    def _query(self, messages: Any, **kwargs: Any) -> Any:
        parent_query = getattr(super(), "_query")
        transport_context = self._agentmon_transport_context()
        active = getattr(transport_context, "active", None)
        if not isinstance(active, dict):
            return parent_query(messages, **kwargs)
        active["attempt_count"] = int(active.get("attempt_count") or 0) + 1
        active["attempt_observed"] = True
        attempt_index = active["attempt_count"]
        start_mono_ns = time.monotonic_ns()
        safe_emit(
            self._agentmon_sink(),
            "model_request.attempt.start",
            {
                "span_id": active.get("request_id"),
                "parent_span_id": active.get("request_id"),
                "step_id": active.get("step_id"),
                "attempt_index": attempt_index,
                "retry_index": attempt_index - 1,
                "start_mono_ns": start_mono_ns,
            },
        )
        try:
            result = parent_query(messages, **kwargs)
        except BaseException as exc:
            end_mono_ns = time.monotonic_ns()
            safe_emit(
                self._agentmon_sink(),
                "model_request.attempt.error",
                {
                    "span_id": active.get("request_id"),
                    "parent_span_id": active.get("request_id"),
                    "step_id": active.get("step_id"),
                    "attempt_index": attempt_index,
                    "retry_index": attempt_index - 1,
                    "end_mono_ns": end_mono_ns,
                    "duration_ns": end_mono_ns - start_mono_ns,
                    "error_type": type(exc).__name__,
                    "status": "error",
                },
            )
            raise
        end_mono_ns = time.monotonic_ns()
        safe_emit(
            self._agentmon_sink(),
            "model_request.attempt.end",
            {
                "span_id": active.get("request_id"),
                "parent_span_id": active.get("request_id"),
                "step_id": active.get("step_id"),
                "attempt_index": attempt_index,
                "retry_index": attempt_index - 1,
                "end_mono_ns": end_mono_ns,
                "duration_ns": end_mono_ns - start_mono_ns,
                "status": "success",
            },
        )
        return result


def inject_request_headers(
    kwargs: dict[str, Any],
    run_id: str,
    request_id: str,
    *,
    agent_id: str | None = None,
    target: Any = None,
) -> bool:
    supports_headers = accepts_keyword(target, "extra_headers") if target is not None else True
    if target is not None and not supports_headers:
        return False
    headers = kwargs.get("extra_headers")
    if headers is None and target is not None and not accepts_explicit_keyword(target, "extra_headers"):
        return False
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        return False
    headers.setdefault("X-AgentMon-Run-Id", run_id)
    if agent_id not in {None, ""}:
        headers.setdefault("X-AgentMon-Agent-Id", str(agent_id))
    headers.setdefault("X-AgentMon-Request-Id", request_id)
    headers.setdefault("X-AgentMon-Model-Call-Id", request_id)
    headers.setdefault("X-AgentMon-Client-Request-Id", request_id)
    kwargs["extra_headers"] = headers
    return True


def accepts_explicit_keyword(target: Any, name: str) -> bool:
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.name == name and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            return True
    return False


def accepts_keyword(target: Any, name: str) -> bool:
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return True
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == name and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            return True
    return False


def summarize_messages(messages: Any) -> dict[str, Any]:
    return capture_context(messages).public


def text_summary_target(target: Any) -> Any:
    """Keep mini-SWE retries/cost accounting, but accept text for this request."""
    model = getattr(target, "__self__", None)
    if not inspect.ismethod(target) or not callable(getattr(model, "_parse_actions", None)):
        return target

    def query_summary(messages: Any, **kwargs: Any) -> Any:
        # A request-local view leaves the live agent's action parser unchanged.
        summary_model = copy(model)
        summary_model._parse_actions = lambda response: []
        kwargs["tool_choice"] = "none"
        return target.__func__(summary_model, messages, **kwargs)

    return query_summary


def build_model_summary_provider(
    *,
    target: Any,
    base_kwargs: Mapping[str, Any],
    sink: AdapterEventSink,
    run_id: str,
    step_id: str | None,
    parent_model_call_id: str,
    model_name: str | None,
    agent_id: str | None = None,
) -> Any:
    target = text_summary_target(target)

    def summarize(request: ContextSummaryRequest) -> ContextSummaryResult:
        summary_request_id = new_request_id()
        summary_messages = summary_request_messages(request)
        summary_kwargs = dict(base_kwargs)
        headers = summary_kwargs.get("extra_headers")
        if isinstance(headers, Mapping):
            summary_kwargs["extra_headers"] = dict(headers)
        if accepts_keyword(target, "max_tokens"):
            summary_kwargs["max_tokens"] = request.target_tokens
        if request.model and accepts_keyword(target, "model"):
            summary_kwargs["model"] = request.model
        headers_injected = inject_request_headers(
            summary_kwargs,
            run_id,
            summary_request_id,
            agent_id=agent_id,
            target=target,
        )
        attributes = {
            **capture_context(summary_messages).public,
            "model_call_id": summary_request_id,
            "client_request_id": summary_request_id,
            "agentmon_model_request_id": summary_request_id,
            "request_id": summary_request_id,
            "request_purpose": "context_summary",
            "parent_model_call_id": parent_model_call_id,
            "step_id": step_id,
            "model_name": request.model or model_name,
            "summary_target_tokens": request.target_tokens,
            "summary_source_item_count": request.metadata.get(
                "removed_item_count"
            ),
            "summary_source_group_count": request.metadata.get(
                "removed_group_count"
            ),
            "headers_injected": headers_injected,
        }
        started_mono_ns = time.monotonic_ns()
        usage: dict[str, Any] = {}
        with sink.span(
            span_id=summary_request_id,
            span_type="model_request",
            name="context_summary_model_request",
            attributes=attributes,
            source="mini_swe",
        ) as span:
            response = target(summary_messages, **summary_kwargs)
            usage = extract_usage(response)
            summary_text = extract_response_text(response)
            span["end_payload"] = {
                "usage": usage,
                "request_purpose": "context_summary",
                "parent_model_call_id": parent_model_call_id,
                "summary_output_present": bool(summary_text.strip()),
                **extract_response_identifiers(response),
            }
            if not summary_text.strip():
                raise ValueError("context_summary_response_text_unavailable")
        ended_mono_ns = time.monotonic_ns()
        input_tokens = integer_usage(usage, "prompt_tokens", "input_tokens")
        output_tokens = integer_usage(
            usage,
            "completion_tokens",
            "output_tokens",
        )
        total_tokens = integer_usage(usage, "total_tokens")
        cached_tokens = integer_usage(usage, "cached_tokens")
        uncached_tokens = (
            max(input_tokens - cached_tokens, 0)
            if input_tokens is not None and cached_tokens is not None
            else None
        )
        return ContextSummaryResult(
            summary=summary_text,
            request_id=summary_request_id,
            model=request.model or model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            uncached_prompt_tokens=uncached_tokens,
            started_mono_ns=started_mono_ns,
            ended_mono_ns=ended_mono_ns,
            duration_s=(ended_mono_ns - started_mono_ns) / 1_000_000_000,
            token_quality=(
                "exact_api_usage"
                if input_tokens is not None and output_tokens is not None
                else "PARTIAL_API_USAGE"
            ),
            limitations=(
                ()
                if input_tokens is not None and output_tokens is not None
                else ("summary_usage_tokens_incomplete",)
            ),
        )

    return summarize


def summary_request_messages(request: ContextSummaryRequest) -> list[dict[str, str]]:
    try:
        serialized = json.dumps(
            request.context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        serialized = str(request.context)
    return [
        {"role": "system", "content": request.instruction},
        {
            "role": "user",
            "content": "Earlier context records to summarize:\n" + serialized,
        },
    ]


def extract_response_text(response: Any) -> str:
    choices = _get_nested(response, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        message = _get_nested(choices[0], "message")
        content = _get_nested(message, "content")
        if content not in {None, ""}:
            return str(content)
        text = _get_nested(choices[0], "text")
        if text not in {None, ""}:
            return str(text)
    for key in ("output_text", "content", "text"):
        value = _get_nested(response, key)
        if value not in {None, ""}:
            return str(value)
    return ""


def integer_usage(usage: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def extract_usage(response: Any) -> dict[str, Any]:
    usage = _get_nested(response, "usage")
    if usage is None:
        extra = _get_nested(response, "extra")
        if isinstance(extra, Mapping):
            usage = extra.get("usage")
            if usage is None:
                nested_response = extra.get("response")
                usage = _get_nested(nested_response, "usage")
    if usage is None:
        return {}
    if isinstance(usage, Mapping):
        result = {
            str(key): value
            for key, value in usage.items()
            if key in USAGE_KEYS or str(key).endswith("_tokens")
        }
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, Mapping):
            cached = prompt_details.get("cached_tokens")
            if cached is not None:
                result.setdefault("cached_tokens", cached)
        return result
    result: dict[str, Any] = {}
    for key in USAGE_KEYS:
        value = getattr(usage, key, None)
        if value is not None:
            result[key] = value
    return result


def extract_response_identifiers(response: Any) -> dict[str, str]:
    response_id = _get_nested(response, "id")
    if response_id in {None, ""}:
        extra = _get_nested(response, "extra")
        if isinstance(extra, Mapping):
            nested = extra.get("response")
            if isinstance(nested, Mapping):
                response_id = nested.get("id")
            elif nested is not None:
                response_id = getattr(nested, "id", None)
    if response_id in {None, ""}:
        return {}
    value = str(response_id)
    return {"response_id": value, "api_request_id": value, "server_request_id": value}


USAGE_KEYS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "cost",
}


def _get_model_name(inner_model: Any, kwargs: Mapping[str, Any]) -> str | None:
    for key in ("model_name", "model", "name"):
        value = getattr(inner_model, key, None)
        if value:
            return str(value)
    for key in ("model_name", "model"):
        value = kwargs.get(key)
        if value:
            return str(value)
    return None


def _get_nested(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def safe_emit(sink: AdapterEventSink, event_type: str, payload: dict[str, Any]) -> None:
    try:
        sink.emit(event_type, payload=payload)
    except Exception:
        pass


def load_adapter_config(sink: AdapterEventSink) -> AgentMonConfig:
    try:
        return load_config()
    except Exception as exc:
        safe_emit(
            sink,
            "mini_swe.adapter.error",
            {
                "where": "context_compression.config",
                "error_type": type(exc).__name__,
            },
        )
        return AgentMonConfig()


def safe_prepare_context(
    compressor: MiniSweContextCompressor,
    messages: Any,
    *,
    sink: AdapterEventSink,
    summary_provider: Any = None,
) -> Any:
    try:
        return compressor.prepare(
            messages,
            summary_provider=summary_provider,
        )
    except Exception as exc:
        safe_emit(
            sink,
            "mini_swe.adapter.error",
            {
                "where": "context_compression.prepare",
                "error_type": type(exc).__name__,
            },
        )
        return None


def compression_telemetry(decision: Any) -> dict[str, Any]:
    if decision is None:
        return {
            "context_compression_status": "ERROR_BYPASS",
            "context_compression_reason": "adapter_preparation_failed",
            "context_compression_applied": False,
            "context_compression_target_achieved": None,
        }
    try:
        return decision.telemetry()
    except Exception:
        return {
            "context_compression_status": "ERROR_BYPASS",
            "context_compression_reason": "telemetry_serialization_failed",
            "context_compression_applied": False,
            "context_compression_target_achieved": None,
        }


def emit_context_compression_decision(
    sink: AdapterEventSink,
    decision: Any,
    *,
    request_id: str,
    step_id: str | None,
) -> None:
    if decision is None or not decision.configured_enabled:
        return
    safe_emit(
        sink,
        "context_compression.decision",
        {
            "model_call_id": request_id,
            "client_request_id": request_id,
            "agentmon_model_request_id": request_id,
            "request_id": request_id,
            "step_id": step_id,
            **decision.telemetry(),
        },
    )


def safe_begin_workload(
    tracker: ModelWorkloadTracker,
    messages: Any,
    *,
    step_id: str | None,
    sink: AdapterEventSink,
) -> dict[str, Any]:
    try:
        attributes, _ = tracker.begin(messages, step_id=step_id)
        attributes["workload_observation_quality"] = "complete"
        return attributes
    except Exception as exc:
        safe_emit(
            sink,
            "mini_swe.adapter.error",
            {"where": "model_workload.begin", "error_type": type(exc).__name__},
        )
        return {
            "message_count": None,
            "input_chars": None,
            "workload_observation_quality": "unavailable_instrumentation_error",
        }


def safe_finish_workload(
    tracker: ModelWorkloadTracker,
    usage: Mapping[str, Any],
    *,
    transport_attempt_count: int | None,
    transport_attempt_observed: bool,
    sink: AdapterEventSink,
) -> dict[str, Any]:
    try:
        return tracker.finish(
            usage,
            transport_attempt_count=transport_attempt_count,
            transport_attempt_observed=transport_attempt_observed,
        )
    except Exception as exc:
        safe_emit(
            sink,
            "mini_swe.adapter.error",
            {"where": "model_workload.finish", "error_type": type(exc).__name__},
        )
        return {
            "context_prompt_tokens": None,
            "context_prompt_tokens_delta": None,
            "transport_attempt_count": transport_attempt_count if transport_attempt_observed else None,
            "retry_count": (
                max(0, int(transport_attempt_count or 0) - 1)
                if transport_attempt_observed
                else None
            ),
            "retry_observation_quality": (
                "partial_instrumentation_error" if transport_attempt_observed else "unavailable_outer_query_only"
            ),
        }


try:
    from minisweagent.models.litellm_model import LitellmModel
except Exception:

    class InstrumentedLitellmModel(InstrumentedModelMixin):  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("minisweagent.models.litellm_model.LitellmModel is not importable")

else:

    class InstrumentedLitellmModel(InstrumentedModelMixin, LitellmModel):  # type: ignore[no-redef]
        pass
