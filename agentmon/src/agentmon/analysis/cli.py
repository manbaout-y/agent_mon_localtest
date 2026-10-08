from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from agentmon.analysis.config import load_analysis_config


EXIT_SUCCESS = 0
EXIT_USAGE = 64
EXIT_BASELINE_MISMATCH = 2
EXIT_PREFLIGHT_FAILURE = 3
EXIT_EXECUTION_FAILURE = 4
EXIT_VALIDATION_FAILURE = 5
EXIT_RESUME_INCOMPATIBLE = 6
EXIT_REQUIRED_ARTIFACT_UNAVAILABLE = 7


def add_analysis_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    analysis = subparsers.add_parser("analysis", help="run deterministic experiment analysis SOP")
    analysis_sub = analysis.add_subparsers(dest="analysis_command")

    preflight = analysis_sub.add_parser("preflight", help="check analysis dependencies and paths before heavy scans")
    add_common_analysis_args(preflight)
    preflight.add_argument("--input", required=True, help="experiment or export root")
    preflight.add_argument("--output", help="analysis bundle root or writable output directory")
    preflight.add_argument("--profile", default="auto")
    preflight.add_argument("--json", action="store_true", help="print full preflight JSON")

    run_all = analysis_sub.add_parser("run-all", help="prepare, validate, analyze, build evidence, and model pack")
    add_common_analysis_args(run_all)
    run_all.add_argument("--input", required=True, help="experiment or export root")
    run_all.add_argument("--output", required=True, help="analysis bundle root")
    run_all.add_argument("--profile", default="auto")
    run_all.add_argument("--preflight", action="store_true", help="run dependency/path preflight before scanning inputs")

    prepare = analysis_sub.add_parser("prepare", help="discover, inventory, and normalize an experiment")
    add_common_analysis_args(prepare)
    prepare.add_argument("--input", required=True, help="input root")
    prepare.add_argument("--output", required=True, help="analysis bundle root")
    prepare.add_argument("--profile", default="auto")

    validate = analysis_sub.add_parser("validate", help="validate an existing analysis bundle")
    add_common_analysis_args(validate)
    validate.add_argument("--bundle", required=True, help="analysis bundle root")

    build_pack = analysis_sub.add_parser("build-model-pack", help="build compact model_input_pack from a bundle")
    add_common_analysis_args(build_pack)
    build_pack.add_argument("--bundle", required=True, help="analysis bundle root")
    build_pack.add_argument("--output", help="model input pack root")

    verify = analysis_sub.add_parser("verify-report", help="validate a model-generated report against a model pack")
    add_common_analysis_args(verify)
    verify.add_argument("--model-pack", required=True, help="model_input_pack root")
    verify.add_argument("--report", required=True, help="report markdown path")
    verify.add_argument("--output", help="validation output directory; defaults to report directory")

    compare = analysis_sub.add_parser("compare", help="compare two or more analysis bundles")
    add_common_analysis_args(compare)
    compare.add_argument("--bundle", action="append", required=True, help="analysis bundle root; repeat")
    compare.add_argument("--output", required=True, help="comparison output root")

    analyze = analysis_sub.add_parser("analyze", help="recompute selected analysis modules for a bundle")
    add_common_analysis_args(analyze)
    analyze.add_argument("--bundle", required=True, help="analysis bundle root")
    analyze.add_argument("--module", help="comma-separated modules: throughput,lifecycle,pipeline,p2,...")

    template = analysis_sub.add_parser("build-contract-template", help="write an experiment contract template")
    add_common_analysis_args(template)
    template.add_argument("--output", required=True, help="template JSON output path")

    validate_contract_cmd = analysis_sub.add_parser("validate-contract", help="validate an experiment contract file")
    add_common_analysis_args(validate_contract_cmd)
    validate_contract_cmd.add_argument("--contract", required=True, help="experiment contract JSON/YAML path")

    build_handoff = analysis_sub.add_parser("build-handoff", help="build basic handoff registers from an analysis bundle")
    add_common_analysis_args(build_handoff)
    build_handoff.add_argument("--bundle", required=True, help="analysis bundle root")


