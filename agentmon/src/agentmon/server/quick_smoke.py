from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tarfile
import time
from typing import Any, Sequence
import zipfile


SCHEMA_VERSION = "1.0"
ROOT = Path(__file__).resolve().parents[3]


def stage_result(
    *,
    step: str,
    status: str,
    reason: str,
    next_step: str,
    json_path: str = "",
    log_path: str = "",
    suggestion: str = "",
    purpose: str = "",
    pass_condition: str = "",
) -> dict[str, Any]:
    return {
        "step": step,
        "status": status,
        "reason": reason,
        "next": next_step,
        "json": json_path,
        "log": log_path,
        "suggestion": suggestion,
        "purpose": purpose,
        "pass_condition": pass_condition,
    }


def print_stage_result(result: dict[str, Any]) -> None:
    fields = [
        ("STEP", "step"),
        ("PURPOSE", "purpose"),
        ("STATUS", "status"),
        ("REASON", "reason"),
        ("NEXT", "next"),
        ("PASS_CONDITION", "pass_condition"),
        ("JSON", "json"),
        ("LOG", "log"),
        ("SUGGESTION", "suggestion"),
    ]
    for label, key in fields:
        value = result.get(key)
        if value not in (None, ""):
            print(f"{label}={value}")


def suggestion_for_blockers(blockers: Sequence[str]) -> str:
    if not blockers:
        return ""
    suggestions = {
        "prefix_cache_disable_args_unresolved": (
            "Rerun static preflight and pass its prefix_cache_cli_options, or provide "
            "--prefix-cache-args explicitly; do not guess the flag name."
        ),
        "tool_call_parser_unresolved": (
            "Provide --tool-call-parser from vLLM parser discovery, or set "
            "--require-tool-calling=no for a control smoke that does not need tools."
        ),
        "agentmon_resolved_api_base_missing_expected_url": (
            "Check --agentmon-config-template and verify generated agentmon_config_runtime.toml "
            "contains the current VLLM_BASE_URL."
        ),
        "mini_model_config_missing_expected_url": (
            "Check --mini-model-config and verify generated mini_model_runtime.yaml points to "
            "the current VLLM_BASE_URL."
        ),
    }
    messages: list[str] = []
    for blocker in sorted(set(str(item) for item in blockers)):
        messages.append(suggestions.get(blocker, f"Resolve {blocker} in request/profile/preflight evidence."))
    return " ".join(messages)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = prepare_quick_smoke(args)
    except Exception as exc:  # noqa: BLE001 - CLI should return actionable gate data.
        print_stage_result(
            stage_result(
                step="QUICK_SMOKE_PREPARE",
                status="FAIL",
                reason=exc.__class__.__name__,
                next_step="STOP",
                suggestion=(
                    "Check --request, --server-profile, --source-root, --static-preflight-json, "
                    "and JSON/TOML/YAML syntax before retrying."
                ),
            )
        )
        print(f"agentmon server quick smoke error: {exc}", file=sys.stderr)
        return 2
    print_stage_result(report["stage_result"])
    print(f"QUICK_SMOKE_PREPARE={report['status']}")
    print(f"OUTPUT_DIR={report['output_dir']}")
    print(f"CONTRACT={report['artifacts']['contract']}")
    if report.get("blocking_reasons"):
        print("BLOCKING_REASONS=" + ",".join(str(item) for item in report["blocking_reasons"]))
    return 0 if report["status"] in {"PASS", "PARTIAL"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Synthesize a self-contained AgentMon server quick-smoke instance from a small intent request. "
            "This command discovers and writes candidate runtime files; it does not start vLLM."
        )
    )
    parser.add_argument("--request", help="experiment request JSON/TOML/YAML produced from natural-language intent")
    parser.add_argument("--server-profile", help="server profile JSON; overrides request.source.server_profile")
    parser.add_argument("--output-dir", help="directory for synthesized runtime instance")
    parser.add_argument("--output-root", help="parent directory used when --output-dir is omitted")
    parser.add_argument("--source-root", help="unpacked AgentMon source root")
    parser.add_argument("--benchmark-zip", help="benchmark dist.zip path")
    parser.add_argument("--devices", help="target device ids, e.g. 0,1")
    parser.add_argument("--cards", type=int, help="number of cards when --devices is omitted")
    parser.add_argument("--port", type=int, help="target vLLM port")
    parser.add_argument("--host", help="target vLLM host")
    parser.add_argument("--model-path", help="model path")
    parser.add_argument("--served-model", help="served model name")
    parser.add_argument("--max-model-len", type=int, help="max model length")
    parser.add_argument("--tp-size", type=int, help="tensor parallel size")
    parser.add_argument("--agentmon-python", help="AgentMon Python")
    parser.add_argument("--vllm-python", help="vLLM Python")
    parser.add_argument("--vllm-bin", help="vLLM executable")
    parser.add_argument("--mini-swe-bin", help="mini-SWE executable")
    parser.add_argument("--mini-model-config", help="source mini-SWE model config to rewrite")
    parser.add_argument("--agentmon-config-template", help="source AgentMon TOML config to rewrite")
    parser.add_argument("--static-preflight-json", help="static preflight JSON used as discovery evidence")
    parser.add_argument("--prefix-cache", choices=["disabled", "enabled", "any"], help="expected prefix cache setting")
    parser.add_argument("--prefix-cache-args", help="exact vLLM CLI args controlling prefix cache")
    parser.add_argument("--tool-call-parser", help="exact vLLM tool-call parser name")
    parser.add_argument("--require-tool-calling", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--smoke-task", action="append", help="task id for startup smoke; may be repeated")
    parser.add_argument("--pilot-task", action="append", help="task id for pilot; may be repeated")
    return parser


def prepare_quick_smoke(args: argparse.Namespace) -> dict[str, Any]:
    request = load_request(args.request)
    profile = load_profile(first_present(args.server_profile, dotted(request, "source.server_profile"), request.get("server_profile")))
    profile_env = as_dict(profile.get("environment"))
    profile_preflight = as_dict(profile.get("preflight"))
    source_root = Path(first_present(args.source_root, dotted(request, "source.repo"), ROOT)).expanduser().resolve()
    output_dir = output_directory(args, request)
    output_dir.mkdir(parents=True, exist_ok=True)

    host = str(first_present(args.host, dotted(request, "resources.host"), profile_env.get("VLLM_HOST"), "127.0.0.1"))
    port = int(first_present(args.port, dotted(request, "resources.preferred_port"), profile_env.get("VLLM_PORT"), 8000))
    base_url = f"http://{host}:{port}/v1"
    served_model = str(first_present(args.served_model, dotted(request, "model.served_model"), profile_env.get("SERVED_MODEL"), ""))
    model_path = str(first_present(args.model_path, dotted(request, "model.model_path"), profile_env.get("MODEL_PATH"), ""))
    max_model_len = int(first_present(args.max_model_len, dotted(request, "model.max_model_len"), profile_env.get("MAX_MODEL_LEN"), 32768))
    devices = select_devices(args, request, profile_env)
    tp_size = int(first_present(args.tp_size, dotted(request, "resources.tensor_parallel_size"), dotted(request, "model.tensor_parallel_size"), len(devices.split(",")) if devices else 1))
    agentmon_home = str(first_present(dotted(request, "paths.agentmon_home"), profile_env.get("AGENTMON_HOME"), "/tmp/agentmon"))
    agentmon_py = str(first_present(args.agentmon_python, profile_env.get("AGENTMON_PY"), sys.executable))
    vllm_py = str(first_present(args.vllm_python, profile_env.get("VLLM_PY"), sys.executable))
    vllm_bin = str(first_present(args.vllm_bin, profile_env.get("VLLM_BIN"), str(Path(vllm_py).with_name("vllm"))))
    vllm_source_root = str(first_present(dotted(request, "paths.vllm_source_root"), profile_env.get("VLLM_SOURCE_ROOT"), ""))
    mini_swe_bin = str(first_present(args.mini_swe_bin, profile_env.get("MINI_SWE_BIN"), "mini"))
    acl_py = str(first_present(dotted(request, "paths.acl_python_path"), profile_env.get("ACL_PY"), acl_from_profile(profile_env), ""))
    benchmark_zip = str(first_present(args.benchmark_zip, dotted(request, "workload.benchmark_zip"), ""))
    prefix_expectation = str(first_present(args.prefix_cache, dotted(request, "requirements.prefix_cache"), profile_preflight.get("expect_prefix_cache"), "any"))
    static_preflight = Path(args.static_preflight_json).expanduser() if args.static_preflight_json else None
    prefix_args, prefix_evidence = resolve_prefix_cache_args(
        explicit=args.prefix_cache_args or dotted(request, "runtime.prefix_cache_args"),
        expectation=prefix_expectation,
        static_preflight=static_preflight,
    )
    tool_call_required = resolve_tool_call_required(args, request)
    tool_parser, tool_evidence = resolve_tool_call_parser(
        explicit=args.tool_call_parser or dotted(request, "model.tool_call_parser") or dotted(request, "requirements.tool_call_parser"),
        served_model=served_model,
        model_path=model_path,
        candidate_rules=as_list(profile_preflight.get("tool_call_parser_candidates")),
    )
    configured_smoke_tasks = list(
        args.smoke_task
        or as_list(dotted(request, "workload.smoke_tasks"))
        or as_list(dotted(request, "smoke.tasks"))
    )
    smoke_tasks, smoke_task_evidence = resolve_smoke_tasks(configured_smoke_tasks, benchmark_zip)
    pilot_tasks = list(args.pilot_task or as_list(dotted(request, "workload.pilot_tasks")) or as_list(dotted(request, "pilot.task_ids")) or [])

    paths = synthesized_paths(output_dir)
    instance = {
        "agentmon_repo": str(source_root),
        "agentmon_config": str(paths["agentmon_config"]),
        "agentmon_home": agentmon_home,
        "agentmon_python": agentmon_py,
        "vllm_python": vllm_py,
        "vllm_bin": vllm_bin,
        "vllm_source_root": vllm_source_root,
        "mini_swe_bin": mini_swe_bin,
        "acl_python_path": acl_py,
        "devices": devices,
        "tp_size": tp_size,
        "host": host,
        "port": port,
        "base_url": base_url,
        "model_path": model_path,
        "served_model": served_model,
        "max_model_len": max_model_len,
        "prefix_cache_expectation": prefix_expectation,
        "prefix_cache_args": prefix_args,
        "tool_call_required": tool_call_required,
        "tool_call_parser": tool_parser,
        "benchmark_zip": benchmark_zip,
        "events_path": str(paths["events"]),
        "p2_root": str(output_dir),
        "service_start_script": str(paths["start_script"]),
        "service_log": str(paths["service_log"]),
        "pid_file": str(paths["pid_file"]),
        "mini_model_config": str(paths["mini_model_config"]),
    }
    blockers = blocking_reasons(instance, prefix_evidence, tool_evidence)
    if not smoke_tasks:
        blockers.append("smoke_task_unresolved")
    write_instance_env(paths["instance_env"], instance, source_root, acl_py)
    write_resource_gate_script(paths["resource_gate"], paths["resource_gate_report"], paths["instance_env"])
    write_start_script(paths["start_script"], instance)
    write_service_start_gate_script(paths["service_start_gate"], paths["service_start_gate_report"], paths["instance_env"])
    write_tool_call_probe(paths["tool_call_probe"], instance)
    write_tool_call_gate_script(
        paths["tool_call_gate"],
        paths["tool_call_gate_report"],
        paths["instance_env"],
        paths["tool_call_probe"],
    )
    write_mini_swe_probe(paths["mini_swe_probe"], instance)
    source_mini_model = first_present(args.mini_model_config, profile_env.get("MINI_MODEL_CONFIG"))
    write_mini_model_config(paths["mini_model_config"], source_mini_model, base_url, served_model)
    source_agentmon_config = first_present(args.agentmon_config_template, profile_preflight.get("agentmon_config"), str(source_root / "examples" / "config.target.toml"))
    source_agentmon_config = expand_vars(str(source_agentmon_config), {"AGENTMON_REPO": str(source_root), **string_map(profile_env)})
    write_agentmon_config(paths["agentmon_config"], source_agentmon_config, instance)
    write_watchdog_script(paths["watchdog"], instance)

    consistency = config_consistency(paths["agentmon_config"], paths["mini_model_config"], instance)
    if consistency["status"] == "FAIL":
        blockers.extend(consistency["blocking_reasons"])
    status = "PASS" if not blockers else "PARTIAL"
    stage = stage_result(
        step="QUICK_SMOKE_PREPARE",
        status=status,
        reason="pass" if status == "PASS" else "blocking_reasons_present",
        next_step="RESOURCE_GATE" if status == "PASS" else "STOP",
        json_path=str(paths["prepare_report"]),
        log_path="",
        suggestion=suggestion_for_blockers(blockers),
        purpose="synthesize one isolated server smoke instance without starting vLLM",
        pass_condition="contract status PASS and blocking_reasons empty",
    )
    contract = {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.server_quick_smoke.contract",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "stage_result": stage,
        "experiment_request": request,
        "profile": {
            "name": profile.get("name") or "",
            "description": profile.get("description") or "",
        },
        "intent": {
            "experiment_name": dotted(request, "experiment.name"),
            "intent": dotted(request, "experiment.intent"),
        },
        "discovered": {
            "prefix_cache": prefix_evidence,
            "tool_call_parser": tool_evidence,
            "smoke_tasks": smoke_task_evidence,
            "source": "request/profile/static-artifact synthesis; no runtime service was started",
        },
        "synthesized": instance,
        "config_consistency": consistency,
        "smoke": {
            "tasks": smoke_tasks,
            "startup_watchdog_seconds": int(first_present(dotted(request, "requirements.startup_watchdog_seconds"), 60)),
        },
        "pilot": {
            "tasks": pilot_tasks,
        },
        "blocking_reasons": sorted(set(blockers)),
        "limitations": [
            "LLM/request fields express intent; server protocol facts must be discovered or verified by gates.",
            "Heuristic tool parser candidates are not facts until the tool-call probe passes.",
            "This prepare step writes self-contained runtime files; it does not start vLLM or run benchmark tasks.",
        ],
    }
    write_json(paths["contract"], contract)
    report = {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.server_quick_smoke.prepare_report",
        "status": contract["status"],
        "stage_result": stage,
        "output_dir": str(output_dir),
        "artifacts": {key: str(path) for key, path in paths.items()},
        "blocking_reasons": contract["blocking_reasons"],
    }
    write_json(paths["prepare_report"], report)
    return report


def load_request(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    request_path = Path(path).expanduser()
    text = request_path.read_text(encoding="utf-8")
    suffix = request_path.suffix.lower()
    if suffix == ".json":
        value = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        value = load_yaml_request(text)
    elif suffix in {".toml", ".tml"}:
        value = load_toml_request(text)
    else:
        raise ValueError(f"unsupported quick-smoke request format: {suffix or '<none>'}")
    if not isinstance(value, dict):
        raise ValueError("quick-smoke request must be an object")
    return value


def load_yaml_request(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("YAML quick-smoke requests require PyYAML; use JSON in this environment") from exc
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("quick-smoke request must be an object")
    return value


def load_toml_request(text: str) -> dict[str, Any]:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        except ImportError as exc:
            raise ValueError("TOML quick-smoke requests require Python 3.11 tomllib or tomli; use JSON in this environment") from exc
    value = tomllib.loads(text)
    if not isinstance(value, dict):
        raise ValueError("quick-smoke request must be an object")
    return value


def load_profile(path: Any) -> dict[str, Any]:
    if not path:
        return {}
    profile_path = Path(str(path)).expanduser()
    if not profile_path.is_absolute():
        candidates = [Path.cwd() / profile_path, ROOT / profile_path]
        profile_path = next((candidate for candidate in candidates if candidate.exists()), candidates[-1])
    value = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"server profile must be an object: {profile_path}")
    return value


def output_directory(args: argparse.Namespace, request: dict[str, Any]) -> Path:
    if args.output_dir:
        return Path(args.output_dir).expanduser().resolve()
    output_root = Path(first_present(args.output_root, dotted(request, "paths.output_root"), "/tmp/agentmon-quick-smoke")).expanduser()
    name = slug(str(first_present(dotted(request, "experiment.name"), "agentmon-server-quick-smoke")))
    return (output_root / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}").resolve()


def synthesized_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "contract": output_dir / "quick-smoke-contract.json",
        "prepare_report": output_dir / "quick-smoke-prepare.json",
        "instance_env": output_dir / "runtime-instance.env",
        "resource_gate": output_dir / "resource-gate.sh",
        "resource_gate_report": output_dir / "resource-gate.json",
        "start_script": output_dir / "start-vllm-p2.sh",
        "service_start_gate": output_dir / "service-start-gate.sh",
        "service_start_gate_report": output_dir / "service-start-gate.json",
        "agentmon_config": output_dir / "agentmon_config_runtime.toml",
        "mini_model_config": output_dir / "mini_model_runtime.yaml",
        "tool_call_probe": output_dir / "tool-call-probe.json",
        "tool_call_gate": output_dir / "tool-call-gate.sh",
        "tool_call_gate_report": output_dir / "tool-call-gate.json",
        "mini_swe_probe": output_dir / "mini-swe-noninteractive-probe.sh",
        "watchdog": output_dir / "startup-watchdog.sh",
        "events": output_dir / "events.jsonl",
        "service_log": output_dir / "vllm-server.log",
        "pid_file": output_dir / "vllm-server.pid",
    }


def select_devices(args: argparse.Namespace, request: dict[str, Any], profile_env: dict[str, Any]) -> str:
    explicit = first_present(args.devices, dotted(request, "resources.preferred_devices"), dotted(request, "resources.devices"))
    if isinstance(explicit, list):
        return ",".join(str(item) for item in explicit)
    if explicit:
        return str(explicit)
    visible = str(profile_env.get("ASCEND_RT_VISIBLE_DEVICES") or "")
    rows = [item.strip() for item in visible.split(",") if item.strip()]
    cards = int(first_present(args.cards, dotted(request, "resources.cards"), len(rows) if rows else 1))
    return ",".join(rows[:cards]) if rows else ",".join(str(index) for index in range(cards))


def resolve_smoke_tasks(configured: list[Any], benchmark_zip: str) -> tuple[list[str], dict[str, Any]]:
    explicit = [str(item).strip() for item in configured if str(item).strip()]
    if explicit:
        return explicit, {"status": "provided", "source": "request_or_cli", "task_ids": explicit}
    discovered = discover_benchmark_task_ids(benchmark_zip)
    if discovered:
        selected = [discovered[0]]
        return selected, {
            "status": "discovered",
            "source": str(Path(benchmark_zip).expanduser().resolve()),
            "selection_rule": "first_lexical_task_id",
            "task_ids": selected,
            "available_task_count": len(discovered),
        }
    return [], {
        "status": "unresolved",
        "reason": "no_explicit_smoke_task_and_no_task_id_discovered_from_benchmark",
        "required_evidence": (
            "workload.smoke_tasks, --smoke-task, or a readable benchmark zip "
            "with tasks/<task-id>/ entries"
        ),
    }


def discover_benchmark_task_ids(path: str) -> list[str]:
    archive = Path(path).expanduser().resolve() if path else None
    if archive is None or not archive.is_file() or not zipfile.is_zipfile(archive):
        return []
    task_ids: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as outer:
            task_ids.update(task_ids_from_archive_names(outer.namelist()))
            nested_archives = [
                info
                for info in outer.infolist()
                if not info.is_dir() and info.filename.lower().endswith((".tar.gz", ".tgz"))
            ]
            for info in nested_archives:
                with outer.open(info) as compressed:
                    # Stream member names only; task contents are never
                    # extracted into the source or smoke workspace.
                    with tarfile.open(fileobj=compressed, mode="r|gz") as nested:
                        for member in nested:
                            task_id = task_id_from_archive_name(member.name)
                            if task_id:
                                task_ids.add(task_id)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError):
        return []
    return sorted(task_ids)


