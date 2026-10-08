from __future__ import annotations

from pathlib import Path
import shutil
import time
import traceback
from typing import Any

from agentmon.analysis.analyzers import analyze_bundle
from agentmon.analysis.artifacts import build_artifact_manifest
from agentmon.analysis.config import AnalysisConfig, write_resolved_config
from agentmon.analysis.contract import write_experiment_contract
from agentmon.analysis.discovery import DiscoveryResult, discover_experiment
from agentmon.analysis.execution import AnalysisRunContext, STAGE_NAMES, StageRangeComplete, build_stage_execution_plan, stop_after_stage
from agentmon.analysis.evidence import build_evidence_bundle
from agentmon.analysis.failure_diagnosis import write_failure_diagnosis
from agentmon.analysis.handoff import build_handoff_package
from agentmon.analysis.inventory import BaselineExpectations, build_input_inventory, check_input_baseline, write_input_inventory
from agentmon.analysis.io import read_bundle_table, read_json, write_json, write_markdown, write_table, write_table_manifest
from agentmon.analysis.model_pack import build_model_input_pack
from agentmon.analysis.normalize import NormalizedDataset, load_normalized_dataset, normalize_experiment, normalized_omitted_tables
from agentmon.analysis.preflight import run_analysis_preflight
from agentmon.analysis.p2_index import build_or_reuse_p2_index
from agentmon.analysis.resume import build_analysis_manifest, config_fingerprint, decide_resume, source_fingerprint, write_analysis_manifest
from agentmon.analysis.semantics import write_semantics_bundle
from agentmon.analysis.validators import validate_analysis_bundle
from agentmon.config import AgentMonConfig
from agentmon.optimizer.harness.runner import run_optimizer_experiment_harness
from agentmon.optimizer.registry import optimizer_strategy_catalog


