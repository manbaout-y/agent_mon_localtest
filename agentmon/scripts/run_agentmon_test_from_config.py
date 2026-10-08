#!/usr/bin/env python3
from __future__ import annotations

import argparse
import compileall
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import time
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEFAULT_CONFIG = ROOT / "agentmon_server_test_config.json"
EXAMPLE_CONFIG = ROOT / "examples" / "server_test_config.json"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.init_config:
        config_path = Path(args.init_config).expanduser()
        if config_path.exists() and not args.force:
            print(f"配置已存在，未覆盖：{config_path}", file=sys.stderr)
            print("如需覆盖，请加 --force。", file=sys.stderr)
            return 64
        seed_config(config_path, server_profile=args.server_profile)
        print(f"CONFIG_INITIALIZED={config_path}")
        return 0

    config_path = Path(args.config).expanduser() if args.config else DEFAULT_CONFIG
    if not config_path.exists():
        if args.config:
            print(f"配置不存在：{config_path}", file=sys.stderr)
            print(
                f"请先运行：{Path(sys.argv[0]).name} --init-config {config_path}",
                file=sys.stderr,
            )
            return 64
        cfg = default_self_check_config()
        config_label = "<builtin:self_check>"
    else:
        cfg = resolve_test_config(json.loads(config_path.read_text(encoding="utf-8")), config_path)
        config_label = str(config_path)

    report_dir = Path(cfg.get("report_dir") or default_report_dir()).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)
    actions = cfg.get("actions") or ["self_check"]

    results: dict[str, Any] = {
        "schema_version": "1.0",
        "repo": str(ROOT),
        "config_path": config_label,
        "report_dir": str(report_dir),
        "actions": {},
    }
    status = "PASS"
    for action in actions:
        started = time.monotonic()
        try:
            if action == "self_check":
                action_result = run_self_check(cfg, report_dir)
            elif action == "offline_analysis":
                action_result = run_offline_analysis(cfg, report_dir)
            elif action == "preflight":
                action_result = run_preflight(cfg, report_dir)
            else:
                action_result = {"status": "FAIL", "reason": f"unknown action: {action}"}
        except Exception as exc:  # noqa: BLE001 - test harness should report every failure as data.
            action_result = {"status": "FAIL", "error": type(exc).__name__, "message": str(exc)}
        action_result["duration_seconds"] = time.monotonic() - started
        results["actions"][action] = action_result
        print(f"{action}: {action_result.get('status')}")
        if action_result.get("status") == "FAIL":
            status = "FAIL"
        elif action_result.get("status") in {"PARTIAL", "SKIPPED", "NEEDS_INPUT"} and status == "PASS":
            status = "PARTIAL"

    results["status"] = status
    summary_path = report_dir / "test_summary.json"
    write_json(summary_path, results)
    print(f"REPORT_DIR={report_dir}")
    print(f"SUMMARY={summary_path}")
    return 0 if status in {"PASS", "PARTIAL"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run AgentMon package self-check, offline analysis, and server preflight from JSON config."
    )
    parser.add_argument("config", nargs="?", help="JSON config to execute; omitted means built-in self_check")
    parser.add_argument("--init-config", help="write a config template to this path and exit")
    parser.add_argument("--server-profile", help="optional server profile path to include in the initialized config")
    parser.add_argument("--force", action="store_true", help="overwrite --init-config target if it already exists")
    return parser


def default_self_check_config() -> dict[str, Any]:
    return {
        "actions": ["self_check"],
        "server_profile": "",
        "report_dir": "",
        "self_check": {"run_unit_tests": False},
    }


def seed_config(config_path: Path, *, server_profile: str | None = None) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if EXAMPLE_CONFIG.exists():
        payload = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        if server_profile:
            payload["server_profile"] = server_profile
        write_json(config_path, payload)
        return
    payload = default_self_check_config()
    payload["offline_analysis"] = {"input_path": "", "output_path": "", "output_format": "csv"}
    payload["preflight"] = {}
    if server_profile:
        payload["server_profile"] = server_profile
    write_json(config_path, payload)