def add_common_analysis_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="ignore prior analysis manifest/cache and run fresh")
    parser.add_argument("--force-resume", action="store_true", help="resume despite manifest incompatibility; warnings are recorded")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-stage", action="append", default=[], help="force a named stage and its affected downstream work")
    parser.add_argument("--invalidate-stage", action="append", default=[], help="invalidate a named stage without deleting prior artifacts")
    parser.add_argument("--from-stage", help="start execution planning at this stage")
    parser.add_argument("--to-stage", help="stop execution planning at this stage")
    parser.add_argument("--dry-run", action="store_true", help="write the stage execution plan without scanning inputs")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--temp-dir")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--config", help="analysis config JSON/YAML")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--output-format", choices=["parquet", "csv"], default="parquet")
    parser.add_argument("--no-figures", action="store_true")
    parser.add_argument("--fast-smoke", action="store_true", help="skip very large optional outputs for quick validation")
    parser.add_argument("--status-file", help="write progress heartbeat JSON to this path")
    parser.add_argument("--progress-interval-seconds", type=float, help="minimum seconds between progress status updates")
    parser.add_argument("--timeout-seconds", type=float, help="optional cooperative wall-clock timeout with checkpointed failure status")
    parser.add_argument("--min-free-bytes", type=int, help="optional preflight disk free-space floor")
    parser.add_argument("--memory-limit-bytes", type=int, help="cap analysis worker parallelism using an estimated memory budget")
    parser.add_argument("--full-jsonl-integrity", action="store_true", help="perform full JSONL integrity scan during discovery")
    parser.add_argument("--full-file-checksums", action="store_true", help="compute content SHA256 for every discovered file (expensive)")
    parser.add_argument("--expected-runs", type=int, help="optional expected discovered run directory count")
    parser.add_argument("--expected-request-attempts", type=int, help="optional expected normalized request attempt count")
    parser.add_argument("--expected-p2-requests", type=int, help="optional expected normalized P2 request count")
    parser.add_argument("--allow-baseline-mismatch", action="store_true", help="continue when optional expected counts do not match")
    parser.add_argument(
        "--omit-normalized-table",
        action="append",
        default=[],
        help="do not populate this normalized table; may be repeated",
    )
    parser.add_argument(
        "--include-event-inventory",
        action="store_true",
        help="materialize the optional per-event audit table; disabled by default for large inputs",
    )
    parser.add_argument("--skip-evidence", action="store_true", help="skip 04_evidence output generation")
    parser.add_argument("--skip-handoff", action="store_true", help="skip 05_handoff output generation")
    parser.add_argument("--skip-model-pack", action="store_true", help="skip model_input_pack generation")
    parser.add_argument("--experiment-contract", help="explicit experiment contract JSON/YAML for analysis data foundation")


