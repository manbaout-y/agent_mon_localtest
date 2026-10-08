from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from agentmon.config import AgentMonConfig
from agentmon.experiments.evidence import build_evidence_outputs, load_batch_experiment, source_file_inventory, write_evidence_outputs
from agentmon.experiments.evaluation import build_mechanism_evaluation_from_timeline, write_evaluation_outputs
from agentmon.experiments.preflight import build_optimizer_experiment_preflight, write_optimizer_experiment_preflight
from agentmon.experiments.replay import run_optimizer_advice_replay_streaming
from agentmon.experiments.reports import write_optimizer_replay_report
from agentmon.optimizer.harness.schemas import COMPLETE, FORBIDDEN_CONCLUSIONS, PARTIAL, UNRESOLVED
from agentmon.pipeline import FAILED, SKIPPED, ArtifactRef, PipelineExecutor, PipelineStageResult, StageSpec
from agentmon.pipeline.identity import detect_source_identity
from agentmon.pipeline.identity import file_sha256


OPTIMIZER_PIPELINE_SPECS = (
    StageSpec("source_identity", "preparation", cacheable=False),
    StageSpec("input_inventory", "preparation", cacheable=False, dependencies=("source_identity",)),
    StageSpec("input_preflight", "preparation", cacheable=False, dependencies=("input_inventory",)),
    StageSpec("m1_evidence_build", "evidence", cacheable=False, dependencies=("input_inventory",)),
    StageSpec(
        "m2_decision_replay",
        "replay",
        cacheable=False,
        dependencies=("input_preflight",),
        required_capabilities=("timeline_order_resolved",),
        accepted_upstream_outcomes=(COMPLETE, PARTIAL),
    ),
    StageSpec(
        "m3_mechanism_evaluation",
        "evaluation",
        cacheable=False,
        dependencies=("m1_evidence_build", "m2_decision_replay"),
        required_capabilities=("decision_stream_complete", "evidence_pack_available"),
        accepted_upstream_outcomes=(COMPLETE, PARTIAL),
    ),
    StageSpec(
        "report_build",
        "reporting",
        cacheable=False,
        dependencies=("m1_evidence_build", "m2_decision_replay", "m3_mechanism_evaluation"),
        accepted_upstream_outcomes=(COMPLETE, PARTIAL, UNRESOLVED, FAILED, SKIPPED),
    ),
    StageSpec("artifact_validation", "validation", cacheable=False, dependencies=("report_build",), accepted_upstream_outcomes=(COMPLETE, PARTIAL)),
)