def default_report_dir() -> str:
    return str(ROOT / "agentmon_test_reports" / time.strftime("%Y%m%d-%H%M%S"))


def resolve_test_config(cfg: dict[str, Any], config_path: Path) -> dict[str, Any]:
    profile_ref = cfg.get("server_profile") or cfg.get("server_profile_path")
    if not profile_ref:
        return expand_placeholders(cfg, default_variables({}))

    profile, profile_path = load_server_profile(profile_ref, config_path)
    profile_defaults = {
        key: value
        for key, value in profile.items()
        if key in {"environment", "self_check", "offline_analysis", "preflight"}
    }
    merged = deep_merge(profile_defaults, cfg)
    variables = default_variables(as_dict(profile.get("environment")))
    resolved = expand_placeholders(merged, variables)
    resolved["resolved_server_profile"] = {
        "name": profile.get("name") or "",
        "path": str(profile_path) if profile_path else "",
    }
    return resolved


def load_server_profile(profile_ref: Any, config_path: Path) -> tuple[dict[str, Any], Path | None]:
    if isinstance(profile_ref, dict):
        return dict(profile_ref), None
    raw = str(profile_ref or "").strip()
    if not raw:
        return {}, None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        candidates = [config_path.parent / path, ROOT / path]
        path = next((candidate for candidate in candidates if candidate.exists()), candidates[-1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"server profile must be a JSON object: {path}")
    return payload, path


def default_variables(profile_env: dict[str, Any]) -> dict[str, str]:
    variables = {key: str(value) for key, value in profile_env.items() if value not in {None, ""}}
    variables.update({key: value for key, value in os.environ.items() if value})
    variables["AGENTMON_REPO"] = str(ROOT)
    variables.setdefault("AGENTMON_CONFIG", str(ROOT / "examples" / "config.target.toml"))
    return variables


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def expand_placeholders(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: expand_placeholders(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_placeholders(item, variables) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    for key, replacement in variables.items():
        text = text.replace("${" + key + "}", replacement)
    return text


def run_self_check(cfg: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    compile_ok = compileall.compile_dir(str(SRC), quiet=1)
    script_errors: list[str] = []
    for script in (
        ROOT / "scripts" / "agentmon_batch_runtime.py",
        ROOT / "scripts" / "agentmon_experiment.py",
        ROOT / "scripts" / "agentmon_experiment_init.py",
        ROOT / "scripts" / "agentmon_experiment_orchestrator.py",
        ROOT / "scripts" / "agentmon_release_gate.py",
        ROOT / "scripts" / "agentmon_server_preflight.py",
        ROOT / "scripts" / "agentmon_server_quick_smoke.py",
        ROOT / "scripts" / "run_agentmon_analysis_from_path.py",
        ROOT / "scripts" / "run_agentmon_test_from_config.py",
    ):
        try:
            py_compile.compile(str(script), doraise=True)
        except py_compile.PyCompileError as exc:
            script_errors.append(str(exc))

    from agentmon.adapters import adapter_capability_catalog

    catalog = adapter_capability_catalog()
    unit_test_result: dict[str, Any] | None = None
    if as_bool(as_dict(cfg.get("self_check")).get("run_unit_tests")):
        unit_test_result = run_unit_tests(report_dir)

    result = {
        "status": "PASS" if compile_ok and not script_errors and not failed_unit_tests(unit_test_result) else "FAIL",
        "python": sys.executable,
        "python_version": list(sys.version_info[:3]),
        "compile_src_ok": compile_ok,
        "script_compile_errors": script_errors,
        "adapter_capabilities": catalog,
        "unit_tests": unit_test_result,
    }
    write_json(report_dir / "self_check.json", result)
    return result


def run_unit_tests(report_dir: Path) -> dict[str, Any]:
    log_path = report_dir / "unit_tests.log"
    stream = TeeText()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = runner.run(suite)
    log_path.write_text(stream.text, encoding="utf-8")
    return {
        "status": "PASS" if result.wasSuccessful() else "FAIL",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "log_path": str(log_path),
    }


def run_offline_analysis(cfg: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    offline = as_dict(cfg.get("offline_analysis"))
    input_path = str(offline.get("input_path") or "").strip()
    if not input_path:
        result = {
            "status": "NEEDS_INPUT",
            "reason": "offline_analysis.input_path is empty",
            "missing_fields": ["offline_analysis.input_path"],
            "output_path": str(offline.get("output_path") or report_dir / "analysis_bundle"),
        }
        write_json(report_dir / "offline_analysis.json", result)
        return result
    input_root = Path(input_path).expanduser().resolve()
    output_root = Path(
        str(offline.get("output_path") or report_dir / "analysis_bundle")
    ).expanduser().resolve()

    from agentmon.analysis.config import load_analysis_config
    from agentmon.analysis.pipeline import build_status_lines, run_all_analysis

    output_overrides = {
        "format": offline.get("output_format") or "csv",
        "figures": False,
        "fast_smoke": as_bool(offline.get("fast_smoke")),
        "include_evidence": not as_bool(offline.get("skip_evidence")),
        "include_handoff": not as_bool(offline.get("skip_handoff")),
        "include_model_pack": not as_bool(offline.get("skip_model_pack")),
    }
    if "omit_normalized_tables" in offline:
        output_overrides["omit_normalized_tables"] = as_list(offline.get("omit_normalized_tables"))
    cfg_overrides = {
        "profile": offline.get("profile") or "auto",
        "output": output_overrides,
        "runtime": {
            "jobs": int_or_default(offline.get("jobs"), 1),
            "temp_dir": offline.get("temp_dir"),
            "status_file": str(output_root / "analysis_status.json"),
            "memory_limit_bytes": offline.get("memory_limit_bytes"),
            "min_free_bytes": offline.get("min_free_bytes"),
            "timeout_seconds": offline.get("timeout_seconds"),
        },
        "baseline": {
            "expected_runs": offline.get("expected_runs"),
            "expected_request_attempts": offline.get("expected_request_attempts"),
            "expected_p2_requests": offline.get("expected_p2_requests"),
            "allow_baseline_mismatch": as_bool(offline.get("allow_baseline_mismatch")),
        },
    }
    analysis_cfg = load_analysis_config(overrides=cfg_overrides)
    result = run_all_analysis(
        input_root=str(input_root),
        output_root=str(output_root),
        cfg=analysis_cfg,
        output_format=str(offline.get("output_format") or "csv"),
        no_figures=True,
        force=as_bool(offline.get("force")),
        resume=as_bool(offline.get("resume")),
        fresh=as_bool(offline.get("fresh")),
        force_resume=as_bool(offline.get("force_resume")),
        preflight=as_bool(offline.get("analysis_preflight", True)),
        strict=as_bool(offline.get("strict")),
        force_stages=[str(item) for item in as_list(offline.get("force_stages"))],
        invalidate_stages=[str(item) for item in as_list(offline.get("invalidate_stages"))],
        from_stage=str(offline.get("from_stage")) if offline.get("from_stage") else None,
        to_stage=str(offline.get("to_stage")) if offline.get("to_stage") else None,
        dry_run=as_bool(offline.get("dry_run")),
        experiment_contract=str(Path(str(offline.get("experiment_contract"))).expanduser().resolve()) if offline.get("experiment_contract") else None,
    )
    status_lines = build_status_lines(str(output_root))
    analysis_status = result.get("analysis_status")
    action_status = "FAIL" if analysis_status == "FAIL" or (as_bool(offline.get("strict")) and analysis_status != "PASS") else str(analysis_status or "FAIL")
    action_result = {
        "status": action_status,
        "analysis_status": result.get("analysis_status"),
        "input_path": str(input_root),
        "output_path": str(output_root),
        "fast_smoke": analysis_cfg.output.fast_smoke,
        "jobs": analysis_cfg.runtime.jobs,
        "experiment_contract": str(offline.get("experiment_contract") or ""),
        "omitted_normalized_tables": list(analysis_cfg.output.omit_normalized_tables or []),
        "status_lines": status_lines,
        "run_summary": str(output_root / "run_summary.json"),
    }
    write_json(report_dir / "offline_analysis.json", action_result)
    return action_result


def run_preflight(cfg: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    preflight = as_dict(cfg.get("preflight"))
    missing_fields = preflight_missing_fields(preflight)
    if missing_fields:
        action_result = {
            "status": "NEEDS_INPUT",
            "reason": "preflight required inputs are not supplied yet",
            "missing_fields": missing_fields,
            "profile": cfg.get("resolved_server_profile"),
        }
        write_json(report_dir / "preflight_wrapper.json", action_result)
        return action_result

    maybe_create_events_parent(preflight)

    python = str(preflight.get("agentmon_python") or sys.executable)
    output = Path(str(preflight.get("output") or report_dir / "preflight.json")).expanduser()
    command = [
        python,
        str(ROOT / "scripts" / "agentmon_server_preflight.py"),
        "--agentmon-repo",
        str(ROOT),
        "--agentmon-python",
        python,
        "--output",
        str(output),
    ]
    add_optional_arg(command, "--mini-swe-python", preflight.get("mini_swe_python"))
    add_optional_arg(command, "--vllm-python", preflight.get("vllm_python"))
    add_optional_arg(command, "--config", preflight.get("agentmon_config"))
    add_optional_arg(command, "--events-path", preflight.get("events_path"))
    add_optional_arg(command, "--vllm-launch-command-file", preflight.get("vllm_launch_command_file"))
    add_optional_arg(command, "--expect-prefix-cache", preflight.get("expect_prefix_cache"))
    add_optional_arg(command, "--runtime-base-url", preflight.get("runtime_base_url"))
    add_optional_arg(command, "--runtime-model", preflight.get("runtime_model"))
    add_optional_arg(command, "--timeout", preflight.get("timeout_seconds"))
    for path in as_list(preflight.get("acl_python_paths")):
        add_optional_arg(command, "--acl-python-path", path)
    if as_bool(preflight.get("deep")):
        command.append("--deep")
    if as_bool(preflight.get("strict")):
        command.append("--strict")
    if as_bool(preflight.get("require_runtime_p2")):
        command.append("--require-runtime-p2")
    if as_bool(preflight.get("runtime_stream_probe")):
        command.append("--runtime-stream-probe")

    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    completed = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    parsed = read_json_if_exists(output)
    action_result = {
        "status": "PASS" if completed.returncode == 0 and as_dict(parsed).get("status") == "ready" else "FAIL",
        "returncode": completed.returncode,
        "output": str(output),
        "stdout_tail": completed.stdout[-20000:],
        "stderr_tail": completed.stderr[-20000:],
        "preflight_status": as_dict(parsed).get("status"),
    }
    write_json(report_dir / "preflight_wrapper.json", action_result)
    return action_result


def preflight_missing_fields(preflight: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if as_bool(preflight.get("deep")) and not str(preflight.get("vllm_python") or "").strip():
        missing.append("preflight.vllm_python")
    if as_bool(preflight.get("require_runtime_p2")) and not str(preflight.get("events_path") or "").strip():
        missing.append("preflight.events_path")
    runtime_base_url = str(preflight.get("runtime_base_url") or "").strip()
    runtime_model = str(preflight.get("runtime_model") or "").strip()
    if bool(runtime_base_url) != bool(runtime_model):
        missing.append("preflight.runtime_base_url/runtime_model")
    if as_bool(preflight.get("runtime_stream_probe")) and (not runtime_base_url or not runtime_model):
        if "preflight.runtime_base_url/runtime_model" not in missing:
            missing.append("preflight.runtime_base_url/runtime_model")
    return missing


def maybe_create_events_parent(preflight: dict[str, Any]) -> None:
    if not as_bool(preflight.get("create_events_parent")):
        return
    events_path = str(preflight.get("events_path") or "").strip()
    if not events_path:
        return
    Path(events_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def add_optional_arg(command: list[str], name: str, value: Any) -> None:
    if value is None or value == "":
        return
    command.extend([name, str(value)])


def failed_unit_tests(result: dict[str, Any] | None) -> bool:
    return result is not None and result.get("status") == "FAIL"


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class TeeText:
    def __init__(self) -> None:
        self.parts: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def write(self, text: str) -> int:
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
