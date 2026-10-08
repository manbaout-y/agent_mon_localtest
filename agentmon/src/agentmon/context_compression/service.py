from __future__ import annotations

import math
import time
from typing import Any, Mapping

from agentmon.config import AgentMonConfig

from .registry import (
    ContextCompressionStrategy,
    ContextCompressionTrigger,
    build_context_compression_strategy,
    build_context_compression_trigger,
)
from .types import (
    ContextCompressionDecision,
    ContextCompressionRequest,
    ContextMeasurement,
)


class ContextCompressionService:
    def __init__(
        self,
        cfg: AgentMonConfig,
        *,
        strategy: ContextCompressionStrategy | None = None,
        trigger: ContextCompressionTrigger | None = None,
    ) -> None:
        self.cfg = cfg
        self._strategy = strategy
        self._trigger = trigger

    def prepare(
        self,
        context: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ContextCompressionDecision:
        configured = self.cfg.context_compression.enabled
        effective = configured and self.cfg.runtime_optimization.enabled
        empty = ContextMeasurement(None, item_count_or_none(context), "UNRESOLVED")
        if not effective:
            return self._decision(
                context=context,
                status="DISABLED",
                reason=(
                    "context_compression_disabled"
                    if not configured
                    else "runtime_optimization_disabled"
                ),
                original=empty,
                compressed=empty,
                window_tokens=resolve_window_tokens(self.cfg),
                target_achieved=None,
            )

        try:
            strategy = self._strategy or build_context_compression_strategy(self.cfg)
            self._strategy = strategy
        except Exception as exc:
            return self._decision(
                context=context,
                status="ERROR_BYPASS",
                reason=f"strategy_load_failed:{type(exc).__name__}",
                original=empty,
                compressed=empty,
                window_tokens=resolve_window_tokens(self.cfg),
                target_achieved=None,
                limitations=("original_context_forwarded",),
            )

        try:
            trigger = self._trigger or build_context_compression_trigger(self.cfg)
            self._trigger = trigger
        except Exception as exc:
            return self._decision(
                context=context,
                status="ERROR_BYPASS",
                reason=f"trigger_load_failed:{type(exc).__name__}",
                original=empty,
                compressed=empty,
                window_tokens=resolve_window_tokens(self.cfg),
                target_achieved=None,
                limitations=("original_context_forwarded",),
            )

        metadata_map = dict(metadata or {})
        try:
            original = strategy.measure(context, metadata_map)
        except Exception as exc:
            return self._decision(
                context=context,
                status="ERROR_BYPASS",
                reason=f"measurement_failed:{type(exc).__name__}",
                original=empty,
                compressed=empty,
                window_tokens=resolve_window_tokens(self.cfg),
                target_achieved=None,
                limitations=("original_context_forwarded",),
            )

        window_tokens = resolve_window_tokens(self.cfg)
        if window_tokens is None:
            return self._decision(
                context=context,
                status="UNRESOLVED",
                reason="model_context_window_unavailable",
                original=original,
                compressed=original,
                window_tokens=None,
                target_achieved=None,
                limitations=("compression_not_applied",),
            )
        trigger_tokens = max(
            1,
            int(math.ceil(window_tokens * self.cfg.context_compression.trigger_ratio)),
        )
        target_tokens = max(
            1,
            int(math.floor(window_tokens * self.cfg.context_compression.target_ratio)),
        )
        request = ContextCompressionRequest(
            context=context,
            window_tokens=window_tokens,
            trigger_tokens=trigger_tokens,
            target_tokens=target_tokens,
            measurement=original,
            metadata=metadata_map,
        )
        try:
            trigger_result = trigger.evaluate(request)
        except Exception as exc:
            return self._decision(
                context=context,
                status="ERROR_BYPASS",
                reason=f"trigger_failed:{type(exc).__name__}",
                original=original,
                compressed=original,
                window_tokens=window_tokens,
                trigger_tokens=trigger_tokens,
                target_tokens=target_tokens,
                target_achieved=None,
                limitations=("original_context_forwarded",),
            )
        if trigger_result.triggered is None:
            return self._decision(
                context=context,
                status="UNRESOLVED",
                reason=trigger_result.reason,
                original=original,
                compressed=original,
                window_tokens=window_tokens,
                trigger_tokens=trigger_tokens,
                target_tokens=target_tokens,
                target_achieved=None,
                trigger_reason=trigger_result.reason,
                limitations=(
                    *original.limitations,
                    *trigger_result.limitations,
                ),
            )
        if not trigger_result.triggered:
            return self._decision(
                context=context,
                status="NOT_TRIGGERED",
                reason=trigger_result.reason,
                original=original,
                compressed=original,
                window_tokens=window_tokens,
                trigger_tokens=trigger_tokens,
                target_tokens=target_tokens,
                target_achieved=None,
                trigger_reason=trigger_result.reason,
                limitations=(
                    *original.limitations,
                    *trigger_result.limitations,
                ),
            )
        compression_started_mono_ns = time.monotonic_ns()
        try:
            result = strategy.compress(request)
            compressed = result.measurement or strategy.measure(
                result.context,
                metadata_for_result(metadata_map, context, result.context),
            )
        except Exception as exc:
            compression_ended_mono_ns = time.monotonic_ns()
            return self._decision(
                context=context,
                status="ERROR_BYPASS",
                reason=f"compression_failed:{type(exc).__name__}",
                original=original,
                compressed=original,
                window_tokens=window_tokens,
                trigger_tokens=trigger_tokens,
                target_tokens=target_tokens,
                target_achieved=None,
                trigger_reason=trigger_result.reason,
                started_mono_ns=compression_started_mono_ns,
                ended_mono_ns=compression_ended_mono_ns,
                duration_s=(
                    compression_ended_mono_ns - compression_started_mono_ns
                )
                / 1_000_000_000,
                limitations=("original_context_forwarded",),
            )
        compression_ended_mono_ns = time.monotonic_ns()

        if compressed.token_count is None:
            status = "PARTIAL"
            target_achieved = None
        else:
            target_achieved = compressed.token_count <= target_tokens
            status = "COMPLETE" if target_achieved else "PARTIAL"
        if (
            compressed.token_count is not None
            and original.token_count is not None
            and compressed.token_count > original.token_count
        ):
            return self._decision(
                context=context,
                status="ERROR_BYPASS",
                reason="compressed_context_larger_than_original",
                original=original,
                compressed=original,
                window_tokens=window_tokens,
                trigger_tokens=trigger_tokens,
                target_tokens=target_tokens,
                target_achieved=False,
                trigger_reason=trigger_result.reason,
                started_mono_ns=compression_started_mono_ns,
                ended_mono_ns=compression_ended_mono_ns,
                duration_s=(
                    compression_ended_mono_ns - compression_started_mono_ns
                )
                / 1_000_000_000,
                summary=result.summary,
                limitations=("original_context_forwarded",),
            )
        return self._decision(
            context=result.context,
            status=status,
            reason=result.reason,
            original=original,
            compressed=compressed,
            window_tokens=window_tokens,
            trigger_tokens=trigger_tokens,
            target_tokens=target_tokens,
            target_achieved=target_achieved,
            trigger_reason=trigger_result.reason,
            started_mono_ns=compression_started_mono_ns,
            ended_mono_ns=compression_ended_mono_ns,
            duration_s=(
                compression_ended_mono_ns - compression_started_mono_ns
            )
            / 1_000_000_000,
            summary=result.summary,
            limitations=(
                *original.limitations,
                *trigger_result.limitations,
                *result.limitations,
            ),
        )

    def _decision(
        self,
        *,
        context: Any,
        status: str,
        reason: str,
        original: ContextMeasurement,
        compressed: ContextMeasurement,
        window_tokens: int | None,
        target_achieved: bool | None,
        trigger_reason: str | None = None,
        trigger_tokens: int | None = None,
        target_tokens: int | None = None,
        started_mono_ns: int | None = None,
        ended_mono_ns: int | None = None,
        duration_s: float | None = None,
        summary: Any = None,
        limitations: tuple[str, ...] = (),
    ) -> ContextCompressionDecision:
        return ContextCompressionDecision(
            context=context,
            status=status,
            reason=reason,
            trigger_reason=trigger_reason,
            trigger_strategy=self.cfg.context_compression.trigger_strategy,
            strategy=self.cfg.context_compression.strategy,
            configured_enabled=self.cfg.context_compression.enabled,
            effective_enabled=(
                self.cfg.context_compression.enabled
                and self.cfg.runtime_optimization.enabled
            ),
            window_tokens=window_tokens,
            trigger_ratio=self.cfg.context_compression.trigger_ratio,
            target_ratio=self.cfg.context_compression.target_ratio,
            trigger_tokens=trigger_tokens,
            target_tokens=target_tokens,
            original=original,
            compressed=compressed,
            target_achieved=target_achieved,
            started_mono_ns=started_mono_ns,
            ended_mono_ns=ended_mono_ns,
            duration_s=duration_s,
            summary=summary,
            limitations=limitations,
        )


def resolve_window_tokens(cfg: AgentMonConfig) -> int | None:
    configured = positive_int_or_none(cfg.context_compression.window_tokens)
    return configured or positive_int_or_none(cfg.model.max_model_len)


def item_count_or_none(context: Any) -> int | None:
    try:
        return len(context)
    except (TypeError, AttributeError):
        return None


def positive_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value in {None, ""}:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def metadata_for_result(
    metadata: dict[str, Any],
    original_context: Any,
    compressed_context: Any,
) -> dict[str, Any]:
    original_items = list(original_context) if is_sequence(original_context) else []
    compressed_items = (
        list(compressed_context) if is_sequence(compressed_context) else []
    )
    estimates = metadata.get("item_token_estimates")
    if not isinstance(estimates, list) or len(estimates) != len(original_items):
        return metadata
    positions: dict[int, list[int]] = {}
    for index, item in enumerate(original_items):
        positions.setdefault(id(item), []).append(index)
    selected_estimates: list[int] = []
    used: dict[int, int] = {}
    for item in compressed_items:
        item_positions = positions.get(id(item), [])
        offset = used.get(id(item), 0)
        if offset >= len(item_positions):
            return metadata
        selected_estimates.append(estimates[item_positions[offset]])
        used[id(item)] = offset + 1
    updated = dict(metadata)
    updated["item_token_estimates"] = selected_estimates
    return updated


def is_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple))
