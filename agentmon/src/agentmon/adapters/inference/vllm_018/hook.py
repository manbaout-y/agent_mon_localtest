from __future__ import annotations

import asyncio
import functools
import importlib
from importlib import metadata as importlib_metadata
import inspect
import os
import threading
import time
from typing import Any, Callable

from .events import VllmP2EventSink, quality_from_correlation
from .hook_config import (
    DEEP_ENV,
    ENABLE_ENV,
    HOT_PATH_ENV,
    INSTALLED_ATTR,
    KV_BLOCKS_ENV,
    MAX_REQUEST_ID_ALIASES,
    MAX_REQUEST_STATE_ENTRIES,
    MAX_SCHEDULER_ITERATIONS,
    ORIGINAL_ATTR,
    PREFIX_CACHE_EXPECTATION_ENV,
    deep_batch_request_limit,
    environment_flag,
    normalized_prefix_cache_expectation,
    prefix_cache_cli_arguments_from_process,
    prefix_cache_expectation_status,
    truthy,
)
from .moe import (
    MOE_CHUNK_TOKENS_ENV,
    MOE_MODE_ENV,
    moe_chunk_tokens_from_env,
    moe_mode_from_env,
    routed_expert_events,
)
from .version import resolve_vllm_version
from agentmon.reporting.ascend import visible_devices_from_env

_INSTALLED = False
_STATE: "VllmP2HookState | None" = None
_PATCHED: list[tuple[type, str, Callable[..., Any]]] = []


class VllmP2HookState:
    def __init__(self, sink: VllmP2EventSink | None = None) -> None:
        self.sink = sink or VllmP2EventSink.from_env()
        self.seen_received: set[str] = set()
        self.seen_queued: set[str] = set()
        self.seen_first_scheduled: set[str] = set()
        self.seen_first_token: set[str] = set()
        self.seen_first_output: set[str] = set()
        self.seen_engine_finished: set[str] = set()
        self.seen_finished: set[str] = set()
        self.seen_prefill_completed: set[str] = set()
        self.deep_enabled = environment_flag(DEEP_ENV, default=True)
        self.moe_mode = moe_mode_from_env()
        self.moe_chunk_tokens = moe_chunk_tokens_from_env()
        self.moe_return_routed_experts_enabled: bool | None = None
        self.moe_expert_count: int | None = None
        self.prefix_cache_expectation = normalized_prefix_cache_expectation(
            os.environ.get(PREFIX_CACHE_EXPECTATION_ENV)
        )
        self.scheduler_stats_depth = 0
        self._request_id_aliases: dict[str, str] = {}
        self._request_contexts: dict[str, dict[str, str]] = {}
        self._request_id_alias_lock = threading.Lock()
        self._seen_lock = threading.Lock()
        self._scheduler_iteration_lock = threading.Lock()
        self._scheduler_iteration_sequence = 0
        self._scheduler_iterations: dict[int, dict[str, Any]] = {}
        self._last_token_ns: dict[str, int] = {}
        self._generation_token_counts: dict[str, int] = {}
        self._api_generation_token_counts: dict[str, int] = {}
        self._api_output_chunk_counts: dict[str, int] = {}
        self._engine_core_observation_local = threading.local()
        self.engine_core_step_present = False
        self.engine_core_step_hooked = False
        self.engine_core_step_with_batch_queue_present = False
        self.engine_core_step_with_batch_queue_hooked = False
        self.async_scheduling_enabled: bool | None = None
        self.async_scheduling_config_source: str | None = None
        self.prefix_cache_enabled: bool | None = None
        self.prefix_cache_config_source: str | None = None
        self.prefix_cache_validation_failed = False
        self.prefix_cache_cli_arguments = prefix_cache_cli_arguments_from_process()

    def mark_seen(self, collection_name: str, request_id: str) -> bool:
        collection = getattr(self, collection_name)
        with self._seen_lock:
            if request_id in collection:
                return False
            collection.add(request_id)
            while len(collection) > MAX_REQUEST_STATE_ENTRIES:
                collection.pop()
        return True

    def emit_error(self, exc: BaseException, where: str) -> None:
        try:
            self.sink.adapter_error(exc, where=where)
        except Exception:
            pass

    def emit_capability(self, status: str, details: dict[str, Any] | None = None) -> None:
        try:
            self.sink.adapter_capability(status=status, details=details)
        except Exception:
            pass

    def next_scheduler_iteration_id(self) -> int:
        with self._scheduler_iteration_lock:
            self._scheduler_iteration_sequence += 1
            return self._scheduler_iteration_sequence

    def remember_scheduler_iteration(self, scheduler_output: Any, record: dict[str, Any]) -> None:
        with self._scheduler_iteration_lock:
            self._scheduler_iterations[id(scheduler_output)] = record
            while len(self._scheduler_iterations) > MAX_SCHEDULER_ITERATIONS:
                self._scheduler_iterations.pop(next(iter(self._scheduler_iterations)))

    def pop_scheduler_iteration(self, scheduler_output: Any) -> dict[str, Any] | None:
        with self._scheduler_iteration_lock:
            return self._scheduler_iterations.pop(id(scheduler_output), None)

    def record_request_id_alias(self, alias_request_id: Any, engine_request_id: Any) -> None:
        alias = none_if_empty(alias_request_id)
        engine_id = none_if_empty(engine_request_id)
        if alias is None or engine_id is None or alias == engine_id:
            return
        with self._request_id_alias_lock:
            self._request_id_aliases[alias] = engine_id
            self._copy_request_context_unlocked(alias, engine_id, api_request_id=alias)
            while len(self._request_id_aliases) > MAX_REQUEST_ID_ALIASES:
                self._request_id_aliases.pop(next(iter(self._request_id_aliases)))
            while len(self._request_contexts) > MAX_REQUEST_ID_ALIASES:
                self._request_contexts.pop(next(iter(self._request_contexts)))

    def resolve_engine_request_id(self, *candidates: Any) -> tuple[str | None, str | None]:
        normalized = [item for candidate in candidates for item in as_request_ids(candidate)]
        if not normalized:
            return None, None
        with self._request_id_alias_lock:
            for candidate in normalized:
                engine_id = self._request_id_aliases.get(candidate)
                if engine_id is not None:
                    return engine_id, candidate if candidate != engine_id else None
        engine_id = normalized[0]
        for candidate in normalized[1:]:
            if candidate != engine_id:
                return engine_id, candidate
        return engine_id, None

    def clear_request_ids(self, *request_ids: Any) -> None:
        ids = {item for request_id in request_ids for item in as_request_ids(request_id)}
        if not ids:
            return
        with self._request_id_alias_lock:
            for alias, engine_id in list(self._request_id_aliases.items()):
                if alias in ids or engine_id in ids:
                    ids.update((alias, engine_id))
                    self._request_id_aliases.pop(alias, None)
            for request_id in ids:
                self._request_contexts.pop(request_id, None)
        with self._seen_lock:
            for request_id in ids:
                self._last_token_ns.pop(request_id, None)
                self._generation_token_counts.pop(request_id, None)
                self._api_generation_token_counts.pop(request_id, None)
                self._api_output_chunk_counts.pop(request_id, None)

    def record_last_token(self, request_id: str, observed_ns: int) -> None:
        with self._seen_lock:
            self._last_token_ns[request_id] = observed_ns
            while len(self._last_token_ns) > MAX_REQUEST_STATE_ENTRIES:
                self._last_token_ns.pop(next(iter(self._last_token_ns)))

    def pop_last_token(self, request_id: str) -> int | None:
        with self._seen_lock:
            return self._last_token_ns.pop(request_id, None)

    def add_generation_tokens(self, request_id: str, count: int) -> int:
        with self._seen_lock:
            total = self._generation_token_counts.get(request_id, 0) + count
            self._generation_token_counts[request_id] = total
            while len(self._generation_token_counts) > MAX_REQUEST_STATE_ENTRIES:
                self._generation_token_counts.pop(next(iter(self._generation_token_counts)))
            return total

    def pop_generation_tokens(self, request_id: str) -> int | None:
        with self._seen_lock:
            return self._generation_token_counts.pop(request_id, None)

    def generation_tokens(self, request_id: str) -> int | None:
        with self._seen_lock:
            return self._generation_token_counts.get(request_id)

    def record_api_generation_tokens(
        self,
        request_id: str,
        count: int | None,
        output_kind: str | None,
    ) -> int | None:
        if count is None:
            with self._seen_lock:
                return self._api_generation_token_counts.get(request_id)
        kind = normalize_output_kind(output_kind)
        with self._seen_lock:
            previous = self._api_generation_token_counts.get(request_id, 0)
            chunks = self._api_output_chunk_counts.get(request_id, 0)
            if kind in {"cumulative", "final_only"}:
                total = max(previous, count)
            elif kind == "delta":
                total = previous + count
            else:
                total = count if chunks == 0 else previous + count
            self._api_generation_token_counts[request_id] = total
            self._api_output_chunk_counts[request_id] = chunks + 1
            while len(self._api_generation_token_counts) > MAX_REQUEST_STATE_ENTRIES:
                oldest = next(iter(self._api_generation_token_counts))
                self._api_generation_token_counts.pop(oldest, None)
                self._api_output_chunk_counts.pop(oldest, None)
            return total

    def enter_engine_core_observation(self) -> bool:
        depth = int(getattr(self._engine_core_observation_local, "depth", 0))
        self._engine_core_observation_local.depth = depth + 1
        return depth == 0

    def leave_engine_core_observation(self) -> None:
        depth = int(getattr(self._engine_core_observation_local, "depth", 1)) - 1
        if depth <= 0:
            try:
                del self._engine_core_observation_local.depth
            except AttributeError:
                pass
        else:
            self._engine_core_observation_local.depth = depth

    def record_request_context(self, request_id: Any, **context: Any) -> None:
        req_id = none_if_empty(request_id)
        if req_id is None:
            return
        context_keys = {
            "agent_request_id",
            "agentmon_model_request_id",
            "model_call_id",
            "client_request_id",
            "agentmon_run_id",
            "agent_id",
            "api_request_id",
            "server_request_id",
        }
        clean = {key: str(value) for key, value in context.items() if key in context_keys and value not in {None, ""}}
        if not clean:
            return
        with self._request_id_alias_lock:
            current = dict(self._request_contexts.get(req_id) or {})
            current.update(clean)
            self._request_contexts[req_id] = current
            alias = self._request_id_aliases.get(req_id)
            if alias:
                alias_current = dict(self._request_contexts.get(alias) or {})
                alias_current.update(current)
                self._request_contexts[alias] = alias_current
            while len(self._request_contexts) > MAX_REQUEST_ID_ALIASES:
                self._request_contexts.pop(next(iter(self._request_contexts)))

    def enrich_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        candidates = [
            enriched.get("vllm_request_id"),
            enriched.get("request_id"),
            enriched.get("api_request_id"),
            enriched.get("agent_request_id"),
        ]
        with self._request_id_alias_lock:
            for candidate in candidates:
                for request_id in as_request_ids(candidate):
                    context = self._request_contexts.get(request_id)
                    if context:
                        for key, value in context.items():
                            enriched.setdefault(key, value)
                        break
        if enriched.get("agentmon_model_request_id") is None and enriched.get("agent_request_id") is not None:
            enriched["agentmon_model_request_id"] = enriched["agent_request_id"]
        if enriched.get("model_call_id") is None and enriched.get("agentmon_model_request_id") is not None:
            enriched["model_call_id"] = enriched["agentmon_model_request_id"]
        if enriched.get("client_request_id") is None and enriched.get("model_call_id") is not None:
            enriched["client_request_id"] = enriched["model_call_id"]
        if enriched.get("server_request_id") is None and enriched.get("api_request_id") is not None:
            enriched["server_request_id"] = enriched["api_request_id"]
        if enriched.get("engine_request_id") is None:
            engine_request_id = enriched.get("vllm_request_id") or enriched.get("request_id")
            if engine_request_id is not None:
                enriched["engine_request_id"] = engine_request_id
        return enriched

    def _copy_request_context_unlocked(self, source_id: str, target_id: str, **extra: Any) -> None:
        context = dict(self._request_contexts.get(source_id) or {})
        context.update({key: str(value) for key, value in extra.items() if value not in {None, ""}})
        if context:
            current = dict(self._request_contexts.get(target_id) or {})
            current.update(context)
            self._request_contexts[target_id] = current


