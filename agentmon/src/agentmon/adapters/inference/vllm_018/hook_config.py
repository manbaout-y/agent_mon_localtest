from __future__ import annotations

import os
import sys
from typing import Any


ENABLE_ENV = "AGENTMON_VLLM_P2_ENABLE"
HOT_PATH_ENV = "AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS"
KV_BLOCKS_ENV = "AGENTMON_VLLM_P2_HOOK_KV_BLOCKS"
DEEP_ENV = "AGENTMON_VLLM_P2_DEEP"
DEEP_MAX_BATCH_REQUESTS_ENV = "AGENTMON_VLLM_P2_DEEP_MAX_BATCH_REQUESTS"
PREFIX_CACHE_EXPECTATION_ENV = "AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE"
INSTALLED_ATTR = "__agentmon_p2_wrapped__"
ORIGINAL_ATTR = "__agentmon_p2_original__"
MAX_REQUEST_ID_ALIASES = 4096
MAX_REQUEST_STATE_ENTRIES = 4096
MAX_SCHEDULER_ITERATIONS = 1024


def deep_batch_request_limit() -> int:
    raw = os.environ.get(DEEP_MAX_BATCH_REQUESTS_ENV, "256")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 256
    return max(1, min(value, 4096))


def normalized_prefix_cache_expectation(value: Any) -> str:
    normalized = str(value or "any").strip().lower()
    return normalized if normalized in {"any", "enabled", "disabled"} else "any"


def prefix_cache_cli_arguments_from_process() -> list[str]:
    return [
        argument
        for argument in sys.argv[1:]
        if argument.startswith("--")
        and "prefix" in argument.lower()
        and "cach" in argument.lower()
    ][:16]


def prefix_cache_expectation_status(expectation: str, actual: bool | None) -> str:
    if expectation == "any":
        return "not_requested"
    if actual is None:
        return "unavailable"
    return "passed" if actual is (expectation == "enabled") else "failed"


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def environment_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return truthy(value)