def prepare_analysis_bundle(
    *,
    input_root: str | Path,
    output_root: str | Path,
    cfg: AnalysisConfig,
    output_format: str,
    force: bool = False,
    resume: bool = False,
    context: AnalysisRunContext | None = None,
    strict_profile: bool = False,
    rebuild_p2_index: bool = False,
    to_stage: str | None = None,
    experiment_contract: str | Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    bundle = Path(output_root).expanduser().resolve()
    bundle.mkdir(parents=True, exist_ok=True)
    if context:
        context.start_stage("discovery")
    discovery = discover_experiment(input_root, cfg)
    if context:
        context.update_progress(RUNS_DISCOVERED=len(discovery.run_inventory), EVENT_FILES_COMPLETED=0)
        context.finish_stage("discovery", row_counts={"runs_discovered": len(discovery.run_inventory), "batches_discovered": len(discovery.batch_inventory)})
        stop_after_stage("discovery", to_stage)
        context.start_stage("manifest")
    write_manifest(discovery, bundle, cfg)
    if context:
        context.finish_stage("manifest", output_files=[str(bundle / "manifest" / "experiment_manifest.json")])
        stop_after_stage("manifest", to_stage)
        context.start_stage("inventory")
    write_inventory(discovery, bundle, output_format=output_format)
    if context:
        context.finish_stage(
            "inventory",
            output_files=[
                str(bundle / "00_inventory" / "inventory_summary.json"),
                str(bundle / "00_inventory" / "inventory_manifest.json"),
            ],
            row_counts={"files": len(discovery.file_inventory), "runs": len(discovery.run_inventory)},
        )
        stop_after_stage("inventory", to_stage)
        context.start_stage("contract")
    contract = write_experiment_contract(
        bundle_root=bundle,
        discovery=discovery,
        cfg=cfg,
        output_format=output_format,
        contract_path=experiment_contract,
    )
    if context:
        context.finish_stage(
            "contract",
            status=contract.get("status", "PARTIAL"),
            output_files=existing_paths(
                bundle / "00_inventory" / "experiment_contract.json",
                bundle / "00_inventory" / "contract_manifest.json",
                bundle / "00_inventory" / "expected_artifacts.json",
            ),
            row_counts={
                "contract_validation": int(contract.get("validation_rows") or 0),
                "research_questions": int(contract.get("research_question_rows") or 0),
            },
        )
        stop_after_stage("contract", to_stage)
        context.start_stage("schema_resolution")
        context.finish_stage("schema_resolution", status="PASS", row_counts={"schema_versions": len(discovery.schema_versions)})
        stop_after_stage("schema_resolution", to_stage)
        context.start_stage("semantics_export")
    semantics = write_semantics_bundle(
        bundle_root=bundle,
        profile=cfg.profile,
        output_format=output_format,
    )
    if context:
        context.finish_stage(
            "semantics_export",
            status=semantics.get("status", "PARTIAL"),
            output_files=existing_paths(
                bundle / "02_semantics" / "semantics_manifest.json",
                bundle / "02_semantics" / "metric_dictionary.md",
                bundle / "02_semantics" / "entity_set_definitions.md",
                bundle / "02_semantics" / "clock_domain_audit.md",
            ),
            row_counts={
                "metrics": int(semantics.get("metric_count") or 0),
                "entity_sets": int(semantics.get("entity_set_count") or 0),
                "clock_domains": int(semantics.get("clock_domain_count") or 0),
            },
        )
        stop_after_stage("semantics_export", to_stage)
        context.start_stage("build_p2_index")
    p2_progress = {"events": 0, "bytes": 0, "files": 0}

    def p2_index_progress(row: dict[str, Any]) -> None:
        p2_progress["events"] = int(row.get("events_parsed") or 0)
        p2_progress["bytes"] = int(row.get("processed_bytes") or 0)
        p2_progress["files"] = int(row.get("source_files_completed") or 0)
        if context:
            context.heartbeat(
                EVENT_FILES_COMPLETED=p2_progress["files"],
                EVENT_BYTES_READ=p2_progress["bytes"],
                RAW_EVENTS_READ=p2_progress["events"],
                CURRENT_SOURCE_FILE=row.get("source_file"),
                P2_INDEX_EVENTS_INDEXED=int(row.get("events_indexed") or 0),
                LAST_PROGRESS_AT=time.time(),
            )

    p2_index = build_or_reuse_p2_index(
        shared_p2_sources(discovery),
        cache_root=bundle / "cache",
        cfg=cfg,
        force_rebuild=rebuild_p2_index,
        progress_callback=p2_index_progress,
    )
    p2_index_payload = p2_index.to_dict()
    write_json(bundle / "00_inventory" / "p2_index_metrics.json", p2_index_payload)
    if context:
        context.finish_stage(
            "build_p2_index",
            status="SKIPPED" if p2_index.status == "NOT_APPLICABLE" else p2_index.status,
            output_files=[p2_index.manifest_path],
            row_counts={"events_indexed": int(p2_index.metrics.get("events_indexed") or 0)},
            cache_hit=p2_index.status == "CACHED",
        )
        stop_after_stage("build_p2_index", to_stage)
        context.start_stage("normalize_runs")
    progress_totals = {"runs": 0, "bytes": 0, "events": 0, "requests": 0, "p2": 0}
    progress_run_ids: set[str] = set()

    def normalization_progress(row: dict[str, Any]) -> None:
        progress_key = str(row.get("run_id") or row.get("task_id") or progress_totals["runs"])
        if progress_key in progress_run_ids:
            return
        progress_run_ids.add(progress_key)
        progress_totals["runs"] += 1
        progress_totals["bytes"] += int(row.get("event_bytes") or 0)
        progress_totals["events"] += int(row.get("raw_events") or 0)
        progress_totals["requests"] += int(row.get("requests") or 0)
        progress_totals["p2"] += int(row.get("p2_requests") or 0)
        if context:
            context.heartbeat(
                RUNS_SCANNED=progress_totals["runs"],
                RUNS_PARSED=progress_totals["runs"],
                CURRENT_RUN_ID=row.get("run_id"),
                EVENT_FILES_COMPLETED=p2_progress["files"] + progress_totals["runs"],
                EVENT_BYTES_READ=p2_progress["bytes"] + progress_totals["bytes"],
                RAW_EVENTS_READ=p2_progress["events"] + progress_totals["events"],
                REQUEST_ATTEMPTS_PARSED=progress_totals["requests"],
                P2_REQUESTS_PARSED=progress_totals["p2"],
                LAST_PROGRESS_AT=time.time(),
            )
    normalized = normalize_experiment(
        discovery,
        bundle,
        cfg,
        output_format=output_format,
        strict_profile=strict_profile,
        p2_index_path=p2_index.index_path,
        progress_callback=normalization_progress,
    )
    p2_index_payload = finalize_p2_index_metrics(p2_index_payload, normalized)
    write_json(bundle / "00_inventory" / "p2_index_metrics.json", p2_index_payload)
    if context:
        context.update_progress(
            RUNS_PARSED=len(normalized.tables.get("runs", [])),
            REQUEST_ATTEMPTS_PARSED=len(normalized.tables.get("requests", [])),
            P2_REQUESTS_PARSED=len(normalized.tables.get("p2_requests", [])),
        )
        context.finish_stage(
            "normalize_runs",
            output_files=normalized_stage_output_files(normalized, "normalize_runs"),
            row_counts={
                "runs": len(normalized.tables.get("runs", [])),
                "requests": len(normalized.tables.get("requests", [])),
                "p2_requests": len(normalized.tables.get("p2_requests", [])),
            },
            warnings=(
                [str(normalized.execution_metadata.get("fallback_reason"))]
                if normalized.execution_metadata.get("parallel_status") == "FALLBACK_SERIAL"
                else None
            ),
        )
        stop_after_stage("normalize_runs", to_stage)
        for stage_name in ("normalize_requests", "normalize_scheduler", "normalize_resources", "p1_p2_audit"):
            context.start_stage(stage_name)
            context.finish_stage(
                stage_name,
                status="PASS",
                output_files=normalized_stage_output_files(normalized, stage_name),
                row_counts={
                    table_name: len(normalized.tables.get(table_name, []))
                    for table_name in normalized_stage_tables(stage_name)
                },
            )
            stop_after_stage(stage_name, to_stage)
    result = {
        "schema_version": "1.0",
        "command": "analysis.prepare",
        "bundle": str(bundle),
        "experiment_id": discovery.manifest["experiment_id"],
        "experiments_found": len(discovery.experiment_inventory),
        "trials_found": len(discovery.trial_inventory),
        "batches_found": len(discovery.batch_inventory),
        "runs_parsed": len(normalized.tables.get("runs", [])),
        "request_attempts_parsed": len(normalized.tables.get("requests", [])),
        "p2_requests_parsed": len(normalized.tables.get("p2_requests", [])),
        "skipped_runs": normalized.skipped_runs,
        "fast_smoke": cfg.output.fast_smoke,
        "normalization_jobs_requested": cfg.runtime.jobs,
        "normalization_execution": normalized.execution_metadata,
        "omitted_normalized_tables": sorted(normalized_omitted_tables(cfg)),
        "include_evidence": cfg.output.include_evidence,
        "include_model_pack": cfg.output.include_model_pack,
        "include_handoff": cfg.output.include_handoff,
        "contract": contract,
        "semantics": {
            "status": semantics.get("status"),
            "profile": semantics.get("profile"),
            "metric_count": semantics.get("metric_count"),
        },
        "p2_index": p2_index_payload,
        "duration_s": time.monotonic() - started,
    }
    write_readme(bundle)
    write_json(bundle / "run_summary.json", result)
    return {"result": result, "discovery": discovery, "normalized": normalized}


def run_all_analysis(
    *,
    input_root: str | Path,
    output_root: str | Path,
    cfg: AnalysisConfig,
    output_format: str,
    force: bool = False,
    resume: bool = False,
    fresh: bool = False,
    force_resume: bool = False,
    preflight: bool = False,
    strict: bool = False,
    no_figures: bool = False,
    log_level: str = "INFO",
    force_stages: list[str] | None = None,
    invalidate_stages: list[str] | None = None,
    from_stage: str | None = None,
    to_stage: str | None = None,
    dry_run: bool = False,
    experiment_contract: str | Path | None = None,
) -> dict[str, Any]:
    bundle = Path(output_root).expanduser().resolve()
    bundle.mkdir(parents=True, exist_ok=True)
    context = AnalysisRunContext(
        bundle_root=bundle,
        status_file=cfg.runtime.status_file,
        temp_dir=cfg.runtime.temp_dir,
        progress_interval_seconds=cfg.runtime.progress_interval_seconds,
        timeout_seconds=cfg.runtime.timeout_seconds,
        log_level=log_level,
        config_hash=config_fingerprint(cfg),
        code_version=source_fingerprint(),
    )
    try:
        execution_plan = build_stage_execution_plan(
            bundle,
            resume=resume or from_stage is not None or bool(force_stages) or bool(invalidate_stages),
            from_stage=from_stage,
            to_stage=to_stage,
            force_stages=force_stages,
            invalidate_stages=invalidate_stages,
            dry_run=dry_run,
        )
        if dry_run:
            result = {
                "schema_version": "1.0",
                "command": "analysis.run-all",
                "bundle": str(bundle),
                "analysis_status": "SKIPPED",
                "final_status": "SKIPPED",
                "process_status": "COMPLETE",
                "reason": "dry_run",
                "stage_execution_plan": execution_plan,
            }
            write_json(bundle / "run_summary.json", result)
            write_skipped_contract(bundle, "dry_run")
            context.complete("SKIPPED")
            return result
        return _run_all_analysis_impl(
            input_root=input_root,
            output_root=output_root,
            cfg=cfg,
            output_format=output_format,
            force=force,
            resume=resume,
            fresh=fresh,
            force_resume=force_resume,
            preflight=preflight,
            strict=strict,
            no_figures=no_figures,
            log_level=log_level,
            context=context,
            force_stages=force_stages,
            invalidate_stages=invalidate_stages,
            from_stage=from_stage,
            to_stage=to_stage,
            experiment_contract=experiment_contract,
        )
    except StageRangeComplete as stopped:
        context.skip_pending_after(stopped.stage_name, reason="outside_requested_stage_range")
        result = {
            "schema_version": "1.0",
            "command": "analysis.run-all",
            "bundle": str(bundle),
            "analysis_status": "SKIPPED",
            "final_status": "SKIPPED",
            "process_status": "COMPLETE",
            "reason": "requested_terminal_stage_completed",
            "terminal_stage": stopped.stage_name,
            "terminal_stage_status": context.records[stopped.stage_name].status,
            "stage_completion_status": {name: record.status for name, record in context.records.items()},
        }
        write_skipped_contract(bundle, f"completed_through_stage:{stopped.stage_name}")
        write_json(bundle / "run_summary.json", result)
        write_analysis_manifest(
            bundle,
            build_analysis_manifest(
                input_root=input_root,
                output_root=bundle,
                cfg=cfg,
                output_format=output_format,
                input_inventory=read_json(bundle / "00_inventory" / "input_inventory_summary.json", default={}),
                stage_status=result["stage_completion_status"],
                final_status="SKIPPED",
                cli_arguments={"to_stage": stopped.stage_name},
            ),
        )
        context.complete("SKIPPED")
        return result
    except Exception as exc:  # noqa: BLE001 - convert crashes into an auditable execution result
        failed_stage = context.current_phase or "unknown"
        if failed_stage in context.records and context.records[failed_stage].status == "RUNNING":
            context.fail_stage(failed_stage, exc)
        result = early_result(
            bundle=bundle,
            command="analysis.run-all",
            final_status="FAIL",
            reason="execution_failure",
            preflight_status="UNKNOWN",
            input_inventory_status="UNKNOWN",
            input_baseline_status="UNKNOWN",
            resume_status="UNKNOWN",
            extra={
                "execution_status": "FAIL",
                "failed_stage": failed_stage,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        write_failure_contract(bundle, result)
        write_analysis_manifest(
            bundle,
            build_analysis_manifest(
                input_root=input_root,
                output_root=bundle,
                cfg=cfg,
                output_format=output_format,
                input_inventory=read_json(bundle / "00_inventory" / "input_inventory_summary.json", default={}),
                stage_status={name: record.status for name, record in context.records.items()},
                final_status="FAIL",
                cli_arguments={"failure": "execution_failure"},
            ),
        )
        write_failure_diagnosis(bundle, result, context)
        context.complete("FAIL")
        return result


def _run_all_analysis_impl(
    *,
    input_root: str | Path,
    output_root: str | Path,
    cfg: AnalysisConfig,
    output_format: str,
    force: bool = False,
    resume: bool = False,
    fresh: bool = False,
    force_resume: bool = False,
    preflight: bool = False,
    strict: bool = False,
    no_figures: bool = False,
    log_level: str = "INFO",
    context: AnalysisRunContext,
    force_stages: list[str] | None = None,
    invalidate_stages: list[str] | None = None,
    from_stage: str | None = None,
    to_stage: str | None = None,
    experiment_contract: str | Path | None = None,
) -> dict[str, Any]:
    bundle = Path(output_root).expanduser().resolve()
    cli_arguments = {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "output_format": output_format,
        "force": force,
        "resume": resume,
        "fresh": fresh,
        "force_resume": force_resume,
        "preflight": preflight,
        "strict": strict,
        "no_figures": no_figures,
        "force_stages": force_stages or [],
        "invalidate_stages": invalidate_stages or [],
        "from_stage": from_stage,
        "to_stage": to_stage,
        "experiment_contract": str(experiment_contract) if experiment_contract else None,
    }
    preflight_result = None
    if preflight or strict:
        context.start_stage("preflight")
        preflight_result = run_analysis_preflight(
            input_root=input_root,
            output_root=output_root,
            temp_dir=cfg.runtime.temp_dir,
            profile=cfg.profile,
            output_format=output_format,
            min_free_bytes=cfg.runtime.min_free_bytes,
            json_output_path=bundle / "analysis_preflight.json",
        )
        preflight_status = preflight_result.get("python_preflight_status")
        context.finish_stage("preflight", status="PASS" if preflight_status == "PASS" else "FAIL", output_files=[str(bundle / "analysis_preflight.json")])
        if preflight_status != "PASS":
            result = early_result(
                bundle=bundle,
                command="analysis.run-all",
                final_status="FAIL",
                reason="preflight_failed",
                preflight_status="FAIL",
                input_inventory_status="SKIPPED",
                input_baseline_status="SKIPPED",
                resume_status="NOT_EVALUATED",
            )
            write_failure_contract(bundle, result)
            write_analysis_manifest(
                bundle,
                build_analysis_manifest(
                    input_root=input_root,
                    output_root=bundle,
                    cfg=cfg,
                    output_format=output_format,
                    input_inventory=None,
                    stage_status={name: record.status for name, record in context.records.items()},
                    final_status="FAIL",
                    cli_arguments=cli_arguments,
                ),
            )
            write_failure_diagnosis(bundle, result, context)
            context.complete("FAIL")
            return result
        stop_after_stage("preflight", to_stage)
    else:
        context.start_stage("preflight")
        context.finish_stage("preflight", status="SKIPPED", warnings=["preflight_not_requested"])
        stop_after_stage("preflight", to_stage)
    context.start_stage("input_inventory")
    input_inventory = build_input_inventory(input_root)
    write_input_inventory(bundle, input_inventory)
    context.set_input_hashes({"input_fingerprint": str(input_inventory.get("input_fingerprint") or "")})
    context.update_progress(
        RUNS_DISCOVERED=input_inventory.get("run_directories_discovered", 0),
        EVENT_BYTES_DISCOVERED=input_inventory.get("total_events_bytes", 0),
    )
    context.finish_stage("input_inventory", status=input_inventory.get("inventory_status", "PARTIAL"), output_files=[str(bundle / "00_inventory" / "input_inventory_summary.json")])
    stop_after_stage("input_inventory", to_stage)
    expectations = baseline_expectations(cfg)
    context.start_stage("baseline_check")
    baseline = check_input_baseline(input_inventory, expectations)
    write_json(bundle / "00_inventory" / "input_baseline_check.json", baseline)
    context.finish_stage("baseline_check", status=baseline.get("input_baseline_status", "PARTIAL"), output_files=[str(bundle / "00_inventory" / "input_baseline_check.json")])
    if baseline.get("input_baseline_status") == "FAIL":
        result = early_result(
            bundle=bundle,
            command="analysis.run-all",
            final_status="FAIL",
            reason="input_baseline_mismatch",
            preflight_status=(preflight_result or {}).get("python_preflight_status", "NOT_RUN"),
            input_inventory_status=input_inventory.get("inventory_status"),
            input_baseline_status="FAIL",
            resume_status="NOT_EVALUATED",
            extra={"input_baseline": baseline},
        )
        write_failure_contract(bundle, result)
        write_analysis_manifest(
            bundle,
            build_analysis_manifest(
                input_root=input_root,
                output_root=bundle,
                cfg=cfg,
                output_format=output_format,
                input_inventory=input_inventory,
                stage_status={name: record.status for name, record in context.records.items()},
                final_status="FAIL",
                cli_arguments=cli_arguments,
            ),
        )
        write_failure_diagnosis(bundle, result, context)
        context.complete("FAIL")
        return result
    stop_after_stage("baseline_check", to_stage)

    context.start_stage("resume_check")
    resume_decision = decide_resume(
        bundle_root=bundle,
        input_root=input_root,
        cfg=cfg,
        output_format=output_format,
        input_inventory=input_inventory,
        resume=(resume or from_stage is not None or bool(force_stages) or bool(invalidate_stages)) and not fresh,
        force_resume=force_resume,
    )
    write_json(bundle / "resume_status.json", resume_decision.to_dict())
    if resume_decision.status == "REJECTED":
        context.finish_stage("resume_check", status="FAIL", output_files=[str(bundle / "resume_status.json")])
        result = early_result(
            bundle=bundle,
            command="analysis.run-all",
            final_status="FAIL",
            reason="resume_rejected",
            preflight_status=(preflight_result or {}).get("python_preflight_status", "NOT_RUN"),
            input_inventory_status=input_inventory.get("inventory_status"),
            input_baseline_status=baseline.get("input_baseline_status"),
            resume_status="REJECTED",
            extra={"resume": resume_decision.to_dict()},
        )
        write_failure_contract(bundle, result)
        write_json(
            bundle / "resume_rejected_attempt.json",
            build_analysis_manifest(
                input_root=input_root,
                output_root=bundle,
                cfg=cfg,
                output_format=output_format,
                input_inventory=input_inventory,
                stage_status={name: record.status for name, record in context.records.items()},
                final_status="FAIL",
                cli_arguments=cli_arguments,
            ),
        )
        write_failure_diagnosis(bundle, result, context)
        context.complete("FAIL")
        return result

    forced = set(force_stages or []) | set(invalidate_stages or [])
    normalization_stages = {"discovery", "manifest", "inventory", "schema_resolution", "build_p2_index", "normalize_runs", "normalize_requests", "normalize_scheduler", "normalize_resources", "p1_p2_audit"}
    force_normalization = bool(forced & normalization_stages)
    if from_stage and STAGE_NAMES.index(from_stage) > STAGE_NAMES.index("p1_p2_audit") and not resume_decision.cached_normalized:
        context.finish_stage("resume_check", status="FAIL", output_files=[str(bundle / "resume_status.json")])
        result = early_result(
            bundle=bundle,
            command="analysis.run-all",
            final_status="FAIL",
            reason="from_stage_requires_compatible_normalized_cache",
            preflight_status=(preflight_result or {}).get("python_preflight_status", "NOT_RUN"),
            input_inventory_status=input_inventory.get("inventory_status"),
            input_baseline_status=baseline.get("input_baseline_status"),
            resume_status=resume_decision.status,
        )
        write_failure_contract(bundle, result)
        write_failure_diagnosis(bundle, result, context)
        context.complete("FAIL")
        return result
    context.finish_stage(
        "resume_check",
        status="SKIPPED" if resume_decision.status == "NOT_REQUESTED" else "PASS",
        output_files=[str(bundle / "resume_status.json")],
        cache_hit=resume_decision.cached_normalized,
        warnings=list(resume_decision.warnings),
    )
    stop_after_stage("resume_check", to_stage)
    if resume_decision.cached_normalized and not force_normalization:
        context.start_stage("discovery")
        discovery = discover_experiment(input_root, cfg)
        context.finish_stage("discovery", status="PASS", row_counts={"runs_discovered": len(discovery.run_inventory)}, cache_hit=False)
        stop_after_stage("discovery", to_stage)
        context.start_stage("manifest")
        write_manifest(discovery, bundle, cfg)
        context.finish_stage("manifest", status="PASS", output_files=[str(bundle / "manifest" / "experiment_manifest.json")])
        stop_after_stage("manifest", to_stage)
        context.start_stage("inventory")
        write_inventory(discovery, bundle, output_format=output_format)
        context.finish_stage(
            "inventory",
            status="PASS",
            output_files=[
                str(bundle / "00_inventory" / "inventory_summary.json"),
                str(bundle / "00_inventory" / "inventory_manifest.json"),
            ],
            row_counts={"files": len(discovery.file_inventory), "runs": len(discovery.run_inventory)},
        )
        stop_after_stage("inventory", to_stage)
        context.start_stage("contract")
        contract = write_experiment_contract(
            bundle_root=bundle,
            discovery=discovery,
            cfg=cfg,
            output_format=output_format,
            contract_path=experiment_contract,
        )
        context.finish_stage(
            "contract",
            status=contract.get("status", "PARTIAL"),
            output_files=existing_paths(
                bundle / "00_inventory" / "experiment_contract.json",
                bundle / "00_inventory" / "contract_manifest.json",
                bundle / "00_inventory" / "expected_artifacts.json",
            ),
            row_counts={
                "contract_validation": int(contract.get("validation_rows") or 0),
                "research_questions": int(contract.get("research_question_rows") or 0),
            },
        )
        stop_after_stage("contract", to_stage)
        context.start_stage("schema_resolution")
        context.finish_stage("schema_resolution", status="CACHED", row_counts={"schema_versions": len(discovery.schema_versions)}, cache_hit=True)
        stop_after_stage("schema_resolution", to_stage)
        context.start_stage("semantics_export")
        semantics = write_semantics_bundle(
            bundle_root=bundle,
            profile=cfg.profile,
            output_format=output_format,
        )
        context.finish_stage(
            "semantics_export",
            status=semantics.get("status", "PARTIAL"),
            output_files=existing_paths(
                bundle / "02_semantics" / "semantics_manifest.json",
                bundle / "02_semantics" / "metric_dictionary.md",
                bundle / "02_semantics" / "entity_set_definitions.md",
                bundle / "02_semantics" / "clock_domain_audit.md",
            ),
            row_counts={
                "metrics": int(semantics.get("metric_count") or 0),
                "entity_sets": int(semantics.get("entity_set_count") or 0),
                "clock_domains": int(semantics.get("clock_domain_count") or 0),
            },
        )
        stop_after_stage("semantics_export", to_stage)
        normalized = load_normalized_dataset(bundle)
        for stage_name in ("build_p2_index", "normalize_runs", "normalize_requests", "normalize_scheduler", "normalize_resources", "p1_p2_audit"):
            context.start_stage(stage_name)
            cached_outputs = (
                [str(bundle / "cache" / "p2_index_manifest.json")]
                if stage_name == "build_p2_index" and (bundle / "cache" / "p2_index_manifest.json").is_file()
                else normalized_stage_output_files(normalized, stage_name, bundle_root=bundle)
            )
            context.finish_stage(
                stage_name,
                status="CACHED",
                output_files=cached_outputs,
                row_counts={
                    table_name: len(normalized.tables.get(table_name, []))
                    for table_name in normalized_stage_tables(stage_name)
                },
                cache_hit=True,
            )
            stop_after_stage(stage_name, to_stage)
        prepared = {
            "result": prepared_result_from_cached(bundle, discovery, normalized, started=time.monotonic(), cfg=cfg),
            "discovery": discovery,
            "normalized": normalized,
        }
        prepared["result"]["contract"] = contract
        prepared["result"]["semantics"] = {
            "status": semantics.get("status"),
            "profile": semantics.get("profile"),
            "metric_count": semantics.get("metric_count"),
        }
    else:
        prepared = prepare_analysis_bundle(
            input_root=input_root,
            output_root=output_root,
            cfg=cfg,
            output_format=output_format,
            force=force,
            resume=resume,
            context=context,
            strict_profile=strict,
            rebuild_p2_index=fresh or force or "build_p2_index" in forced,
            to_stage=to_stage,
            experiment_contract=experiment_contract,
        )
    discovery: DiscoveryResult = prepared["discovery"]
    normalized: NormalizedDataset = prepared["normalized"]
    baseline = check_input_baseline(
        input_inventory,
        expectations,
        parsed_request_attempts=len(normalized.tables.get("requests", [])),
        parsed_p2_requests=len(normalized.tables.get("p2_requests", [])),
    )
    write_json(bundle / "00_inventory" / "input_baseline_check.json", baseline)
    context.start_stage("baseline_check")
    context.finish_stage(
        "baseline_check",
        status=baseline.get("input_baseline_status", "PARTIAL"),
        output_files=[str(bundle / "00_inventory" / "input_baseline_check.json")],
    )
    if baseline.get("input_baseline_status") == "FAIL":
        result = {
            **prepared["result"],
            "command": "analysis.run-all",
            "analysis_status": "FAIL",
            "final_status": "FAIL",
            "preflight_status": (preflight_result or {}).get("python_preflight_status", "NOT_RUN"),
            "input_inventory_status": input_inventory.get("inventory_status"),
            "input_baseline_status": "FAIL",
            "resume_status": resume_decision.status,
            "input_baseline": baseline,
        }
        write_failure_contract(bundle, result)
        write_analysis_manifest(
            bundle,
            build_analysis_manifest(
                input_root=input_root,
                output_root=bundle,
                cfg=cfg,
                output_format=output_format,
                input_inventory=input_inventory,
                stage_status={name: record.status for name, record in context.records.items()},
                final_status="FAIL",
                cli_arguments=cli_arguments,
            ),
        )
        write_failure_diagnosis(bundle, result, context)
        context.complete("FAIL")
        return result

    context.start_stage("validation")
    validation = validate_analysis_bundle(
        bundle_root=bundle,
        cfg=cfg,
        discovery=discovery,
        normalized=normalized,
        strict=strict,
        output_format=output_format,
    )
    context.finish_stage("validation", status=validation.get("overall_status", "PARTIAL"), output_files=[str(bundle / "02_validation" / "analysis_ready.json")])
    stop_after_stage("validation", to_stage)
    stage_cache = cached_stage_names(bundle)
    analysis = analyze_bundle(
        bundle_root=bundle,
        cfg=cfg,
        normalized=normalized,
        output_format=output_format,
        no_figures=no_figures,
        stage_context=context,
        terminal_stage=to_stage,
        stop_callback=lambda stage_name: stop_after_stage(stage_name, to_stage),
        cached_stages=stage_cache,
    )
    context.start_stage("evidence_build")
    if "evidence_build" in stage_cache and (bundle / "04_evidence" / "claim_registry.json").is_file():
        evidence = {"schema_version": "1.0", "status": "CACHED", "path": str(bundle / "04_evidence")}
        context.finish_stage(
            "evidence_build",
            status="CACHED",
            output_files=existing_paths(
                bundle / "04_evidence" / "evidence_manifest.json",
                bundle / "04_evidence" / "claim_registry.json",
            ),
            cache_hit=True,
        )
    else:
        evidence = (
            skipped_output(bundle, "04_evidence", "evidence", "disabled_by_fast_smoke_or_config")
            if cfg.output.fast_smoke or not cfg.output.include_evidence
            else build_evidence_bundle(
                bundle_root=bundle,
                output_format=output_format,
                include_claim_registry=cfg.output.include_claim_registry,
            )
        )
        context.finish_stage(
            "evidence_build",
            status=evidence.get("status", "PASS") if evidence.get("status") == "SKIPPED" else "PASS",
            output_files=existing_paths(
                bundle / "04_evidence" / "evidence_manifest.json",
                bundle / "04_evidence" / "claim_registry.json",
                bundle / "04_evidence" / "skip_reason.json",
            ),
        )
    stop_after_stage("evidence_build", to_stage)
    context.start_stage("optimizer_harness_build")
    if "optimizer_harness_build" in stage_cache and (bundle / "04_evidence" / "optimizer_experiment" / "optimizer_harness_manifest.json").is_file():
        optimizer_harness = read_json(
            bundle / "04_evidence" / "optimizer_experiment" / "optimizer_harness_manifest.json",
            default={},
        )
        optimizer_harness = {**optimizer_harness, "status": "CACHED"}
        context.finish_stage(
            "optimizer_harness_build",
            status="CACHED",
            output_files=[str(bundle / "04_evidence" / "optimizer_experiment" / "optimizer_harness_manifest.json")],
            cache_hit=True,
        )
    else:
        optimizer_harness = (
            skipped_output(
                bundle,
                "04_evidence/optimizer_experiment",
                "optimizer_harness",
                "disabled_by_fast_smoke_or_config",
            )
            if cfg.output.fast_smoke or not cfg.output.include_evidence
            else build_optimizer_harness_stage_bundle(bundle, discovery)
        )
        context.finish_stage(
            "optimizer_harness_build",
            status=optimizer_harness.get("status", "PARTIAL"),
            output_files=existing_paths(
                bundle / "04_evidence" / "optimizer_experiment" / "optimizer_harness_manifest.json",
                bundle / "04_evidence" / "optimizer_experiment" / "skip_reason.json",
            ),
            row_counts={
                "batches": int(optimizer_harness.get("batch_count") or 0),
                "decisions": int(optimizer_harness.get("decision_count") or 0),
            },
        )
    stop_after_stage("optimizer_harness_build", to_stage)
    context.start_stage("handoff_build")
    if "handoff_build" in stage_cache and (bundle / "05_handoff" / "handoff_manifest.json").is_file():
        handoff = read_json(bundle / "05_handoff" / "handoff_manifest.json", default={})
        handoff = {**handoff, "status": "CACHED"}
        context.finish_stage(
            "handoff_build",
            status="CACHED",
            output_files=[str(bundle / "05_handoff" / "handoff_manifest.json")],
            cache_hit=True,
        )
    else:
        handoff = (
            skipped_output(bundle, "05_handoff", "handoff", "disabled_by_fast_smoke_or_config")
            if cfg.output.fast_smoke or not cfg.output.include_handoff
            else build_handoff_package(bundle_root=bundle, output_format=output_format)
        )
        context.finish_stage(
            "handoff_build",
            status=handoff.get("status", "PASS"),
            output_files=existing_paths(
                bundle / "05_handoff" / "handoff_manifest.json",
                bundle / "05_handoff" / "skip_reason.json",
            ),
        )
    stop_after_stage("handoff_build", to_stage)
    skip_model_pack = (
        cfg.output.fast_smoke
        or not cfg.output.include_model_pack
        or evidence.get("status") == "SKIPPED"
    )
    context.start_stage("model_pack_build")
    if "model_pack_build" in stage_cache and (bundle / "model_input_pack" / "model_pack_manifest.json").is_file():
        model_pack = read_json(bundle / "model_input_pack" / "model_pack_manifest.json", default={})
        model_pack = {**model_pack, "status": "CACHED"}
        context.finish_stage(
            "model_pack_build",
            status="CACHED",
            output_files=[str(bundle / "model_input_pack" / "model_pack_manifest.json")],
            cache_hit=True,
        )
    else:
        model_pack = (
            skipped_output(bundle, "model_input_pack", "model_pack", "disabled_by_fast_smoke_or_config")
            if skip_model_pack
            else build_model_input_pack(bundle_root=bundle, include_modules=cfg.output.include_module_packs)
        )
        context.finish_stage(
            "model_pack_build",
            status=model_pack.get("status", "PASS"),
            output_files=existing_paths(
                bundle / "model_input_pack" / "model_pack_manifest.json",
                bundle / "model_input_pack" / "skip_reason.json",
            ),
        )
    stop_after_stage("model_pack_build", to_stage)
    context.start_stage("report_prompt_build")
    prompt_status = (
        "SKIPPED"
        if model_pack.get("status") == "SKIPPED"
        else "PASS"
        if (bundle / "model_input_pack" / "report_prompt.md").is_file()
        else "PARTIAL"
    )
    context.finish_stage(
        "report_prompt_build",
        status="CACHED" if "report_prompt_build" in stage_cache and prompt_status == "PASS" else prompt_status,
        output_files=existing_paths(bundle / "model_input_pack" / "report_prompt.md"),
        cache_hit="report_prompt_build" in stage_cache and prompt_status == "PASS",
    )
    stop_after_stage("report_prompt_build", to_stage)
    write_json(
        bundle / "run_summary.json",
        {
            **prepared["result"],
            "command": "analysis.run-all",
            "analysis_status": validation.get("overall_status"),
            "final_status": "PENDING_ARTIFACT_VALIDATION",
        },
    )
    write_analysis_manifest(
        bundle,
        build_analysis_manifest(
            input_root=input_root,
            output_root=bundle,
            cfg=cfg,
            output_format=output_format,
            input_inventory=input_inventory,
            stage_status={name: record.status for name, record in context.records.items()},
            final_status="PENDING_ARTIFACT_VALIDATION",
            cli_arguments=cli_arguments,
        ),
    )
    context.start_stage("artifact_validation")
    artifacts = build_artifact_manifest(bundle, cfg)
    context.finish_stage("artifact_validation", status=artifacts.get("artifact_status", "PARTIAL"), output_files=[str(bundle / "artifact_manifest.json")])
    stop_after_stage("artifact_validation", to_stage)
    final_status = combine_final_status(validation.get("overall_status"), analysis.get("status"), artifacts.get("artifact_status"))
    summary = {
        **prepared["result"],
        "command": "analysis.run-all",
        "process_status": "COMPLETE",
        "analysis_status": final_status,
        "final_status": final_status,
        "preflight_status": (preflight_result or {}).get("python_preflight_status", "NOT_RUN"),
        "input_inventory_status": input_inventory.get("inventory_status"),
        "input_baseline_status": baseline.get("input_baseline_status"),
        "resume_status": resume_decision.status,
        "resume": resume_decision.to_dict(),
        "input_baseline": baseline,
        "analysis": analysis,
        "evidence": evidence,
        "optimizer_harness": optimizer_harness,
        "handoff": handoff,
        "model_pack": model_pack,
        "artifact_status": artifacts.get("artifact_status"),
        "required_artifact_status": artifacts.get("required_artifact_status"),
        "artifacts": artifacts,
    }
    context.start_stage("final_summary")
    write_json(bundle / "run_summary.json", summary)
    context.finish_stage("final_summary", status=str(final_status), output_files=[str(bundle / "run_summary.json")])
    write_analysis_manifest(
        bundle,
        build_analysis_manifest(
            input_root=input_root,
            output_root=bundle,
            cfg=cfg,
            output_format=output_format,
            input_inventory=input_inventory,
            stage_status={name: record.status for name, record in context.records.items()},
            final_status=str(final_status),
            cli_arguments=cli_arguments,
        ),
    )
    if final_status == "FAIL":
        write_failure_diagnosis(bundle, summary, context)
    context.complete(str(final_status))
    return summary


def baseline_expectations(cfg: AnalysisConfig) -> BaselineExpectations:
    return BaselineExpectations(
        expected_runs=cfg.baseline.expected_runs,
        expected_request_attempts=cfg.baseline.expected_request_attempts,
        expected_p2_requests=cfg.baseline.expected_p2_requests,
        allow_mismatch=cfg.baseline.allow_baseline_mismatch,
    )


def normalized_stage_tables(stage_name: str) -> tuple[str, ...]:
    return {
        "normalize_runs": ("experiments", "trials", "batches", "runs", "steps", "spans", "trajectories", "task_results"),
        "normalize_requests": ("requests", "request_lifecycle", "request_id_aliases"),
        "normalize_scheduler": ("scheduler_iterations",),
        "normalize_resources": ("resource_samples", "workers", "worker_intervals"),
        "p1_p2_audit": (
            "p1_request_attempts",
            "p2_requests",
            "request_parse_audit",
            "request_parse_conservation",
            "request_identity_audit",
            "p2_index_query_audit",
            "profile_detection_audit",
        ),
    }.get(stage_name, ())


def normalized_stage_output_files(
    normalized: NormalizedDataset,
    stage_name: str,
    *,
    bundle_root: str | Path | None = None,
) -> list[str]:
    files: list[str] = []
    normalized_dir = Path(bundle_root) / "01_normalized" if bundle_root is not None else None
    for name in normalized_stage_tables(stage_name):
        artifact = normalized.artifacts.get(name, {})
        if artifact.get("path"):
            files.append(str(artifact["path"]))
            continue
        if normalized_dir is not None:
            matches = [
                candidate
                for suffix in (".parquet", ".csv.gz", ".csv")
                if (candidate := normalized_dir / f"{name}{suffix}").is_file()
            ]
            if matches:
                files.append(str(matches[0]))
    return files


def existing_paths(*paths: Path) -> list[str]:
    return [str(path) for path in paths if path.is_file()]


def shared_p2_sources(discovery: DiscoveryResult) -> list[str]:
    run_event_paths = {
        str(Path(str(row.get("events_path"))).resolve())
        for row in discovery.run_inventory
        if row.get("events_path")
    }
    return [
        str(Path(str(path)).resolve())
        for path in discovery.manifest.get("p2_stream_locations", [])
        if str(Path(str(path)).resolve()) not in run_event_paths
    ]


def finalize_p2_index_metrics(payload: dict[str, Any], normalized: NormalizedDataset) -> dict[str, Any]:
    result = {**payload, "metrics": dict(payload.get("metrics") or {})}
    rows = normalized.tables.get("p2_index_query_audit", [])
    result["metrics"]["index_query_wall_s"] = sum(float(row.get("index_query_wall_s") or 0.0) for row in rows)
    result["metrics"]["events_scanned_per_run"] = {
        str(row.get("run_id")): int(row.get("events_scanned_per_run") or 0)
        for row in rows
    }
    result["metrics"]["bytes_scanned_per_run"] = {
        str(row.get("run_id")): int(row.get("bytes_scanned_per_run") or 0)
        for row in rows
    }
    result["metrics"]["query_count"] = len(rows)
    return result


def combine_final_status(*statuses: Any) -> str:
    values = {str(status) for status in statuses if status is not None}
    if "FAIL" in values:
        return "FAIL"
    if values & {"PARTIAL", "UNAVAILABLE", "UNRESOLVED"}:
        return "PARTIAL"
    return "PASS"


def cached_stage_names(bundle: Path) -> set[str]:
    plan = read_json(bundle / "stage_execution_plan.json", default={})
    rows = plan.get("stages", []) if isinstance(plan, dict) else []
    return {
        str(row.get("stage_name"))
        for row in rows
        if isinstance(row, dict) and row.get("action") == "CACHED"
    }


def build_optimizer_harness_stage_bundle(bundle: Path, discovery: DiscoveryResult) -> dict[str, Any]:
    target = bundle / "04_evidence" / "optimizer_experiment"
    if target.exists() and target.resolve().is_relative_to(bundle.resolve()):
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    batch_rows = [
        row for row in discovery.batch_inventory
        if row.get("batch_dir") and Path(str(row["batch_dir"])).is_dir()
    ]
    if not batch_rows:
        return skipped_output(
            bundle,
            "04_evidence/optimizer_experiment",
            "optimizer_harness",
            "no_batch_artifacts_discovered",
        )
    strategies = [
        str(row["name"])
        for row in optimizer_strategy_catalog()
        if row.get("name")
    ]
    optimizer_cfg = AgentMonConfig()
    results: list[dict[str, Any]] = []
    for index, row in enumerate(batch_rows, start=1):
        batch_dir = Path(str(row["batch_dir"])).expanduser().resolve()
        batch_id = safe_artifact_name(str(row.get("batch_id") or batch_dir.name or f"batch-{index}"))
        output_dir = target / f"{index:03d}-{batch_id}"
        result = run_optimizer_experiment_harness(
            batch_root=batch_dir,
            output_dir=output_dir,
            cfg=optimizer_cfg,
            strategies=strategies,
        )
        results.append(result)
    status_values = {str(row.get("data_quality_status") or "PARTIAL") for row in results}
    status = "PASS" if status_values <= {"COMPLETE"} else "PARTIAL"
    manifest = {
        "schema_version": "1.0",
        "artifact_type": "optimizer_harness",
        "status": status,
        "path": str(target),
        "batch_count": len(results),
        "strategy_count": len(strategies),
        "snapshot_count": sum(int(row.get("snapshot_count") or 0) for row in results),
        "decision_count": sum(int(row.get("decision_count") or 0) for row in results),
        "conflict_count": sum(int(row.get("conflict_count") or 0) for row in results),
        "strategies": strategies,
        "batches": results,
        "limitations": [
            "Analysis SOP integration runs the optimizer harness as an offline evidence side bundle.",
            "It does not enforce optimizer actions or claim causal speedup.",
        ],
    }
    write_json(target / "optimizer_harness_manifest.json", manifest)
    return manifest


def safe_artifact_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    return cleaned.strip("-") or "batch"


def early_result(
    *,
    bundle: Path,
    command: str,
    final_status: str,
    reason: str,
    preflight_status: Any,
    input_inventory_status: Any,
    input_baseline_status: Any,
    resume_status: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": "1.0",
        "command": command,
        "bundle": str(bundle),
        "analysis_status": final_status,
        "final_status": final_status,
        "reason": reason,
        "preflight_status": preflight_status,
        "input_inventory_status": input_inventory_status,
        "input_baseline_status": input_baseline_status,
        "resume_status": resume_status,
        "heavy_analysis_skipped": 1,
        "process_status": "FAIL" if final_status == "FAIL" else "COMPLETE",
    }
    if extra:
        result.update(extra)
    return result


def write_failure_contract(bundle: Path, result: dict[str, Any]) -> None:
    reason = str(result.get("reason") or "execution_failure")
    module_status = {
        "inventory": result.get("input_inventory_status") or "SKIPPED",
        "schema": "SKIPPED",
        "request_conservation": "SKIPPED",
        "time_conservation": "SKIPPED",
        "lifecycle": "SKIPPED",
        "little_law": "SKIPPED",
        "pipeline_reconciliation": "SKIPPED",
        "resource": "SKIPPED",
        "p1_capture": "SKIPPED",
        "token_timeseries": "SKIPPED",
        "p2_audit": "SKIPPED",
        "npu_mapping": "UNRESOLVED",
    }
    ready = {
        "schema_version": "1.0",
        "overall_status": "FAIL",
        "module_status": module_status,
        "allowed_conclusions": [],
        "forbidden_conclusions": ["Do not draw analysis conclusions from an incomplete or failed bundle."],
        "unresolved_inputs": [reason],
        "required_followup_experiments": [],
        "failure": {
            "reason": reason,
            "exception_type": result.get("exception_type"),
            "exception_message": result.get("exception_message"),
        },
    }
    write_json(bundle / "02_validation" / "analysis_ready.json", ready)
    write_json(bundle / "02_validation" / "validation_status.json", ready)
    write_json(
        bundle / "02_validation" / "data_quality_gate.json",
        {
            "schema_version": "1.0",
            "gate_status": "FAIL",
            "module_status": module_status,
            "blocking_module_count": 1,
            "blocking_modules": ["execution"],
            "forbidden_conclusions": ready["forbidden_conclusions"],
            "unresolved_inputs": ready["unresolved_inputs"],
            "required_followup_experiments": [],
            "notes": "Analysis did not complete; no missing evidence was imputed.",
        },
    )
    write_json(bundle / "analysis_ready.json", ready)
    write_json(bundle / "run_summary.json", result)


def write_skipped_contract(bundle: Path, reason: str) -> None:
    ready = {
        "schema_version": "1.0",
        "overall_status": "SKIPPED",
        "module_status": {},
        "allowed_conclusions": [],
        "forbidden_conclusions": ["Do not draw conclusions from a dry-run execution plan."],
        "unresolved_inputs": [reason],
        "required_followup_experiments": [],
    }
    write_json(bundle / "analysis_ready.json", ready)
    write_json(bundle / "02_validation" / "analysis_ready.json", ready)
    write_json(
        bundle / "02_validation" / "data_quality_gate.json",
        {
            "schema_version": "1.0",
            "gate_status": "SKIPPED",
            "module_status": {},
            "blocking_module_count": 0,
            "blocking_modules": [],
            "forbidden_conclusions": ready["forbidden_conclusions"],
            "unresolved_inputs": ready["unresolved_inputs"],
            "required_followup_experiments": [],
            "notes": "Dry-run or requested stop did not produce analyzable outputs.",
        },
    )


def prepared_result_from_cached(
    bundle: Path,
    discovery: DiscoveryResult,
    normalized: NormalizedDataset,
    *,
    started: float,
    cfg: AnalysisConfig,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "command": "analysis.prepare",
        "bundle": str(bundle),
        "experiment_id": discovery.manifest["experiment_id"],
        "experiments_found": len(discovery.experiment_inventory),
        "trials_found": len(discovery.trial_inventory),
        "batches_found": len(discovery.batch_inventory),
        "runs_parsed": len(normalized.tables.get("runs", [])),
        "request_attempts_parsed": len(normalized.tables.get("requests", [])),
        "p2_requests_parsed": len(normalized.tables.get("p2_requests", [])),
        "skipped_runs": normalized.skipped_runs,
        "fast_smoke": cfg.output.fast_smoke,
        "normalization_jobs_requested": cfg.runtime.jobs,
        "normalization_execution": {**normalized.execution_metadata, "cache_reused": True},
        "omitted_normalized_tables": sorted(normalized_omitted_tables(cfg)),
        "include_evidence": cfg.output.include_evidence,
        "include_model_pack": cfg.output.include_model_pack,
        "include_handoff": cfg.output.include_handoff,
        "duration_s": time.monotonic() - started,
    }


def skipped_output(bundle: Path, relative_dir: str, artifact_type: str, reason: str) -> dict[str, Any]:
    target = bundle / relative_dir
    resolved_bundle = bundle.resolve()
    resolved_target = target.resolve()
    if target.exists() and resolved_target != resolved_bundle and resolved_target.is_relative_to(resolved_bundle):
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "artifact_type": artifact_type,
        "status": "SKIPPED",
        "reason": reason,
        "path": str(target),
    }
    write_json(target / "skip_reason.json", payload)
    return payload


def write_manifest(discovery: DiscoveryResult, bundle: Path, cfg: AnalysisConfig) -> None:
    manifest_dir = bundle / "manifest"
    write_json(manifest_dir / "experiment_manifest.json", discovery.manifest)
    write_json(manifest_dir / "experiment_manifest.yaml", discovery.manifest)
    write_resolved_config(bundle / "analysis_config.resolved.yaml", cfg)


def write_inventory(discovery: DiscoveryResult, bundle: Path, *, output_format: str) -> None:
    inventory_dir = bundle / "00_inventory"
    tables = {
        "file_inventory": discovery.file_inventory,
        "experiment_inventory": discovery.experiment_inventory,
        "trial_inventory": discovery.trial_inventory,
        "batch_inventory": discovery.batch_inventory,
        "run_inventory": discovery.run_inventory,
        "missing_files": discovery.missing_files,
        "checksum_validation": discovery.checksum_validation,
        "jsonl_integrity": discovery.jsonl_integrity,
        "schema_versions": discovery.schema_versions,
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for name, rows in tables.items():
        artifacts[name] = write_table(inventory_dir, name, rows, output_format=output_format)
    summary = {
        "schema_version": "1.0",
        "status": discovery.experiment_inventory[0].get("status") if discovery.experiment_inventory else "PARTIAL",
        "file_count": len(discovery.file_inventory),
        "batch_count": len(discovery.batch_inventory),
        "run_count": len(discovery.run_inventory),
        "jsonl_stream_count": len(discovery.jsonl_integrity),
        "missing_file_count": len(discovery.missing_files),
    }
    write_json(inventory_dir / "inventory_summary.json", summary)
    write_table_manifest(inventory_dir / "inventory_manifest.json", artifacts)


def validate_existing_bundle(
    *,
    bundle_root: str | Path,
    cfg: AnalysisConfig,
    strict: bool,
    output_format: str,
) -> dict[str, Any]:
    return validate_analysis_bundle(
        bundle_root=bundle_root,
        cfg=cfg,
        strict=strict,
        output_format=output_format,
    )


def analyze_existing_bundle(
    *,
    bundle_root: str | Path,
    cfg: AnalysisConfig,
    modules: set[str] | None,
    output_format: str,
    no_figures: bool,
) -> dict[str, Any]:
    return analyze_bundle(
        bundle_root=bundle_root,
        cfg=cfg,
        modules=modules,
        output_format=output_format,
        no_figures=no_figures,
    )


def build_status_lines(bundle_root: str | Path) -> list[str]:
    bundle = Path(bundle_root)
    manifest = read_json(bundle / "manifest" / "experiment_manifest.json", default={})
    ready = read_json(bundle / "02_validation" / "analysis_ready.json", default={})
    run_summary = read_json(bundle / "run_summary.json", default={})
    module_status = ready.get("module_status", {}) if isinstance(ready.get("module_status"), dict) else {}
    artifact_manifest = read_json(bundle / "artifact_manifest.json", default={})
    experiment_contract = read_json(bundle / "00_inventory" / "experiment_contract.json", default={})
    semantics_manifest = read_json(bundle / "02_semantics" / "semantics_manifest.json", default={})
    data_quality_gate = read_json(bundle / "02_validation" / "data_quality_gate.json", default={})
    handoff_manifest = read_json(bundle / "05_handoff" / "handoff_manifest.json", default={})
    p2_index = read_json(bundle / "00_inventory" / "p2_index_metrics.json", default={})
    p2_metrics = p2_index.get("metrics", {}) if isinstance(p2_index, dict) else {}
    normalization_execution = read_json(bundle / "01_normalized" / "normalization_execution.json", default={})
    final_status = run_summary.get("final_status", ready.get("overall_status", "PARTIAL"))
    heavy_skipped = bool(run_summary.get("heavy_analysis_skipped"))
    runs_parsed = 0 if heavy_skipped else len(read_bundle_table(bundle, "01_normalized", "runs"))
    requests_parsed = 0 if heavy_skipped else len(read_bundle_table(bundle, "01_normalized", "requests"))
    p2_parsed = 0 if heavy_skipped else len(read_bundle_table(bundle, "01_normalized", "p2_requests"))
    return [
        f"PROCESS_STATUS={run_summary.get('process_status', 'FAIL' if final_status == 'FAIL' else 'COMPLETE')}",
        f"PREFLIGHT_STATUS={run_summary.get('preflight_status', 'NOT_RUN')}",
        f"INPUT_INVENTORY_STATUS={run_summary.get('input_inventory_status', module_status.get('inventory', 'PARTIAL'))}",
        f"INPUT_BASELINE_STATUS={run_summary.get('input_baseline_status', 'PASS')}",
        f"RESUME_STATUS={run_summary.get('resume_status', 'NOT_REQUESTED')}",
        f"EXPERIMENT_CONTRACT_STATUS={experiment_contract.get('contract_status', 'UNKNOWN')}",
        f"SEMANTICS_STATUS={semantics_manifest.get('status', 'UNKNOWN')}",
        f"PARSE_STATUS={module_status.get('request_conservation', 'PARTIAL')}",
        f"DATA_VALIDATION_STATUS={ready.get('overall_status', 'PARTIAL')}",
        f"DATA_QUALITY_GATE_STATUS={data_quality_gate.get('gate_status', 'UNKNOWN')}",
        f"ARTIFACT_STATUS={artifact_manifest.get('artifact_status', run_summary.get('artifact_status', 'PARTIAL'))}",
        f"ANALYSIS_SOP_STATUS={final_status}",
        f"FINAL_STATUS={final_status}",
        f"EXPERIMENTS_FOUND={run_summary.get('experiments_found', 0)}",
        f"TRIALS_FOUND={run_summary.get('trials_found', 0)}",
        f"BATCHES_FOUND={run_summary.get('batches_found', 0)}",
        f"RUNS_EXPECTED={manifest.get('expected_run_count') if manifest.get('expected_run_count') is not None else 'UNKNOWN'}",
        f"RUNS_PARSED={runs_parsed}",
        f"REQUEST_ATTEMPTS_PARSED={requests_parsed}",
        f"P2_REQUESTS_PARSED={p2_parsed}",
        f"P2_SOURCE_FULL_SCAN_COUNT={p2_metrics.get('full_scan_count', 0)}",
        f"P2_SOURCE_INCREMENTAL_SCAN_COUNT={p2_metrics.get('incremental_scan_count', 0)}",
        f"P2_INDEX_REUSED={str(bool(p2_metrics.get('index_reused'))).lower()}",
        f"P2_INDEX_INCREMENTALLY_UPDATED={str(bool(p2_metrics.get('index_incrementally_updated'))).lower()}",
        f"P2_EVENTS_PARSED={p2_metrics.get('events_parsed', 0)}",
        f"P2_EVENTS_INDEXED={p2_metrics.get('events_indexed', 0)}",
        f"P2_INDEX_QUERY_COUNT={p2_metrics.get('query_count', 0)}",
        f"NORMALIZATION_JOBS_REQUESTED={normalization_execution.get('requested_jobs', run_summary.get('normalization_jobs_requested', 1))}",
        f"NORMALIZATION_JOBS_EFFECTIVE={normalization_execution.get('effective_jobs', 1)}",
        f"NORMALIZATION_PARALLEL_STATUS={normalization_execution.get('parallel_status', 'UNKNOWN')}",
        "",
        f"INVENTORY_STATUS={module_status.get('inventory', 'PARTIAL')}",
        f"SCHEMA_STATUS={module_status.get('schema', 'PARTIAL')}",
        f"TIME_CONSERVATION_STATUS={module_status.get('time_conservation', 'PARTIAL')}",
        f"LIFECYCLE_STATUS={module_status.get('lifecycle', 'PARTIAL')}",
        f"LITTLE_LAW_STATUS={module_status.get('little_law', 'PARTIAL')}",
        f"PIPELINE_RECONCILIATION_STATUS={module_status.get('pipeline_reconciliation', 'PARTIAL')}",
        f"THROUGHPUT_STATUS={status_from_table(bundle, 'throughput_summary')}",
        f"CONCURRENCY_STATUS={status_from_table(bundle, 'concurrency_summary')}",
        f"TAIL_STATUS={status_from_table(bundle, 'completion_tail')}",
        f"RESOURCE_STATUS={module_status.get('resource', 'PARTIAL')}",
        f"P1_CAPTURE_STATUS={module_status.get('p1_capture', 'PARTIAL')}",
        f"TOKEN_TIMESERIES_STATUS={module_status.get('token_timeseries', 'PARTIAL')}",
        f"P2_AUDIT_STATUS={module_status.get('p2_audit', 'PARTIAL')}",
        f"REQUEST_ENTITY_AUDIT_STATUS={module_status.get('request_conservation', 'PARTIAL')}",
        f"PUBLISHABILITY_GAP_MAPPING_STATUS={module_status.get('p2_audit', 'PARTIAL')}",
        f"PIPELINE_PHASE_STATUS={module_status.get('pipeline_reconciliation', 'PARTIAL')}",
        f"ENGINE_METRIC_SEMANTICS_STATUS={status_from_table(bundle, 'engine_state_classification')}",
        f"NPU_MAPPING_STATUS={module_status.get('npu_mapping', 'UNRESOLVED')}",
        "",
        f"MODEL_INPUT_PACK={bundle / 'model_input_pack'}",
        f"HANDOFF_STATUS={handoff_manifest.get('status', 'SKIPPED')}",
        f"ANALYSIS_READY_FILE={bundle / 'analysis_ready.json'}",
        f"UNRESOLVED_QUESTIONS={len(read_json(bundle / '04_evidence' / 'unresolved_questions.json', default=[]))}",
        f"FORBIDDEN_CONCLUSIONS={len(read_json(bundle / '04_evidence' / 'forbidden_conclusions.json', default=[]))}",
        f"MODULE_PACK_STATUS={run_summary.get('model_pack', {}).get('module_pack_status', 'SKIPPED') if isinstance(run_summary.get('model_pack'), dict) else 'SKIPPED'}",
        f"CLAIM_REGISTRY_STATUS={run_summary.get('evidence', {}).get('claim_registry_status', 'SKIPPED') if isinstance(run_summary.get('evidence'), dict) else 'SKIPPED'}",
    ]


def status_from_table(bundle: Path, table: str) -> str:
    rows = read_bundle_table(bundle, "03_analysis", table)
    statuses = {str(row.get("status") or "") for row in rows}
    if "FAIL" in statuses:
        return "FAIL"
    if "PASS" in statuses and "PARTIAL" not in statuses:
        return "PASS"
    return "PARTIAL"


def write_readme(bundle: Path) -> None:
    write_markdown(
        bundle / "README.md",
        """# AgentMon Analysis Bundle

This bundle was generated by `agentmon analysis`.

Directory contract:

- `manifest/`: discovered experiment manifest.
- `00_inventory/`: file/run/batch inventory, experiment contract, research questions, and expected artifact declarations.
- `01_normalized/`: standard fact tables by experiment/trial/batch/run/request/pipeline/resource level.
- `02_validation/`: deterministic readiness gates.
- `02_semantics/`: metric dictionary, entity set definitions, and clock-domain audit.
- `03_analysis/`: deterministic analysis outputs.
- `04_evidence/`: facts, associations, hypotheses and conclusion policy.
- `05_handoff/`: basic observation, anomaly, contradiction, unresolved-question, and backlog registers.
- `model_input_pack/`: compact pack for local model report generation.
""",
    )