def install_from_env() -> dict[str, Any]:
    if not truthy(os.environ.get(ENABLE_ENV)):
        return {"status": "disabled", "reason": f"{ENABLE_ENV} is not truthy"}
    return install()


def install(*, sink: VllmP2EventSink | None = None, force: bool = False) -> dict[str, Any]:
    global _INSTALLED, _STATE
    if _INSTALLED and not force:
        return {"status": "already_installed", "patched": len(_PATCHED)}

    state = VllmP2HookState(sink=sink)
    version_resolution = resolve_vllm_version()
    version = version_resolution.selected_version
    if version_resolution.version_status != "compatible":
        state.emit_capability(
            "unsupported",
            {
                "detected_vllm_version": version,
                "target_vllm_version": "0.18.0",
                "vllm_version_status": version_resolution.version_status,
                "vllm_version_evidence": version_resolution.to_dict(),
            },
        )
        return {
            "status": "unsupported",
            "detected_vllm_version": version,
            "vllm_version_status": version_resolution.version_status,
            "vllm_version_evidence": version_resolution.to_dict(),
        }

    _STATE = state
    patched: list[str] = []
    skipped: list[str] = []
    engine_core_cls: type | None = None
    engine_core_step_exists = False
    engine_core_async_path_exists = False

    for module_name, class_name, specs in hook_targets():
        cls = resolve_class(module_name, class_name)
        if cls is None:
            skipped.append(f"{module_name}:{class_name}")
            continue
        if module_name == "vllm.v1.engine.core" and class_name == "EngineCore":
            engine_core_cls = cls
            engine_core_step_exists = callable(getattr(cls, "step", None))
            engine_core_async_path_exists = callable(getattr(cls, "step_with_batch_queue", None))
        for method_name, factory in specs:
            if patch_method(cls, method_name, factory(state)):
                patched.append(f"{module_name}:{class_name}.{method_name}")
            else:
                skipped.append(f"{module_name}:{class_name}.{method_name}")

    _INSTALLED = True
    deep_required_suffixes = ("Scheduler.update_from_output",)
    deep_optional_suffixes = (
        "KVCacheManager.get_computed_blocks",
        "KVCacheManager.allocate_slots",
        "KVCacheManager.cache_blocks",
        "KVCacheManager.free",
        "KVCacheManager.evict_blocks",
        "KVCacheManager.reset_prefix_cache",
    )
    deep_missing_required = (
        [suffix for suffix in deep_required_suffixes if not any(item.endswith(suffix) for item in patched)]
        if state.deep_enabled
        else []
    )
    deep_missing_optional = (
        [suffix for suffix in deep_optional_suffixes if not any(item.endswith(suffix) for item in patched)]
        if state.deep_enabled
        else []
    )
    deep_missing = [*deep_missing_required, *deep_missing_optional]
    engine_core_step_hooked = bool(
        engine_core_cls is not None
        and getattr(getattr(engine_core_cls, "step", None), INSTALLED_ATTR, False)
    )
    engine_core_async_path_hooked = bool(
        engine_core_cls is not None
        and getattr(getattr(engine_core_cls, "step_with_batch_queue", None), INSTALLED_ATTR, False)
    )
    state.engine_core_step_present = engine_core_step_exists
    state.engine_core_step_hooked = engine_core_step_hooked
    state.engine_core_step_with_batch_queue_present = engine_core_async_path_exists
    state.engine_core_step_with_batch_queue_hooked = engine_core_async_path_hooked
    async_path_collection_status = (
        "ready"
        if engine_core_async_path_exists and engine_core_async_path_hooked
        else "required_hook_missing"
        if engine_core_async_path_exists
        else "method_not_available"
    )
    token_boundary_missing = [
        name
        for name, present, hooked in (
            ("EngineCore.step", engine_core_step_exists, engine_core_step_hooked),
            (
                "EngineCore.step_with_batch_queue",
                engine_core_async_path_exists,
                engine_core_async_path_hooked,
            ),
        )
        if present and not hooked
    ]
    engine_token_boundary_collection = (
        "supported"
        if engine_core_step_hooked and (not engine_core_async_path_exists or engine_core_async_path_hooked)
        else "unsupported"
    )
    status = (
        "supported"
        if patched and not deep_missing and not token_boundary_missing
        else "partial"
    )
    emit_moe_routing_capability(state)
    state.emit_capability(
        status,
        {
            "detected_vllm_version": version,
            "detected_vllm_ascend_version": installed_distribution_version(
                "vllm-ascend",
                "vllm_ascend",
            ),
            "patched": patched,
            "skipped": skipped,
            "hot_path_get_computed_blocks": truthy(os.environ.get(HOT_PATH_ENV)) or state.deep_enabled,
            "hot_path_kv_blocks": truthy(os.environ.get(KV_BLOCKS_ENV)) or state.deep_enabled,
            "deep_collection": state.deep_enabled,
            "moe_routing_mode": state.moe_mode,
            "moe_routing_mode_source": MOE_MODE_ENV,
            "moe_routing_chunk_tokens": state.moe_chunk_tokens,
            "moe_routing_chunk_tokens_source": MOE_CHUNK_TOKENS_ENV,
            "enable_return_routed_experts": None,
            "deep_missing_targets": deep_missing,
            "deep_missing_required_targets": deep_missing_required,
            "deep_missing_optional_targets": deep_missing_optional,
            "deep_kv_lifecycle_status": (
                "complete"
                if state.deep_enabled and not deep_missing_optional
                else "partial" if state.deep_enabled else "disabled"
            ),
            "deep_prefill_boundary": (
                "scheduler.update_from_output"
                if state.deep_enabled and not deep_missing_required
                else "unavailable" if state.deep_enabled else "disabled"
            ),
            "engine_core_step_present": engine_core_step_exists,
            "engine_core_step_hooked": engine_core_step_hooked,
            "engine_core_step_with_batch_queue_present": engine_core_async_path_exists,
            "engine_core_step_with_batch_queue_hooked": engine_core_async_path_hooked,
            "async_scheduling_enabled": None,
            "asynchronous_scheduling_collection_status": async_path_collection_status,
            "engine_token_boundary_collection": engine_token_boundary_collection,
            "missing_required": token_boundary_missing,
            "prefix_cache_enabled": None,
            "prefix_cache_config_source": "unavailable_before_scheduler_initialization",
            "prefix_cache_launch_source": (
                "explicit_cli" if state.prefix_cache_cli_arguments else "engine_args_or_model_default"
            ),
            "prefix_cache_cli_arguments": state.prefix_cache_cli_arguments,
            "prefix_cache_expectation": state.prefix_cache_expectation,
            **visible_devices_from_env(),
        },
    )
    return {
        "status": status,
        "detected_vllm_version": version,
        "patched": patched,
        "skipped": skipped,
        "deep_missing_required_targets": deep_missing_required,
        "deep_missing_optional_targets": deep_missing_optional,
        "engine_core_step_present": engine_core_step_exists,
        "engine_core_step_hooked": engine_core_step_hooked,
        "engine_core_step_with_batch_queue_present": engine_core_async_path_exists,
        "engine_core_step_with_batch_queue_hooked": engine_core_async_path_hooked,
        "asynchronous_scheduling_collection_status": async_path_collection_status,
        "engine_token_boundary_collection": engine_token_boundary_collection,
        "missing_required": token_boundary_missing,
    }


def installed_distribution_version(*names: str) -> str | None:
    for name in names:
        try:
            return importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            continue
        except Exception:
            return None
    return None


def uninstall_for_tests() -> None:
    global _INSTALLED, _STATE
    for cls, method_name, original in reversed(_PATCHED):
        setattr(cls, method_name, original)
    _PATCHED.clear()
    _INSTALLED = False
    _STATE = None


