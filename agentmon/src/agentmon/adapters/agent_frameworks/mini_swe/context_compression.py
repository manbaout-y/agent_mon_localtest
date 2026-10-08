from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import math
from typing import Any

from agentmon.config import AgentMonConfig
from agentmon.context_compression import (
    ContextCompressionDecision,
    ContextCompressionService,
    ContextSummaryRequest,
    ContextSummaryResult,
)


INSTRUCTION_ROLES = {"system", "developer"}
TOOL_RESULT_ROLES = {"tool", "observation"}


class MiniSweContextCompressor:
    """Translate mini-SWE messages into framework-neutral compression groups."""

    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg
        self.service = ContextCompressionService(cfg)

    def prepare(
        self,
        messages: Any,
        *,
        summary_provider: Callable[[ContextSummaryRequest], ContextSummaryResult]
        | None = None,
    ) -> ContextCompressionDecision:
        return self.service.prepare(
            messages,
            metadata=build_message_compression_metadata(
                messages,
                self.cfg,
                summary_provider=summary_provider,
            ),
        )


def build_message_compression_metadata(
    messages: Any,
    cfg: AgentMonConfig,
    *,
    summary_provider: Callable[[ContextSummaryRequest], ContextSummaryResult]
    | None = None,
) -> dict[str, Any]:
    if not is_message_sequence(messages):
        return {
            "measurement_quality": "UNRESOLVED",
            "measurement_limitations": ["mini_swe_messages_not_a_sequence"],
        }
    groups, required_group_ids = group_mini_swe_messages(messages)
    chars_per_token = cfg.context_compression.estimator_chars_per_token
    metadata = {
        "groups": groups,
        "required_group_ids": required_group_ids,
        "item_token_estimates": [
            estimate_message_tokens(message, chars_per_token)
            for message in messages
        ],
        "measurement_quality": f"estimated_serialized_chars_div_{chars_per_token:g}",
        "measurement_limitations": [
            "model_tokenizer_not_invoked",
            "chat_template_overhead_not_exact",
        ],
    }
    if summary_provider is not None:
        metadata.update(
            {
                "summary_provider": summary_provider,
                "summary_item_factory": summary_message,
                "summary_item_estimator": lambda item: estimate_message_tokens(
                    item,
                    chars_per_token,
                ),
            }
        )
    return metadata


def summary_message(summary: str) -> dict[str, str]:
    return {
        "role": "user",
        "content": "Earlier context summary for continuation:\n" + summary.strip(),
    }


def group_mini_swe_messages(
    messages: Sequence[Any],
) -> tuple[list[list[int]], list[int]]:
    groups: list[list[int]] = []
    required_group_ids: list[int] = []
    index = 0
    while index < len(messages) and message_role(messages[index]) in INSTRUCTION_ROLES:
        required_group_ids.append(len(groups))
        groups.append([index])
        index += 1

    if index < len(messages):
        # mini-SWE's first non-instruction message carries the task contract.
        required_group_ids.append(len(groups))
        groups.append([index])
        index += 1

    while index < len(messages):
        group = [index]
        role = message_role(messages[index])
        index += 1
        if role == "assistant":
            while index < len(messages) and message_role(messages[index]) in TOOL_RESULT_ROLES:
                group.append(index)
                index += 1
        groups.append(group)
    return groups, required_group_ids


def estimate_message_tokens(message: Any, chars_per_token: float) -> int:
    try:
        serialized = json.dumps(
            message,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        serialized = str(message)
    return max(1, int(math.ceil(len(serialized) / chars_per_token)))


def message_role(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("role") or "unknown").strip().lower()
    return "unknown"


def is_message_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )
