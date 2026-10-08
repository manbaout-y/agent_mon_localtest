#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import uuid4


SCHEMA_VERSION = "1.1"
MINIMUM_PYTHON = (3, 10)
SUPPORTED_VLLM_VERSION = "0.18.0"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_DEEP_TIMEOUT_SECONDS = 60.0
DEFAULT_RUNTIME_STREAM_MAX_TOKENS = 128
DEFAULT_RUNTIME_CONCURRENCY = 4
DEFAULT_RUNTIME_CONCURRENT_MAX_TOKENS = 192
MINIMUM_CONCURRENT_GENERATION_TOKENS = 96
ACL_PYTHON_PATH_ENV = "AGENTMON_ACL_PYTHON_PATH"
RUNTIME_REQUIRED_LIFECYCLE = {
    "vllm.request.received",
    "vllm.request.queued",
    "vllm.request.first_scheduled",
    "vllm.request.prefill_completed",
    "vllm.request.first_token",
    "vllm.request.first_output",
    "vllm.request.engine_finished",
    "vllm.request.finished",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an unpacked AgentMon tree against target server runtimes; "
            "active HTTP probes run only when --runtime-base-url and --runtime-model are supplied."
        )
    )
    parser.add_argument(
        "--agentmon-repo",
        default=str(Path(__file__).resolve().parents[3]),
        help="unpacked AgentMon source root",
    )
    parser.add_argument(
        "--agentmon-python",
        default=sys.executable,
        help="Python used to run AgentMon",
    )
    parser.add_argument("--mini-swe-python", help="mini-SWE 2.4.3 environment Python")
    parser.add_argument("--vllm-python", help="Python from the actual vLLM/CANN service environment")
    parser.add_argument(
        "--acl-python-path",
        action="append",
        default=[],
        help=(
            "append an ACL Python module directory to probe PYTHONPATH; may be repeated. "
            f"The {ACL_PYTHON_PATH_ENV} environment variable is also supported"
        ),
    )
    parser.add_argument("--config", help="AgentMon TOML config to load")
    parser.add_argument("--events-path", help="intended service-scoped P2 events.jsonl path")
    parser.add_argument("--vllm-launch-command-file", help="exact vLLM launch command used by the service")
    parser.add_argument(
        "--expect-prefix-cache",
        choices=("any", "enabled", "disabled"),
        default="any",
        help="validate the launch command and runtime observations against this prefix-cache state",
    )
    parser.add_argument(
        "--require-runtime-p2",
        action="store_true",
        help="require runtime capability and request events from --events-path",
    )
    parser.add_argument(
        "--prefix-cache-probe-min-requests",
        type=int,
        default=2,
        help="minimum request count required for disabled-prefix-cache runtime validation",
    )
    parser.add_argument(
        "--runtime-base-url",
        help="OpenAI-compatible vLLM base URL; supplying this and --runtime-model enables active probes",
    )
    parser.add_argument("--runtime-model", help="served model name used by active runtime probes")
    parser.add_argument(
        "--runtime-stream-probe",
        action="store_true",
        help="send and strictly audit one streaming long-output request",
    )
    parser.add_argument(
        "--runtime-stream-max-tokens",
        type=int,
        default=DEFAULT_RUNTIME_STREAM_MAX_TOKENS,
    )
    parser.add_argument(
        "--runtime-concurrency",
        type=int,
        default=DEFAULT_RUNTIME_CONCURRENCY,
        help="number of simultaneous non-streaming long-output requests; zero disables this probe",
    )
    parser.add_argument(
        "--runtime-concurrent-max-tokens",
        type=int,
        default=DEFAULT_RUNTIME_CONCURRENT_MAX_TOKENS,
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="require the deep scheduler boundary and audit optional KV lifecycle methods",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return non-zero for warnings as well as required check failures",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=(
            "subprocess and active-probe timeout in seconds; defaults to 60 with --deep "
            "and 15 otherwise"
        ),
    )
    parser.add_argument("--output", help="also write the JSON result to this path")
    return parser


def effective_timeout_seconds(args: argparse.Namespace) -> float:
    if args.timeout is not None:
        return float(args.timeout)
    return DEFAULT_DEEP_TIMEOUT_SECONDS if args.deep else DEFAULT_TIMEOUT_SECONDS


def make_check(
    name: str,
    status: str,
    *,
    required: bool,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "required": required,
        "detail": detail or {},
    }


def cli_path(value: str | os.PathLike[str]) -> Path:
    """Make a CLI path absolute without resolving a venv interpreter symlink."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def python_identity(python: Path | None) -> dict[str, str | None]:
    if python is None:
        return {"supplied_path": None, "resolved_interpreter": None}
    try:
        resolved = str(python.resolve())
    except OSError:
        resolved = None
    return {"supplied_path": str(python), "resolved_interpreter": resolved}


def configured_acl_python_paths(
    cli_values: Sequence[str],
    base: dict[str, str] | None = None,
) -> list[Path]:
    env = os.environ if base is None else base
    raw_values = list(cli_values)
    env_value = env.get(ACL_PYTHON_PATH_ENV)
    if env_value:
        raw_values.extend(env_value.split(os.pathsep))
    paths: list[Path] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for value in str(raw_value).split(os.pathsep):
            if not value:
                continue
            path = cli_path(value)
            key = str(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
    return paths


def clean_python_env(
    repo: Path,
    base: dict[str, str] | None = None,
    *,
    extra_python_paths: Sequence[Path] = (),
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["AGENTMON_REPO"] = str(repo)
    existing = env.get("PYTHONPATH")
    components = [str(repo / "src")]
    if existing:
        components.extend(existing.split(os.pathsep))
    components.extend(str(path) for path in extra_python_paths)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(item for item in components if item))
    return env


def disabled_p2_env(
    repo: Path,
    *,
    extra_python_paths: Sequence[Path] = (),
) -> dict[str, str]:
    env = clean_python_env(repo, extra_python_paths=extra_python_paths)
    for name in (
        "AGENTMON_VLLM_P2_ENABLE",
        "AGENTMON_VLLM_P2_DEEP",
        "AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS",
        "AGENTMON_VLLM_P2_HOOK_KV_BLOCKS",
    ):
        env.pop(name, None)
    return env


def run_command(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "duration_seconds": time.monotonic() - started,
            "timeout_seconds": timeout,
            "stdout": _bounded_text(exc.stdout),
            "stderr": _bounded_text(exc.stderr),
        }
    except OSError as exc:
        return {
            "status": "failed",
            "duration_seconds": time.monotonic() - started,
            "error": type(exc).__name__,
            "message": str(exc),
        }
    return {
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": time.monotonic() - started,
        "stdout": _bounded_text(completed.stdout),
        "stderr": _bounded_text(completed.stderr),
    }


def _bounded_text(value: Any, limit: int = 131072) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text[-limit:]


def last_json_object(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def check_repo(repo: Path) -> dict[str, Any]:
    required_paths = [
        "pyproject.toml",
        "src/agentmon/__init__.py",
        "src/agentmon/cli.py",
        "src/sitecustomize.py",
        "scripts/agentmon_batch_runtime.py",
        "scripts/agentmon_step_sequence.py",
        "tests/unit",
    ]
    missing = [item for item in required_paths if not (repo / item).exists()]
    return make_check(
        "source_tree",
        "ok" if repo.is_dir() and not missing else "failed",
        required=True,
        detail={"repo": str(repo), "missing_paths": missing},
    )


def check_agentmon_python(repo: Path, python: Path, timeout: float) -> dict[str, Any]:
    script = (
        "import agentmon,json,sys;"
        "print(json.dumps({'version':agentmon.__version__,'python':sys.executable,"
        "'python_version':list(sys.version_info[:3])},sort_keys=True))"
    )
    command = run_command(
        [str(python), "-c", script],
        env=disabled_p2_env(repo),
        timeout=timeout,
    )
    payload = last_json_object(command.get("stdout", ""))
    version = tuple(payload.get("python_version", [])) if payload else ()
    okay = command["status"] == "ok" and version >= MINIMUM_PYTHON
    return make_check(
        "agentmon_python",
        "ok" if okay else command["status"] if command["status"] != "ok" else "failed",
        required=True,
        detail={
            "python": str(python),
            "python_identity": python_identity(python),
            "probe": payload,
            "command": command,
        },
    )


def check_agentmon_cli(repo: Path, python: Path, timeout: float) -> dict[str, Any]:
    command = run_command(
        [str(python), "-m", "agentmon", "--version"],
        env=disabled_p2_env(repo),
        timeout=timeout,
    )
    okay = command["status"] == "ok" and command.get("stdout", "").startswith("agentmon ")
    return make_check(
        "agentmon_cli",
        "ok" if okay else command["status"] if command["status"] != "ok" else "failed",
        required=True,
        detail={"command": command},
    )


def check_config(repo: Path, python: Path, config: Path | None, timeout: float) -> dict[str, Any]:
    if config is None:
        return make_check("config", "skipped", required=False, detail={"reason": "not supplied"})
    script = (
        "import json,sys;from agentmon.config import load_config;"
        "c=load_config(sys.argv[1]);"
        "print(json.dumps({'output_root':c.general.output_root,'agent_python':c.agent.python,"
        "'model_name':c.vllm.model_name},sort_keys=True))"
    )
    command = run_command(
        [str(python), "-c", script, str(config)],
        env=disabled_p2_env(repo),
        timeout=timeout,
    )
    return make_check(
        "config",
        command["status"],
        required=True,
        detail={"path": str(config), "probe": last_json_object(command.get("stdout", "")), "command": command},
    )


def check_sitecustomize(repo: Path, python: Path, timeout: float) -> dict[str, Any]:
    script = (
        "import json,sitecustomize,sys;"
        "print(json.dumps({'file':sitecustomize.__file__,"
        "'hook_imported':'agentmon.adapters.inference.vllm_018.hook' in sys.modules,"
        "'legacy_hook_imported':'agentmon.adapters.vllm_018.hook' in sys.modules},sort_keys=True))"
    )
    command = run_command(
        [str(python), "-c", script],
        env=disabled_p2_env(repo),
        timeout=timeout,
    )
    payload = last_json_object(command.get("stdout", ""))
    expected = (repo / "src" / "sitecustomize.py").resolve()
    actual = Path(str(payload.get("file", ""))).resolve() if payload and payload.get("file") else None
    okay = (
        command["status"] == "ok"
        and actual == expected
        and not payload.get("hook_imported", True)
        and not payload.get("legacy_hook_imported", True)
    )
    return make_check(
        "sitecustomize_disabled",
        "ok" if okay else command["status"] if command["status"] != "ok" else "failed",
        required=True,
        detail={"expected": str(expected), "probe": payload, "command": command},
    )


def check_mini_swe(repo: Path, python: Path | None, timeout: float) -> dict[str, Any]:
    if python is None:
        return make_check("mini_swe_p1", "skipped", required=False, detail={"reason": "not supplied"})
    script = """
