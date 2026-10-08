"""P2 adapter helpers for vLLM 0.18.x.

This package intentionally does not import or patch vLLM at import time. The
runtime hook layer should be added only after target-server discovery confirms
the exact vLLM 0.18.0 internals.
"""

from importlib import import_module

# Offline readers/version checks must not eagerly load Linux procfs or hook.py.
# Public exports and explicit hook installation behavior remain the same.
_EXPORTS = {
    "VllmP2EventSink": ("events", "VllmP2EventSink"),
    "Vllm018InferenceTelemetryProvider": ("adapter", "Vllm018InferenceTelemetryProvider"),
    "provider": ("adapter", "provider"),
    "install_vllm_p2_hooks": ("hook", "install"),
    "install_vllm_p2_hooks_from_env": ("hook", "install_from_env"),
    "calculate_kv_pool": ("kv", "calculate_kv_pool"),
}
for _module, _names in {
    "importer": ("is_p2_global_event", "is_p2_run_capability_event", "normalize_imported_p2_event", "summarize_p2_imported_events"),
    "p2_index": ("build_or_load_p2_batch_index", "build_p2_batch_index", "filter_p2_events_for_response_ids", "p2_service_bounds",
                 "read_jsonl_events", "select_indexed_p2_events", "write_jsonl_events"),
    "version": ("UnsupportedVllmVersion", "is_supported_vllm_version", "require_supported_vllm_version"),
}.items():
    _EXPORTS.update({name: (_module, name) for name in _names})


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(f"{__name__}.{module}"), attribute)
    globals()[name] = value
    return value

__all__ = [
    "UnsupportedVllmVersion",
    "Vllm018InferenceTelemetryProvider",
    "VllmP2EventSink",
    "build_or_load_p2_batch_index",
    "build_p2_batch_index",
    "calculate_kv_pool",
    "filter_p2_events_for_response_ids",
    "install_vllm_p2_hooks",
    "install_vllm_p2_hooks_from_env",
    "is_p2_global_event",
    "is_p2_run_capability_event",
    "is_supported_vllm_version",
    "p2_service_bounds",
    "normalize_imported_p2_event",
    "provider",
    "read_jsonl_events",
    "require_supported_vllm_version",
    "select_indexed_p2_events",
    "summarize_p2_imported_events",
    "write_jsonl_events",
]