def task_ids_from_archive_names(names: Sequence[str]) -> set[str]:
    return {
        task_id
        for name in names
        if (task_id := task_id_from_archive_name(name)) is not None
    }


def task_id_from_archive_name(name: str) -> str | None:
    parts = tuple(part for part in Path(name).parts if part not in {"", "."})
    for index, part in enumerate(parts[:-1]):
        if part != "tasks":
            continue
        candidate = parts[index + 1].strip()
        if candidate and candidate not in {".", ".."}:
            return candidate
    return None


def resolve_prefix_cache_args(*, explicit: Any, expectation: str, static_preflight: Path | None) -> tuple[str, dict[str, Any]]:
    if explicit:
        return str(explicit), {"status": "provided", "source": "request_or_cli", "value": str(explicit)}
    from_preflight = prefix_cache_args_from_preflight(static_preflight)
    if from_preflight:
        return from_preflight, {"status": "discovered", "source": str(static_preflight), "value": from_preflight}
    if expectation == "disabled":
        return "", {
            "status": "unresolved",
            "reason": "prefix_cache_disabled_but_disable_cli_args_unknown",
            "required_evidence": "static preflight prefix_cache_cli_options or explicit --prefix-cache-args",
        }
    return "", {"status": "not_required", "reason": f"prefix_cache_expectation={expectation}"}


def prefix_cache_args_from_preflight(path: Path | None) -> str:
    if not path or not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    detail = as_dict(as_dict(data.get("checks")).get("vllm_runtime")).get("detail")
    detail = detail if isinstance(detail, dict) else {}
    value = detail.get("prefix_cache_cli_options") or detail.get("prefix_cache_disable_options")
    values = value if isinstance(value, list) else [value] if value else []
    for item in values:
        text = str(item or "").strip()
        if text and ("disable" in text or "no-" in text):
            return text
    return ""


def resolve_tool_call_required(args: argparse.Namespace, request: dict[str, Any]) -> bool:
    if args.require_tool_calling == "yes":
        return True
    if args.require_tool_calling == "no":
        return False
    value = dotted(request, "requirements.agent_tool_calling")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "required", "on"}
    return bool(value)


def resolve_tool_call_parser(
    *,
    explicit: Any,
    served_model: str,
    model_path: str,
    candidate_rules: list[Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    if explicit:
        return str(explicit), {"status": "provided", "source": "request_or_cli", "value": str(explicit)}
    probe = f"{served_model} {model_path}".lower().replace("-", "_")
    for raw_rule in candidate_rules or []:
        rule = as_dict(raw_rule)
        parser = str(rule.get("parser") or "").strip()
        required = [
            str(item).lower().replace("-", "_")
            for item in as_list(rule.get("model_name_contains"))
            if str(item).strip()
        ]
        if parser and required and all(item in probe for item in required):
            return parser, {
                "status": "candidate",
                "source": "server_profile:preflight.tool_call_parser_candidates",
                "value": parser,
                "matched_model_name_contains": required,
                "requires_gate": "tool_call_probe",
            }
    return "", {
        "status": "unresolved",
        "reason": "tool_call_parser_unknown",
        "required_evidence": "vLLM parser registry discovery or explicit --tool-call-parser",
    }


def blocking_reasons(instance: dict[str, Any], prefix_evidence: dict[str, Any], tool_evidence: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for key in ("agentmon_python", "vllm_python", "vllm_bin", "model_path", "served_model", "devices", "base_url"):
        if not instance.get(key):
            blockers.append(f"missing:{key}")
    if instance.get("prefix_cache_expectation") == "disabled" and prefix_evidence.get("status") == "unresolved":
        blockers.append("prefix_cache_disable_args_unresolved")
    if instance.get("tool_call_required") and tool_evidence.get("status") == "unresolved":
        blockers.append("tool_call_parser_unresolved")
    return blockers


def write_instance_env(path: Path, instance: dict[str, Any], source_root: Path, acl_py: str) -> None:
    pythonpath_parts = [str(source_root / "src")]
    if instance.get("vllm_source_root"):
        pythonpath_parts.append(str(instance["vllm_source_root"]))
    if acl_py:
        pythonpath_parts.append(acl_py)
    pythonpath = ":".join(pythonpath_parts)
    rows = {
        "AGENTMON_REPO": str(source_root),
        "AGENTMON_CONFIG": instance["agentmon_config"],
        "AGENTMON_HOME": instance["agentmon_home"],
        "AGENTMON_PY": instance["agentmon_python"],
        "VLLM_PY": instance["vllm_python"],
        "VLLM_BIN": instance["vllm_bin"],
        "VLLM_SOURCE_ROOT": instance["vllm_source_root"],
        "MINI_SWE_BIN": instance["mini_swe_bin"],
        "ACL_PY": acl_py,
        "TARGET_DEVICES": instance["devices"],
        "TP_SIZE": str(instance["tp_size"]),
        "VLLM_HOST": instance["host"],
        "VLLM_PORT": str(instance["port"]),
        "VLLM_BASE_URL": instance["base_url"],
        "MODEL_PATH": instance["model_path"],
        "SERVED_MODEL": instance["served_model"],
        "MAX_MODEL_LEN": str(instance["max_model_len"]),
        "PREFIX_CACHE_EXPECTATION": instance["prefix_cache_expectation"],
        "VLLM_PREFIX_CACHE_ARGS": instance["prefix_cache_args"],
        "TOOL_CALL_REQUIRED": "1" if instance["tool_call_required"] else "0",
        "TOOL_CALL_PARSER": instance["tool_call_parser"],
        "P2_ROOT": instance["p2_root"],
        "AGENTMON_EVENTS_PATH": instance["events_path"],
        "SERVICE_START_SCRIPT": instance["service_start_script"],
        "SERVICE_LOG": instance["service_log"],
        "PID_FILE": instance["pid_file"],
        "MINI_MODEL_CONFIG_RUNTIME": instance["mini_model_config"],
        "AGENTMON_ACL_PYTHON_PATH": acl_py,
        "PYTHONPATH": pythonpath,
        "PYTHONNOUSERSITE": "1",
    }
    text = "\n".join(f"export {key}={shlex.quote(value)}" for key, value in rows.items()) + "\n"
    write_private_text(path, text, mode=0o600)


def write_start_script(path: Path, instance: dict[str, Any]) -> None:
    args: list[str] = [
        "serve",
        instance["model_path"],
        "--served-model-name",
        instance["served_model"],
        "--host",
        instance["host"],
        "--port",
        str(instance["port"]),
        "--tensor-parallel-size",
        str(instance["tp_size"]),
        "--max-model-len",
        str(instance["max_model_len"]),
    ]
    if instance["prefix_cache_args"]:
        args.extend(shlex.split(instance["prefix_cache_args"]))
    if instance["tool_call_required"] and instance["tool_call_parser"]:
        args.extend(["--enable-auto-tool-choice", "--tool-call-parser", instance["tool_call_parser"]])
    command = " ".join([shlex.quote(instance["vllm_bin"]), *[shlex.quote(item) for item in args]])
    pythonpath_parts = [f"{instance['agentmon_repo']}/src"]
    if instance.get("vllm_source_root"):
        pythonpath_parts.append(str(instance["vllm_source_root"]))
    if instance["acl_python_path"]:
        pythonpath_parts.append(instance["acl_python_path"])
    pythonpath = ":".join(pythonpath_parts)
    pythonpath_value = shlex.quote(pythonpath) if pythonpath else '"${PYTHONPATH:-}"'
    text = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"export ASCEND_RT_VISIBLE_DEVICES={shlex.quote(instance['devices'])}",
            "export AGENTMON_VLLM_P2_ENABLE=1",
            "export AGENTMON_VLLM_P2_DEEP=1",
            "export AGENTMON_VLLM_P2_DEEP_MAX_BATCH_REQUESTS=256",
            f"export AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE={shlex.quote(instance['prefix_cache_expectation'])}",
            f"export AGENTMON_EVENTS_PATH={shlex.quote(instance['events_path'])}",
            f"export AGENTMON_ACL_PYTHON_PATH={shlex.quote(instance['acl_python_path'])}",
            f"export PYTHONPATH={pythonpath_value}",
            "export PYTHONNOUSERSITE=1",
            f"exec {command}",
            "",
        ]
    )
    write_private_text(path, text, mode=0o700)


def write_tool_call_probe(path: Path, instance: dict[str, Any]) -> None:
    payload = {
        "model": instance["served_model"],
        "messages": [{"role": "user", "content": "Call the probe function with status ok."}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "agentmon_probe",
                    "description": "AgentMon quick-smoke tool-call probe.",
                    "parameters": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": ["status"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "max_tokens": 128,
    }
    write_json(path, payload)


def write_resource_gate_script(path: Path, report_path: Path, instance_env: Path) -> None:
    text = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"INSTANCE_ENV={shlex.quote(str(instance_env))}",
            f"GATE_JSON={shlex.quote(str(report_path))}",
            "set -a",
            ". \"$INSTANCE_ENV\"",
            "set +a",
            "STEP_NAME=RESOURCE_GATE",
            "PURPOSE_TEXT='verify isolated devices, free port, writable P2 root, and fresh events path before vLLM start'",
            "PASS_CONDITION_TEXT='port can be bound, P2 root is writable, events path is fresh, and device isolation is confirmed'",
            "export PURPOSE_TEXT PASS_CONDITION_TEXT",
            "GATE_LOG=${RESOURCE_GATE_LOG:-$P2_ROOT/resource-gate-console.log}",
            "mkdir -p \"$P2_ROOT\" \"$(dirname \"$GATE_LOG\")\"",
            "if command -v npu-smi >/dev/null 2>&1; then npu-smi info > \"$GATE_LOG\" 2>&1 || true; else echo 'npu-smi unavailable' > \"$GATE_LOG\"; fi",
            "emit_result() {",
            "  local status=\"$1\" reason=\"$2\" next_step=\"$3\" suggestion=\"$4\"",
            "  \"$AGENTMON_PY\" - \"$GATE_JSON\" \"$STEP_NAME\" \"$status\" \"$reason\" \"$next_step\" \"$GATE_LOG\" \"$suggestion\" <<'PY'",
            "import json, os, sys",
            "from datetime import datetime, timezone",
            "from pathlib import Path",
            "path, step, status, reason, next_step, log_path, suggestion = sys.argv[1:8]",
            "payload = {",
            "    'schema_version': '1.0',",
            "    'result_type': 'agentmon.server_quick_smoke.gate_result',",
            "    'created_at': datetime.now(timezone.utc).isoformat(),",
            "    'step': step,",
            "    'purpose': os.environ.get('PURPOSE_TEXT', ''),",
            "    'pass_condition': os.environ.get('PASS_CONDITION_TEXT', ''),",
            "    'status': status,",
            "    'reason': reason,",
            "    'next': next_step,",
            "    'log': log_path,",
            "    'suggestion': suggestion,",
            "    'p2_root': os.environ.get('P2_ROOT', ''),",
            "    'events_path': os.environ.get('AGENTMON_EVENTS_PATH', ''),",
            "    'target_devices': os.environ.get('TARGET_DEVICES', ''),",
            "    'base_url': os.environ.get('VLLM_BASE_URL', ''),",
            "}",
            "Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\\n', encoding='utf-8')",
            "print(f'STEP={step}')",
            "print(f'PURPOSE={payload[\"purpose\"]}')",
            "print(f'STATUS={status}')",
            "print(f'REASON={reason}')",
            "print(f'NEXT={next_step}')",
            "print(f'PASS_CONDITION={payload[\"pass_condition\"]}')",
            "print(f'JSON={path}')",
            "print(f'LOG={log_path}')",
            "if suggestion:",
            "    print(f'SUGGESTION={suggestion}')",
            "print(f'RESOURCE_GATE={status}')",
            "raise SystemExit(0 if status == 'PASS' else 1)",
            "PY",
            "}",
            "test -x \"$AGENTMON_PY\" || emit_result FAIL agentmon_python_not_executable STOP 'Fix AGENTMON_PY in runtime-instance.env.'",
            "test -x \"$VLLM_BIN\" || emit_result FAIL vllm_bin_not_executable STOP 'Fix VLLM_BIN or rerun quick-smoke with --vllm-bin.'",
            "test -d \"$MODEL_PATH\" || emit_result FAIL model_path_missing STOP 'Fix MODEL_PATH or rerun quick-smoke with --model-path.'",
            "test -w \"$P2_ROOT\" || emit_result FAIL p2_root_not_writable STOP 'Choose a writable output root for this run.'",
            "if [ -e \"$AGENTMON_EVENTS_PATH\" ]; then",
            "  emit_result FAIL events_path_already_exists STOP 'Use a fresh P2_ROOT/events.jsonl so old events cannot contaminate this gate.'",
            "fi",
            "\"$AGENTMON_PY\" - \"$VLLM_HOST\" \"$VLLM_PORT\" <<'PY' || emit_result FAIL port_in_use STOP 'Choose a new --port or stop the old service using this port.'",
            "import socket, sys",
            "host, port = sys.argv[1], int(sys.argv[2])",
            "sock = socket.socket()",
            "try:",
            "    sock.bind((host, port))",
            "finally:",
            "    sock.close()",
            "PY",
            "DEVICE_CONFIRM=${RESOURCE_GATE_MANUAL_DEVICE_CONFIRM:-${RESOURCE_GATE_ASSUME_DEVICE_ISOLATED:-}}",
            "if [ \"$DEVICE_CONFIRM\" != YES ]; then",
            "  emit_result PARTIAL device_isolation_unconfirmed STOP 'Inspect LOG for npu-smi evidence, then rerun with RESOURCE_GATE_MANUAL_DEVICE_CONFIRM=YES if TARGET_DEVICES are reserved for this run.'",
            "fi",
            "emit_result PASS pass SERVICE_START_GATE ''",
            "",
        ]
    )
    write_private_text(path, text, mode=0o700)


def write_service_start_gate_script(path: Path, report_path: Path, instance_env: Path) -> None:
    text = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"INSTANCE_ENV={shlex.quote(str(instance_env))}",
            f"GATE_JSON={shlex.quote(str(report_path))}",
            "set -a",
            ". \"$INSTANCE_ENV\"",
            "set +a",
            "STEP_NAME=SERVICE_START_GATE",
            "PURPOSE_TEXT='start the isolated vLLM service and verify the configured model is listed on this Base URL'",
            "PASS_CONDITION_TEXT='service PID is alive and /v1/models lists SERVED_MODEL before timeout'",
            "export PURPOSE_TEXT PASS_CONDITION_TEXT",
            "START_TIMEOUT=${SERVICE_START_TIMEOUT_SECONDS:-600}",
            "mkdir -p \"$P2_ROOT\"",
            "emit_result() {",
            "  local status=\"$1\" reason=\"$2\" next_step=\"$3\" suggestion=\"$4\"",
            "  \"$AGENTMON_PY\" - \"$GATE_JSON\" \"$STEP_NAME\" \"$status\" \"$reason\" \"$next_step\" \"$SERVICE_LOG\" \"$suggestion\" <<'PY'",
            "import json, os, sys",
            "from datetime import datetime, timezone",
            "from pathlib import Path",
            "path, step, status, reason, next_step, log_path, suggestion = sys.argv[1:8]",
            "payload = {",
            "    'schema_version': '1.0',",
            "    'result_type': 'agentmon.server_quick_smoke.gate_result',",
            "    'created_at': datetime.now(timezone.utc).isoformat(),",
            "    'step': step,",
            "    'purpose': os.environ.get('PURPOSE_TEXT', ''),",
            "    'pass_condition': os.environ.get('PASS_CONDITION_TEXT', ''),",
            "    'status': status,",
            "    'reason': reason,",
            "    'next': next_step,",
            "    'log': log_path,",
            "    'suggestion': suggestion,",
            "    'pid_file': os.environ.get('PID_FILE', ''),",
            "    'service_start_script': os.environ.get('SERVICE_START_SCRIPT', ''),",
            "    'base_url': os.environ.get('VLLM_BASE_URL', ''),",
            "    'served_model': os.environ.get('SERVED_MODEL', ''),",
            "}",
            "Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\\n', encoding='utf-8')",
            "print(f'STEP={step}')",
            "print(f'PURPOSE={payload[\"purpose\"]}')",
            "print(f'STATUS={status}')",
            "print(f'REASON={reason}')",
            "print(f'NEXT={next_step}')",
            "print(f'PASS_CONDITION={payload[\"pass_condition\"]}')",
            "print(f'JSON={path}')",
            "print(f'LOG={log_path}')",
            "if suggestion:",
            "    print(f'SUGGESTION={suggestion}')",
            "print(f'SERVICE_START_GATE={status}')",
            "raise SystemExit(0 if status == 'PASS' else 1)",
            "PY",
            "}",
            "test -x \"$SERVICE_START_SCRIPT\" || emit_result FAIL start_script_not_executable STOP 'Rerun quick-smoke prepare or chmod 700 the generated start-vllm-p2.sh.'",
            "\"$AGENTMON_PY\" - \"$VLLM_HOST\" \"$VLLM_PORT\" <<'PY' || emit_result FAIL port_in_use STOP 'RESOURCE_GATE did not reserve this port; choose a new --port or stop the old service.'",
            "import socket, sys",
            "host, port = sys.argv[1], int(sys.argv[2])",
            "sock = socket.socket()",
            "try:",
            "    sock.bind((host, port))",
            "finally:",
            "    sock.close()",
            "PY",
            "rm -f \"$AGENTMON_EVENTS_PATH\"",
            ": > \"$SERVICE_LOG\"",
            "nohup \"$SERVICE_START_SCRIPT\" > \"$SERVICE_LOG\" 2>&1 &",
            "SERVICE_PID=$!",
            "echo \"$SERVICE_PID\" > \"$PID_FILE\"",
            "deadline=$((SECONDS + START_TIMEOUT))",
            "while [ \"$SECONDS\" -lt \"$deadline\" ]; do",
            "  if ! kill -0 \"$SERVICE_PID\" >/dev/null 2>&1; then",
            "    emit_result FAIL service_exited_before_ready STOP 'Open LOG and fix vLLM/model/CANN/start-script errors before retrying.'",
            "  fi",
            "  if \"$AGENTMON_PY\" - \"$VLLM_BASE_URL\" \"$SERVED_MODEL\" <<'PY' >/dev/null 2>&1; then",
            "import json, sys, urllib.request",
            "base, model = sys.argv[1], sys.argv[2]",
            "with urllib.request.urlopen(base.rstrip('/') + '/models', timeout=3) as resp:",
            "    data = json.loads(resp.read().decode('utf-8'))",
            "ids = [str(item.get('id', '')) for item in data.get('data', []) if isinstance(item, dict)]",
            "raise SystemExit(0 if model in ids else 1)",
            "PY",
            "    emit_result PASS model_listed RUNTIME_P2_GATE ''",
            "    exit 0",
            "  fi",
            "  sleep 5",
            "done",
            "emit_result FAIL model_not_listed STOP 'Check LOG for slow startup or wrong SERVED_MODEL/Base URL; increase SERVICE_START_TIMEOUT_SECONDS only after verifying the service is still loading.'",
            "",
        ]
    )
    write_private_text(path, text, mode=0o700)


def write_tool_call_gate_script(path: Path, report_path: Path, instance_env: Path, probe_path: Path) -> None:
    text = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"INSTANCE_ENV={shlex.quote(str(instance_env))}",
            f"GATE_JSON={shlex.quote(str(report_path))}",
            f"PROBE_JSON={shlex.quote(str(probe_path))}",
            "set -a",
            ". \"$INSTANCE_ENV\"",
            "set +a",
            "\"$AGENTMON_PY\" - \"$GATE_JSON\" \"$PROBE_JSON\" \"$VLLM_BASE_URL\" \"$TOOL_CALL_REQUIRED\" <<'PY'",
            "import json, sys, urllib.error, urllib.request",
            "from datetime import datetime, timezone",
            "from pathlib import Path",
            "",
            "gate_json, probe_json, base_url, required = sys.argv[1:5]",
            "step = 'TOOL_CALL_GATE'",
            "purpose = 'verify that the running service accepts OpenAI-compatible tool calls for the smoke request'",
            "pass_condition = 'tool calling is not required, or chat completion returns finish_reason=tool_calls with parseable function arguments'",
            "status = 'FAIL'",
            "reason = 'unknown'",
            "next_step = 'STOP'",
            "suggestion = ''",
            "detail = {}",
            "",
            "if required != '1':",
            "    status, reason, next_step = 'PASS', 'tool_call_not_required', 'RUNTIME_P2_GATE'",
            "else:",
            "    try:",
            "        payload = json.loads(Path(probe_json).read_text(encoding='utf-8'))",
            "        body = json.dumps(payload).encode('utf-8')",
            "        request = urllib.request.Request(",
            "            base_url.rstrip('/') + '/chat/completions',",
            "            data=body,",
            "            headers={'Content-Type': 'application/json'},",
            "            method='POST',",
            "        )",
            "        with urllib.request.urlopen(request, timeout=60) as response:",
            "            data = json.loads(response.read().decode('utf-8'))",
            "        choice = (data.get('choices') or [{}])[0]",
            "        message = choice.get('message') if isinstance(choice, dict) else {}",
            "        message = message if isinstance(message, dict) else {}",
            "        calls = message.get('tool_calls') or choice.get('tool_calls') or []",
            "        finish = choice.get('finish_reason')",
            "        detail = {'finish_reason': finish, 'tool_call_count': len(calls)}",
            "        if finish != 'tool_calls' or not calls:",
            "            reason = 'tool_call_not_returned'",
            "            suggestion = 'Restart with --enable-auto-tool-choice and the parser recorded in TOOL_CALL_PARSER, then rerun this gate.'",
            "        else:",
            "            function = (calls[0] or {}).get('function') or {}",
            "            name = function.get('name')",
            "            arguments = function.get('arguments') or '{}'",
            "            try:",
            "                parsed_arguments = json.loads(arguments)",
            "            except json.JSONDecodeError:",
            "                parsed_arguments = None",
            "            detail.update({'function_name': name, 'arguments_parseable': parsed_arguments is not None})",
            "            if name == 'agentmon_probe' and parsed_arguments is not None:",
            "                status, reason, next_step = 'PASS', 'tool_call_probe_passed', 'RUNTIME_P2_GATE'",
            "            else:",
            "                reason = 'tool_call_malformed'",
            "                suggestion = 'Check parser compatibility for the served model and inspect the raw response in JSON detail.'",
            "    except urllib.error.HTTPError as exc:",
            "        body = exc.read().decode('utf-8', errors='replace')",
            "        detail = {'http_status': exc.code, 'body_excerpt': body[:1000]}",
            "        reason = 'http_error'",
            "        suggestion = 'If the response mentions auto tool choice or parser support, restart vLLM with --enable-auto-tool-choice --tool-call-parser \"$TOOL_CALL_PARSER\".'",
            "    except Exception as exc:  # noqa: BLE001",
            "        detail = {'exception': exc.__class__.__name__, 'message': str(exc)}",
            "        reason = 'request_failed'",
            "        suggestion = 'Check SERVICE_START_GATE, VLLM_BASE_URL, SERVED_MODEL, and service log before retrying.'",
            "",
            "payload = {",
            "    'schema_version': '1.0',",
            "    'result_type': 'agentmon.server_quick_smoke.gate_result',",
            "    'created_at': datetime.now(timezone.utc).isoformat(),",
            "    'step': step,",
            "    'purpose': purpose,",
            "    'pass_condition': pass_condition,",
            "    'status': status,",
            "    'reason': reason,",
            "    'next': next_step,",
            "    'json': gate_json,",
            "    'suggestion': suggestion,",
            "    'base_url': base_url,",
            "    'detail': detail,",
            "}",
            "Path(gate_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\\n', encoding='utf-8')",
            "print(f'STEP={step}')",
            "print(f'PURPOSE={purpose}')",
            "print(f'STATUS={status}')",
            "print(f'REASON={reason}')",
            "print(f'NEXT={next_step}')",
            "print(f'PASS_CONDITION={pass_condition}')",
            "print(f'JSON={gate_json}')",
            "if suggestion:",
            "    print(f'SUGGESTION={suggestion}')",
            "print(f'TOOL_CALL_GATE={status}')",
            "raise SystemExit(0 if status == 'PASS' else 1)",
            "PY",
            "",
        ]
    )
    write_private_text(path, text, mode=0o700)


def write_mini_swe_probe(path: Path, instance: dict[str, Any]) -> None:
    home = str(Path(instance["p2_root"]) / "mini-swe-probe-home")
    xdg = str(Path(instance["p2_root"]) / "mini-swe-probe-xdg")
    text = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"export HOME={shlex.quote(home)}",
            f"export XDG_CONFIG_HOME={shlex.quote(xdg)}",
            "export MSWEA_CONFIGURED=1",
            "export MSWEA_COST_TRACKING=ignore_errors",
            "export OPENAI_API_KEY=not-needed",
            f"export PYTHONPATH={shlex.quote(instance['agentmon_repo'] + '/src')}",
            "mkdir -p \"$HOME\" \"$XDG_CONFIG_HOME\"",
            "STEP_NAME=MINI_SWE_NONINTERACTIVE_GATE",
            "PURPOSE_TEXT='verify mini-SWE can run in isolated noninteractive mode before batch execution'",
            "PASS_CONDITION_TEXT='mini --help exits without prompting for global setup and without timeout'",
            "LOG_PATH=\"$XDG_CONFIG_HOME/mini-help.stderr.log\"",
            f"timeout 10s {shlex.quote(instance['mini_swe_bin'])} --help > \"$XDG_CONFIG_HOME/mini-help.stdout.log\" 2> \"$XDG_CONFIG_HOME/mini-help.stderr.log\" || RC=$?",
            "RC=${RC:-0}",
            "if grep -R \"we need to set up your global config\" \"$XDG_CONFIG_HOME\" >/dev/null 2>&1; then",
            "  echo STEP=\"$STEP_NAME\"",
            "  echo PURPOSE=\"$PURPOSE_TEXT\"",
            "  echo STATUS=FAIL",
            "  echo MINI_SWE_NONINTERACTIVE_GATE=FAIL",
            "  echo REASON=interactive_setup_prompt",
            "  echo NEXT=STOP",
            "  echo PASS_CONDITION=\"$PASS_CONDITION_TEXT\"",
            "  echo LOG=\"$LOG_PATH\"",
            "  echo SUGGESTION='Precreate or point mini-SWE config in the isolated HOME/XDG, then rerun this gate.'",
            "  exit 1",
            "fi",
            "if [ \"$RC\" -eq 124 ]; then",
            "  echo STEP=\"$STEP_NAME\"",
            "  echo PURPOSE=\"$PURPOSE_TEXT\"",
            "  echo STATUS=FAIL",
            "  echo MINI_SWE_NONINTERACTIVE_GATE=FAIL",
            "  echo REASON=timeout",
            "  echo NEXT=STOP",
            "  echo PASS_CONDITION=\"$PASS_CONDITION_TEXT\"",
            "  echo LOG=\"$LOG_PATH\"",
            "  echo SUGGESTION='Inspect mini-help stdout/stderr logs; verify MINI_SWE_BIN points to the intended environment.'",
            "  exit 1",
            "fi",
            "echo STEP=\"$STEP_NAME\"",
            "echo PURPOSE=\"$PURPOSE_TEXT\"",
            "if [ \"$RC\" -ne 0 ]; then",
            "  echo STATUS=FAIL",
            "  echo REASON=mini_help_failed",
            "  echo NEXT=STOP",
            "  echo LOG=\"$LOG_PATH\"",
            "  echo MINI_SWE_NONINTERACTIVE_GATE=FAIL",
            "  exit \"$RC\"",
            "fi",
            "echo STATUS=PASS",
            "echo REASON=noninteractive_help_completed",
            "echo NEXT=SERVICE_START_GATE",
            "echo PASS_CONDITION=\"$PASS_CONDITION_TEXT\"",
            "echo LOG=\"$LOG_PATH\"",
            "echo MINI_SWE_NONINTERACTIVE_GATE=PASS",
            "",
        ]
    )
    write_private_text(path, text, mode=0o700)


def write_mini_model_config(path: Path, source: Any, base_url: str, served_model: str) -> None:
    source_path = Path(str(source)).expanduser() if source else None
    if source_path and source_path.is_file():
        text = source_path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"http://127\.0\.0\.1:\d+/v1", base_url, text)
        text = re.sub(r"http://localhost:\d+/v1", base_url, text)
        if base_url not in text:
            text += f"\n# AgentMon quick-smoke override\nmodel_kwargs:\n  api_base: {json.dumps(base_url)}\n"
    else:
        text = "\n".join(
            [
                "# Generated by agentmon_server_quick_smoke.py",
                "model_kwargs:",
                f"  api_base: {json.dumps(base_url)}",
                f"  model: {json.dumps('hosted_vllm/' + served_model if served_model else 'hosted_vllm/model')}",
                "",
            ]
        )
    write_private_text(path, text)


def write_agentmon_config(path: Path, source: str, instance: dict[str, Any]) -> None:
    source_path = Path(source).expanduser()
    if source_path.is_file():
        text = source_path.read_text(encoding="utf-8", errors="replace")
    else:
        text = default_agentmon_config()
    replacements = {
        ("vllm", "api_base"): instance["base_url"],
        ("vllm", "models_url"): f"{instance['base_url']}/models",
        ("vllm", "metrics_url"): instance["base_url"].rsplit("/v1", 1)[0] + "/metrics",
        ("vllm", "model_name"): instance["served_model"],
        ("model", "path"): instance["model_path"],
        ("model", "max_model_len"): str(instance["max_model_len"]),
        ("model", "tensor_parallel_size"): str(instance["tp_size"]),
        ("ascend", "device_ids"): instance["devices"],
        ("finalization", "p2_events_path"): instance["events_path"],
    }
    for (section, key), value in replacements.items():
        text = set_toml_value(text, section, key, value)
    write_private_text(path, text)


def default_agentmon_config() -> str:
    return "\n".join(
        [
            "[general]",
            'output_root = "/tmp/agentmon/runs"',
            "",
            "[agent]",
            'type = "mini-swe"',
            'env_set = ["MSWEA_CONFIGURED=1", "MSWEA_COST_TRACKING=ignore_errors", "OPENAI_API_KEY=not-needed"]',
            "",
            "[vllm]",
            'api_base = "http://127.0.0.1:8000/v1"',
            'models_url = "http://127.0.0.1:8000/v1/models"',
            'metrics_url = "http://127.0.0.1:8000/metrics"',
            'model_name = "model"',
            "",
            "[model]",
            'path = ""',
            "max_model_len = 32768",
            "tensor_parallel_size = 1",
            "",
            "[ascend]",
            'device_ids = "auto"',
            "",
            "[finalization]",
            'p2_events_path = ""',
            "",
        ]
    )


def set_toml_value(text: str, section: str, key: str, value: str) -> str:
    header_re = re.compile(rf"^\[{re.escape(section)}\]\s*$", re.MULTILINE)
    match = header_re.search(text)
    encoded = encode_toml_value(value)
    line = f"{key} = {encoded}"
    if not match:
        return text.rstrip() + f"\n\n[{section}]\n{line}\n"
    start = match.end()
    next_match = re.search(r"^\[[^\]]+\]\s*$", text[start:], re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    body = text[start:end]
    key_re = re.compile(rf"^({re.escape(key)}\s*=\s*).*$", re.MULTILINE)
    if key_re.search(body):
        body = key_re.sub(line, body)
    else:
        body = body.rstrip() + "\n" + line + "\n"
    return text[:start] + body + text[end:]


def encode_toml_value(value: str) -> str:
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return value
    return json.dumps(value)


def write_watchdog_script(path: Path, instance: dict[str, Any]) -> None:
    text = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"EVENTS={shlex.quote(instance['events_path'])}",
            f"SERVICE_LOG={shlex.quote(instance['service_log'])}",
            "STEP_NAME=STARTUP_WATCHDOG_GATE",
            "PURPOSE_TEXT='verify the running smoke produced AgentMon P2 events for this isolated events.jsonl'",
            "PASS_CONDITION_TEXT='events.jsonl exists and is nonempty after the startup/runtime probe'",
            "echo WATCHDOG_EVENTS_PATH=\"$EVENTS\"",
            "echo WATCHDOG_SERVICE_LOG=\"$SERVICE_LOG\"",
            "if [ ! -f \"$EVENTS\" ]; then",
            "  echo STEP=\"$STEP_NAME\"",
            "  echo PURPOSE=\"$PURPOSE_TEXT\"",
            "  echo STATUS=FAIL",
            "  echo REASON=events_missing",
            "  echo NEXT=STOP",
            "  echo PASS_CONDITION=\"$PASS_CONDITION_TEXT\"",
            "  echo LOG=\"$SERVICE_LOG\"",
            "  echo SUGGESTION='Check SERVICE_START_GATE, AGENTMON_EVENTS_PATH, and whether the request actually reached this vLLM service.'",
            "  echo STARTUP_WATCHDOG=FAIL",
            "  exit 1",
            "fi",
            "if [ -s \"$EVENTS\" ]; then",
            "  echo STEP=\"$STEP_NAME\"",
            "  echo PURPOSE=\"$PURPOSE_TEXT\"",
            "  echo STATUS=PASS",
            "  echo REASON=p2_events_nonempty",
            "  echo NEXT=PILOT_READY_GATE",
            "  echo PASS_CONDITION=\"$PASS_CONDITION_TEXT\"",
            "  echo LOG=\"$SERVICE_LOG\"",
            "  echo STARTUP_WATCHDOG=PASS",
            "  exit 0",
            "fi",
            "echo STEP=\"$STEP_NAME\"",
            "echo PURPOSE=\"$PURPOSE_TEXT\"",
            "echo STATUS=FAIL",
            "echo STARTUP_WATCHDOG=FAIL",
            "echo REASON=no_p2_events_yet",
            "echo NEXT=STOP",
            "echo PASS_CONDITION=\"$PASS_CONDITION_TEXT\"",
            "echo LOG=\"$SERVICE_LOG\"",
            "echo SUGGESTION='Send the Runtime P2 probe to VLLM_BASE_URL and inspect SERVICE_LOG for AgentMon hook installation or request routing issues.'",
            "exit 1",
            "",
        ]
    )
    write_private_text(path, text, mode=0o700)


def config_consistency(agentmon_config: Path, mini_model_config: Path, instance: dict[str, Any]) -> dict[str, Any]:
    expected = instance["base_url"]
    agentmon_text = agentmon_config.read_text(encoding="utf-8", errors="replace") if agentmon_config.is_file() else ""
    mini_text = mini_model_config.read_text(encoding="utf-8", errors="replace") if mini_model_config.is_file() else ""
    agentmon_urls = sorted(set(re.findall(r"https?://[^\"'\s]+", agentmon_text)))
    mini_urls = sorted(set(re.findall(r"https?://[^\"'\s]+", mini_text)))
    bad_agentmon = [url for url in agentmon_urls if "/v1" in url and not url.startswith(expected)]
    bad_mini = [url for url in mini_urls if "/v1" in url and not url.startswith(expected)]
    blockers = []
    if expected not in "\n".join(agentmon_urls):
        blockers.append("agentmon_resolved_api_base_missing_expected_url")
    if expected not in "\n".join(mini_urls):
        blockers.append("mini_model_config_missing_expected_url")
    blockers.extend(f"agentmon_config_unexpected_url:{url}" for url in bad_agentmon)
    blockers.extend(f"mini_model_config_unexpected_url:{url}" for url in bad_mini)
    return {
        "status": "FAIL" if blockers else "PASS",
        "expected_base_url": expected,
        "agentmon_urls": agentmon_urls,
        "mini_model_urls": mini_urls,
        "blocking_reasons": blockers,
    }


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def dotted(mapping: dict[str, Any], path: str) -> Any:
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def acl_from_profile(env: dict[str, Any]) -> str:
    cann_root = str(env.get("CANN_ROOT") or "")
    return str(Path(cann_root) / "python" / "site-packages") if cann_root else ""


def string_map(value: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(item) for key, item in value.items() if item not in (None, "")}


def expand_vars(value: str, variables: dict[str, str]) -> str:
    result = value
    for key, replacement in variables.items():
        result = result.replace("${" + key + "}", replacement)
    return result


def slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower())
    return text.strip("-") or "agentmon-server-quick-smoke"


def write_json(path: Path, payload: Any) -> None:
    write_private_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_private_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(mode)
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
