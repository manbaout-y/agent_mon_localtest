from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any, Callable, Iterable

from .model import CACHED, COMPLETE, EXECUTED, FAILED, PARTIAL, SKIPPED, UNRESOLVED, ArtifactRef, PipelineStageResult, StageSpec


StageFunction = Callable[["PipelineContext"], PipelineStageResult]


@dataclass
class PipelineContext:
    output_root: Path
    status_path: Path
    state: dict[str, Any] = field(default_factory=dict)
    progress_interval_seconds: float | None = None
    _last_progress: float = field(default=0.0, init=False, repr=False)

    def stage_root(self, stage_name: str) -> Path:
        path = self.output_root / "stages" / stage_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def heartbeat(self, stage_name: str, *, current: int | None = None, total: int | None = None, detail: dict[str, Any] | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self.progress_interval_seconds is not None and now - self._last_progress < self.progress_interval_seconds:
            return
        self._last_progress = now
        atomic_json(
            self.status_path,
            {
                "schema_version": "2.0",
                "result_type": "agentmon.pipeline.status",
                "state": "RUNNING",
                "stage": stage_name,
                "current": current,
                "total": total,
                "updated_at": now_iso(),
                "detail": detail or {},
            },
        )


class PipelineExecutor:
    """Small DAG executor with explicit outcome/capability contracts."""

    def __init__(
        self,
        *,
        output_root: str | Path,
        specs: Iterable[StageSpec],
        status_file: str | Path | None = None,
        progress_interval_seconds: float | None = None,
        resume: bool = False,
        source_identity: dict[str, Any] | None = None,
    ) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.specs = {spec.name: spec for spec in specs}
        if not self.specs:
            raise ValueError("pipeline requires at least one stage")
        self._validate_specs()
        self.order = topological_order(self.specs)
        self.context = PipelineContext(
            output_root=self.output_root,
            status_path=Path(status_file).expanduser().resolve() if status_file else self.output_root / "pipeline_status.json",
            progress_interval_seconds=progress_interval_seconds,
        )
        self.resume = resume
        self.source_identity = source_identity or {}
        self.results: dict[str, PipelineStageResult] = {}
        self.dispositions: dict[str, str] = {}
        self.started_at = now_iso()
        atomic_json(self.output_root / "pipeline_dag.json", {"schema_version": "2.0", "nodes": stage_nodes(self.specs), "edges": stage_edges(self.specs)})

    def run(self, functions: dict[str, StageFunction]) -> dict[str, PipelineStageResult]:
        missing = set(functions) - set(self.specs)
        if missing:
            raise ValueError(f"functions supplied for unknown stages: {','.join(sorted(missing))}")
        for stage_name in self.order:
            spec = self.specs[stage_name]
            cached = self._load_cached(stage_name, spec) if self.resume else None
            if cached is not None:
                self.results[stage_name] = cached
                self.dispositions[stage_name] = CACHED
                continue
            blocked = self._blocking_result(spec)
            if blocked is not None:
                self._record(stage_name, blocked, EXECUTED)
                continue
            function = functions.get(stage_name)
            if function is None:
                self._record(stage_name, PipelineStageResult(outcome=SKIPPED, reason_codes=("stage_not_selected",)), EXECUTED)
                continue
            self.context.heartbeat(stage_name, force=True)
            started = time.monotonic()
            try:
                result = function(self.context)
            except Exception as exc:  # noqa: BLE001 - failure must be emitted as data.
                result = PipelineStageResult(
                    outcome=FAILED,
                    reason_codes=("stage_exception",),
                    payload={"exception_type": type(exc).__name__, "exception_message": str(exc)},
                )
            result.payload = {**result.payload, "wall_s": time.monotonic() - started}
            # A requested resume does not prove that a stage continued from a
            # checkpoint. Only validated cache loads receive a non-EXECUTED
            # disposition; stage-specific checkpoint reuse stays in payload.
            self._record(stage_name, result, EXECUTED)
            if result.outcome == FAILED:
                # Independent branches may still run, but all dependents receive an explicit skip.
                continue
        self._write_manifest()
        outcomes = {item.outcome for item in self.results.values()}
        final_outcome = (
            FAILED
            if FAILED in outcomes
            else UNRESOLVED
            if UNRESOLVED in outcomes
            else PARTIAL
            if outcomes & {PARTIAL, SKIPPED}
            else COMPLETE
        )
        atomic_json(
            self.context.status_path,
            {
                "schema_version": "2.0",
                "result_type": "agentmon.pipeline.status",
                # Execution completion and evidence outcome are separate. A
                # finished PARTIAL evidence bundle is not a running failure.
                "state": COMPLETE if final_outcome != FAILED else FAILED,
                "stage": "complete",
                "updated_at": now_iso(),
                "detail": {
                    "outcome": final_outcome,
                    "snapshot_count": self.context.state.get("replay_outputs", {}).get("snapshot_count"),
                    "decision_count": self.context.state.get("replay_outputs", {}).get("decision_count"),
                },
            },
        )
        return dict(self.results)

    def _blocking_result(self, spec: StageSpec) -> PipelineStageResult | None:
        upstream = [self.results[name] for name in spec.dependencies if name in self.results]
        failed = [
            name
            for name in spec.dependencies
            if self.results.get(name)
            and self.results[name].outcome == FAILED
            and FAILED not in spec.accepted_upstream_outcomes
        ]
        unacceptable = [
            name
            for name in spec.dependencies
            if self.results.get(name)
            and self.results[name].outcome not in spec.accepted_upstream_outcomes
            and name not in failed
        ]
        capabilities = {capability for item in upstream for capability in item.provided_capabilities}
        missing = [capability for capability in spec.required_capabilities if capability not in capabilities]
        if failed:
            return PipelineStageResult(outcome=SKIPPED, reason_codes=("upstream_failed", *tuple(f"upstream:{item}" for item in failed)))
        if unacceptable:
            return PipelineStageResult(outcome=SKIPPED, reason_codes=("upstream_outcome_not_accepted", *tuple(f"upstream:{item}" for item in unacceptable)))
        if missing:
            return PipelineStageResult(outcome=SKIPPED, reason_codes=("required_capability_missing",), missing_capabilities=tuple(missing))
        return None

    def _record(self, stage_name: str, result: PipelineStageResult, disposition: str) -> None:
        self.results[stage_name] = result
        self.dispositions[stage_name] = disposition
        record = {
            "schema_version": "2.0",
            "stage_name": stage_name,
            "disposition": disposition,
            "source_identity": self.source_identity,
            "finished_at": now_iso(),
            **result.to_dict(),
        }
        atomic_json(self.context.stage_root(stage_name) / "stage-status.json", record)
        self.context.heartbeat(stage_name, detail={"outcome": result.outcome}, force=True)

    def _load_cached(self, stage_name: str, spec: StageSpec) -> PipelineStageResult | None:
        if not spec.cacheable:
            return None
        path = self.output_root / "stages" / stage_name / "stage-status.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if raw.get("source_identity") != self.source_identity or raw.get("outcome") not in {COMPLETE, PARTIAL}:
            return None
        artifacts = tuple(ArtifactRef(**item) for item in raw.get("artifacts", []) if isinstance(item, dict))
        if any(not artifact_matches(item) for item in artifacts):
            return None
        return PipelineStageResult(
            outcome=str(raw["outcome"]),
            artifacts=artifacts,
            row_counts=dict(raw.get("row_counts") or {}),
            reason_codes=tuple(raw.get("reason_codes") or ()),
            provided_capabilities=tuple(raw.get("provided_capabilities") or ()),
            missing_capabilities=tuple(raw.get("missing_capabilities") or ()),
            evidence_sources=tuple(raw.get("evidence_sources") or ()),
            data_quality=raw.get("data_quality"),
            limitations=tuple(raw.get("limitations") or ()),
            forbidden_conclusions=tuple(raw.get("forbidden_conclusions") or ()),
            payload=dict(raw.get("payload") or {}),
        )

    def _write_manifest(self) -> None:
        atomic_json(
            self.output_root / "pipeline_manifest.json",
            {
                "schema_version": "2.0",
                "result_type": "agentmon.pipeline.manifest",
                "started_at": self.started_at,
                "finished_at": now_iso(),
                "source_identity": self.source_identity,
                "stages": [
                    {
                        "stage_name": name,
                        "disposition": self.dispositions.get(name),
                        **self.results[name].to_dict(),
                    }
                    for name in self.order
                    if name in self.results
                ],
            },
        )

    def _validate_specs(self) -> None:
        for spec in self.specs.values():
            unknown = set(spec.dependencies) - set(self.specs)
            if unknown:
                raise ValueError(f"stage {spec.name} depends on unknown stage(s): {','.join(sorted(unknown))}")


def stage_nodes(specs: dict[str, StageSpec]) -> list[dict[str, Any]]:
    return [
        {
            "stage_name": spec.name,
            "group": spec.group,
            "cacheable": spec.cacheable,
            "inputs": list(spec.inputs),
            "outputs": list(spec.outputs),
            "dependencies": list(spec.dependencies),
            "required_capabilities": list(spec.required_capabilities),
            "accepted_upstream_outcomes": list(spec.accepted_upstream_outcomes),
        }
        for spec in specs.values()
    ]


def stage_edges(specs: dict[str, StageSpec]) -> list[dict[str, str]]:
    return [
        {"from": dependency, "to": spec.name}
        for spec in specs.values()
        for dependency in spec.dependencies
    ]


def topological_order(specs: dict[str, StageSpec]) -> list[str]:
    pending = {name: set(spec.dependencies) for name, spec in specs.items()}
    result: list[str] = []
    while pending:
        ready = sorted(name for name, dependencies in pending.items() if not dependencies)
        if not ready:
            raise ValueError("pipeline stage graph contains a cycle")
        for name in ready:
            result.append(name)
            pending.pop(name)
        done = set(ready)
        for dependencies in pending.values():
            dependencies.difference_update(done)
    return result


def artifact_matches(artifact: ArtifactRef) -> bool:
    path = Path(artifact.path)
    if not path.is_file():
        return False
    return artifact.sha256 is None or file_sha256(path) == artifact.sha256


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