def dispatch_analysis(args: argparse.Namespace) -> int:
    if args.analysis_command is None:
        return EXIT_USAGE
    cfg = build_config_from_args(args)
    output_format = args.output_format
    if args.analysis_command == "preflight":
        from agentmon.analysis.preflight import (
            preflight_json_for_stdout,
            preflight_status_lines,
            run_analysis_preflight,
        )

        result = run_analysis_preflight(
            input_root=args.input,
            output_root=args.output,
            temp_dir=args.temp_dir,
            profile=args.profile,
            output_format=output_format,
            min_free_bytes=args.min_free_bytes,
            json_output_path=(Path(args.output) / "analysis_preflight.json") if args.output else None,
        )
        if args.json:
            print(preflight_json_for_stdout(result))
        else:
            for line in preflight_status_lines(result):
                print(line)
        return EXIT_PREFLIGHT_FAILURE if result.get("python_preflight_status") != "PASS" else EXIT_SUCCESS
    if args.analysis_command == "run-all":
        from agentmon.analysis.pipeline import build_status_lines, run_all_analysis

        result = run_all_analysis(
            input_root=args.input,
            output_root=args.output,
            cfg=cfg,
            output_format=output_format,
            force=args.force,
            resume=args.resume and not args.fresh,
            fresh=args.fresh,
            force_resume=args.force_resume,
            preflight=args.preflight,
            strict=args.strict,
            no_figures=args.no_figures,
            log_level=args.log_level,
            force_stages=args.force_stage,
            invalidate_stages=args.invalidate_stage,
            from_stage=args.from_stage,
            to_stage=args.to_stage,
            dry_run=args.dry_run,
            experiment_contract=args.experiment_contract,
        )
        for line in build_status_lines(args.output):
            print(line)
        return strict_exit_code(result) if args.strict else EXIT_SUCCESS
    if args.analysis_command == "prepare":
        from agentmon.analysis.pipeline import prepare_analysis_bundle

        prepared = prepare_analysis_bundle(
            input_root=args.input,
            output_root=args.output,
            cfg=cfg,
            output_format=output_format,
            force=args.force,
            resume=args.resume and not args.fresh,
            experiment_contract=args.experiment_contract,
        )
        print(json.dumps(prepared["result"], ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if args.analysis_command == "validate":
        from agentmon.analysis.pipeline import validate_existing_bundle

        result = validate_existing_bundle(
            bundle_root=args.bundle,
            cfg=cfg,
            strict=args.strict,
            output_format=output_format,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_VALIDATION_FAILURE if args.strict and result.get("overall_status") != "PASS" else EXIT_SUCCESS
    if args.analysis_command == "analyze":
        from agentmon.analysis.pipeline import analyze_existing_bundle

        modules = parse_modules(args.module)
        result = analyze_existing_bundle(
            bundle_root=args.bundle,
            cfg=cfg,
            modules=modules,
            output_format=output_format,
            no_figures=args.no_figures,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if args.analysis_command == "build-model-pack":
        from agentmon.analysis.evidence import build_evidence_bundle
        from agentmon.analysis.handoff import build_handoff_package
        from agentmon.analysis.model_pack import build_model_input_pack

        if not (Path(args.bundle) / "04_evidence" / "verified_facts.json").exists():
            build_evidence_bundle(bundle_root=args.bundle, output_format=output_format)
        if cfg.output.include_handoff and not (Path(args.bundle) / "05_handoff" / "handoff_manifest.json").exists():
            build_handoff_package(bundle_root=args.bundle, output_format=output_format)
        result = build_model_input_pack(bundle_root=args.bundle, output_root=args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_SUCCESS if result.get("status") == "PASS" else EXIT_EXECUTION_FAILURE
    if args.analysis_command == "build-handoff":
        from agentmon.analysis.handoff import build_handoff_package

        result = build_handoff_package(bundle_root=args.bundle, output_format=output_format)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_SUCCESS if result.get("status") in {"PASS", "PARTIAL"} else EXIT_EXECUTION_FAILURE
    if args.analysis_command == "build-contract-template":
        from agentmon.analysis.contract import write_contract_template

        result = write_contract_template(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_SUCCESS
    if args.analysis_command == "validate-contract":
        from agentmon.analysis.contract import load_experiment_contract, validate_contract

        contract = load_experiment_contract(args.contract) or {}
        rows = validate_contract(contract)
        result = {
            "schema_version": "1.0",
            "status": "PASS" if all(row.get("status") == "PASS" for row in rows) else "PARTIAL",
            "contract": args.contract,
            "missing_required_fields": [row.get("field_name") for row in rows if row.get("status") != "PASS"],
            "validation": rows,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_SUCCESS if result["status"] == "PASS" else EXIT_VALIDATION_FAILURE
    if args.analysis_command == "verify-report":
        from agentmon.analysis.report_validation import verify_model_report

        result = verify_model_report(model_pack=args.model_pack, report_path=args.report, output_dir=args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_SUCCESS if result.get("status") == "PASS" else EXIT_VALIDATION_FAILURE
    if args.analysis_command == "compare":
        from agentmon.analysis.compare import compare_bundles

        result = compare_bundles(bundle_paths=args.bundle, output_root=args.output, output_format=output_format)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_SUCCESS if result.get("status") == "PASS" else EXIT_VALIDATION_FAILURE
    return EXIT_USAGE


def build_config_from_args(args: argparse.Namespace):
    overrides = {}
    if getattr(args, "profile", None):
        overrides["profile"] = args.profile
    if args.jobs is not None:
        overrides.setdefault("runtime", {})["jobs"] = args.jobs
    if args.temp_dir:
        overrides.setdefault("runtime", {})["temp_dir"] = args.temp_dir
    if args.keep_temp:
        overrides.setdefault("runtime", {})["keep_temp"] = True
    if args.output_format:
        overrides.setdefault("output", {})["format"] = args.output_format
    if args.no_figures:
        overrides.setdefault("output", {})["figures"] = False
    if args.status_file:
        overrides.setdefault("runtime", {})["status_file"] = args.status_file
    if args.progress_interval_seconds is not None:
        overrides.setdefault("runtime", {})["progress_interval_seconds"] = args.progress_interval_seconds
    if args.timeout_seconds is not None:
        overrides.setdefault("runtime", {})["timeout_seconds"] = args.timeout_seconds
    if args.min_free_bytes is not None:
        overrides.setdefault("runtime", {})["min_free_bytes"] = args.min_free_bytes
    if args.memory_limit_bytes is not None:
        overrides.setdefault("runtime", {})["memory_limit_bytes"] = args.memory_limit_bytes
    if args.full_jsonl_integrity:
        overrides.setdefault("runtime", {})["full_jsonl_integrity"] = True
    if args.full_file_checksums:
        overrides.setdefault("runtime", {})["full_file_checksums"] = True
    if args.expected_runs is not None:
        overrides.setdefault("baseline", {})["expected_runs"] = args.expected_runs
    if args.expected_request_attempts is not None:
        overrides.setdefault("baseline", {})["expected_request_attempts"] = args.expected_request_attempts
    if args.expected_p2_requests is not None:
        overrides.setdefault("baseline", {})["expected_p2_requests"] = args.expected_p2_requests
    if args.allow_baseline_mismatch:
        overrides.setdefault("baseline", {})["allow_baseline_mismatch"] = True
    if args.fast_smoke:
        overrides.setdefault("output", {})["fast_smoke"] = True
    if args.omit_normalized_table:
        overrides.setdefault("output", {})["omit_normalized_tables"] = args.omit_normalized_table
    if args.include_event_inventory:
        configured = (
            tuple(args.omit_normalized_table)
            if args.omit_normalized_table
            else load_analysis_config(args.config).output.omit_normalized_tables
            if args.config
            else default_omitted_tables()
        )
        overrides.setdefault("output", {})["omit_normalized_tables"] = [name for name in configured if name != "event_inventory"]
    if args.skip_evidence:
        overrides.setdefault("output", {})["include_evidence"] = False
    if args.skip_handoff:
        overrides.setdefault("output", {})["include_handoff"] = False
    if args.skip_model_pack:
        overrides.setdefault("output", {})["include_model_pack"] = False
    return load_analysis_config(args.config, overrides=overrides)


def default_omitted_tables() -> tuple[str, ...]:
    return load_analysis_config().output.omit_normalized_tables


def strict_exit_code(result: dict[str, object]) -> int:
    final_status = result.get("final_status") or result.get("analysis_status")
    if result.get("reason") == "requested_terminal_stage_completed":
        return EXIT_VALIDATION_FAILURE if result.get("terminal_stage_status") == "FAIL" else EXIT_SUCCESS
    if result.get("execution_status") == "FAIL" or result.get("reason") == "execution_failure":
        return EXIT_EXECUTION_FAILURE
    if result.get("preflight_status") == "FAIL":
        return EXIT_PREFLIGHT_FAILURE
    if result.get("input_baseline_status") == "FAIL":
        return EXIT_BASELINE_MISMATCH
    if result.get("resume_status") == "REJECTED":
        return EXIT_RESUME_INCOMPATIBLE
    if result.get("required_artifact_status") == "FAIL":
        return EXIT_REQUIRED_ARTIFACT_UNAVAILABLE
    if final_status != "PASS":
        return EXIT_VALIDATION_FAILURE
    return EXIT_SUCCESS


def parse_modules(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}