def hook_targets() -> list[tuple[str, str, list[tuple[str, Callable[[VllmP2HookState], Callable[..., Any]]]]]]:
    deep_enabled = environment_flag(DEEP_ENV, default=True)
    targets: list[tuple[str, str, list[tuple[str, Callable[[VllmP2HookState], Callable[..., Any]]]]]] = [
        (
            "vllm.v1.engine.async_llm",
            "AsyncLLM",
            [
                ("add_request", wrap_async_add_request),
                ("generate", wrap_async_generate),
                ("abort", wrap_async_abort),
            ],
        ),
        (
            "vllm.v1.engine.llm_engine",
            "LLMEngine",
            [
                ("add_request", wrap_llm_add_request),
                ("step", wrap_llm_step),
            ],
        ),
        (
            "vllm.v1.engine.core",
            "EngineCore",
            [
                ("add_request", wrap_engine_core_add_request),
                ("step", wrap_engine_core_step),
                ("step_with_batch_queue", wrap_engine_core_step_with_batch_queue),
            ],
        ),
        (
            "vllm.v1.core.sched.scheduler",
            "Scheduler",
            [
                ("__init__", wrap_scheduler_init),
                ("add_request", wrap_scheduler_add_request),
                ("schedule", wrap_scheduler_schedule),
                ("make_stats", wrap_scheduler_make_stats),
            ],
        ),
        (
            "vllm.v1.core.kv_cache_manager",
            "KVCacheManager",
            [
                ("__init__", wrap_kv_manager_init),
                ("make_prefix_cache_stats", wrap_kv_make_prefix_cache_stats),
            ],
        ),
    ]
    if deep_enabled:
        targets[3][2].append(("update_from_output", wrap_scheduler_update_from_output))
    if truthy(os.environ.get(HOT_PATH_ENV)) or deep_enabled:
        targets[-1][2].append(("get_computed_blocks", wrap_kv_get_computed_blocks))
    if truthy(os.environ.get(KV_BLOCKS_ENV)) or deep_enabled:
        targets[-1][2].extend(
            [
                ("allocate_slots", wrap_kv_allocate_slots),
                ("cache_blocks", wrap_kv_cache_blocks),
                ("free", wrap_kv_free),
                ("evict_blocks", wrap_kv_evict_blocks),
                ("reset_prefix_cache", wrap_kv_reset_prefix_cache),
            ]
        )
    return targets


def resolve_class(module_name: str, class_name: str) -> type | None:
    try:
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
    except Exception as exc:
        state = _STATE
        if state is not None:
            state.emit_error(exc, f"resolve:{module_name}:{class_name}")
        return None
    return cls if isinstance(cls, type) else None


def patch_method(cls: type, method_name: str, wrapper_factory: Callable[[Callable[..., Any]], Callable[..., Any]]) -> bool:
    original = getattr(cls, method_name, None)
    if original is None or getattr(original, INSTALLED_ATTR, False):
        return False
    wrapper = wrapper_factory(original)
    setattr(wrapper, INSTALLED_ATTR, True)
    setattr(wrapper, ORIGINAL_ATTR, original)
    setattr(cls, method_name, wrapper)
    _PATCHED.append((cls, method_name, original))
    return True