import json
from agentmon.adapters.agent_frameworks.mini_swe.agent import InstrumentedInteractiveAgent
from agentmon.adapters.agent_frameworks.mini_swe.environment import InstrumentedMiniSweLocalEnvironment
from agentmon.adapters.agent_frameworks.mini_swe.model import InstrumentedLitellmModel
print(json.dumps({
    "agent": f"{InstrumentedInteractiveAgent.__module__}.{InstrumentedInteractiveAgent.__name__}",
    "model": f"{InstrumentedLitellmModel.__module__}.{InstrumentedLitellmModel.__name__}",
    "environment": f"{InstrumentedMiniSweLocalEnvironment.__module__}.{InstrumentedMiniSweLocalEnvironment.__name__}",
}, sort_keys=True))
"""
    env = disabled_p2_env(repo)
    import_command = run_command([str(python), "-c", script], env=env, timeout=timeout)
    mini_candidates = [python.parent / "mini", python.parent / "mini-swe-agent"]
    mini_bin = next((item for item in mini_candidates if item.is_file()), None)
    help_command = (
        run_command([str(mini_bin), "--help"], env=env, timeout=timeout)
        if mini_bin is not None
        else {"status": "failed", "message": "mini executable not found"}
    )
    help_text = f"{help_command.get('stdout', '')}\n{help_command.get('stderr', '')}"
    options = {
        name: name in help_text
        for name in ("--agent-class", "--model-class", "--environment-class")
    }
    okay = import_command["status"] == "ok" and help_command["status"] == "ok" and all(options.values())
    return make_check(
        "mini_swe_p1",
        "ok" if okay else "failed",
        required=True,
        detail={
            "python": str(python),
            "python_identity": python_identity(python),
            "mini_binary": str(mini_bin) if mini_bin else None,
            "adapter_import": last_json_object(import_command.get("stdout", "")),
            "class_options": options,
            "import_command": import_command,
            "help_command": help_command,
        },
    )


VLLM_PROBE = r'''
import argparse
import importlib
import inspect
import json

result = {
    "imports": {},
    "targets": {},
    "alias": {},
    "agentmon_hook_targets": {},
    "engine_args": {},
}
for module_name in ("vllm", "vllm_ascend", "acl"):
    try:
        module = importlib.import_module(module_name)
        result["imports"][module_name] = {
            "status": "ok",
            "file": getattr(module, "__file__", None),
            "version": getattr(module, "__version__", None),
        }
    except Exception as exc:
        result["imports"][module_name] = {
            "status": "failed",
            "error": type(exc).__name__,
            "message": str(exc),
        }

targets = [
    ("vllm.v1.engine.async_llm", "AsyncLLM", ["add_request", "generate", "abort"]),
    ("vllm.v1.engine.llm_engine", "LLMEngine", ["add_request", "step"]),
    ("vllm.v1.engine.core", "EngineCore", ["add_request", "step", "step_with_batch_queue"]),
    ("vllm.v1.core.sched.scheduler", "Scheduler", ["__init__", "add_request", "schedule", "make_stats", "update_from_output"]),
    ("vllm.v1.core.kv_cache_manager", "KVCacheManager", ["__init__", "make_prefix_cache_stats", "get_computed_blocks", "allocate_slots", "cache_blocks", "free", "evict_blocks", "reset_prefix_cache"]),
]
for module_name, class_name, methods in targets:
    key = f"{module_name}:{class_name}"
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
        method_info = {}
        for method_name in methods:
            method = getattr(cls, method_name, None)
            method_signature = None
            signature_error = None
            signature_valid = None
            if method is not None:
                try:
                    signature = inspect.signature(method)
                    method_signature = str(signature)
                    if method_name in {"step", "step_with_batch_queue"}:
                        parameters = list(signature.parameters.values())
                        signature_valid = (
                            bool(parameters)
                            and parameters[0].name == "self"
                            and all(
                                parameter.default is not inspect.Parameter.empty
                                or parameter.kind in (
                                    inspect.Parameter.VAR_POSITIONAL,
                                    inspect.Parameter.VAR_KEYWORD,
                                )
                                for parameter in parameters[1:]
                            )
                        )
                except Exception as exc:
                    signature_error = f"{type(exc).__name__}: {exc}"
            method_info[method_name] = {
                "present": method is not None,
                "signature": method_signature,
                "signature_valid": signature_valid,
                "signature_error": signature_error,
            }
        result["targets"][key] = {
            "status": "ok",
            "class_name": cls.__name__,
            "class_module": cls.__module__,
            "source_file": inspect.getsourcefile(cls),
            "methods": method_info,
        }
    except Exception as exc:
        result["targets"][key] = {
            "status": "failed",
            "error": type(exc).__name__,
            "message": str(exc),
        }

try:
    alias = getattr(importlib.import_module("vllm.engine.async_llm_engine"), "AsyncLLMEngine")
    impl = getattr(importlib.import_module("vllm.v1.engine.async_llm"), "AsyncLLM")
    result["alias"] = {
        "present": True,
        "same_object": alias is impl,
        "class_name": alias.__name__,
        "class_module": alias.__module__,
    }
except Exception as exc:
    result["alias"] = {"present": False, "error": type(exc).__name__, "message": str(exc)}

try:
    from agentmon.adapters.inference.vllm_018.hook import hook_targets

    for module_name, class_name, specs in hook_targets():
        result["agentmon_hook_targets"][f"{module_name}:{class_name}"] = [
            method_name for method_name, _factory in specs
        ]
except Exception as exc:
    result["agentmon_hook_targets"] = {
        "status": "failed",
        "error": type(exc).__name__,
        "message": str(exc),
    }

try:
    from vllm.engine.arg_utils import EngineArgs

    signature = inspect.signature(EngineArgs)
    for name in ("enable_prefix_caching", "async_scheduling"):
        parameter = signature.parameters.get(name)
        result["engine_args"][f"{name}_default"] = (
            None
            if parameter is None or parameter.default is inspect.Parameter.empty
            else parameter.default
        )
    try:
        from vllm.utils.argparse_utils import FlexibleArgumentParser
        parser = FlexibleArgumentParser(add_help=False)
    except Exception:
        parser = argparse.ArgumentParser(add_help=False)
    EngineArgs.add_cli_args(parser)
    for action in parser._actions:
        if action.dest not in {"enable_prefix_caching", "async_scheduling"}:
            continue
        result["engine_args"][f"{action.dest}_cli"] = {
            "option_strings": list(action.option_strings),
            "action_class": type(action).__name__,
            "default": action.default,
            "const": getattr(action, "const", None),
            "nargs": action.nargs,
        }
except Exception as exc:
    result["engine_args"]["probe_error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(result, sort_keys=True))
'''


def check_vllm(
    repo: Path,
    python: Path | None,
    timeout: float,
    deep: bool,
    *,
    launch_command: str | None = None,
    prefix_cache_expectation: str = "any",
    acl_python_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    if python is None:
        status = "failed" if deep else "skipped"
        return make_check(
            "vllm_runtime",
            status,
            required=deep,
            detail={"reason": "--vllm-python is required with --deep" if deep else "not supplied"},
        )
    probe_env = disabled_p2_env(repo, extra_python_paths=acl_python_paths)
    command = run_command(
        [str(python), "-c", VLLM_PROBE],
        env=probe_env,
        timeout=timeout,
    )
    probe_detail = {
        "python": str(python),
        "python_identity": python_identity(python),
        "acl_python_paths": [str(path) for path in acl_python_paths],
        "runtime_environment": {
            "PYTHONPATH": probe_env.get("PYTHONPATH"),
            "LD_LIBRARY_PATH": probe_env.get("LD_LIBRARY_PATH"),
            "ASCEND_HOME_PATH": probe_env.get("ASCEND_HOME_PATH"),
        },
        "supported_version": SUPPORTED_VLLM_VERSION,
        "probe_status": command.get("status"),
        "probe_timeout": command.get("status") == "timeout",
        "command": command,
    }
    if command.get("status") != "ok":
        return make_check(
            "vllm_runtime",
            "timeout" if command.get("status") == "timeout" else "failed",
            required=True,
            detail=probe_detail,
        )
    payload = last_json_object(command.get("stdout", "")) or {}
    imports = payload.get("imports", {})
    targets = payload.get("targets", {})
    vllm_version = str(imports.get("vllm", {}).get("version") or "")
    import_failures = [name for name in ("vllm", "vllm_ascend", "acl") if imports.get(name, {}).get("status") != "ok"]
    base_methods = {
        "vllm.v1.engine.async_llm:AsyncLLM": ["add_request", "generate", "abort"],
        "vllm.v1.engine.llm_engine:LLMEngine": ["add_request", "step"],
        "vllm.v1.engine.core:EngineCore": ["add_request", "step"],
        "vllm.v1.core.sched.scheduler:Scheduler": ["__init__", "add_request", "schedule", "make_stats"],
        "vllm.v1.core.kv_cache_manager:KVCacheManager": ["__init__", "make_prefix_cache_stats"],
    }
    missing_base = missing_methods(targets, base_methods)
    deep_required = {"vllm.v1.core.sched.scheduler:Scheduler": ["update_from_output"]}
    deep_optional = {
        "vllm.v1.core.kv_cache_manager:KVCacheManager": [
            "get_computed_blocks",
            "allocate_slots",
            "cache_blocks",
            "free",
            "evict_blocks",
            "reset_prefix_cache",
        ]
    }
    missing_deep_required = missing_methods(targets, deep_required) if deep else []
    missing_deep_optional = missing_methods(targets, deep_optional) if deep else []
    engine_core = targets.get("vllm.v1.engine.core:EngineCore", {})
    async_method = engine_core.get("methods", {}).get("step_with_batch_queue", {})
    step_method = engine_core.get("methods", {}).get("step", {})
    async_path_present = async_method.get("present") is True
    async_signature_valid = async_method.get("signature_valid") is True
    step_signature_valid = step_method.get("signature_valid") is True
    declared_hook_methods = payload.get("agentmon_hook_targets", {}).get(
        "vllm.v1.engine.core:EngineCore",
        [],
    )
    async_hook_declared = "step_with_batch_queue" in declared_hook_methods
    step_hook_declared = "step" in declared_hook_methods
    engine_args = payload.get("engine_args", {})
    prefix_launch = resolve_boolean_launch_setting(
        launch_command,
        engine_args.get("enable_prefix_caching_cli", {}),
        default=engine_args.get("enable_prefix_caching_default"),
    )
    async_launch = resolve_boolean_launch_setting(
        launch_command,
        engine_args.get("async_scheduling_cli", {}),
        default=engine_args.get("async_scheduling_default"),
    )
    async_path_required_failure = (
        async_path_present and (not async_signature_valid or not async_hook_declared)
    ) or (
        async_launch.get("value") is True
        and (not async_path_present or not async_signature_valid or not async_hook_declared)
    )
    prefix_launch_failure = (
        prefix_cache_expectation != "any"
        and (
            prefix_launch.get("value") is not (prefix_cache_expectation == "enabled")
            or prefix_launch.get("source") != "explicit_cli"
        )
    )
    actual_async = targets.get("vllm.v1.engine.async_llm:AsyncLLM", {})
    true_impl = (
        actual_async.get("class_name") == "AsyncLLM"
        and actual_async.get("class_module") == "vllm.v1.engine.async_llm"
    )
    required_ok = (
        command["status"] == "ok"
        and not import_failures
        and vllm_version.split("+", 1)[0] == SUPPORTED_VLLM_VERSION
        and not missing_base
        and not missing_deep_required
        and step_signature_valid
        and step_hook_declared
        and not async_path_required_failure
        and not prefix_launch_failure
        and true_impl
    )
    status = "failed" if not required_ok else "warning" if missing_deep_optional else "ok"
    return make_check(
        "vllm_runtime",
        status,
        required=True,
        detail={
            **probe_detail,
            "acl_file": imports.get("acl", {}).get("file"),
            "detected_version": vllm_version or None,
            "import_failures": import_failures,
            "missing_base_targets": missing_base,
            "missing_deep_required_targets": missing_deep_required,
            "missing_deep_optional_targets": missing_deep_optional,
            "engine_core_step_with_batch_queue_present": async_path_present,
            "engine_core_step_signature": step_method.get("signature"),
            "engine_core_step_signature_valid": step_signature_valid,
            "engine_core_step_hook_declared": step_hook_declared,
            "engine_core_step_with_batch_queue_signature": async_method.get("signature"),
            "engine_core_step_with_batch_queue_signature_valid": async_signature_valid,
            "engine_core_step_with_batch_queue_hook_declared": async_hook_declared,
            "asynchronous_scheduling_collection_status": (
                "ready"
                if async_path_present and async_signature_valid and async_hook_declared
                else "required_failure"
                if async_path_present or async_launch.get("value") is True
                else "method_not_available"
            ),
            "async_scheduling_launch_setting": async_launch,
            "async_scheduling_enabled": async_launch.get("value"),
            "async_scheduling_config_source": async_launch.get("source"),
            "prefix_cache_expectation": prefix_cache_expectation,
            "prefix_cache_launch_setting": prefix_launch,
            "prefix_cache_launch_validation": (
                "failed" if prefix_launch_failure else "passed" if prefix_cache_expectation != "any" else "not_requested"
            ),
            "prefix_cache_cli_options": engine_args.get("enable_prefix_caching_cli", {}).get(
                "option_strings",
                [],
            ),
            "true_async_llm_implementation": true_impl,
            "probe": payload,
        },
    )


def missing_methods(targets: dict[str, Any], expected: dict[str, list[str]]) -> list[str]:
    missing: list[str] = []
    for target, methods in expected.items():
        target_info = targets.get(target, {})
        if target_info.get("status") != "ok":
            missing.append(target)
            continue
        available = target_info.get("methods", {})
        for method in methods:
            if not available.get(method, {}).get("present"):
                missing.append(f"{target}.{method}")
    return missing


def resolve_boolean_launch_setting(
    command: str | None,
    action: dict[str, Any],
    *,
    default: Any,
) -> dict[str, Any]:
    options = [str(item) for item in action.get("option_strings", []) if item]
    try:
        tokens = shlex.split(command) if command else []
    except ValueError as exc:
        return {
            "value": None,
            "source": "invalid_launch_command",
            "error": str(exc),
            "supported_options": options,
        }
    resolved: bool | None = None
    matched_option: str | None = None
    for index, token in enumerate(tokens):
        flag, separator, inline = token.partition("=")
        if flag not in options:
            continue
        matched_option = flag
        if separator:
            resolved = parse_bool(inline)
        elif flag.startswith("--no-") or flag.startswith("--disable-"):
            resolved = False
        elif action.get("action_class") in {"BooleanOptionalAction", "_StoreTrueAction"}:
            resolved = True
        elif index + 1 < len(tokens):
            resolved = parse_bool(tokens[index + 1])
    if matched_option is not None:
        return {
            "value": resolved,
            "source": "explicit_cli",
            "matched_option": matched_option,
            "supported_options": options,
        }
    default_value = default if isinstance(default, bool) else None
    return {
        "value": default_value,
        "source": "engine_args_default" if default_value is not None else "unresolved_default",
        "matched_option": None,
        "supported_options": options,
    }


def parse_bool(value: Any) -> bool | None:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def check_events_path(path: Path | None) -> dict[str, Any]:
    if path is None:
        return make_check("events_path", "skipped", required=False, detail={"reason": "not supplied"})
    parent = path.parent
    if not parent.is_dir():
        return make_check(
            "events_path",
            "failed",
            required=True,
            detail={"path": str(path), "error": "parent directory does not exist"},
        )
    probe = parent / f".agentmon-preflight-{os.getpid()}-{uuid4().hex}"
    mode = None
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, b"ok\n")
        finally:
            os.close(fd)
        mode = stat.S_IMODE(probe.stat().st_mode)
    except OSError as exc:
        return make_check(
            "events_path",
            "failed",
            required=True,
            detail={"path": str(path), "error": type(exc).__name__, "message": str(exc)},
        )
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass
    parent_mode = stat.S_IMODE(parent.stat().st_mode)
    target_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    private = mode == 0o600 and parent_mode & 0o077 == 0 and (target_mode is None or target_mode & 0o077 == 0)
    return make_check(
        "events_path",
        "ok" if private else "warning",
        required=True,
        detail={
            "path": str(path),
            "parent_mode": oct(parent_mode),
            "existing_target_mode": oct(target_mode) if target_mode is not None else None,
            "probe_mode": oct(mode) if mode is not None else None,
            "private_permissions": private,
        },
    )


def read_p2_event_stream(path: Path, *, start_offset: int = 0) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    invalid_json_lines = 0
    event_ids: set[str] = set()
    duplicate_event_ids = 0
    sequence_last: dict[tuple[str, str | None], int] = {}
    sequence_duplicates = 0
    sequence_regressions = 0
    with path.open("rb") as handle:
        handle.seek(max(0, start_offset))
        data = handle.read()
    for raw_line in data.splitlines():
        try:
            event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid_json_lines += 1
            continue
        if not isinstance(event, dict) or event.get("source") != "vllm_p2":
            continue
        event_id = event.get("event_id")
        if event_id not in {None, ""}:
            normalized_event_id = str(event_id)
            if normalized_event_id in event_ids:
                duplicate_event_ids += 1
            event_ids.add(normalized_event_id)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        source_pid = payload.get("source_pid")
        sequence = integer_or_none(payload.get("sequence"))
        if source_pid is not None and sequence is not None:
            sequence_key = (str(source_pid), none_if_empty(payload.get("source_starttime_ticks")))
            previous = sequence_last.get(sequence_key)
            if previous is not None:
                if sequence == previous:
                    sequence_duplicates += 1
                elif sequence < previous:
                    sequence_regressions += 1
            sequence_last[sequence_key] = sequence
        events.append(event)
    return {
        "events": events,
        "start_offset": max(0, start_offset),
        "bytes_read": len(data),
        "invalid_json_lines": invalid_json_lines,
        "duplicate_event_ids": duplicate_event_ids,
        "sequence_duplicates": sequence_duplicates,
        "sequence_regressions": sequence_regressions,
    }


def event_stream_integrity(snapshot: dict[str, Any]) -> dict[str, Any]:
    failures = []
    for field in (
        "invalid_json_lines",
        "duplicate_event_ids",
        "sequence_duplicates",
        "sequence_regressions",
    ):
        if int(snapshot.get(field) or 0) > 0:
            failures.append(field)
    return {
        "status": "pass" if not failures else "fail",
        "invalid_json_lines": int(snapshot.get("invalid_json_lines") or 0),
        "duplicate_event_ids": int(snapshot.get("duplicate_event_ids") or 0),
        "sequence_duplicates": int(snapshot.get("sequence_duplicates") or 0),
        "sequence_regressions": int(snapshot.get("sequence_regressions") or 0),
        "failures": failures,
    }


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def parse_streaming_response_lines(lines: Sequence[bytes | str]) -> dict[str, Any]:
    api_request_id: str | None = None
    usage_completion_tokens: int | None = None
    chunk_count = 0
    invalid_chunk_count = 0
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            invalid_chunk_count += 1
            continue
        if not isinstance(chunk, dict):
            invalid_chunk_count += 1
            continue
        chunk_count += 1
        if chunk.get("id") not in {None, ""}:
            api_request_id = str(chunk["id"])
        usage = chunk.get("usage") if isinstance(chunk.get("usage"), dict) else {}
        observed_usage = integer_or_none(usage.get("completion_tokens"))
        if observed_usage is not None:
            usage_completion_tokens = observed_usage
    return {
        "api_request_id": api_request_id,
        "api_generation_tokens": usage_completion_tokens,
        "stream_chunk_count": chunk_count,
        "invalid_stream_chunk_count": invalid_chunk_count,
    }


def send_chat_completion(
    base_url: str,
    model: str,
    *,
    prompt: str,
    max_tokens: int,
    stream: bool,
    timeout: float,
) -> dict[str, Any]:
    started_wall_ns = time.time_ns()
    started_mono_ns = time.monotonic_ns()
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    http_request = urllib_request.Request(
        chat_completions_url(base_url),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(http_request, timeout=timeout) as response:
            if stream:
                parsed = parse_streaming_response_lines(list(response))
            else:
                payload = json.loads(response.read().decode("utf-8"))
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                parsed = {
                    "api_request_id": none_if_empty(payload.get("id")),
                    "api_generation_tokens": integer_or_none(usage.get("completion_tokens")),
                    "stream_chunk_count": None,
                    "invalid_stream_chunk_count": 0,
                }
            status_code = getattr(response, "status", 200)
    except urllib_error.HTTPError as exc:
        return runtime_http_failure(exc, started_wall_ns, started_mono_ns, response_body=exc.read())
    except (urllib_error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return runtime_http_failure(exc, started_wall_ns, started_mono_ns)
    ended_wall_ns = time.time_ns()
    ended_mono_ns = time.monotonic_ns()
    failures = []
    if parsed.get("api_request_id") is None:
        failures.append("api_request_id_missing")
    if parsed.get("api_generation_tokens") is None:
        failures.append("api_usage_completion_tokens_missing")
    if int(parsed.get("invalid_stream_chunk_count") or 0) > 0:
        failures.append("invalid_stream_chunk")
    return {
        "status": "pass" if not failures and 200 <= int(status_code) < 300 else "fail",
        "http_status": int(status_code),
        "request_started_wall_ns": started_wall_ns,
        "request_finished_wall_ns": ended_wall_ns,
        "request_started_mono_ns": started_mono_ns,
        "request_finished_mono_ns": ended_mono_ns,
        "duration_seconds": max(0.0, (ended_mono_ns - started_mono_ns) / 1_000_000_000),
        **parsed,
        "failures": failures,
    }


def runtime_http_failure(
    exc: BaseException,
    started_wall_ns: int,
    started_mono_ns: int,
    *,
    response_body: bytes | None = None,
) -> dict[str, Any]:
    return {
        "status": "fail",
        "api_request_id": None,
        "api_generation_tokens": None,
        "stream_chunk_count": None,
        "request_started_wall_ns": started_wall_ns,
        "request_finished_wall_ns": time.time_ns(),
        "request_started_mono_ns": started_mono_ns,
        "request_finished_mono_ns": time.monotonic_ns(),
        "error": type(exc).__name__,
        "message": str(exc),
        "response_body": _bounded_text(response_body, limit=8192),
        "failures": ["api_request_failed"],
    }


def run_concurrent_api_requests(
    base_url: str,
    model: str,
    *,
    concurrency: int,
    max_tokens: int,
    timeout: float,
) -> list[dict[str, Any]]:
    barrier = threading.Barrier(concurrency)

    def worker(index: int) -> dict[str, Any]:
        try:
            barrier.wait(timeout=timeout)
        except threading.BrokenBarrierError as exc:
            now_wall = time.time_ns()
            now_mono = time.monotonic_ns()
            return runtime_http_failure(exc, now_wall, now_mono)
        return send_chat_completion(
            base_url,
            model,
            prompt=(
                f"Concurrency probe {index}-{uuid4().hex}. "
                "Output the integers from 1 through 120 separated by single spaces. "
                "Output nothing else."
            ),
            max_tokens=max_tokens,
            stream=False,
            timeout=timeout,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        return list(executor.map(worker, range(concurrency)))


def payload_of(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def engine_request_id_from_event(event: dict[str, Any]) -> str | None:
    payload = payload_of(event)
    return none_if_empty(
        payload.get("vllm_request_id")
        or payload.get("engine_request_id")
        or payload.get("request_id")
    )


def api_request_ids_from_event(event: dict[str, Any]) -> set[str]:
    payload = payload_of(event)
    return {
        value
        for value in (
            none_if_empty(payload.get("api_request_id")),
            none_if_empty(payload.get("server_request_id")),
            none_if_empty(payload.get("external_request_id")),
        )
        if value is not None
    }


def engine_ids_for_api_request(events: Sequence[dict[str, Any]], api_request_id: str) -> set[str]:
    result: set[str] = set()
    for event in events:
        engine_id = engine_request_id_from_event(event)
        if engine_id is None:
            continue
        if api_request_id in api_request_ids_from_event(event) or engine_id == api_request_id:
            result.add(engine_id)
    return result


def request_event_audit(events: Sequence[dict[str, Any]], api_request_id: str) -> dict[str, Any]:
    engine_ids = engine_ids_for_api_request(events, api_request_id)
    request_events = [
        event for event in events if engine_request_id_from_event(event) in engine_ids
    ]
    counts = {
        event_type: sum(event.get("event_type") == event_type for event in request_events)
        for event_type in RUNTIME_REQUIRED_LIFECYCLE
    }
    lifecycle_failures = []
    for event_type in sorted(RUNTIME_REQUIRED_LIFECYCLE):
        count = counts[event_type]
        short_name = event_type.rsplit(".", 1)[-1]
        if count == 0:
            lifecycle_failures.append(f"missing_{short_name}")
        elif count > 1:
            lifecycle_failures.append(f"duplicate_{short_name}")
    if len(engine_ids) == 0:
        correlation_status = "unmatched"
    elif len(engine_ids) == 1:
        correlation_status = "exact"
    else:
        correlation_status = "multiple"

    by_type: dict[str, dict[str, Any]] = {}
    for event in request_events:
        event_type = str(event.get("event_type") or "")
        by_type.setdefault(event_type, event)
    timestamps = {
        "received": event_timestamp(by_type.get("vllm.request.received"), "received_ns"),
        "queued": event_timestamp(by_type.get("vllm.request.queued"), "queued_ns"),
        "first_scheduled": event_timestamp(
            by_type.get("vllm.request.first_scheduled"), "first_scheduled_ns"
        ),
        "prefill_completed": event_timestamp(
            by_type.get("vllm.request.prefill_completed"), "prefill_completed_ns"
        ),
        "first_token": event_timestamp(by_type.get("vllm.request.first_token"), "first_token_ns"),
        "first_output": event_timestamp(by_type.get("vllm.request.first_output"), "first_output_ns"),
        "engine_finished": event_timestamp(
            by_type.get("vllm.request.engine_finished"), "engine_finished_ns"
        ),
        "finished": event_timestamp(by_type.get("vllm.request.finished"), "finished_ns"),
    }
    engine_payload = payload_of(by_type.get("vllm.request.engine_finished", {}))
    finished_payload = payload_of(by_type.get("vllm.request.finished", {}))
    last_token_ns = integer_or_none(engine_payload.get("last_token_ns"))
    if counts["vllm.request.engine_finished"] == 1 and last_token_ns is None:
        lifecycle_failures.append("last_token_ns_missing")
    ordering_pairs = [
        ("received", "queued"),
        ("queued", "first_scheduled"),
        ("first_scheduled", "prefill_completed"),
        ("prefill_completed", "first_token"),
        ("engine_finished", "finished"),
        ("first_token", "first_output"),
        ("first_output", "finished"),
    ]
    for left, right in ordering_pairs:
        if timestamps[left] is None or timestamps[right] is None:
            continue
        if timestamps[left] > timestamps[right]:
            lifecycle_failures.append(f"timestamp_order_{left}_after_{right}")
    if timestamps["first_token"] is not None and last_token_ns is not None:
        if timestamps["first_token"] > last_token_ns:
            lifecycle_failures.append("timestamp_order_first_token_after_last_token")
    if last_token_ns is not None and timestamps["engine_finished"] is not None:
        if last_token_ns > timestamps["engine_finished"]:
            lifecycle_failures.append("timestamp_order_last_token_after_engine_finished")
    cached_values = [
        integer_or_none(payload_of(event).get("cached_prompt_tokens"))
        for event in request_events
    ]
    cached_values = [value for value in cached_values if value is not None]
    return {
        "api_request_id": api_request_id,
        "vllm_request_id": next(iter(engine_ids)) if len(engine_ids) == 1 else None,
        "engine_request_ids": sorted(engine_ids),
        "correlation_status": correlation_status,
        "event_counts": counts,
        "first_token_count": counts["vllm.request.first_token"],
        "engine_finished_count": counts["vllm.request.engine_finished"],
        "lifecycle_failures": sorted(set(lifecycle_failures)),
        "timestamps": timestamps,
        "last_token_ns": last_token_ns,
        "last_token_ns_present": last_token_ns is not None,
        "engine_generation_tokens": integer_or_none(engine_payload.get("generation_tokens")),
        "finished_generation_tokens": integer_or_none(finished_payload.get("generation_tokens")),
        "cached_prompt_tokens_max": max(cached_values, default=None),
    }


def event_timestamp(event: dict[str, Any] | None, payload_key: str) -> int | None:
    if not event:
        return None
    return integer_or_none(payload_of(event).get(payload_key) or event.get("monotonic_time_ns"))


def audit_stream_probe(api_result: dict[str, Any], events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    api_request_id = none_if_empty(api_result.get("api_request_id"))
    audit = request_event_audit(events, api_request_id) if api_request_id else None
    failures = list(api_result.get("failures") or [])
    if api_request_id is None:
        failures.append("api_request_id_missing")
    elif audit is not None:
        if audit["correlation_status"] != "exact":
            failures.append(f"correlation_{audit['correlation_status']}")
        failures.extend(audit["lifecycle_failures"])
        api_tokens = integer_or_none(api_result.get("api_generation_tokens"))
        engine_tokens = audit.get("engine_generation_tokens")
        finished_tokens = audit.get("finished_generation_tokens")
        if api_tokens is None:
            failures.append("api_usage_completion_tokens_missing")
        elif api_tokens <= 1:
            failures.append("generation_tokens_not_long_output")
        if engine_tokens != api_tokens:
            failures.append("engine_generation_tokens_mismatch")
        if finished_tokens != api_tokens:
            failures.append("finished_generation_tokens_mismatch")
        if not audit["last_token_ns_present"]:
            failures.append("last_token_ns_missing")
    return {
        "status": "pass" if not failures else "fail",
        "api_request_id": api_request_id,
        "vllm_request_id": audit.get("vllm_request_id") if audit else None,
        "api_generation_tokens": integer_or_none(api_result.get("api_generation_tokens")),
        "p2_generation_tokens": audit.get("engine_generation_tokens") if audit else None,
        "finished_generation_tokens": audit.get("finished_generation_tokens") if audit else None,
        "stream_chunk_count": integer_or_none(api_result.get("stream_chunk_count")),
        "first_token_count": audit.get("first_token_count", 0) if audit else 0,
        "engine_finished_count": audit.get("engine_finished_count", 0) if audit else 0,
        "last_token_ns_present": bool(audit and audit["last_token_ns_present"]),
        "request_started_wall_ns": api_result.get("request_started_wall_ns"),
        "request_finished_wall_ns": api_result.get("request_finished_wall_ns"),
        "failures": sorted(set(failures)),
    }


def event_relates_to_engine_ids(event: dict[str, Any], engine_ids: set[str]) -> bool:
    if engine_request_id_from_event(event) in engine_ids:
        return True
    summaries = payload_of(event).get("request_summaries")
    if not isinstance(summaries, list):
        return False
    return any(
        isinstance(item, dict) and none_if_empty(item.get("vllm_request_id")) in engine_ids
        for item in summaries
    )


def audit_concurrency_probe(
    api_results: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    requested = len(api_results)
    succeeded = sum(result.get("status") == "pass" for result in api_results)
    api_ids = [
        value
        for result in api_results
        if (value := none_if_empty(result.get("api_request_id"))) is not None
    ]
    audits = [request_event_audit(events, api_request_id) for api_request_id in api_ids]
    correlation_failure_count = sum(audit["correlation_status"] != "exact" for audit in audits)
    exact_engine_ids = [
        str(audit["vllm_request_id"])
        for audit in audits
        if audit.get("vllm_request_id") is not None
    ]
    collision_count = len(exact_engine_ids) - len(set(exact_engine_ids))
    correlation_failure_count += max(0, collision_count)
    lifecycle_failure_count = sum(bool(audit["lifecycle_failures"]) for audit in audits)
    token_mismatch_count = 0
    insufficient_generation_count = 0
    audit_by_api = {audit["api_request_id"]: audit for audit in audits}
    for result in api_results:
        api_id = none_if_empty(result.get("api_request_id"))
        audit = audit_by_api.get(api_id)
        api_tokens = integer_or_none(result.get("api_generation_tokens"))
        if api_tokens is None or api_tokens < MINIMUM_CONCURRENT_GENERATION_TOKENS:
            insufficient_generation_count += 1
        if audit is None or api_tokens is None or (
            audit.get("engine_generation_tokens") != api_tokens
            or audit.get("finished_generation_tokens") != api_tokens
        ):
            token_mismatch_count += 1
    engine_id_set = set(exact_engine_ids)
    relevant_events = [event for event in events if event_relates_to_engine_ids(event, engine_id_set)]
    batch_counts = [
        integer_or_none(payload_of(event).get("batch_request_count"))
        for event in relevant_events
    ]
    batch_counts = [value for value in batch_counts if value is not None]
    max_batch_request_count = max(batch_counts, default=0)
    continuous_batching_observed = any(
        payload_of(event).get("continuous_batching_observed") is True
        for event in relevant_events
    ) or max_batch_request_count >= 2
    failures = []
    if succeeded != requested:
        failures.append("concurrent_request_failure")
    if len(set(api_ids)) != requested:
        failures.append("api_request_ids_not_unique")
    if len(set(exact_engine_ids)) != requested:
        failures.append("engine_request_ids_not_one_to_one")
    if correlation_failure_count:
        failures.append("request_id_correlation_failure")
    if lifecycle_failure_count:
        failures.append("request_lifecycle_failure")
    if token_mismatch_count:
        failures.append("generation_tokens_mismatch")
    if insufficient_generation_count:
        failures.append("concurrent_generation_below_96_tokens")
    if max_batch_request_count < 2 or not continuous_batching_observed:
        failures.append("concurrent_requests_did_not_overlap")
    adapter_error_count = sum(event.get("event_type") == "vllm.adapter.error" for event in events)
    if adapter_error_count and succeeded != requested:
        failures.append("adapter_error_with_request_failure")
    return {
        "status": "pass" if not failures else "fail",
        "requested": requested,
        "succeeded": succeeded,
        "unique_api_request_ids": len(set(api_ids)),
        "unique_vllm_request_ids": len(set(exact_engine_ids)),
        "max_batch_request_count": max_batch_request_count,
        "continuous_batching_observed": continuous_batching_observed,
        "correlation_failure_count": correlation_failure_count,
        "lifecycle_failure_count": lifecycle_failure_count,
        "token_mismatch_count": token_mismatch_count,
        "insufficient_generation_count": insufficient_generation_count,
        "adapter_error_count": adapter_error_count,
        "requests": audits,
        "failures": failures,
    }


def audit_prefix_cache_probe(
    api_results: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    *,
    expectation: str,
    runtime_capability: dict[str, Any],
) -> dict[str, Any]:
    api_ids = [
        value
        for result in api_results
        if (value := none_if_empty(result.get("api_request_id"))) is not None
    ]
    audits = [request_event_audit(events, api_request_id) for api_request_id in api_ids]
    cached_values = [
        integer_or_none(audit.get("cached_prompt_tokens_max")) for audit in audits
    ]
    cached_values = [value for value in cached_values if value is not None]
    failures = []
    if sum(result.get("status") == "pass" for result in api_results) != 2:
        failures.append("prefix_cache_probe_request_failure")
    if len(api_ids) != 2 or any(audit["correlation_status"] != "exact" for audit in audits):
        failures.append("prefix_cache_probe_correlation_failure")
    if len(cached_values) != 2:
        failures.append("prefix_cache_accounting_missing")
    if expectation != "any":
        expected_enabled = expectation == "enabled"
        if runtime_capability.get("prefix_cache_enabled") is not expected_enabled:
            failures.append("prefix_cache_runtime_config")
    if expectation == "disabled" and any(value > 0 for value in cached_values):
        failures.append("prefix_cache_hit_while_disabled")
    return {
        "status": "pass" if not failures else "fail",
        "requested": 2,
        "succeeded": sum(result.get("status") == "pass" for result in api_results),
        "api_request_ids": api_ids,
        "prefix_cache_expectation": expectation,
        "prefix_cache_enabled": runtime_capability.get("prefix_cache_enabled"),
        "cached_prompt_tokens_max": max(cached_values, default=None),
        "failures": failures,
    }


def latest_runtime_capability(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("event_type") != "vllm.adapter.capability":
            continue
        payload = payload_of(event)
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        if details.get("capability_phase") == "scheduler_runtime_configuration":
            return {"status": payload.get("status"), **details}
    return {}


def wait_for_probe_events(
    path: Path,
    *,
    start_offset: int,
    api_request_ids: set[str],
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + min(max(timeout, 1.0), 30.0)
    snapshot = read_p2_event_stream(path, start_offset=start_offset)
    while api_request_ids and time.monotonic() < deadline:
        events = snapshot["events"]
        complete = True
        for api_request_id in api_request_ids:
            engine_ids = engine_ids_for_api_request(events, api_request_id)
            if len(engine_ids) != 1:
                complete = False
                break
            engine_id = next(iter(engine_ids))
            event_types = {
                event.get("event_type")
                for event in events
                if engine_request_id_from_event(event) == engine_id
            }
            if not RUNTIME_REQUIRED_LIFECYCLE.issubset(event_types):
                complete = False
                break
        if complete:
            return snapshot
        time.sleep(0.1)
        snapshot = read_p2_event_stream(path, start_offset=start_offset)
    return snapshot


def skipped_active_runtime_result(reason: str = "runtime base URL/model not supplied") -> dict[str, Any]:
    skipped = {"status": "skipped", "failures": [], "reason": reason}
    return {
        "status": "skipped",
        "runtime_prefix_cache_probe": dict(skipped),
        "runtime_stream_probe": dict(skipped),
        "runtime_concurrency_probe": dict(skipped),
        "event_stream_integrity": dict(skipped),
        "failures": [],
    }


def run_active_runtime_probes(
    *,
    base_url: str,
    model: str,
    events_path: Path,
    stream_probe: bool,
    stream_max_tokens: int,
    concurrency: int,
    concurrent_max_tokens: int,
    prefix_cache_expectation: str,
    timeout: float,
) -> dict[str, Any]:
    start_offset = events_path.stat().st_size if events_path.exists() else 0
    prefix_prompt = (
        "AgentMon prefix-cache release probe. Keep this complete prompt stable across both requests. "
        "Return only the word OK. The repeated context intentionally contains enough deterministic text "
        "to exercise prefix-cache accounting without changing request semantics."
    )
    prefix_results = [
        send_chat_completion(
            base_url,
            model,
            prompt=prefix_prompt,
            max_tokens=4,
            stream=False,
            timeout=timeout,
        )
        for _ in range(2)
    ]
    stream_result = (
        send_chat_completion(
            base_url,
            model,
            prompt="Output the integers from 1 through 60 separated by single spaces. Output nothing else.",
            max_tokens=stream_max_tokens,
            stream=True,
            timeout=timeout,
        )
        if stream_probe
        else None
    )
    concurrent_results = (
        run_concurrent_api_requests(
            base_url,
            model,
            concurrency=concurrency,
            max_tokens=concurrent_max_tokens,
            timeout=timeout,
        )
        if concurrency > 0
        else []
    )
    all_api_results = [*prefix_results, *concurrent_results]
    if stream_result is not None:
        all_api_results.append(stream_result)
    api_request_ids = {
        value
        for result in all_api_results
        if (value := none_if_empty(result.get("api_request_id"))) is not None
    }
    active_snapshot = wait_for_probe_events(
        events_path,
        start_offset=start_offset,
        api_request_ids=api_request_ids,
        timeout=timeout,
    )
    full_snapshot = read_p2_event_stream(events_path)
    active_events = active_snapshot["events"]
    runtime_capability = latest_runtime_capability(full_snapshot["events"])
    prefix_audit = audit_prefix_cache_probe(
        prefix_results,
        active_events,
        expectation=prefix_cache_expectation,
        runtime_capability=runtime_capability,
    )
    stream_audit = (
        audit_stream_probe(stream_result, active_events)
        if stream_result is not None
        else {"status": "skipped", "failures": [], "reason": "--runtime-stream-probe not supplied"}
    )
    concurrency_audit = (
        audit_concurrency_probe(concurrent_results, active_events)
        if concurrent_results
        else {"status": "skipped", "failures": [], "reason": "runtime concurrency is zero"}
    )
    integrity = event_stream_integrity(full_snapshot)
    failures = []
    for name, result in (
        ("runtime_prefix_cache_probe", prefix_audit),
        ("runtime_stream_probe", stream_audit),
        ("runtime_concurrency_probe", concurrency_audit),
        ("event_stream_integrity", integrity),
    ):
        if result.get("status") == "fail":
            failures.append(name)
    return {
        "status": "pass" if not failures else "fail",
        "event_window_start_offset": start_offset,
        "event_window_event_count": len(active_events),
        "runtime_prefix_cache_probe": prefix_audit,
        "runtime_stream_probe": stream_audit,
        "runtime_concurrency_probe": concurrency_audit,
        "event_stream_integrity": integrity,
        "failures": failures,
    }


def integer_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def none_if_empty(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def check_runtime_p2_events(
    path: Path | None,
    *,
    required: bool,
    prefix_cache_expectation: str,
    minimum_prefix_probe_requests: int,
) -> dict[str, Any]:
    if not required:
        return make_check("p2_runtime_capability", "skipped", required=False)
    if path is None or not path.is_file():
        return make_check(
            "p2_runtime_capability",
            "failed",
            required=True,
            detail={"reason": "--events-path must be an existing P2 event stream"},
        )
    snapshot = read_p2_event_stream(path)
    events = snapshot["events"]
    invalid_lines = int(snapshot["invalid_json_lines"])
    duplicate_event_ids = int(snapshot["duplicate_event_ids"])
    sequence_duplicates = int(snapshot["sequence_duplicates"])
    sequence_regressions = int(snapshot["sequence_regressions"])
    runtime_capabilities = []
    cached_by_request: dict[str, float] = {}
    lifecycle_by_request: dict[str, set[str]] = {}
    lifecycle_counts: dict[tuple[str, str], int] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event.get("event_type") == "vllm.adapter.capability":
            details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
            if details.get("capability_phase") in {
                "scheduler_runtime_configuration",
                "runtime_prefix_cache_validation",
            }:
                runtime_capabilities.append({"status": payload.get("status"), **details})
        if str(event.get("event_type") or "").startswith("vllm.request."):
            cached = payload.get("cached_prompt_tokens")
            request_id = payload.get("vllm_request_id") or payload.get("request_id")
            if request_id is not None:
                normalized_request_id = str(request_id)
                normalized_event_type = str(event.get("event_type"))
                lifecycle_by_request.setdefault(normalized_request_id, set()).add(normalized_event_type)
                key = (normalized_request_id, normalized_event_type)
                lifecycle_counts[key] = lifecycle_counts.get(key, 0) + 1
            if (
                request_id is not None
                and isinstance(cached, (int, float))
                and not isinstance(cached, bool)
                and cached >= 0
            ):
                cached_by_request[str(request_id)] = max(float(cached), cached_by_request.get(str(request_id), 0.0))
    latest_runtime = next(
        (
            item
            for item in reversed(runtime_capabilities)
            if item.get("capability_phase") == "scheduler_runtime_configuration"
        ),
        {},
    )
    failures: list[str] = []
    if invalid_lines:
        failures.append("invalid_json_lines")
    if duplicate_event_ids:
        failures.append("duplicate_event_ids")
    if sequence_duplicates:
        failures.append("sequence_duplicates")
    if sequence_regressions:
        failures.append("sequence_regressions")
    if not latest_runtime:
        failures.append("runtime_capability_missing")
    if latest_runtime.get("async_scheduling_enabled") is True and latest_runtime.get(
        "engine_core_step_with_batch_queue_hooked"
    ) is not True:
        failures.append("async_engine_core_hook_missing")
    if latest_runtime.get("engine_token_boundary_collection") != "supported":
        failures.append("engine_token_boundary_collection")
    if prefix_cache_expectation != "any":
        expected_enabled = prefix_cache_expectation == "enabled"
        if latest_runtime.get("prefix_cache_enabled") is not expected_enabled:
            failures.append("prefix_cache_runtime_config")
    if prefix_cache_expectation == "disabled":
        if len(cached_by_request) < max(1, minimum_prefix_probe_requests):
            failures.append("prefix_cache_probe_request_count")
        if any(value > 0 for value in cached_by_request.values()):
            failures.append("prefix_cache_hit_while_disabled")
    if any(item.get("prefix_cache_expectation_status") == "failed" for item in runtime_capabilities):
        failures.append("prefix_cache_capability_validation")
    completed_request_ids = {
        request_id
        for request_id, event_types in lifecycle_by_request.items()
        if "vllm.request.finished" in event_types
    }
    lifecycle_missing = {
        request_id: sorted(RUNTIME_REQUIRED_LIFECYCLE - lifecycle_by_request[request_id])
        for request_id in sorted(completed_request_ids)
        if not RUNTIME_REQUIRED_LIFECYCLE.issubset(lifecycle_by_request[request_id])
    }
    if not completed_request_ids:
        failures.append("completed_request_lifecycle_missing")
    elif lifecycle_missing:
        failures.append("request_lifecycle_incomplete")
    duplicate_boundaries = {
        f"{request_id}:{event_type}": count
        for (request_id, event_type), count in lifecycle_counts.items()
        if event_type in {"vllm.request.first_token", "vllm.request.engine_finished"}
        and count > 1
    }
    if duplicate_boundaries:
        failures.append("duplicate_engine_lifecycle_boundary")
    return make_check(
        "p2_runtime_capability",
        "failed" if failures else "ok",
        required=True,
        detail={
            "events_path": str(path),
            "event_count": len(events),
            "invalid_json_lines": invalid_lines,
            "duplicate_event_ids": duplicate_event_ids,
            "sequence_duplicates": sequence_duplicates,
            "sequence_regressions": sequence_regressions,
            "runtime_capability": latest_runtime,
            "prefix_cache_expectation": prefix_cache_expectation,
            "prefix_cache_probe_request_count": len(cached_by_request),
            "prefix_cache_cached_tokens_max": max(cached_by_request.values(), default=None),
            "completed_request_count": len(completed_request_ids),
            "lifecycle_complete_count": len(completed_request_ids) - len(lifecycle_missing),
            "lifecycle_missing_by_request": lifecycle_missing,
            "duplicate_engine_lifecycle_boundaries": duplicate_boundaries,
            "failures": failures,
        },
    )


def check_runtime_probe_arguments(args: argparse.Namespace, events_path: Path | None) -> dict[str, Any]:
    requested = bool(args.runtime_base_url or args.runtime_model or args.runtime_stream_probe)
    if not requested:
        return make_check(
            "runtime_probe_arguments",
            "skipped",
            required=False,
            detail={"reason": "active runtime probe parameters not supplied"},
        )
    failures = []
    if not args.runtime_base_url:
        failures.append("runtime_base_url_missing")
    if not args.runtime_model:
        failures.append("runtime_model_missing")
    if events_path is None or not events_path.is_file():
        failures.append("runtime_events_path_missing")
    if args.runtime_stream_max_tokens <= 1:
        failures.append("runtime_stream_max_tokens_invalid")
    if args.runtime_concurrency < 0:
        failures.append("runtime_concurrency_invalid")
    if args.runtime_concurrency > 0 and args.runtime_concurrent_max_tokens < MINIMUM_CONCURRENT_GENERATION_TOKENS:
        failures.append("runtime_concurrent_max_tokens_below_96")
    return make_check(
        "runtime_probe_arguments",
        "failed" if failures else "ok",
        required=True,
        detail={
            "runtime_base_url": args.runtime_base_url,
            "runtime_model": args.runtime_model,
            "runtime_stream_probe": bool(args.runtime_stream_probe),
            "runtime_stream_max_tokens": args.runtime_stream_max_tokens,
            "runtime_concurrency": args.runtime_concurrency,
            "runtime_concurrent_max_tokens": args.runtime_concurrent_max_tokens,
            "events_path": str(events_path) if events_path else None,
            "failures": failures,
        },
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.agentmon_repo).expanduser().resolve()
    agentmon_python = cli_path(args.agentmon_python)
    mini_python = cli_path(args.mini_swe_python) if args.mini_swe_python else None
    vllm_python = cli_path(args.vllm_python) if args.vllm_python else None
    acl_python_paths = configured_acl_python_paths(args.acl_python_path)
    timeout = effective_timeout_seconds(args)
    config = Path(args.config).expanduser().resolve() if args.config else None
    events_path = Path(args.events_path).expanduser().resolve() if args.events_path else None
    launch_path = (
        Path(args.vllm_launch_command_file).expanduser().resolve()
        if args.vllm_launch_command_file
        else None
    )
    launch_command = (
        launch_path.read_text(encoding="utf-8", errors="replace").strip()
        if launch_path is not None and launch_path.is_file()
        else None
    )
    runtime_argument_check = check_runtime_probe_arguments(args, events_path)
    active_runtime = skipped_active_runtime_result()
    active_runtime_requested = runtime_argument_check["status"] != "skipped"
    if runtime_argument_check["status"] == "ok":
        try:
            active_runtime = run_active_runtime_probes(
                base_url=str(args.runtime_base_url),
                model=str(args.runtime_model),
                events_path=events_path,
                stream_probe=bool(args.runtime_stream_probe),
                stream_max_tokens=args.runtime_stream_max_tokens,
                concurrency=args.runtime_concurrency,
                concurrent_max_tokens=args.runtime_concurrent_max_tokens,
                prefix_cache_expectation=args.expect_prefix_cache,
                timeout=timeout,
            )
        except Exception as exc:
            active_runtime = {
                **skipped_active_runtime_result("active runtime probe raised an internal error"),
                "status": "fail",
                "failures": ["runtime_probe_internal_error"],
                "error": type(exc).__name__,
                "message": str(exc),
            }
    runtime_release_check = make_check(
        "runtime_release_gate",
        "ok" if active_runtime.get("status") == "pass" else "failed"
        if active_runtime.get("status") == "fail" else "skipped",
        required=active_runtime_requested,
        detail=active_runtime,
    )
    checks = [
        check_repo(repo),
        check_agentmon_python(repo, agentmon_python, timeout),
        check_agentmon_cli(repo, agentmon_python, timeout),
        check_config(repo, agentmon_python, config, timeout),
        check_sitecustomize(repo, agentmon_python, timeout),
        check_mini_swe(repo, mini_python, timeout),
        check_vllm(
            repo,
            vllm_python,
            timeout,
            args.deep,
            launch_command=launch_command,
            prefix_cache_expectation=args.expect_prefix_cache,
            acl_python_paths=acl_python_paths,
        ),
        check_events_path(events_path),
        runtime_argument_check,
        runtime_release_check,
        check_runtime_p2_events(
            events_path,
            required=args.require_runtime_p2 or active_runtime_requested,
            prefix_cache_expectation=args.expect_prefix_cache,
            minimum_prefix_probe_requests=args.prefix_cache_probe_min_requests,
        ),
    ]
    required_failures = [
        item["name"]
        for item in checks
        if item["required"] and item["status"] in {"failed", "timeout"}
    ]
    warnings = [item["name"] for item in checks if item["status"] == "warning"]
    status = "failed" if required_failures else "partial" if warnings else "ready"
    static_status = "PASS" if status == "ready" else "FAIL" if required_failures else "PARTIAL"
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "agentmon-server-preflight",
        "status": status,
        "static_server_preflight_status": static_status,
        "eligible_for_runtime_p2_gate": bool(status == "ready"),
        "eligible_for_next_stage": bool(status == "ready"),
        "next_stage": "runtime_p2_gate",
        "strict": bool(args.strict),
        "deep": bool(args.deep),
        "timeout_seconds": timeout,
        "timeout_source": (
            "explicit"
            if args.timeout is not None
            else "deep_default"
            if args.deep
            else "default"
        ),
        "created_wall_ns": time.time_ns(),
        "python_identities": {
            "agentmon": python_identity(agentmon_python),
            "mini_swe": python_identity(mini_python),
            "vllm": python_identity(vllm_python),
        },
        "acl_python_paths": [str(path) for path in acl_python_paths],
        "required_failures": required_failures,
        "warnings": warnings,
        "runtime_prefix_cache_probe": active_runtime["runtime_prefix_cache_probe"],
        "runtime_stream_probe": active_runtime["runtime_stream_probe"],
        "runtime_concurrency_probe": active_runtime["runtime_concurrency_probe"],
        "event_stream_integrity": active_runtime["event_stream_integrity"],
        "checks": checks,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout is not None and args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    report = build_report(args)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    if report["required_failures"]:
        return 1
    if args.strict and report["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