def run_optimizer_experiment_harness(
    *,
    batch_root: str | Path,
    output_dir: str | Path,
    cfg: AgentMonConfig,
    strategies: list[str],
    baseline_strategy: str | None = None,
    mechanism_profile: dict[str, Any] | None = None,
    schema_profile: dict[str, Any] | None = None,
    run_search_roots: list[str | Path] | tuple[str | Path, ...] = (),
    preflight_only: bool = False,
    status_file: str | Path | None = None,
    progress_interval_seconds: float | None = None,
    resume: bool = False,
    memory_budget_bytes: int = 32 * 1024 * 1024,
) -> dict[str, Any]:
    output_root = Path(output_dir).expanduser().resolve()
    source_root = Path(__file__).resolve().parents[4]
    source_identity = detect_source_identity(source_root)
    selected_strategies = strategies or [cfg.optimizer.strategy]
    executor = PipelineExecutor(
        output_root=output_root,
        specs=OPTIMIZER_PIPELINE_SPECS,
        status_file=status_file,
        progress_interval_seconds=progress_interval_seconds,
        resume=resume,
        source_identity=source_identity,
    )

    def source_stage(context):
        path = output_root / "source_identity.json"
        write_json(path, source_identity)
        context.state["source_identity"] = source_identity
        return PipelineStageResult(outcome=COMPLETE, artifacts=(ArtifactRef.from_path(path),), provided_capabilities=("source_identity_recorded",), payload={"claim_class": "DERIVED"})

    def inventory_stage(context):
        data = load_batch_experiment(batch_root, run_search_roots=run_search_roots)
        replay_data = load_batch_experiment(
            batch_root,
            run_search_roots=run_search_roots,
            materialize_step_rows=False,
        )
        context.state["data"] = data
        context.state["replay_data"] = replay_data
        path = context.stage_root("input_inventory") / "input_inventory.json"
        payload = {"batch_root": str(data.batch_root), "source_files": source_file_inventory(data), "source_status": data.source_status}
        context.state["input_identity"] = canonical_sha256(payload["source_files"])
        write_json(path, payload)
        return PipelineStageResult(
            outcome=COMPLETE if data.batch_root.is_dir() else UNRESOLVED,
            artifacts=(ArtifactRef.from_path(path),),
            provided_capabilities=("input_inventory_available",),
            evidence_sources=tuple(item["path"] for item in payload["source_files"].get("run_events", [])),
            data_quality=data.source_status.get("run_events", {}).get("status"),
            payload={"claim_class": "OBSERVED"},
        )

    def preflight_stage(context):
        data = context.state["data"]
        preflight = build_optimizer_experiment_preflight(data, schema_profile)
        path = write_optimizer_experiment_preflight(preflight, output_root)
        context.state["preflight"] = preflight
        capabilities = ["preflight_available"]
        if preflight["status"] != UNRESOLVED:
            capabilities.append("timeline_order_resolved")
        return PipelineStageResult(
            outcome=preflight["status"],
            artifacts=(ArtifactRef.from_path(path),),
            reason_codes=tuple(preflight.get("blocking_reasons") or ()),
            provided_capabilities=tuple(capabilities),
            data_quality=preflight["status"],
            limitations=tuple(preflight.get("limitations") or ()),
            forbidden_conclusions=tuple(FORBIDDEN_CONCLUSIONS),
            payload={"claim_class": "DERIVED"},
        )

    def evidence_stage(context):
        data = context.state["data"]
        outputs = build_evidence_outputs(data, schema_profile)
        outputs["experiment_manifest"]["source_identity"] = source_identity
        paths = write_evidence_outputs(outputs, output_root)
        # The graph and tabular M1 outputs are already published. Keep only
        # the values M2/M3/report consume so the evidence graph and step-row
        # compatibility list do not remain resident through replay.
        context.state["evidence_outputs"] = {
            key: outputs[key]
            for key in ("experiment_manifest", "bottleneck_attribution", "data_quality")
        }
        context.state["evidence_paths"] = paths
        context.state["data"] = context.state["replay_data"]
        quality = outputs["data_quality"].get("overall_status", PARTIAL)
        return PipelineStageResult(
            outcome=quality,
            artifacts=tuple(ArtifactRef.from_path(path) for path in paths.values()),
            provided_capabilities=("evidence_pack_available",),
            data_quality=quality,
            evidence_sources=("batch-results.tsv", "step_csv", "events.jsonl"),
            limitations=tuple(outputs["data_quality"].get("limitations") or ()),
            forbidden_conclusions=tuple(FORBIDDEN_CONCLUSIONS),
            payload={"claim_class": "DERIVED"},
        )

    def replay_stage(context):
        data = context.state["data"]
        stage_root = context.stage_root("m2_decision_replay")
        replay_identity = build_replay_identity(
            cfg=cfg,
            strategies=selected_strategies,
            schema_profile=schema_profile,
            memory_budget_bytes=memory_budget_bytes,
        )
        context.state["replay_identity"] = replay_identity
        outputs = (
            load_completed_replay(
                stage_root,
                output_root,
                context.state["input_identity"],
                source_identity,
                replay_identity,
            )
            if resume
            else None
        )
        if outputs is None:
            outputs = run_optimizer_advice_replay_streaming(
                data,
                cfg,
                selected_strategies,
                output_root,
                schema_profile=schema_profile,
                work_dir=stage_root,
                memory_budget_bytes=memory_budget_bytes,
                input_identity=context.state["input_identity"],
                source_tree_sha256=source_identity.get("source_tree_sha256"),
                replay_identity=replay_identity,
                progress_callback=lambda stage, current, total: context.heartbeat(stage, current=current, total=total),
            )
        context.state["replay_outputs"] = outputs
        artifacts = tuple(ArtifactRef.from_path(path) for key, path in outputs["paths"].items() if key not in {"checkpoint"})
        outcome = COMPLETE if outputs["snapshot_count"] else UNRESOLVED
        return PipelineStageResult(
            outcome=outcome,
            artifacts=artifacts,
            row_counts={"snapshots": outputs["snapshot_count"], "decisions": outputs["decision_count"]},
            provided_capabilities=("nonempty_snapshot_stream", "decision_stream_complete") if outputs["snapshot_count"] else (),
            data_quality=context.state["preflight"]["status"],
            limitations=tuple(FORBIDDEN_CONCLUSIONS),
            forbidden_conclusions=tuple(FORBIDDEN_CONCLUSIONS),
            payload={"claim_class": "INFERRED", "streaming": True},
        )

    def evaluation_stage(context):
        replay = context.state["replay_outputs"]
        evidence = context.state["evidence_outputs"]
        outputs = build_mechanism_evaluation_from_timeline(
            context.state["data"], evidence, replay["paths"]["optimizer_decision_timeline"], replay["strategy_summary"],
            baseline_strategy=baseline_strategy, mechanism_profile=mechanism_profile, schema_profile=schema_profile,
            work_dir=context.stage_root("m3_mechanism_evaluation"),
        )
        paths = write_evaluation_outputs(outputs, output_root)
        context.state["evaluation_outputs"] = outputs
        context.state["evaluation_paths"] = paths
        return PipelineStageResult(outcome=PARTIAL, artifacts=tuple(ArtifactRef.from_path(path) for path in paths.values()), provided_capabilities=("evaluation_proxy_available",), data_quality="PROXY_ONLY", limitations=tuple(outputs["mechanism_comparison"].get("limitations") or ()), forbidden_conclusions=tuple(FORBIDDEN_CONCLUSIONS), payload={"claim_class": "INFERRED"})

    def report_stage(context):
        evidence = context.state.get("evidence_outputs")
        replay = context.state.get("replay_outputs")
        if evidence and replay:
            report_path = write_optimizer_replay_report(evidence, replay, output_root)
            outcome = PARTIAL if context.state["preflight"]["status"] != COMPLETE else COMPLETE
        else:
            report_path = output_root / "optimizer_replay_report.md"
            failed_stages = sorted(
                name for name, result in executor.results.items() if result.outcome == FAILED
            )
            report_path.write_text(
                "# AgentMon Optimizer Experiment Replay Report\n\n"
                "Replay or evidence output was not available.\n\n"
                f"- Preflight status: `{context.state.get('preflight', {}).get('status', UNRESOLVED)}`\n"
                f"- Failed stages: `{failed_stages}`\n\n"
                "## Forbidden Conclusions\n\n"
                + "\n".join(f"- {item}" for item in FORBIDDEN_CONCLUSIONS)
                + "\n",
                encoding="utf-8",
            )
            outcome = PARTIAL
        context.state["report_path"] = report_path
        return PipelineStageResult(outcome=outcome, artifacts=(ArtifactRef.from_path(report_path),), provided_capabilities=("report_available",), data_quality=context.state["preflight"]["status"], limitations=tuple(FORBIDDEN_CONCLUSIONS), forbidden_conclusions=tuple(FORBIDDEN_CONCLUSIONS), payload={"claim_class": "DERIVED"})

    def validation_stage(context):
        missing = []
        for paths in (
            context.state.get("evidence_paths", {}),
            context.state.get("replay_outputs", {}).get("paths", {}),
            context.state.get("evaluation_paths", {}),
        ):
            for raw_path in paths.values():
                path = Path(raw_path)
                if not path.is_file():
                    missing.append(str(path))
        for path in (context.state.get("report_path"),):
            if path is not None and not path.is_file():
                missing.append(str(path))
        path = context.stage_root("artifact_validation") / "artifact_validation.json"
        provenance_path = output_root / "artifact_provenance.json"
        payload = {
            "status": COMPLETE if not missing else UNRESOLVED,
            "missing_files": missing,
            "forbidden_conclusions": FORBIDDEN_CONCLUSIONS,
        }
        write_json(path, payload)
        provenance = build_artifact_provenance(
            output_root=output_root,
            source_identity=source_identity,
            input_identity=context.state.get("input_identity"),
            cfg=cfg,
            schema_profile=schema_profile,
            mechanism_profile=mechanism_profile,
            replay_identity=context.state.get("replay_identity"),
            artifacts=collect_public_artifacts(context),
            validation_status=payload["status"],
        )
        write_json(provenance_path, provenance)
        return PipelineStageResult(
            outcome=payload["status"],
            artifacts=(ArtifactRef.from_path(path), ArtifactRef.from_path(provenance_path)),
            provided_capabilities=("artifact_validation_complete", "artifact_provenance_complete") if not missing else (),
            reason_codes=("missing_artifacts",) if missing else (),
            payload={"claim_class": "DERIVED"},
        )

    functions = {"source_identity": source_stage, "input_inventory": inventory_stage, "input_preflight": preflight_stage, "m1_evidence_build": evidence_stage, "m2_decision_replay": replay_stage, "m3_mechanism_evaluation": evaluation_stage, "report_build": report_stage, "artifact_validation": validation_stage}
    if preflight_only:
        functions = {key: value for key, value in functions.items() if key in {"source_identity", "input_inventory", "input_preflight"}}
    results = executor.run(functions)
    preflight = executor.context.state.get("preflight", {"status": UNRESOLVED, "blocking_reasons": ["preflight_not_run"]})
    replay = executor.context.state.get("replay_outputs", {})
    evaluation = executor.context.state.get("evaluation_outputs", {})
    evidence_paths = executor.context.state.get("evidence_paths", {})
    replay_paths = replay.get("paths", {})
    evaluation_paths = executor.context.state.get("evaluation_paths", {})
    return {
        "schema_version": "2.0",
        "result_type": "agentmon.optimizer_experiment.run_result",
        "status": optimizer_pipeline_status(results, preflight_only=preflight_only, preflight_status=preflight["status"]),
        "batch_root": str(Path(batch_root).expanduser().resolve()),
        "output_dir": str(output_root),
        "preflight": str(output_root / "optimizer_preflight.json"),
        "pipeline_manifest": str(output_root / "pipeline_manifest.json"),
        "artifact_provenance": str(output_root / "artifact_provenance.json"),
        "source_identity": source_identity,
        "blocking_reasons": list(preflight.get("blocking_reasons", [])),
        "strategies": list(selected_strategies),
        "baseline_strategy": evaluation.get("mechanism_comparison", {}).get("baseline_strategy"),
        "evidence_outputs": {key: str(path) for key, path in evidence_paths.items()},
        "replay_outputs": {key: str(path) for key, path in replay_paths.items()},
        "evaluation_outputs": {key: str(path) for key, path in evaluation_paths.items()},
        "report": str(executor.context.state["report_path"]) if "report_path" in executor.context.state else None,
        "evaluation_report": str(evaluation_paths["evaluation_report"]) if "evaluation_report" in evaluation_paths else None,
        "data_quality_status": executor.context.state.get("evidence_outputs", {}).get("data_quality", {}).get("overall_status"),
        "snapshot_count": replay.get("snapshot_count", 0),
        "decision_count": replay.get("decision_count", 0),
        "conflict_count": evaluation.get("conflict_audit", {}).get("conflict_count", 0),
        "stage_outcomes": {name: item.outcome for name, item in results.items()},
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def canonical_sha256(payload: Any) -> str:
    value = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(value).hexdigest()


def build_replay_identity(
    *,
    cfg: AgentMonConfig,
    strategies: list[str],
    schema_profile: dict[str, Any] | None,
    memory_budget_bytes: int,
) -> str:
    """Bind reusable decisions to every input that can change replay output."""
    return canonical_sha256(
        {
            "configuration": cfg.to_dict(),
            "strategies": list(strategies),
            "schema_profile": schema_profile or {},
            "memory_budget_bytes": int(memory_budget_bytes),
        }
    )


def optimizer_pipeline_status(
    results: dict[str, PipelineStageResult],
    *,
    preflight_only: bool,
    preflight_status: str,
) -> str:
    if preflight_only:
        return preflight_status
    outcomes = {result.outcome for result in results.values()}
    if FAILED in outcomes:
        return FAILED
    if UNRESOLVED in outcomes:
        return UNRESOLVED
    if outcomes & {PARTIAL, SKIPPED}:
        return PARTIAL
    return COMPLETE


def collect_public_artifacts(context: Any) -> dict[str, Path]:
    """Return output artifacts only; raw inputs never enter the provenance bundle."""
    artifacts: dict[str, Path] = {}
    for group_name, paths in (
        ("evidence", context.state.get("evidence_paths", {})),
        ("replay", context.state.get("replay_outputs", {}).get("paths", {})),
        ("evaluation", context.state.get("evaluation_paths", {})),
    ):
        for name, path in paths.items():
            candidate = Path(path)
            if candidate.is_file():
                artifacts[f"{group_name}:{name}"] = candidate
    report_path = context.state.get("report_path")
    if report_path is not None and Path(report_path).is_file():
        artifacts["report:optimizer_replay_report"] = Path(report_path)
    return artifacts


def build_artifact_provenance(
    *,
    output_root: Path,
    source_identity: dict[str, Any],
    input_identity: str | None,
    cfg: AgentMonConfig,
    schema_profile: dict[str, Any] | None,
    mechanism_profile: dict[str, Any] | None,
    replay_identity: str | None,
    artifacts: dict[str, Path],
    validation_status: str,
) -> dict[str, Any]:
    """Create a hash-only, privacy-preserving identity chain for public outputs."""
    rows = []
    for name, path in sorted(artifacts.items()):
        try:
            relative = path.resolve().relative_to(output_root)
            display_path = relative.as_posix()
        except ValueError:
            display_path = path.name
        rows.append(
            {
                "artifact_name": name,
                "path": display_path,
                "sha256": file_sha256(path),
                "size_bytes": path.stat().st_size,
                "claim_class": "DERIVED",
            }
        )
    return {
        "schema_version": "2.0",
        "result_type": "agentmon.optimizer_experiment.artifact_provenance",
        "validation_status": validation_status,
        "source_identity": {
            "source_tree_sha256": source_identity.get("source_tree_sha256"),
            "package_sha256": source_identity.get("package_sha256", "UNKNOWN"),
            "source_identity_mode": source_identity.get("source_identity_mode", "UNKNOWN"),
        },
        "input_identity": input_identity or "UNKNOWN",
        "configuration_sha256": canonical_sha256(cfg.to_dict()),
        "schema_profile_sha256": canonical_sha256(schema_profile or {}),
        "mechanism_profile_sha256": canonical_sha256(mechanism_profile or {}),
        "replay_identity": replay_identity or "NOT_APPLICABLE",
        "artifacts": rows,
        "privacy_defaults": {
            "prompts_included": False,
            "summaries_included": False,
            "raw_tool_outputs_included": False,
            "full_code_diffs_included": False,
        },
        "limitations": [
            "This manifest establishes file identity, not causal validity of conclusions.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }


def load_completed_replay(
    stage_root: Path,
    output_root: Path,
    input_identity: str,
    source_identity: dict[str, Any],
    replay_identity: str,
) -> dict[str, Any] | None:
    checkpoint_path = stage_root / "checkpoint.json"
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        checkpoint.get("state") != "COMPLETE"
        or checkpoint.get("input_identity") != input_identity
        or checkpoint.get("source_tree_sha256") != source_identity.get("source_tree_sha256")
        or checkpoint.get("replay_identity") != replay_identity
    ):
        return None
    paths = {
        "optimizer_decision_timeline": output_root / "optimizer_decision_timeline.csv",
        "optimizer_decisions": output_root / "optimizer_decisions.jsonl",
        "optimizer_strategy_summary": output_root / "optimizer_strategy_summary.json",
        "optimizer_risk_audit": output_root / "optimizer_risk_audit.json",
        "prediction_variable_validation": output_root / "prediction_variable_validation.json",
        "prediction_variable_validation_table": output_root / "prediction_variable_validation.csv",
        "snapshot_manifest": stage_root / "snapshot_manifest.json",
        "checkpoint": checkpoint_path,
    }
    if any(not path.is_file() for path in paths.values()):
        return None
    recorded_artifacts = checkpoint.get("artifacts")
    if not isinstance(recorded_artifacts, dict):
        return None
    for key, path in paths.items():
        if key == "checkpoint":
            continue
        record = recorded_artifacts.get(key)
        if not isinstance(record, dict) or record.get("sha256") != file_sha256(path):
            return None
    try:
        summary = json.loads(paths["optimizer_strategy_summary"].read_text(encoding="utf-8"))
        audit = json.loads(paths["optimizer_risk_audit"].read_text(encoding="utf-8"))
        prediction = json.loads(paths["prediction_variable_validation"].read_text(encoding="utf-8"))
        snapshot_manifest = json.loads(paths["snapshot_manifest"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for part in snapshot_manifest.get("parts", []):
        if not isinstance(part, dict) or not part.get("path") or not part.get("sha256"):
            return None
        part_path = Path(str(part["path"]))
        if not part_path.is_file() or file_sha256(part_path) != part["sha256"]:
            return None
    return {
        "strategy_summary": summary,
        "risk_audit": audit,
        "prediction_variable_validation": prediction,
        "paths": {key: str(path) for key, path in paths.items()},
        "snapshot_count": int(checkpoint.get("snapshot_count") or 0),
        "decision_count": int(checkpoint.get("decision_count") or 0),
        "streaming": True,
        "resumed_completed_replay": True,
        "replay_identity": replay_identity,
        "source_identity": source_identity,
    }