def wrap_async_add_request(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            received_ns = time.monotonic_ns()
            result = await original(self, *args, **kwargs)
            try:
                call_request_id = request_id_from_call(args, kwargs)
                request_id = getattr(result, "request_id", None) or call_request_id
                state.record_request_id_alias(call_request_id, request_id)
                header_context = agentmon_context_from_headers(
                    call_argument(original, self, args, kwargs, "trace_headers")
                )
                state.record_request_context(request_id, **header_context)
                emit_received(
                    state,
                    request_id=request_id,
                    agent_request_id=header_context.get("agentmon_model_request_id"),
                    agentmon_run_id=header_context.get("agentmon_run_id"),
                    api_request_id=call_request_id if call_request_id != request_id else None,
                    received_ns=received_ns,
                    model_name=model_name_from_engine(self),
                )
            except Exception as exc:
                state.emit_error(exc, "AsyncLLM.add_request.observe")
            return result

        return wrapper

    return factory


def wrap_async_generate(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            request_id = None
            agent_request_id = None
            output_kind = "unknown"
            try:
                request_id = request_id_from_generate_call(args, kwargs)
                sampling_params = call_argument(original, self, args, kwargs, "sampling_params")
                output_kind = normalize_output_kind(getattr(sampling_params, "output_kind", None))
                header_context = agentmon_context_from_headers(
                    call_argument(original, self, args, kwargs, "trace_headers")
                )
                agent_request_id = header_context.get("agentmon_model_request_id")
                if request_id is not None:
                    state.record_request_context(request_id, **header_context)
            except Exception as exc:
                state.emit_error(exc, "AsyncLLM.generate.before_observe")
            try:
                async for output in original(self, *args, **kwargs):
                    try:
                        observe_request_output(
                            state,
                            output,
                            agent_request_id=agent_request_id,
                            fallback_request_id=request_id,
                            output_kind=output_kind,
                        )
                    except Exception as exc:
                        state.emit_error(exc, "AsyncLLM.generate.observe")
                    yield output
            except (asyncio.CancelledError, GeneratorExit):
                emit_cancelled(state, request_id, agent_request_id=agent_request_id)
                raise
            finally:
                state.clear_request_ids(request_id)

        return wrapper

    return factory


def wrap_async_abort(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        async def wrapper(self: Any, request_id: Any, *args: Any, **kwargs: Any) -> Any:
            result = await original(self, request_id, *args, **kwargs)
            try:
                for item in as_request_ids(request_id):
                    emit_cancelled(state, item)
            except Exception as exc:
                state.emit_error(exc, "AsyncLLM.abort.observe")
            return result

        return wrapper

    return factory


def wrap_llm_add_request(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            received_ns = time.monotonic_ns()
            result = original(self, *args, **kwargs)
            try:
                call_request_id = request_id_from_call(args, kwargs)
                request_id = str(result) if result is not None else call_request_id
                state.record_request_id_alias(call_request_id, request_id)
                header_context = agentmon_context_from_headers(
                    call_argument(original, self, args, kwargs, "trace_headers")
                )
                state.record_request_context(request_id, **header_context)
                emit_received(
                    state,
                    request_id=request_id,
                    agent_request_id=header_context.get("agentmon_model_request_id"),
                    agentmon_run_id=header_context.get("agentmon_run_id"),
                    api_request_id=call_request_id if call_request_id != request_id else None,
                    received_ns=received_ns,
                    model_name=model_name_from_engine(self),
                )
            except Exception as exc:
                state.emit_error(exc, "LLMEngine.add_request.observe")
            return result

        return wrapper

    return factory


def wrap_llm_step(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(self, *args, **kwargs)
            try:
                for output in result or []:
                    observe_request_output(state, output)
            except Exception as exc:
                state.emit_error(exc, "LLMEngine.step.observe")
            return result

        return wrapper

    return factory


def wrap_engine_core_add_request(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(self, request, *args, **kwargs)
            try:
                emit_queued_from_request(state, request)
            except Exception as exc:
                state.emit_error(exc, "EngineCore.add_request.observe")
            return result

        return wrapper

    return factory


def wrap_engine_core_step(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return wrap_engine_core_output_method(state, "EngineCore.step.observe")


def wrap_engine_core_step_with_batch_queue(
    state: VllmP2HookState,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return wrap_engine_core_output_method(state, "EngineCore.step_with_batch_queue.observe")


def wrap_engine_core_output_method(
    state: VllmP2HookState,
    observation_name: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            should_observe = state.enter_engine_core_observation()
            try:
                result = original(self, *args, **kwargs)
            finally:
                state.leave_engine_core_observation()
            if should_observe:
                try:
                    observe_engine_core_step_result(state, result)
                except Exception as exc:
                    state.emit_error(exc, observation_name)
            return result

        return wrapper

    return factory


def wrap_scheduler_init(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            vllm_config = call_argument(original, self, args, kwargs, "vllm_config")
            result = original(self, *args, **kwargs)
            try:
                emit_scheduler_kv_config(state, self, vllm_config=vllm_config)
                emit_scheduler_runtime_capability(state, self, vllm_config=vllm_config)
            except Exception as exc:
                state.emit_error(exc, "Scheduler.__init__.observe")
            return result

        return wrapper

    return factory


def wrap_scheduler_add_request(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            waiting_on_arrival = scheduler_waiting_count(self)
            running_on_arrival = sequence_length(getattr(self, "running", None))
            result = original(self, request, *args, **kwargs)
            try:
                emit_queued_from_request(
                    state,
                    request,
                    queue_depth_on_arrival=waiting_on_arrival,
                    running_requests_on_arrival=running_on_arrival,
                )
            except Exception as exc:
                state.emit_error(exc, "Scheduler.add_request.observe")
            return result

        return wrapper

    return factory


def wrap_scheduler_schedule(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            schedule_start_ns = time.monotonic_ns()
            before: dict[str, dict[str, Any]] = {}
            if state.deep_enabled:
                try:
                    before = scheduler_request_state(self)
                except Exception as exc:
                    state.emit_error(exc, "Scheduler.schedule.before_observe")
            result = original(self, *args, **kwargs)
            try:
                observe_scheduler_output(state, result)
                if state.deep_enabled:
                    record = observe_deep_scheduler_output(
                        state,
                        self,
                        result,
                        before=before,
                        schedule_start_ns=schedule_start_ns,
                        schedule_end_ns=time.monotonic_ns(),
                    )
                    state.remember_scheduler_iteration(result, record)
            except Exception as exc:
                state.emit_error(exc, "Scheduler.schedule.observe")
            return result

        return wrapper

    return factory


def wrap_scheduler_update_from_output(
    state: VllmP2HookState,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, scheduler_output: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                result = original(self, scheduler_output, *args, **kwargs)
            except BaseException:
                state.pop_scheduler_iteration(scheduler_output)
                raise
            try:
                observe_scheduler_iteration_completed(state, scheduler_output, result)
            except Exception as exc:
                state.emit_error(exc, "Scheduler.update_from_output.observe")
            return result

        return wrapper

    return factory


def wrap_scheduler_make_stats(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            state.scheduler_stats_depth += 1
            try:
                result = original(self, *args, **kwargs)
            finally:
                state.scheduler_stats_depth -= 1
            try:
                observe_scheduler_stats(state, result, prefix_cache_status=prefix_status_from_scheduler(self))
            except Exception as exc:
                state.emit_error(exc, "Scheduler.make_stats.observe")
            return result

        return wrapper

    return factory


def wrap_kv_manager_init(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(self, *args, **kwargs)
            try:
                enabled = boolean_or_none(getattr(self, "enable_caching", None))
                status = "enabled" if enabled is True else "disabled" if enabled is False else "unknown"
                state.sink.kv_snapshot(prefix_cache_status=status, source="kv_cache_manager")
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.__init__.observe")
            return result

        return wrapper

    return factory


def wrap_kv_make_prefix_cache_stats(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(self, *args, **kwargs)
            if state.scheduler_stats_depth > 0:
                return result
            try:
                observe_prefix_cache_stats(
                    state,
                    result,
                    prefix_cache_status=(
                        "enabled"
                        if boolean_or_none(getattr(self, "enable_caching", None)) is True
                        else "disabled"
                        if boolean_or_none(getattr(self, "enable_caching", None)) is False
                        else "unknown"
                    ),
                    kv_usage_ratio=getattr(self, "usage", None),
                )
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.make_prefix_cache_stats.observe")
            return result

        return wrapper

    return factory


def wrap_kv_get_computed_blocks(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(self, request, *args, **kwargs)
            try:
                num_cached = result[1] if isinstance(result, tuple) and len(result) > 1 else None
                emit_prompt_cache_accounting_from_request(state, request, cached_prompt_tokens=num_cached)
                emit_kv_block_from_request(
                    state,
                    request,
                    operation="lookup",
                    cache_hit_block_count=kv_block_count(result[0]) if isinstance(result, tuple) and result else None,
                    kv_usage_after=numeric_or_none(getattr(self, "usage", None)),
                    kv_quality="exact",
                )
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.get_computed_blocks.observe")
            return result

        return wrapper

    return factory


def wrap_kv_allocate_slots(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            before = None
            try:
                before = numeric_or_none(getattr(self, "usage", None))
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.allocate_slots.before_observe")
            result = original(self, request, *args, **kwargs)
            try:
                after = numeric_or_none(getattr(self, "usage", None))
                emit_kv_block_from_request(
                    state,
                    request,
                    operation="allocate",
                    allocated_block_count=kv_block_count(result),
                    kv_usage_before=before,
                    kv_usage_after=after,
                    kv_quality="exact" if kv_block_count(result) is not None else "partial",
                )
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.allocate_slots.observe")
            return result

        return wrapper

    return factory


def wrap_kv_cache_blocks(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            before = None
            try:
                before = numeric_or_none(getattr(self, "usage", None))
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.cache_blocks.before_observe")
            result = original(self, request, *args, **kwargs)
            try:
                block_count = kv_blocks_for_request(self, request)
                emit_kv_block_from_request(
                    state,
                    request,
                    operation="cache",
                    cached_block_count=block_count,
                    kv_usage_before=before,
                    kv_usage_after=numeric_or_none(getattr(self, "usage", None)),
                    kv_quality="exact" if block_count is not None else "partial",
                )
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.cache_blocks.observe")
            return result

        return wrapper

    return factory


def wrap_kv_free(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, request_or_request_id: Any, *args: Any, **kwargs: Any) -> Any:
            before = None
            before_blocks = None
            try:
                before = numeric_or_none(getattr(self, "usage", None))
                before_blocks = kv_blocks_for_request(self, request_or_request_id)
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.free.before_observe")
            result = original(self, request_or_request_id, *args, **kwargs)
            try:
                after = numeric_or_none(getattr(self, "usage", None))
                emit_kv_block_from_request(
                    state,
                    request_or_request_id,
                    operation="free",
                    freed_block_count=before_blocks,
                    kv_usage_before=before,
                    kv_usage_after=after,
                    kv_quality="exact" if before_blocks is not None else "partial",
                )
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.free.observe")
            return result

        return wrapper

    return factory


def wrap_kv_evict_blocks(state: VllmP2HookState) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, block_ids: Any = None, *args: Any, **kwargs: Any) -> Any:
            before = None
            try:
                before = numeric_or_none(getattr(self, "usage", None))
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.evict_blocks.before_observe")
            result = original(self, block_ids, *args, **kwargs)
            try:
                state.sink.kv_block_event(
                    operation="evict",
                    evicted_block_count=kv_block_count(block_ids),
                    kv_usage_before=before,
                    kv_usage_after=numeric_or_none(getattr(self, "usage", None)),
                    kv_quality="exact" if kv_block_count(block_ids) is not None else "partial",
                )
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.evict_blocks.observe")
            return result

        return wrapper

    return factory


def wrap_kv_reset_prefix_cache(
    state: VllmP2HookState,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def factory(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            before = None
            try:
                before = numeric_or_none(getattr(self, "usage", None))
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.reset_prefix_cache.before_observe")
            result = original(self, *args, **kwargs)
            try:
                state.sink.kv_reset(
                    succeeded=bool(result) if result is not None else None,
                    kv_usage_before=before,
                    kv_usage_after=numeric_or_none(getattr(self, "usage", None)),
                )
            except Exception as exc:
                state.emit_error(exc, "KVCacheManager.reset_prefix_cache.observe")
            return result

        return wrapper

    return factory


def emit_received(
    state: VllmP2HookState,
    *,
    request_id: Any,
    agent_request_id: str | None,
    agentmon_run_id: str | None = None,
    api_request_id: str | None = None,
    received_ns: int,
    model_name: str | None = None,
) -> None:
    req_id = none_if_empty(request_id)
    if req_id is None or not state.mark_seen("seen_received", req_id):
        return
    state.record_request_context(
        req_id,
        agent_request_id=agent_request_id,
        agentmon_model_request_id=agent_request_id,
        agentmon_run_id=agentmon_run_id,
        api_request_id=api_request_id,
    )
    try:
        emit_request_payload(
            state,
            "vllm.request.received",
            {
                "agent_request_id": agent_request_id,
                "agentmon_model_request_id": agent_request_id,
                "agentmon_run_id": agentmon_run_id,
                "vllm_request_id": req_id,
                "engine_request_id": req_id,
                "api_request_id": api_request_id,
                "server_request_id": api_request_id,
                "run_id": state.sink.run_id,
                "correlation_quality": "direct" if agent_request_id else "server_generated",
                "received_ns": received_ns,
                "model_name": model_name,
            },
        )
    except Exception as exc:
        state.emit_error(exc, "emit_received")


def emit_queued_from_request(
    state: VllmP2HookState,
    request: Any,
    *,
    queue_depth_on_arrival: int | None = None,
    running_requests_on_arrival: int | None = None,
) -> None:
    req_id = none_if_empty(getattr(request, "request_id", None))
    if req_id is None or not state.mark_seen("seen_queued", req_id):
        return
    payload = request_payload(request)
    state.record_request_context(req_id, **payload)
    payload.update(
        {
            "queued_ns": time.monotonic_ns(),
            "queue_depth_on_arrival": queue_depth_on_arrival,
            "running_requests_on_arrival": running_requests_on_arrival,
        }
    )
    emit_request_payload(state, "vllm.request.queued", payload)


def emit_prompt_cache_accounting_from_request(
    state: VllmP2HookState,
    request: Any,
    *,
    cached_prompt_tokens: Any = None,
) -> None:
    payload = request_payload(request)
    prompt_tokens = numeric_or_none(getattr(request, "num_prompt_tokens", None))
    cached_tokens = normalized_cached_tokens(cached_prompt_tokens)
    validate_prefix_cache_observation(
        state,
        cached_prompt_tokens=cached_tokens,
        request_id=payload.get("vllm_request_id"),
    )
    state.record_request_context(payload.get("vllm_request_id"), **payload)
    payload.update(
        {
            "prompt_cache_accounted_ns": time.monotonic_ns(),
            "cached_prompt_tokens": cached_tokens,
            **prompt_token_accounting(prompt_tokens, cached_tokens),
        }
    )
    emit_request_payload(state, "vllm.request.prompt_cache_accounted", payload)


def emit_kv_block_from_request(
    state: VllmP2HookState,
    request: Any,
    *,
    operation: str | None = None,
    allocated_block_count: int | None = None,
    cached_block_count: int | None = None,
    cache_hit_block_count: int | None = None,
    freed_block_count: int | None = None,
    evicted_block_count: int | None = None,
    kv_usage_before: float | int | None = None,
    kv_usage_after: float | int | None = None,
    kv_quality: str = "exact",
) -> None:
    payload = request_payload(request) if not isinstance(request, str) else {"vllm_request_id": request}
    state.record_request_context(payload.get("vllm_request_id"), **payload)
    enriched = state.enrich_payload(payload)
    state.sink.kv_block_event(
        request_id=none_if_empty(enriched.get("vllm_request_id") or request_id_from_request_like(request)),
        operation=operation,
        allocated_block_count=allocated_block_count,
        cached_block_count=cached_block_count,
        cache_hit_block_count=cache_hit_block_count,
        freed_block_count=freed_block_count,
        evicted_block_count=evicted_block_count,
        kv_usage_before=kv_usage_before,
        kv_usage_after=kv_usage_after,
        kv_quality=kv_quality,
        agent_request_id=none_if_empty(enriched.get("agent_request_id")),
        agentmon_run_id=none_if_empty(enriched.get("agentmon_run_id")),
        agentmon_model_request_id=none_if_empty(enriched.get("agentmon_model_request_id")),
        api_request_id=none_if_empty(enriched.get("api_request_id")),
    )


def observe_request_output(
    state: VllmP2HookState,
    output: Any,
    *,
    agent_request_id: str | None = None,
    fallback_request_id: str | None = None,
    output_kind: str | None = None,
) -> None:
    output_request_id = none_if_empty(getattr(output, "request_id", None))
    req_id, api_request_id = state.resolve_engine_request_id(fallback_request_id, output_request_id)
    if req_id is None:
        return
    state.record_request_context(
        req_id,
        agent_request_id=agent_request_id,
        agentmon_model_request_id=agent_request_id,
        api_request_id=api_request_id,
    )
    chunk_generation_tokens = generation_token_count(output)
    api_generation_tokens = state.record_api_generation_tokens(
        req_id,
        chunk_generation_tokens,
        output_kind,
    )
    if chunk_generation_tokens and state.mark_seen("seen_first_output", req_id):
        emit_request_payload(
            state,
            "vllm.request.first_output",
            {
                "agent_request_id": agent_request_id,
                "vllm_request_id": req_id,
                "correlation_quality": "direct" if agent_request_id else "server_generated",
                "first_output_ns": time.monotonic_ns(),
                "first_output_timestamp_source": "async_llm_request_output",
                "first_output_timestamp_quality": "api_output_observed",
            },
        )
    if bool(getattr(output, "finished", False)) and state.mark_seen("seen_finished", req_id):
        prompt_tokens = prompt_token_count(output)
        cached_prompt_tokens = normalized_cached_tokens(getattr(output, "num_cached_tokens", None))
        validate_prefix_cache_observation(
            state,
            cached_prompt_tokens=cached_prompt_tokens,
            request_id=req_id,
        )
        engine_generation_tokens = state.generation_tokens(req_id)
        api_usage_generation_tokens = generation_tokens_from_usage(output)
        generation_tokens = (
            engine_generation_tokens
            if engine_generation_tokens is not None
            else api_generation_tokens
        )
        generation_tokens_source = (
            "engine_core_new_token_ids"
            if engine_generation_tokens is not None
            else "async_llm_request_output_accumulated"
            if api_generation_tokens is not None
            else "unavailable"
        )
        comparison_generation_tokens = (
            api_usage_generation_tokens
            if api_usage_generation_tokens is not None
            else api_generation_tokens
        )
        token_count_consistency = (
            "match"
            if engine_generation_tokens is not None
            and comparison_generation_tokens is not None
            and engine_generation_tokens == comparison_generation_tokens
            else "mismatch"
            if engine_generation_tokens is not None and comparison_generation_tokens is not None
            else "single_source"
        )
        payload = {
            "agent_request_id": agent_request_id,
            "vllm_request_id": req_id,
            "correlation_quality": "direct" if agent_request_id else "server_generated",
            "finished_ns": time.monotonic_ns(),
            "finished_timestamp_source": "async_llm_request_output",
            "finished_timestamp_quality": "api_response_observed",
            "finish_reason": finish_reason_from_output(output),
            "prompt_tokens": prompt_tokens,
            "cached_prompt_tokens": cached_prompt_tokens,
            **prompt_token_accounting(prompt_tokens, cached_prompt_tokens),
            "generation_tokens": generation_tokens,
            "generation_tokens_source": generation_tokens_source,
            "engine_generation_tokens_observed": engine_generation_tokens,
            "api_generation_tokens_observed": api_generation_tokens,
            "api_usage_generation_tokens": api_usage_generation_tokens,
            "generation_tokens_consistency": token_count_consistency,
            "api_output_kind": normalize_output_kind(output_kind),
        }
        if api_request_id is not None:
            payload["api_request_id"] = api_request_id
        observe_moe_routing(
            state,
            output,
            request_payload_base={
                "agent_request_id": agent_request_id,
                "vllm_request_id": req_id,
                "api_request_id": api_request_id,
                "correlation_quality": "direct" if agent_request_id else "server_generated",
            },
            prompt_tokens=prompt_tokens,
        )
        emit_request_payload(
            state,
            "vllm.request.finished",
            payload,
        )
        state.clear_request_ids(req_id, output_request_id, fallback_request_id)


def observe_engine_core_step_result(state: VllmP2HookState, result: Any) -> None:
    if not isinstance(result, tuple) or not result:
        return
    outputs_by_engine = result[0]
    if outputs_by_engine is None:
        return
    values = getattr(outputs_by_engine, "values", None)
    if not callable(values):
        return
    for outputs in values():
        if outputs is None:
            continue
        observe_engine_core_outputs(state, outputs)


def observe_engine_core_outputs(state: VllmP2HookState, outputs: Any) -> None:
    scheduler_stats = getattr(outputs, "scheduler_stats", None)
    if scheduler_stats is not None:
        observe_scheduler_stats(state, scheduler_stats)
    for output in getattr(outputs, "outputs", []) or []:
        req_id = none_if_empty(
            first_present(
                getattr(output, "request_id", None),
                getattr(output, "req_id", None),
            )
        )
        if req_id is None:
            continue
        new_token_count = engine_core_new_token_count(output)
        observed_generation_tokens: int | None = None
        if new_token_count > 0:
            token_observed_ns = time.monotonic_ns()
            state.record_last_token(req_id, token_observed_ns)
            observed_generation_tokens = state.add_generation_tokens(req_id, new_token_count)
            if state.mark_seen("seen_first_token", req_id):
                emit_request_payload(
                    state,
                    "vllm.request.first_token",
                    {
                        "vllm_request_id": req_id,
                        "correlation_quality": "server_generated",
                        "first_token_ns": token_observed_ns,
                        "first_token_observed_count": new_token_count,
                        "engine_generation_tokens_observed": observed_generation_tokens,
                        "first_token_timestamp_source": "engine_core_output",
                        "first_token_timestamp_quality": "engine_process_monotonic",
                    },
                )
        finish_reason = getattr(output, "finish_reason", None)
        output_finished = bool(getattr(output, "finished", False)) or finish_reason is not None
        if output_finished and state.mark_seen("seen_engine_finished", req_id):
            engine_finished_ns = time.monotonic_ns()
            last_token_ns = state.pop_last_token(req_id)
            generation_tokens = state.pop_generation_tokens(req_id)
            emit_request_payload(
                state,
                "vllm.request.engine_finished",
                {
                    "vllm_request_id": req_id,
                    "correlation_quality": "server_generated",
                    "last_token_ns": last_token_ns,
                    "last_token_timestamp_source": "engine_core_output" if last_token_ns is not None else None,
                    "last_token_timestamp_quality": "engine_process_monotonic" if last_token_ns is not None else None,
                    "engine_finished_ns": engine_finished_ns,
                    "finish_reason": safe_str(finish_reason),
                    "generation_tokens": generation_tokens,
                    "generation_tokens_source": "engine_core_new_token_ids",
                    "engine_generation_tokens_observed": generation_tokens,
                    "cached_prompt_tokens": numeric_or_none(getattr(output, "num_cached_tokens", None)),
                    "engine_finished_timestamp_source": "engine_core_output",
                    "engine_finished_timestamp_quality": "engine_process_monotonic",
                },
            )


def scheduler_request_state(scheduler: Any) -> dict[str, dict[str, Any]]:
    requests = getattr(scheduler, "requests", None)
    if not isinstance(requests, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_request_id, request in requests.items():
        request_id = none_if_empty(first_present(getattr(request, "request_id", None), raw_request_id))
        if request_id is None:
            continue
        status = getattr(request, "status", None)
        result[request_id] = {
            "num_computed_tokens": numeric_or_none(getattr(request, "num_computed_tokens", None)),
            "num_prompt_tokens": numeric_or_none(getattr(request, "num_prompt_tokens", None)),
            "num_tokens": numeric_or_none(getattr(request, "num_tokens", None)),
            "num_output_tokens": sequence_length(getattr(request, "output_token_ids", None)),
            "num_cached_tokens": normalized_cached_tokens(getattr(request, "num_cached_tokens", None)),
            "num_preemptions": numeric_or_none(getattr(request, "num_preemptions", None)),
            "status": safe_str(getattr(status, "name", None) or status),
            "trace_headers": getattr(request, "trace_headers", None),
        }
    return result


def observe_deep_scheduler_output(
    state: VllmP2HookState,
    scheduler: Any,
    output: Any,
    *,
    before: dict[str, dict[str, Any]],
    schedule_start_ns: int,
    schedule_end_ns: int,
) -> dict[str, Any]:
    iteration_id = state.next_scheduler_iteration_id()
    after = scheduler_request_state(scheduler)
    scheduled_tokens = scheduler_scheduled_tokens(output)
    new_data = {
        str(item.req_id): item
        for item in (getattr(output, "scheduled_new_reqs", None) or [])
        if none_if_empty(getattr(item, "req_id", None)) is not None
    }
    cached_data = getattr(output, "scheduled_cached_reqs", None)
    cached_ids = [str(item) for item in (getattr(cached_data, "req_ids", None) or [])]
    resumed_ids = {str(item) for item in (getattr(cached_data, "resumed_req_ids", None) or set())}
    cached_before_computed = {
        request_id: numeric_or_none(value)
        for request_id, value in zip(cached_ids, getattr(cached_data, "num_computed_tokens", None) or [])
    }
    request_summaries: list[dict[str, Any]] = []
    phase_counts = {"prefill": 0, "decode": 0, "unknown": 0}
    chunked_prefill_count = 0
    prefill_completion_candidate_count = 0

    for batch_position, (request_id, token_count) in enumerate(scheduled_tokens.items()):
        previous = before.get(request_id, {})
        current = after.get(request_id, {})
        new_item = new_data.get(request_id)
        before_computed = first_present(
            cached_before_computed.get(request_id),
            numeric_or_none(getattr(new_item, "num_computed_tokens", None)),
            previous.get("num_computed_tokens"),
        )
        after_computed = current.get("num_computed_tokens")
        if after_computed is None and before_computed is not None:
            after_computed = before_computed + token_count
        prompt_tokens = first_present(
            current.get("num_prompt_tokens"),
            previous.get("num_prompt_tokens"),
            sequence_length(getattr(new_item, "prompt_token_ids", None)),
        )
        cached_prompt_tokens = first_present(
            current.get("num_cached_tokens"),
            previous.get("num_cached_tokens"),
            normalized_cached_tokens(getattr(new_item, "num_computed_tokens", None)),
        )
        if before_computed is None or prompt_tokens is None:
            phase = "unknown"
        elif before_computed < prompt_tokens:
            phase = "prefill"
        else:
            phase = "decode"
        phase_counts[phase] += 1
        prefill_completion_candidate = bool(
            phase == "prefill"
            and after_computed is not None
            and prompt_tokens is not None
            and after_computed >= prompt_tokens
        )
        chunked_prefill = bool(phase == "prefill" and not prefill_completion_candidate)
        chunked_prefill_count += int(chunked_prefill)
        prefill_completion_candidate_count += int(prefill_completion_candidate)
        if request_id in new_data:
            request_kind = "new"
        elif request_id in resumed_ids:
            request_kind = "resumed"
        else:
            request_kind = "running"
        context_request = getattr(scheduler, "requests", {}).get(request_id) if isinstance(
            getattr(scheduler, "requests", None), dict
        ) else None
        if context_request is not None:
            state.record_request_context(request_id, **agentmon_context_from_headers(getattr(context_request, "trace_headers", None)))
        summary = {
            "vllm_request_id": request_id,
            "scheduler_iteration_id": iteration_id,
            "scheduler_batch_position": batch_position,
            "scheduler_request_kind": request_kind,
            "scheduler_phase": phase,
            "scheduled_token_count": token_count,
            "num_computed_tokens_before": before_computed,
            "num_computed_tokens_after": after_computed,
            "prompt_tokens": prompt_tokens,
            "cached_prompt_tokens": cached_prompt_tokens,
            "chunked_prefill": chunked_prefill,
            "prefill_completion_candidate": prefill_completion_candidate,
            "preemption_count": current.get("num_preemptions"),
        }
        request_summaries.append(summary)

    batch_request_count = len(request_summaries)
    mixed_batch = phase_counts["prefill"] > 0 and phase_counts["decode"] > 0
    common = {
        "scheduler_iteration_id": iteration_id,
        "schedule_start_ns": schedule_start_ns,
        "schedule_end_ns": schedule_end_ns,
        "scheduler_duration_ns": max(0, schedule_end_ns - schedule_start_ns),
        "batch_request_count": batch_request_count,
        "new_request_count": len(new_data),
        "resumed_request_count": len(resumed_ids),
        "running_request_count": max(0, batch_request_count - len(new_data) - len(resumed_ids)),
        "prefill_request_count": phase_counts["prefill"],
        "decode_request_count": phase_counts["decode"],
        "unknown_phase_request_count": phase_counts["unknown"],
        "chunked_prefill_request_count": chunked_prefill_count,
        "prefill_completion_candidate_count": prefill_completion_candidate_count,
        "mixed_prefill_decode_batch": mixed_batch,
        "continuous_batching_observed": batch_request_count > 1,
        "scheduled_tokens": sum(scheduled_tokens.values()),
        "scheduled_prefill_tokens": sum(
            int(item.get("scheduled_token_count") or 0)
            for item in request_summaries
            if item.get("scheduler_phase") == "prefill"
        ),
        "scheduled_decode_tokens": sum(
            int(item.get("scheduled_token_count") or 0)
            for item in request_summaries
            if item.get("scheduler_phase") == "decode"
        ),
        "running_requests_after_schedule": sequence_length(getattr(scheduler, "running", None)),
        "waiting_requests_after_schedule": scheduler_waiting_count(scheduler),
        "preempted_request_count": sequence_length(getattr(output, "preempted_req_ids", None)) or 0,
        "finished_request_count": sequence_length(getattr(output, "finished_req_ids", None)) or 0,
    }
    max_batch_rows = deep_batch_request_limit()
    state.sink.scheduler_iteration(
        "vllm.scheduler.iteration.scheduled",
        payload={
            **common,
            "request_summaries": request_summaries[:max_batch_rows],
            "request_summary_count": batch_request_count,
            "request_summary_truncated_count": max(0, batch_request_count - max_batch_rows),
        },
    )
    for summary in request_summaries:
        emit_request_payload(
            state,
            "vllm.request.scheduler_iteration",
            {
                **summary,
                "correlation_quality": "server_generated",
                "batch_request_count": batch_request_count,
                "batch_prefill_request_count": phase_counts["prefill"],
                "batch_decode_request_count": phase_counts["decode"],
                "schedule_start_ns": schedule_start_ns,
                "schedule_end_ns": schedule_end_ns,
                "scheduler_duration_ns": max(0, schedule_end_ns - schedule_start_ns),
                "mixed_prefill_decode_batch": mixed_batch,
                "continuous_batching_observed": batch_request_count > 1,
                "running_requests_after_schedule": common["running_requests_after_schedule"],
                "waiting_requests_after_schedule": common["waiting_requests_after_schedule"],
            },
        )
    return {**common, "request_summaries": request_summaries}


def observe_scheduler_iteration_completed(
    state: VllmP2HookState,
    scheduler_output: Any,
    engine_outputs: Any,
) -> None:
    record = state.pop_scheduler_iteration(scheduler_output)
    if record is None:
        return
    completed_ns = time.monotonic_ns()
    request_summaries = list(record.pop("request_summaries", []) or [])
    state.sink.scheduler_iteration(
        "vllm.scheduler.iteration.completed",
        payload={
            **record,
            "execution_completed_ns": completed_ns,
            "schedule_to_execution_complete_ns": max(0, completed_ns - int(record["schedule_end_ns"])),
            "engine_output_request_count": engine_output_request_count(engine_outputs),
        },
    )
    for summary in request_summaries:
        request_id = none_if_empty(summary.get("vllm_request_id"))
        if not request_id:
            continue
        emit_request_payload(
            state,
            "vllm.request.scheduler_iteration_completed",
            {
                "vllm_request_id": request_id,
                "correlation_quality": "server_generated",
                "scheduler_iteration_id": summary.get("scheduler_iteration_id"),
                "scheduler_phase": summary.get("scheduler_phase"),
                "scheduled_token_count": summary.get("scheduled_token_count"),
                "batch_enter_ns": record.get("schedule_end_ns"),
                "batch_exit_ns": completed_ns,
                "batch_residence_ns": max(
                    0,
                    completed_ns - int(record["schedule_end_ns"]),
                ),
                "batch_request_count": record.get("batch_request_count"),
            },
        )
    for summary in request_summaries:
        request_id = none_if_empty(summary.get("vllm_request_id"))
        if not request_id or not summary.get("prefill_completion_candidate"):
            continue
        if not state.mark_seen("seen_prefill_completed", request_id):
            continue
        prompt_tokens = numeric_or_none(summary.get("prompt_tokens"))
        cached_prompt_tokens = numeric_or_none(summary.get("cached_prompt_tokens"))
        accounting = prompt_token_accounting(prompt_tokens, cached_prompt_tokens)
        emit_request_payload(
            state,
            "vllm.request.prefill_completed",
            {
                "vllm_request_id": request_id,
                "correlation_quality": "server_generated",
                "prefill_completed_ns": completed_ns,
                "prefill_boundary_source": "scheduler.update_from_output",
                "prefill_boundary_quality": "engine_execution_observed",
                "scheduler_iteration_id": summary.get("scheduler_iteration_id"),
                "batch_request_count": record.get("batch_request_count"),
                "mixed_prefill_decode_batch": record.get("mixed_prefill_decode_batch"),
                "prompt_tokens": prompt_tokens,
                "cached_prompt_tokens": cached_prompt_tokens,
                **accounting,
            },
        )


def observe_scheduler_output(state: VllmP2HookState, output: Any) -> None:
    for item in getattr(output, "scheduled_new_reqs", []) or []:
        req_id = none_if_empty(getattr(item, "req_id", None))
        if req_id and state.mark_seen("seen_first_scheduled", req_id):
            emit_request_payload(
                state,
                "vllm.request.first_scheduled",
                {
                    "vllm_request_id": req_id,
                    "correlation_quality": "server_generated",
                    "first_scheduled_ns": time.monotonic_ns(),
                    "prompt_tokens": sequence_length(getattr(item, "prompt_token_ids", None)),
                    "computed_prefill_tokens": numeric_or_none(getattr(item, "num_computed_tokens", None)),
                },
            )
    for req_id in getattr(output, "preempted_req_ids", None) or []:
        emit_request_payload(
            state,
            "vllm.request.preempted",
            {
                "vllm_request_id": none_if_empty(req_id),
                "correlation_quality": "server_generated",
                "preemption_count": 1,
            },
        )


def observe_scheduler_stats(
    state: VllmP2HookState,
    stats: Any,
    *,
    prefix_cache_status: str | None = None,
) -> None:
    if stats is None:
        return
    prefix_status = prefix_cache_status or "unknown"
    observe_prefix_cache_stats(
        state,
        getattr(stats, "prefix_cache_stats", None),
        prefix_cache_status=prefix_status,
        kv_usage_ratio=getattr(stats, "kv_cache_usage", None),
    )
    state.sink.scheduler_snapshot(
        running_requests=numeric_or_none(
            first_present(
                getattr(stats, "num_running_reqs", None),
                getattr(stats, "num_running_requests", None),
                getattr(stats, "running_requests", None),
            )
        ),
        waiting_requests=numeric_or_none(
            first_present(
                getattr(stats, "num_waiting_reqs", None),
                getattr(stats, "num_waiting_requests", None),
                getattr(stats, "waiting_requests", None),
            )
        ),
        scheduled_requests=numeric_or_none(
            first_present(
                getattr(stats, "num_scheduled_reqs", None),
                getattr(stats, "num_scheduled_requests", None),
                getattr(stats, "scheduled_requests", None),
            )
        ),
        scheduled_tokens=numeric_or_none(
            first_present(
                getattr(stats, "num_scheduled_tokens", None),
                getattr(stats, "scheduled_tokens", None),
            )
        ),
    )
    eviction_events = getattr(stats, "kv_cache_eviction_events", None) or []
    for event in eviction_events:
        request_id = none_if_empty(
            first_present(
                getattr(event, "request_id", None),
                getattr(event, "req_id", None),
            )
        )
        context = state.enrich_payload({"vllm_request_id": request_id}) if request_id is not None else {}
        state.sink.kv_block_event(
            request_id=request_id,
            evicted_block_count=sequence_length(getattr(event, "block_ids", None)),
            kv_quality="exact",
            agent_request_id=none_if_empty(context.get("agent_request_id")),
            agentmon_run_id=none_if_empty(context.get("agentmon_run_id")),
            agentmon_model_request_id=none_if_empty(context.get("agentmon_model_request_id")),
            api_request_id=none_if_empty(context.get("api_request_id")),
        )


def observe_prefix_cache_stats(
    state: VllmP2HookState,
    stats: Any,
    *,
    prefix_cache_status: str,
    kv_usage_ratio: Any = None,
) -> None:
    queries = numeric_or_none(getattr(stats, "queries", None)) if stats is not None else None
    hits = numeric_or_none(getattr(stats, "hits", None)) if stats is not None else None
    if prefix_cache_status == "disabled":
        queries = None
        hits = None
    state.sink.kv_snapshot(
        prefix_cache_status=prefix_cache_status,
        prefix_cache_queries=queries,
        prefix_cache_hits=hits,
        kv_usage_ratio=numeric_or_none(kv_usage_ratio),
        source="scheduler_stats",
    )


def emit_scheduler_kv_config(
    state: VllmP2HookState,
    scheduler: Any,
    *,
    vllm_config: Any = None,
) -> None:
    cache_config = first_present(
        getattr(scheduler, "cache_config", None),
        getattr(vllm_config, "cache_config", None),
    )
    enabled = boolean_or_none(getattr(cache_config, "enable_prefix_caching", None))
    status = "enabled" if enabled is True else "disabled" if enabled is False else "unknown"
    pool_inputs = kv_pool_inputs_from_scheduler(scheduler)
    state.sink.kv_snapshot(
        prefix_cache_status=status,
        pool_inputs=pool_inputs if any(value is not None for value in pool_inputs.values()) else None,
        source="scheduler_config",
    )


def emit_scheduler_runtime_capability(
    state: VllmP2HookState,
    scheduler: Any,
    *,
    vllm_config: Any = None,
) -> None:
    cache_config = getattr(scheduler, "cache_config", None)
    prefix_cache_enabled = boolean_or_none(getattr(cache_config, "enable_prefix_caching", None))
    prefix_source = "scheduler.cache_config.enable_prefix_caching"
    if prefix_cache_enabled is None:
        prefix_cache_enabled = boolean_or_none(
            first_present(
                attr_chain(vllm_config, ("cache_config", "enable_prefix_caching")),
                attr_chain(scheduler, ("vllm_config", "cache_config", "enable_prefix_caching")),
            )
        )
        prefix_source = "vllm_config.cache_config.enable_prefix_caching"
    async_scheduling_enabled = boolean_or_none(
        first_present(
            attr_chain(vllm_config, ("scheduler_config", "async_scheduling")),
            attr_chain(scheduler, ("vllm_config", "scheduler_config", "async_scheduling")),
        )
    )
    async_source = "vllm_config.scheduler_config.async_scheduling"
    if async_scheduling_enabled is None:
        async_scheduling_enabled = boolean_or_none(
            attr_chain(scheduler, ("scheduler_config", "async_scheduling"))
        )
        async_source = "scheduler.scheduler_config.async_scheduling"

    model_config = first_present(
        attr_chain(vllm_config, ("model_config",)),
        attr_chain(scheduler, ("vllm_config", "model_config")),
        getattr(scheduler, "model_config", None),
    )
    return_routed_experts = boolean_or_none(
        getattr(model_config, "enable_return_routed_experts", None)
    )
    expert_count = discover_moe_expert_count(model_config)
    state.moe_return_routed_experts_enabled = return_routed_experts
    state.moe_expert_count = expert_count

    state.prefix_cache_enabled = prefix_cache_enabled
    state.prefix_cache_config_source = prefix_source
    state.async_scheduling_enabled = async_scheduling_enabled
    state.async_scheduling_config_source = async_source

    missing_required: list[str] = []
    if async_scheduling_enabled is True:
        if not state.engine_core_step_with_batch_queue_hooked:
            missing_required.append("EngineCore.step_with_batch_queue")
    elif not state.engine_core_step_hooked:
        missing_required.append("EngineCore.step")
    prefix_validation = prefix_cache_expectation_status(
        state.prefix_cache_expectation,
        prefix_cache_enabled,
    )
    if prefix_validation == "failed":
        state.prefix_cache_validation_failed = True
        missing_required.append("prefix_cache_configuration")
    if state.moe_mode != "off" and return_routed_experts is not True:
        missing_required.append("model_config.enable_return_routed_experts")
    token_boundary = "supported" if not any(item.startswith("EngineCore.") for item in missing_required) else "unsupported"
    status = "supported" if not missing_required else "partial"
    emit_moe_routing_capability(state)
    state.emit_capability(
        status,
        {
            "capability_phase": "scheduler_runtime_configuration",
            "engine_core_step_present": state.engine_core_step_present,
            "engine_core_step_hooked": state.engine_core_step_hooked,
            "engine_core_step_with_batch_queue_present": state.engine_core_step_with_batch_queue_present,
            "engine_core_step_with_batch_queue_hooked": state.engine_core_step_with_batch_queue_hooked,
            "async_scheduling_enabled": async_scheduling_enabled,
            "async_scheduling_config_source": async_source,
            "engine_token_boundary_collection": token_boundary,
            "missing_required": missing_required,
            "prefix_cache_enabled": prefix_cache_enabled,
            "prefix_cache_config_source": prefix_source,
            "prefix_cache_launch_source": (
                "explicit_cli" if state.prefix_cache_cli_arguments else "engine_args_or_model_default"
            ),
            "prefix_cache_cli_arguments": state.prefix_cache_cli_arguments,
            "prefix_cache_expectation": state.prefix_cache_expectation,
            "prefix_cache_expectation_status": prefix_validation,
            "moe_routing_mode": state.moe_mode,
            "enable_return_routed_experts": return_routed_experts,
            "enable_return_routed_experts_source": (
                "vllm_config.model_config.enable_return_routed_experts"
            ),
            "moe_expert_count": expert_count,
            "moe_expert_count_source": (
                "model_config_or_hf_config" if expert_count is not None else "unavailable"
            ),
        },
    )


def validate_prefix_cache_observation(
    state: VllmP2HookState,
    *,
    cached_prompt_tokens: int | float | None,
    request_id: Any,
) -> None:
    if (
        state.prefix_cache_expectation != "disabled"
        or cached_prompt_tokens is None
        or cached_prompt_tokens <= 0
        or state.prefix_cache_validation_failed
    ):
        return
    state.prefix_cache_validation_failed = True
    state.emit_capability(
        "partial",
        {
            "capability_phase": "runtime_prefix_cache_validation",
            "prefix_cache_enabled": state.prefix_cache_enabled,
            "prefix_cache_config_source": state.prefix_cache_config_source,
            "prefix_cache_expectation": "disabled",
            "prefix_cache_expectation_status": "failed",
            "cached_prompt_tokens_observed": cached_prompt_tokens,
            "vllm_request_id": none_if_empty(request_id),
            "missing_required": ["prefix_cache_disabled_but_cache_hit_observed"],
        },
    )


def emit_cancelled(state: VllmP2HookState, request_id: Any, *, agent_request_id: str | None = None) -> None:
    for raw_request_id in as_request_ids(request_id):
        req_id, api_request_id = state.resolve_engine_request_id(raw_request_id)
        if req_id is None:
            continue
        payload = {
            "agent_request_id": agent_request_id,
            "vllm_request_id": req_id,
            "correlation_quality": "direct" if agent_request_id else "server_generated",
            "finished_ns": time.monotonic_ns(),
            "finish_reason": "cancelled",
        }
        if api_request_id is not None:
            payload["api_request_id"] = api_request_id
        emit_request_payload(
            state,
            "vllm.request.cancelled",
            payload,
        )
        state.clear_request_ids(raw_request_id, req_id)


def emit_request_payload(state: VllmP2HookState, event_type: str, payload: dict[str, Any]) -> None:
    try:
        payload = state.enrich_payload(payload)
        quality = quality_from_correlation(str(payload.get("correlation_quality") or "server_generated"))
        state.sink.emit(event_type, payload=payload, quality=quality)
    except Exception as exc:
        state.emit_error(exc, f"emit:{event_type}")


def observe_moe_routing(
    state: VllmP2HookState,
    output: Any,
    *,
    request_payload_base: dict[str, Any],
    prompt_tokens: int | None,
) -> None:
    if state.moe_mode == "off":
        return
    try:
        events = routed_expert_events(
            output,
            mode=state.moe_mode,
            chunk_tokens=state.moe_chunk_tokens,
            prompt_tokens=prompt_tokens,
            configured_expert_count=state.moe_expert_count,
        )
        for event_type, payload in events:
            emit_request_payload(
                state,
                event_type,
                {**request_payload_base, **payload},
            )
    except Exception as exc:
        state.emit_error(exc, "observe_request_output.moe_routing")


def emit_moe_routing_capability(state: VllmP2HookState) -> None:
    requested = state.moe_mode != "off"
    engine_enabled = state.moe_return_routed_experts_enabled
    status = (
        "DISABLED"
        if not requested
        else "SUPPORTED"
        if engine_enabled is True
        else "UNRESOLVED"
        if engine_enabled is None
        else "UNAVAILABLE"
    )
    limitations = [
        "logical_router_ids_only",
        "physical_rank_mapping_requires_contemporaneous_expert_map",
        "per_expert_kernel_time_not_observed",
    ]
    if requested and engine_enabled is not True:
        limitations.append("enable_return_routed_experts_not_confirmed")
    try:
        state.sink.emit(
            "vllm.moe.routing.capability",
            payload={
                "schema_version": "1.0",
                "status": status,
                "mode": state.moe_mode,
                "mode_source": MOE_MODE_ENV,
                "chunk_tokens": state.moe_chunk_tokens,
                "chunk_tokens_source": MOE_CHUNK_TOKENS_ENV,
                "enable_return_routed_experts": engine_enabled,
                "enable_return_routed_experts_source": (
                    "vllm_config.model_config.enable_return_routed_experts"
                ),
                "expert_count": state.moe_expert_count,
                "expert_count_source": (
                    "model_config_or_hf_config"
                    if state.moe_expert_count is not None
                    else "unavailable"
                ),
                "quality": "runtime_configuration_discovery",
                "limitations": limitations,
                "prohibited_conclusions": [
                    "do_not_infer_physical_expert_or_rank_without_expert_map",
                    "do_not_infer_expert_execution_time_from_route_ids",
                ],
            },
            quality="exact" if engine_enabled is not None else "partial",
        )
    except Exception as exc:
        state.emit_error(exc, "emit:vllm.moe.routing.capability")


def request_payload(request: Any) -> dict[str, Any]:
    trace_headers = getattr(request, "trace_headers", None)
    header_context = agentmon_context_from_headers(trace_headers)
    agent_request_id = header_context.get("agentmon_model_request_id")
    prompt_tokens = numeric_or_none(getattr(request, "num_prompt_tokens", None))
    cached_tokens = numeric_or_none(getattr(request, "num_cached_tokens", None))
    if cached_tokens is not None and cached_tokens < 0:
        cached_tokens = None
    payload = {
        "agent_request_id": agent_request_id,
        "vllm_request_id": none_if_empty(getattr(request, "request_id", None)),
        "correlation_quality": "direct" if agent_request_id else "server_generated",
        "prompt_tokens": prompt_tokens,
        "cached_prompt_tokens": cached_tokens,
        "computed_prefill_tokens": numeric_or_none(getattr(request, "num_computed_tokens", None)),
        "preemption_count": numeric_or_none(getattr(request, "num_preemptions", None)),
    }
    payload.update(header_context)
    return payload


def kv_pool_inputs_from_scheduler(scheduler: Any) -> dict[str, Any]:
    cache_config = getattr(scheduler, "cache_config", None)
    model_config = getattr(scheduler, "vllm_config", None)
    model_config = getattr(model_config, "model_config", None)
    parallel_config = getattr(scheduler, "parallel_config", None)
    return {
        "num_gpu_blocks": getattr(cache_config, "num_gpu_blocks", None),
        "block_size": getattr(scheduler, "block_size", None) or getattr(cache_config, "block_size", None),
        "cache_dtype": getattr(cache_config, "cache_dtype", None),
        "num_layers_per_rank": safe_model_value(model_config, "get_num_layers_by_block_type", parallel_config),
        "num_kv_heads_per_rank": first_present(
            safe_model_value(model_config, "get_num_kv_heads", parallel_config),
            attr_chain(model_config, ("hf_config", "num_key_value_heads")),
            attr_chain(model_config, ("hf_text_config", "num_key_value_heads")),
        ),
        "head_dim": first_present(
            safe_model_value(model_config, "get_head_size"),
            attr_chain(model_config, ("hf_config", "head_dim")),
            attr_chain(model_config, ("hf_text_config", "head_dim")),
        ),
    }


def prefix_status_from_scheduler(scheduler: Any) -> str:
    cache_config = getattr(scheduler, "cache_config", None)
    enabled = boolean_or_none(getattr(cache_config, "enable_prefix_caching", None))
    return "enabled" if enabled is True else "disabled" if enabled is False else "unknown"


def agent_request_id_from_headers(headers: Any) -> str | None:
    return agentmon_context_from_headers(headers).get("agentmon_model_request_id")


def call_argument(
    original: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    name: str,
) -> Any:
    if name in kwargs:
        return kwargs[name]
    try:
        bound = inspect.signature(original).bind_partial(instance, *args, **kwargs)
    except (TypeError, ValueError):
        return None
    return bound.arguments.get(name)


def agentmon_context_from_headers(headers: Any) -> dict[str, str]:
    if not headers:
        return {}
    try:
        items = headers.items()
    except AttributeError:
        return {}
    normalized = {str(key).lower().replace("_", "-"): value for key, value in items}
    context: dict[str, str] = {}
    run_id = normalized.get("x-agentmon-run-id") or normalized.get("agentmon-run-id")
    if run_id not in {None, ""}:
        context["agentmon_run_id"] = str(run_id)
    agent_id = normalized.get("x-agentmon-agent-id") or normalized.get(
        "agentmon-agent-id"
    )
    if agent_id not in {None, ""}:
        context["agent_id"] = str(agent_id)
    for key in (
        "x-agentmon-request-id",
        "agentmon-request-id",
        "agentmon-model-request-id",
        "x-agentmon-model-request-id",
    ):
        value = normalized.get(key)
        if value not in {None, ""}:
            context["agentmon_model_request_id"] = str(value)
            context["agent_request_id"] = str(value)
            break
    model_call_id = normalized.get("x-agentmon-model-call-id") or normalized.get("agentmon-model-call-id")
    client_request_id = normalized.get("x-agentmon-client-request-id") or normalized.get("agentmon-client-request-id")
    if model_call_id not in {None, ""}:
        context["model_call_id"] = str(model_call_id)
    elif context.get("agentmon_model_request_id"):
        context["model_call_id"] = context["agentmon_model_request_id"]
    if client_request_id not in {None, ""}:
        context["client_request_id"] = str(client_request_id)
    elif context.get("model_call_id"):
        context["client_request_id"] = context["model_call_id"]
    return context


def request_id_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    value = kwargs.get("request_id")
    if value is None and args:
        value = args[0]
    return none_if_empty(value)


def request_id_from_generate_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    value = kwargs.get("request_id")
    if value is None and len(args) >= 3:
        value = args[2]
    return none_if_empty(value)


def request_id_from_request_like(value: Any) -> str | None:
    if isinstance(value, str):
        return none_if_empty(value)
    return none_if_empty(first_present(getattr(value, "request_id", None), getattr(value, "req_id", None)))


def as_request_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value if item not in {None, ""}]
    except TypeError:
        text = none_if_empty(value)
        return [text] if text else []


def generation_token_count(output: Any) -> int | None:
    completions = getattr(output, "outputs", None)
    if completions is None:
        return None
    total = 0
    found = False
    for item in completions:
        token_ids = getattr(item, "token_ids", None)
        length = sequence_length(token_ids)
        if length is not None:
            total += length
            found = True
    return total if found else None


def generation_tokens_from_usage(output: Any) -> int | None:
    usage = getattr(output, "usage", None)
    if usage is None and isinstance(output, dict):
        usage = output.get("usage")
    for key in ("completion_tokens", "output_tokens", "generation_tokens"):
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        normalized = numeric_or_none(value)
        if normalized is not None and normalized >= 0:
            return int(normalized)
    return None


def engine_core_new_token_count(output: Any) -> int:
    """Return tokens produced by one EngineCore step, not cumulative output."""
    for attr_name in ("new_token_ids", "sampled_token_ids", "token_ids"):
        length = sequence_length(getattr(output, attr_name, None))
        if length is not None:
            return length
    return 0


def prompt_token_count(output: Any) -> int | None:
    return sequence_length(getattr(output, "prompt_token_ids", None))


def prompt_token_accounting(prompt_tokens: Any, cached_prompt_tokens: Any) -> dict[str, Any]:
    prompt = numeric_or_none(prompt_tokens)
    cached = numeric_or_none(cached_prompt_tokens)
    if prompt is None or cached is None or prompt < 0 or cached < 0:
        return {
            "new_prompt_tokens": None,
            "uncached_prompt_tokens": None,
            "computed_prefill_tokens": None,
            "recomputed_prompt_tokens": None,
            "recomputed_token_quality": "unavailable_not_observed",
            "prompt_token_accounting_quality": "unavailable",
        }
    uncached = max(0, int(prompt) - min(int(prompt), int(cached)))
    return {
        "new_prompt_tokens": uncached,
        "uncached_prompt_tokens": uncached,
        "computed_prefill_tokens": uncached,
        "recomputed_prompt_tokens": None,
        "recomputed_token_quality": "unavailable_not_observed",
        "prompt_token_accounting_quality": "derived_prompt_minus_cached",
    }


def finish_reason_from_output(output: Any) -> str | None:
    completions = getattr(output, "outputs", None) or []
    for item in completions:
        reason = getattr(item, "finish_reason", None)
        if reason is not None:
            return safe_str(reason)
    return safe_str(getattr(output, "finish_reason", None))


def model_name_from_engine(engine: Any) -> str | None:
    model_config = getattr(engine, "model_config", None)
    return none_if_empty(
        first_present(
            getattr(model_config, "served_model_name", None),
            getattr(model_config, "model", None),
        )
    )


def safe_model_value(model_config: Any, method_name: str, *args: Any) -> Any:
    method = getattr(model_config, method_name, None)
    if method is None:
        return None
    try:
        return method(*[arg for arg in args if arg is not None])
    except Exception:
        try:
            return method()
        except Exception:
            return None


def discover_moe_expert_count(model_config: Any) -> int | None:
    candidates = [
        model_config,
        getattr(model_config, "hf_config", None),
        getattr(model_config, "text_config", None),
        attr_chain(model_config, ("hf_config", "text_config")),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        for name in (
            "num_experts",
            "n_routed_experts",
            "num_local_experts",
            "moe_num_experts",
        ):
            value = numeric_or_none(getattr(candidate, name, None))
            if value is not None and value > 0:
                return int(value)
    return None


def attr_chain(obj: Any, names: tuple[str, ...]) -> Any:
    value = obj
    for name in names:
        value = getattr(value, name, None)
        if value is None:
            return None
    return value


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and not (isinstance(value, str) and value == ""):
            return value
    return None


def sequence_length(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return len(value)
    except TypeError:
        return None


def scheduler_scheduled_tokens(output: Any) -> dict[str, int]:
    raw = getattr(output, "num_scheduled_tokens", None)
    if isinstance(raw, dict):
        result: dict[str, int] = {}
        for request_id, value in raw.items():
            count = numeric_or_none(value)
            if count is not None and count >= 0:
                result[str(request_id)] = int(count)
        return result
    result = {}
    for item in getattr(output, "scheduled_new_reqs", None) or []:
        request_id = none_if_empty(getattr(item, "req_id", None))
        prompt_tokens = sequence_length(getattr(item, "prompt_token_ids", None))
        if request_id is not None and prompt_tokens is not None:
            result[request_id] = prompt_tokens
    return result


def scheduler_waiting_count(scheduler: Any) -> int | None:
    counts = []
    for name in ("waiting", "skipped_waiting"):
        count = sequence_length(getattr(scheduler, name, None))
        if count is not None:
            counts.append(count)
    return sum(counts) if counts else None


def normalized_cached_tokens(value: Any) -> int | float | None:
    cached = numeric_or_none(value)
    if cached is None or cached < 0:
        return None
    return cached


def engine_output_request_count(engine_outputs: Any) -> int | None:
    if not isinstance(engine_outputs, dict):
        return None
    count = 0
    found = False
    for rank_output in engine_outputs.values():
        outputs = getattr(rank_output, "outputs", None)
        length = sequence_length(outputs)
        if length is not None:
            count += length
            found = True
    return count if found else None


def kv_block_count(value: Any) -> int | None:
    if value is None:
        return None
    for method_name in ("get_block_ids", "block_ids"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return kv_block_count(method())
            except TypeError:
                try:
                    return kv_block_count(method(allow_none=True))
                except Exception:
                    pass
            except Exception:
                pass
    for attr_name in ("block_ids", "blocks", "block_hashes"):
        attr = getattr(value, attr_name, None)
        if attr is not None:
            return kv_block_count(attr)
    if isinstance(value, dict):
        return sum(count for item in value.values() if (count := kv_block_count(item)) is not None)
    if isinstance(value, (str, bytes, bytearray)):
        return None
    try:
        items = list(value)
    except TypeError:
        return None
    if not items:
        return 0
    nested_counts = [kv_block_count(item) for item in items]
    if any(count is not None for count in nested_counts):
        return sum(count or 0 for count in nested_counts)
    return len(items)


def kv_blocks_for_request(manager: Any, request: Any) -> int | None:
    request_id = request_id_from_request_like(request)
    if request_id is None:
        return None
    for method_name in ("get_block_ids", "get_blocks"):
        method = getattr(manager, method_name, None)
        if not callable(method):
            continue
        try:
            return kv_block_count(method(request_id))
        except Exception:
            continue
    return None


def numeric_or_none(value: Any) -> int | float | None:
    if value is None or (isinstance(value, str) and value == ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def boolean_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def normalize_output_kind(value: Any) -> str:
    if value is None:
        return "unknown"
    candidate = getattr(value, "name", value)
    normalized = str(candidate).strip().lower().rsplit(".", 1)[-1]
    aliases = {
        "delta": "delta",
        "cumulative": "cumulative",
        "final_only": "final_only",
        "final-only": "final_only",
    }
    return aliases.get(normalized, "unknown")


def none_if_empty(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and value == ""):
        return None
    return str(value)


def safe_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
