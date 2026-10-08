from __future__ import annotations

from uuid import uuid4


def new_span_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def new_step_id() -> str:
    return new_span_id("step")


def new_request_id() -> str:
    return new_span_id("model")


def new_tool_call_id() -> str:
    return new_span_id("tool")
