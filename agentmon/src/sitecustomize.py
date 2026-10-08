from __future__ import annotations

import os
import sys


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


if _truthy(os.environ.get("AGENTMON_VLLM_P2_ENABLE")):
    try:
        from agentmon.adapters.inference.vllm_018.hook import install_from_env

        install_from_env()
    except Exception as exc:
        print(f"agentmon vllm p2 hook bootstrap failed: {exc}", file=sys.stderr)
