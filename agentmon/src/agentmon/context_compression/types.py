from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ContextMeasurement:
    token_count: int | None
    item_count: int | None
    quality: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextCompressionRequest:
    context: Any
    window_tokens: int
    trigger_tokens: int
    target_tokens: int
    measurement: ContextMeasurement
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextSummaryRequest:
    context: Any
    instruction: str
    target_tokens: int
    model: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextSummaryResult:
    summary: str
    status: str = "COMPLETE"
    request_id: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    uncached_prompt_tokens: int | None = None
    started_mono_ns: int | None = None
    ended_mono_ns: int | None = None
    duration_s: float | None = None
    token_quality: str = "UNRESOLVED"
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextCompressionResult:
    context: Any
    reason: str
    limitations: tuple[str, ...] = ()
    measurement: ContextMeasurement | None = None
    summary: ContextSummaryResult | None = None


@dataclass(frozen=True)
class ContextCompressionTriggerResult:
    triggered: bool | None
    reason: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextCompressionDecision:
    context: Any
    status: str
    reason: str
    trigger_reason: str | None
    trigger_strategy: str
    strategy: str
    configured_enabled: bool
    effective_enabled: bool
    window_tokens: int | None
    trigger_ratio: float
    target_ratio: float
    trigger_tokens: int | None
    target_tokens: int | None
    original: ContextMeasurement
    compressed: ContextMeasurement
    target_achieved: bool | None
    started_mono_ns: int | None = None
    ended_mono_ns: int | None = None
    duration_s: float | None = None
    summary: ContextSummaryResult | None = None
    limitations: tuple[str, ...] = ()
    prohibited_conclusions: tuple[str, ...] = (
        "estimated_token_counts_are_not_exact_tokenizer_results",
        "size_reduction_does_not_prove_semantic_equivalence",
        "model_summary_does_not_prove_information_preservation",
    )

    @property
    def applied(self) -> bool:
        if self.status not in {"COMPLETE", "PARTIAL"}:
            return False
        token_reduction = optional_difference(
            self.original.token_count,
            self.compressed.token_count,
        )
        item_reduction = optional_difference(
            self.original.item_count,
            self.compressed.item_count,
        )
        return bool(
            (token_reduction is not None and token_reduction > 0)
            or (item_reduction is not None and item_reduction > 0)
        )

    def telemetry(self) -> dict[str, Any]:
        original_tokens = self.original.token_count
        compressed_tokens = self.compressed.token_count
        original_items = self.original.item_count
        compressed_items = self.compressed.item_count
        summary = self.summary
        return {
            "context_compression_status": self.status,
            "context_compression_reason": self.reason,
            "context_compression_trigger_reason": self.trigger_reason,
            "context_compression_trigger_strategy": self.trigger_strategy,
            "context_compression_strategy": self.strategy,
            "context_compression_configured_enabled": self.configured_enabled,
            "context_compression_effective_enabled": self.effective_enabled,
            "context_compression_applied": self.applied,
            "context_compression_window_tokens": self.window_tokens,
            "context_compression_trigger_ratio": self.trigger_ratio,
            "context_compression_target_ratio": self.target_ratio,
            "context_compression_trigger_tokens": self.trigger_tokens,
            "context_compression_target_tokens": self.target_tokens,
            "context_compression_original_tokens": original_tokens,
            "context_compression_compressed_tokens": compressed_tokens,
            "context_compression_removed_tokens": optional_difference(
                original_tokens,
                compressed_tokens,
            ),
            "context_compression_original_message_count": original_items,
            "context_compression_compressed_message_count": compressed_items,
            "context_compression_removed_message_count": optional_difference(
                original_items,
                compressed_items,
            ),
            "context_compression_ratio": optional_ratio(
                compressed_tokens,
                original_tokens,
            ),
            "context_compression_measurement_quality": self.compressed.quality,
            "context_compression_target_achieved": self.target_achieved,
            "context_compression_started_mono_ns": self.started_mono_ns,
            "context_compression_ended_mono_ns": self.ended_mono_ns,
            "context_compression_duration_s": self.duration_s,
            "context_summary_status": summary.status if summary else None,
            "context_summary_request_id": summary.request_id if summary else None,
            "context_summary_model": summary.model if summary else None,
            "context_summary_input_tokens": summary.input_tokens if summary else None,
            "context_summary_output_tokens": summary.output_tokens if summary else None,
            "context_summary_total_tokens": summary.total_tokens if summary else None,
            "context_summary_cached_tokens": summary.cached_tokens if summary else None,
            "context_summary_uncached_prompt_tokens": (
                summary.uncached_prompt_tokens if summary else None
            ),
            "context_summary_started_mono_ns": (
                summary.started_mono_ns if summary else None
            ),
            "context_summary_ended_mono_ns": (
                summary.ended_mono_ns if summary else None
            ),
            "context_summary_duration_s": summary.duration_s if summary else None,
            "context_summary_token_quality": summary.token_quality if summary else None,
            "context_summary_limitations": (
                list(unique(summary.limitations)) if summary else []
            ),
            "context_compression_limitations": list(unique(self.limitations)),
            "context_compression_prohibited_conclusions": list(
                self.prohibited_conclusions
            ),
        }


def optional_difference(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left - right


def optional_ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
