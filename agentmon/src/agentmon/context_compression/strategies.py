from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agentmon.config import AgentMonConfig

from .types import (
    ContextCompressionRequest,
    ContextCompressionResult,
    ContextCompressionTriggerResult,
    ContextMeasurement,
    ContextSummaryRequest,
    ContextSummaryResult,
)


DEFAULT_SUMMARY_INSTRUCTION = """Create a compact continuation summary of the earlier coding-agent context.
Preserve the task objective, explicit constraints, decisions, repository facts, files changed, commands and tools used,
test outcomes, unresolved errors, pending work, and any exact identifiers needed to continue safely.
Do not invent facts. Clearly retain unknowns and failed attempts. Return only the continuation summary."""


class TokenRatioContextCompressionTrigger:
    """Trigger when the measured context reaches the configured window ratio."""

    name = "token_ratio"

    def __init__(
        self,
        cfg: AgentMonConfig,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self.options = dict(options or {})

    def evaluate(
        self,
        request: ContextCompressionRequest,
    ) -> ContextCompressionTriggerResult:
        token_count = request.measurement.token_count
        if token_count is None:
            return ContextCompressionTriggerResult(
                triggered=None,
                reason="context_token_measurement_unavailable",
                limitations=("compression_not_applied",),
            )
        if token_count < request.trigger_tokens:
            return ContextCompressionTriggerResult(
                triggered=False,
                reason="context_below_trigger",
            )
        return ContextCompressionTriggerResult(
            triggered=True,
            reason="context_reached_token_ratio_trigger",
        )


class RecentGroupsContextCompression:
    """Keep required groups and the newest complete context groups."""

    name = "recent_groups"

    def __init__(
        self,
        cfg: AgentMonConfig,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self.options = dict(options or {})
        self.min_recent_groups = positive_int(
            self.options.get("min_recent_groups"),
            default=1,
        )

    def measure(
        self,
        context: Any,
        metadata: Mapping[str, Any],
    ) -> ContextMeasurement:
        if not is_sequence(context):
            return ContextMeasurement(
                token_count=None,
                item_count=None,
                quality="UNRESOLVED",
                limitations=("context_is_not_a_sequence",),
            )
        estimates = metadata.get("item_token_estimates")
        if not isinstance(estimates, Sequence) or isinstance(
            estimates, (str, bytes, bytearray)
        ):
            return ContextMeasurement(
                token_count=None,
                item_count=len(context),
                quality="UNRESOLVED",
                limitations=("item_token_estimates_unavailable",),
            )
        values = [nonnegative_int(value) for value in estimates]
        if len(values) != len(context) or any(value is None for value in values):
            return ContextMeasurement(
                token_count=None,
                item_count=len(context),
                quality="UNRESOLVED",
                limitations=("item_token_estimates_incomplete",),
            )
        return ContextMeasurement(
            token_count=sum(int(value) for value in values if value is not None),
            item_count=len(context),
            quality=str(metadata.get("measurement_quality") or "estimated"),
            limitations=tuple(
                str(value)
                for value in metadata.get("measurement_limitations", ())
                if value not in {None, ""}
            ),
        )

    def compress(
        self,
        request: ContextCompressionRequest,
    ) -> ContextCompressionResult:
        context = request.context
        if not is_sequence(context):
            return ContextCompressionResult(
                context=context,
                reason="context_is_not_a_sequence",
                limitations=("compression_not_applied",),
            )
        groups = normalized_groups(request.metadata.get("groups"), len(context))
        estimates = normalized_estimates(
            request.metadata.get("item_token_estimates"),
            len(context),
        )
        if groups is None or estimates is None:
            return ContextCompressionResult(
                context=context,
                reason="adapter_group_metadata_unavailable",
                limitations=("compression_not_applied",),
            )

        required_group_ids = {
            int(value)
            for value in request.metadata.get("required_group_ids", ())
            if integer_or_none(value) is not None
        }
        selected_groups = set(required_group_ids)
        selected_tokens = sum(
            group_tokens(group, estimates)
            for group_id, group in enumerate(groups)
            if group_id in selected_groups
        )
        recent_added = 0
        for group_id in range(len(groups) - 1, -1, -1):
            if group_id in selected_groups:
                continue
            tokens = group_tokens(groups[group_id], estimates)
            if (
                recent_added < self.min_recent_groups
                or selected_tokens + tokens <= request.target_tokens
            ):
                selected_groups.add(group_id)
                selected_tokens += tokens
                recent_added += 1
            else:
                break

        selected_indices = sorted(
            index
            for group_id, group in enumerate(groups)
            if group_id in selected_groups
            for index in group
        )
        compressed = [context[index] for index in selected_indices]
        limitations = [
            "semantic_summary_not_generated",
            "removed_context_is_recoverable_only_from_agent_trajectory",
        ]
        if selected_tokens > request.target_tokens:
            limitations.append("required_or_recent_groups_exceed_target")
        return ContextCompressionResult(
            context=compressed,
            reason="required_prefix_and_recent_groups_retained",
            limitations=tuple(limitations),
        )


class ModelSummaryContextCompression(RecentGroupsContextCompression):
    """Replace older complete context groups with a model-generated summary."""

    name = "model_summary"

    def __init__(
        self,
        cfg: AgentMonConfig,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(cfg, options)
        self.instruction = str(
            self.options.get("instruction") or DEFAULT_SUMMARY_INSTRUCTION
        )
        model = self.options.get("model")
        self.model = str(model) if model not in {None, ""} else None

    def compress(
        self,
        request: ContextCompressionRequest,
    ) -> ContextCompressionResult:
        context = request.context
        if not is_sequence(context):
            return ContextCompressionResult(
                context=context,
                reason="context_is_not_a_sequence",
                limitations=("compression_not_applied",),
            )
        groups = normalized_groups(request.metadata.get("groups"), len(context))
        estimates = normalized_estimates(
            request.metadata.get("item_token_estimates"),
            len(context),
        )
        provider = request.metadata.get("summary_provider")
        item_factory = request.metadata.get("summary_item_factory")
        if (
            groups is None
            or estimates is None
            or not isinstance(provider, Callable)
            or not isinstance(item_factory, Callable)
        ):
            return ContextCompressionResult(
                context=context,
                reason="summary_provider_or_group_metadata_unavailable",
                limitations=("compression_not_applied",),
            )

        required_group_ids = {
            int(value)
            for value in request.metadata.get("required_group_ids", ())
            if integer_or_none(value) is not None
        }
        recent_group_ids: set[int] = set()
        for group_id in range(len(groups) - 1, -1, -1):
            if group_id in required_group_ids:
                continue
            recent_group_ids.add(group_id)
            if len(recent_group_ids) >= self.min_recent_groups:
                break
        retained_group_ids = required_group_ids | recent_group_ids
        removed_group_ids = [
            group_id
            for group_id in range(len(groups))
            if group_id not in retained_group_ids
        ]
        if not removed_group_ids:
            return ContextCompressionResult(
                context=context,
                reason="no_older_context_groups_available_for_summary",
                limitations=("compression_not_applied",),
            )

        retained_indices = sorted(
            index
            for group_id, group in enumerate(groups)
            if group_id in retained_group_ids
            for index in group
        )
        removed_indices = sorted(
            index
            for group_id, group in enumerate(groups)
            if group_id in removed_group_ids
            for index in group
        )
        retained_tokens = sum(estimates[index] for index in retained_indices)
        summary_budget = max(1, request.target_tokens - retained_tokens)
        summary_request = ContextSummaryRequest(
            context=[context[index] for index in removed_indices],
            instruction=self.instruction,
            target_tokens=summary_budget,
            model=self.model,
            metadata={
                "removed_item_count": len(removed_indices),
                "removed_group_count": len(removed_group_ids),
            },
        )
        summary = provider(summary_request)
        if not isinstance(summary, ContextSummaryResult):
            raise TypeError("summary_provider_must_return_ContextSummaryResult")
        if summary.status != "COMPLETE" or not summary.summary.strip():
            raise RuntimeError("summary_provider_did_not_return_complete_summary")
        summary_item = item_factory(summary.summary)
        insertion_index = min(removed_indices)
        compressed: list[Any] = []
        inserted = False
        retained_set = set(retained_indices)
        for index, item in enumerate(context):
            if not inserted and index >= insertion_index:
                compressed.append(summary_item)
                inserted = True
            if index in retained_set:
                compressed.append(item)
        if not inserted:
            compressed.append(summary_item)

        summary_tokens = summary.output_tokens
        summary_quality = summary.token_quality
        limitations = list(summary.limitations)
        if summary_tokens is None:
            estimator = request.metadata.get("summary_item_estimator")
            if isinstance(estimator, Callable):
                summary_tokens = nonnegative_int(estimator(summary_item))
                summary_quality = "estimated_summary_item"
                limitations.append("summary_output_token_usage_unavailable")
        measurement = ContextMeasurement(
            token_count=(
                retained_tokens + summary_tokens
                if summary_tokens is not None
                else None
            ),
            item_count=len(compressed),
            quality=(
                f"mixed_{request.measurement.quality}_and_{summary_quality}"
                if summary_tokens is not None
                else "UNRESOLVED"
            ),
            limitations=tuple(
                dict.fromkeys(
                    (
                        *request.measurement.limitations,
                        *limitations,
                        "chat_template_overhead_after_summary_not_exact",
                    )
                )
            ),
        )
        return ContextCompressionResult(
            context=compressed,
            reason="older_context_replaced_by_model_summary",
            limitations=(
                *summary.limitations,
                "model_summary_semantic_equivalence_not_verified",
                "removed_context_is_recoverable_only_from_agent_trajectory",
            ),
            measurement=measurement,
            summary=summary,
        )


def normalized_groups(value: Any, item_count: int) -> list[list[int]] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    groups: list[list[int]] = []
    seen: set[int] = set()
    for raw_group in value:
        if not isinstance(raw_group, Sequence) or isinstance(
            raw_group, (str, bytes, bytearray)
        ):
            return None
        group: list[int] = []
        for raw_index in raw_group:
            index = integer_or_none(raw_index)
            if index is None or not 0 <= index < item_count or index in seen:
                return None
            seen.add(index)
            group.append(index)
        if group:
            groups.append(group)
    return groups if seen == set(range(item_count)) else None


def normalized_estimates(value: Any, item_count: int) -> list[int] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    estimates = [nonnegative_int(item) for item in value]
    if len(estimates) != item_count or any(item is None for item in estimates):
        return None
    return [int(item) for item in estimates if item is not None]


def group_tokens(group: list[int], estimates: list[int]) -> int:
    return sum(estimates[index] for index in group)


def is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def nonnegative_int(value: Any) -> int | None:
    parsed = integer_or_none(value)
    return parsed if parsed is not None and parsed >= 0 else None


def positive_int(value: Any, *, default: int) -> int:
    parsed = integer_or_none(value)
    return parsed if parsed is not None and parsed >= 1 else default
