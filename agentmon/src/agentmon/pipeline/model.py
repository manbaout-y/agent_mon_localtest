from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
UNRESOLVED = "UNRESOLVED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"

EXECUTED = "EXECUTED"
CACHED = "CACHED"
RESUMED = "RESUMED"

TERMINAL_OUTCOMES = frozenset({COMPLETE, PARTIAL, UNRESOLVED, FAILED, SKIPPED})


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    schema: str | None = None
    sha256: str | None = None
    row_count: int | None = None

    @classmethod
    def from_path(cls, path: str | Path, **kwargs: Any) -> "ArtifactRef":
        return cls(path=str(Path(path).expanduser().resolve()), **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "schema": self.schema,
            "sha256": self.sha256,
            "row_count": self.row_count,
        }


@dataclass(frozen=True)
class StageSpec:
    name: str
    group: str
    cacheable: bool = True
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    accepted_upstream_outcomes: tuple[str, ...] = (COMPLETE, PARTIAL)


@dataclass
class PipelineStageResult:
    outcome: str = COMPLETE
    artifacts: tuple[ArtifactRef, ...] = field(default_factory=tuple)
    row_counts: dict[str, int] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    provided_capabilities: tuple[str, ...] = field(default_factory=tuple)
    missing_capabilities: tuple[str, ...] = field(default_factory=tuple)
    evidence_sources: tuple[str, ...] = field(default_factory=tuple)
    data_quality: str | None = None
    limitations: tuple[str, ...] = field(default_factory=tuple)
    forbidden_conclusions: tuple[str, ...] = field(default_factory=tuple)
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in TERMINAL_OUTCOMES:
            raise ValueError(f"invalid pipeline stage outcome: {self.outcome}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason_codes": list(self.reason_codes),
            "provided_capabilities": list(self.provided_capabilities),
            "missing_capabilities": list(self.missing_capabilities),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "row_counts": dict(self.row_counts),
            "evidence_sources": list(self.evidence_sources),
            "data_quality": self.data_quality,
            "limitations": list(self.limitations),
            "forbidden_conclusions": list(self.forbidden_conclusions),
            "payload": self.payload,
        }
