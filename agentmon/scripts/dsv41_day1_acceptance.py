#!/usr/bin/env python3
"""Day 1: read-only checks by default; explicit --live reuses existing smoke.

Never starts/stops a container, installs a hook, or cleans NPU processes.
Service/library logs are filtered to content-free evidence before persistence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agentmon/src"))
from agentmon.paths import make_private_dir, write_private_text
from agentmon.server.preflight import disabled_p2_env, run_command

API = "http://127.0.0.1:8800/v1"
EXPECTED_CLIENT = {"mini-swe-agent": "2.4.3", "litellm": "1.90.1", "openai": "2.44.0"}
# Standard library only; no model or serving import. Never print env or full argv.
RUNTIME_PROGRAM = r'''
import importlib.metadata as md, json, pathlib, sys
flags = {"--max-model-len":"max_model_len", "--max-num-seqs":"max_num_seqs",
         "--max-num-batched-tokens":"max_num_batched_tokens", "--tensor-parallel-size":"tensor_parallel_size",
         "--block-size":"block_size", "--gpu-memory-utilization":"gpu_memory_utilization",
         "--served-model-name":"served_model_name"}
found=[]
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try: args=path.read_bytes().decode(errors='replace').split('\0')
    except OSError: continue
    if not any(a in {'vllm', 'vllm.entrypoints.openai.api_server'} or a.endswith('/vllm') for a in args): continue
    values={}
    for index, arg in enumerate(args):
        key, sep, value=arg.partition('=')
        if key in flags:
            raw=value if sep else (args[index+1] if index+1 < len(args) else '')
            if flags[key]=='served_model_name':
                # Record only the expected public served name; arbitrary values might be private.
                values[flags[key]]=raw if raw=='deepseek-v41' else 'UNKNOWN'
            else:
                try: values[flags[key]]=float(raw) if '.' in raw else int(raw)
                except ValueError: values[flags[key]]='UNKNOWN'
        if key=='--enable-expert-parallel': values['expert_parallel']=True
    if values: found.append({'pid':int(path.parent.name), 'settings':values, 'source':'observed_process_argv'})
packages={}
for name in ('vllm','vllm-ascend','torch','torch-npu'):
    try: packages[name]=md.version(name)
    except md.PackageNotFoundError: packages[name]=None
print(json.dumps({'python':sys.executable,'python_version':sys.version.split()[0], 'packages':packages,
                  'version_source':'distribution_metadata_not_loaded_module','launch_settings':found}))
'''
CONTAINER_API_PROGRAM = "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8800/v1/models',timeout=15); print(r.read(65536).decode())"


def json_stdout(result):
    if result.get("returncode") != 0:
        return None
    try:
        return json.loads(result.get("stdout", ""))
    except ValueError:
        return None


def model_summary(data):
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return {"reachable": False, "expected_model": False, "models": []}
    models = [{"id": row.get("id") if row.get("id") == "deepseek-v41" else "OTHER_MODEL",
               "max_model_len": row.get("max_model_len") if type(row.get("max_model_len")) is int else None}
              for row in data["data"] if isinstance(row, dict)]
    return {"reachable": True, "expected_model": any(row["id"] == "deepseek-v41" for row in models), "models": models}


def probe_host():
    try:
        # Explicit loopback direct path; enterprise proxy must not receive this probe.
        opener = urlrequest.build_opener(urlrequest.ProxyHandler({}))
        with opener.open(API + "/models", timeout=15) as response:
            return {"http_status": response.status, **model_summary(json.loads(response.read(65536)))}
    except Exception as exc:
        return {"reachable": False, "expected_model": False, "error_type": type(exc).__name__}


def filtered_log(text, *, smoke=False):
    # Unrestricted raw service/library logs can contain prompt/secret fields.
    allowed = r"^(?:[A-Z][A-Z0-9_]*=(?:PASS|FAIL)|CLIENT_SMOKE=(?:PASS|FAIL)|OUTPUT=)" if smoke else r"(?:Application startup complete\.|HTTP server started|Loading weights took [0-9.]+ seconds)"
    lines = []
    for line in text.splitlines():
        match = re.search(allowed, line)
        if match:
            lines.append(match.group(0) if not smoke else line[:400])
        elif re.search(r"\b(?:Traceback|OutOfMemoryError|RuntimeError|ImportError)\b", line):
            lines.append("ERROR_LINE_REDACTED")
    return "\n".join(lines) + "\n"


def audit_smoke(directory: Path, exit_code):
    try:
        report = json.loads((directory / "client_smoke.json").read_text(encoding="utf-8"))
        evidence = [json.loads(line) for line in (directory / "request_evidence.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return {"model_success": False, "smoke_pass": False, "evidence_pass": False, "reason": "missing_or_invalid_smoke_artifacts"}
    expected_checks = {"FIRST_ACTION_VALID", "TOOL_EXECUTION", "SECOND_ACTION_VALID", "WIRE_THINKING_AND_REQUEST_IDS"}
    checks = report.get("checks", {}) if isinstance(report.get("checks"), dict) else {}
    good = exit_code == 0 and report.get("status") == "PASS" and all(checks.get(k) is True for k in expected_checks)
    attempts, calls, runs, apis = [], [], [], []
    evidence_ok = len(evidence) == 2
    for record in evidence:
        a = record.get("agentmon", {})
        values = [a.get(k) for k in ("agent_run_id", "model_request_id", "request_attempt_id", "api_request_id")]
        runs.append(values[0]); calls.append(values[1]); attempts.append(values[2]); apis.append(values[3])
        evidence_ok = evidence_ok and all(isinstance(v, str) and v for v in values)
        evidence_ok = evidence_ok and record.get("source") == "http_interception" and record.get("http_status") == 200
        evidence_ok = evidence_ok and record.get("served_model") == "deepseek-v41"
        evidence_ok = evidence_ok and record.get("effective_controls", {}).get("chat_template_kwargs") == {"thinking": False}
    evidence_ok = bool(evidence_ok and len(set(attempts)) == 2 and len(set(calls)) == 2 and len(set(runs)) == 1 and len(set(apis)) == 2)
    model_success = checks.get("FIRST_ACTION_VALID") is True and any(
        r.get("source") == "http_interception" and r.get("http_status") == 200
        and isinstance(r.get("agentmon"), dict) and isinstance(r["agentmon"].get("api_request_id"), str) for r in evidence)
    return {"model_success": model_success, "smoke_pass": good and report.get("concurrency") == 1,
            "evidence_pass": evidence_ok, "request_count": len(evidence), "client_report": "smoke/client_smoke.json",
            "request_evidence": "smoke/request_evidence.jsonl", "full_agent_task": "NOT_TESTED", "p2": "NOT_ENABLED"}


def run(args, *, runner=run_command, host_probe=probe_host):
    output = args.run_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("run-dir must be new/empty")
    make_private_dir(output)
    for name in ("env", "logs", "api", "source", "binding"):
        make_private_dir(output / name)
    env = disabled_p2_env(ROOT / "agentmon")
    env.update(AGENTMON_VLLM_P2_ENABLE="0", PYTHONUTF8="1")
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    commands = []

    def execute(name, argv, timeout=None):
        result = runner(argv, env=env, timeout=timeout or args.timeout)
        commands.append({"name": name, "status": result.get("status"), "exit_code": result.get("returncode"),
                         "duration_seconds": result.get("duration_seconds"), "error_type": result.get("error")})
        return result

    def save(name, data):
        write_private_text(output / name, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    docker = ["docker", "-H", args.socket]
    listing = execute("docker_containers", docker + ["ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}"])
    write_private_text(output / "env/docker-containers.txt", listing.get("stdout", ""))
    npu = execute("npu_info", ["npu-smi", "info"])
    write_private_text(output / "env/npu-info.txt", npu.get("stdout", ""))
    template = '{"State":{{json .State}},"Image":{{json .Image}},"Config":{"Image":{{json .Config.Image}}},"HostConfig":{"NetworkMode":{{json .HostConfig.NetworkMode}}}}'
    inspected = json_stdout(execute("container_state", docker + ["inspect", args.container, "--format", template]))
    # Only selected inspect fields are requested; Config.Env is never read/exported.
    state, runtime, container_api = {}, {}, {"reachable": False}
    if isinstance(inspected, dict):
        raw = inspected.get("State", {})
        state = {k: raw.get(k) for k in ("Status", "ExitCode", "OOMKilled", "StartedAt", "FinishedAt")}
        state.update(container=args.container, image_id=inspected.get("Image"),
                     image=inspected.get("Config", {}).get("Image"), network_mode=inspected.get("HostConfig", {}).get("NetworkMode"))
    save("env/container-state.json", state or {"status": "MISSING_OR_UNREADABLE"})
    logs = execute("container_logs", docker + ["logs", "--tail", "300", args.container])
    write_private_text(output / "logs/container-before-test.log", filtered_log(logs.get("stdout", "") + logs.get("stderr", "")))
    ports = execute("container_ports", docker + ["port", args.container])
    write_private_text(output / "env/port-publishing.txt", ports.get("stdout", ""))
    if state.get("Status") == "running":
        base = docker + ["exec", "-e", "AGENTMON_VLLM_P2_ENABLE=0", args.container, args.container_python, "-I", "-c"]
        runtime = json_stdout(execute("container_packages_and_config", base + [RUNTIME_PROGRAM])) or {}
        container_api = model_summary(json_stdout(execute("container_models", base + [CONTAINER_API_PROGRAM])))
        git = execute("vllm_git", docker + ["exec", args.container, "git", "-C", "/vllm-workspace/vllm", "rev-parse", "HEAD"])
        write_private_text(output / "source/vllm-git-state.txt", git.get("stdout", "") or "UNKNOWN\n")
    save("env/runtime-python-packages.json", runtime or {"status": "NOT_RUN_OR_UNAVAILABLE"})
    save("api/models-from-container.json", container_api)
    host = host_probe() if state.get("Status") == "running" else {"reachable": False, "reason": "container_not_running"}
    save("api/models.json", host)
    write_private_text(output / "api/models-http-code.txt", str(host.get("http_status", "UNKNOWN")) + "\n")
    config = {"launch_settings": runtime.get("launch_settings", []), "api_models": host.get("models", []),
              "source": "observed_process_argv_and_api", "unknown_policy": "NO_HISTORICAL_DEFAULTS_FILLED",
              "module_versions": "UNKNOWN_METADATA_ONLY", "exclusive_window_confirmed": args.confirm_exclusive_window}
    save("env/run-config-summary.json", config)
    required_settings = ("max_model_len", "max_num_seqs", "max_num_batched_tokens", "tensor_parallel_size",
                         "expert_parallel", "block_size", "gpu_memory_utilization", "served_model_name")
    fields = {}
    for key in required_settings:
        values = [r.get("settings", {}).get(key) for r in runtime.get("launch_settings", []) if key in r.get("settings", {})]
        known = bool(values) and all(v == values[0] for v in values) and values[0] != "UNKNOWN"
        fields[key] = {"value": values[0] if known else "UNKNOWN", "source": "observed_process_argv" if known else "unknown",
                       "evidence_file": "runtime-python-packages.json" if known else None}
    save("env/config-review.example.json", {"container": args.container, "image_id": state.get("image_id"), "fields": fields})
    for filename, arguments in (("agentmon-commit", ["rev-parse", "HEAD"]), ("agentmon-branch", ["branch", "--show-current"]),
                                ("agentmon-status", ["status", "--short"]), ("agentmon-diff-stat", ["diff", "--stat"])):
        git = execute(filename, ["git", "-C", str(ROOT), *arguments])
        write_private_text(output / f"env/{filename}.txt", git.get("stdout", "") or "UNKNOWN_NOT_A_GIT_CHECKOUT\n")
    client_program = "import importlib.metadata as m,json,sys; print(json.dumps({'python':sys.executable,'packages':{n:m.version(n) for n in ['mini-swe-agent','litellm','openai']}}))"
    client = json_stdout(execute("client_packages", [args.client_python, "-c", client_program])) or {}
    save("env/client-python-packages.json", client)
    smoke = {"model_success": False, "smoke_pass": False, "evidence_pass": False, "status": "NOT_RUN"}
    if args.live and host.get("expected_model") and npu.get("returncode") == 0 and client.get("packages") == EXPECTED_CLIENT:
        result = execute("live_smoke", [args.client_python, str(ROOT / "agentmon/scripts/deepseek_v41_client_smoke.py"),
                         "--live", "--kvreuse-root", str(ROOT / "kv_ana"), "--output", str(output / "smoke")], args.smoke_timeout)
        rc = result.get("returncode", 124 if result.get("status") == "timeout" else 125)
        write_private_text(output / "logs/live-smoke.log", filtered_log(result.get("stdout", "") + result.get("stderr", ""), smoke=True))
        write_private_text(output / "logs/live-smoke-exit-code.txt", str(rc) + "\n")
        smoke = audit_smoke(output / "smoke", rc)
        smoke["executed"] = True
    elif args.live:
        smoke["status"] = "BLOCKED_PRECHECK"
    unsuccessful = "NOT_RUN" if not args.live else "FAIL" if smoke.get("executed") else "BLOCKED"
    gates = {
        "D1-G1": {"status": "PASS" if state.get("Status") == "running" else "BLOCKED", "evidence": "env/container-state.json"},
        "D1-G2": {"status": "PASS" if host.get("expected_model") else "BLOCKED", "evidence": "api/models.json",
                  "container_route_only": bool(container_api.get("expected_model") and not host.get("expected_model"))},
        "D1-G3": {"status": "PASS" if smoke["model_success"] else unsuccessful, "evidence": "smoke/client_smoke.json"},
        "D1-G4": {"status": "PASS" if smoke["smoke_pass"] else unsuccessful, "evidence": "logs/live-smoke-exit-code.txt"},
        "D1-G5": {"status": "PASS" if smoke["evidence_pass"] else unsuccessful, "evidence": "smoke/request_evidence.jsonl"},
        "D1-G6": {"status": "BLOCKED", "evidence": "env/runtime-python-packages.json",
                  "reason": "metadata_and_selected_launch_flags_collected; loaded_module_and_complete_configuration_review_required"},
    }
    report = {"status": "DAY1_REAL_PASS" if all(g["status"] == "PASS" for g in gates.values()) else "REAL_ACCEPTANCE_PENDING",
              "gates": gates, "smoke": smoke, "commands": commands, "model_requests_authorized": args.live,
              "container_started": False, "hook_installed": False, "log_policy": "CONTENT_FREE_FILTERED_NOT_UNREDACTED_SERVICE_LOGS"}
    save("day1_acceptance.json", report)
    lines = ["# DAY1_ACCEPTANCE", "", report["status"], "", "| Gate | Status | Evidence |", "|---|---|---|"]
    lines += [f"| {name} | {gate['status']} | {gate['evidence']} |" for name, gate in gates.items()]
    lines += ["", "D1-G6 requires review of loaded module versions via Day 2 runtime Discovery and complete serving configuration.",
              "Host route and container route are recorded separately. Port publication alone does not decide host-network reachability.",
              "The reused smoke makes two sequential model calls and executes one fixed tool command; full Agent task completion is NOT_TESTED.",
              "Logs are content-free filtered; unrestricted raw Docker/library logs may contain private request data and are not saved.",
              "No container start/stop, hook injection, model launch or process cleanup was performed."]
    write_private_text(output / "DAY1_ACCEPTANCE.md", "\n".join(lines) + "\n")
    return report


def finalize(output: Path, discovery_path: Path, config_path: Path):
    """Review recorded evidence only; no Docker, HTTP or model calls."""
    report = json.loads((output / "day1_acceptance.json").read_text(encoding="utf-8"))
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    runtime = json.loads((output / "env/runtime-python-packages.json").read_text(encoding="utf-8"))
    container = json.loads((output / "env/container-state.json").read_text(encoding="utf-8"))
    expected = {"max_model_len", "max_num_seqs", "max_num_batched_tokens", "tensor_parallel_size",
                "expert_parallel", "block_size", "gpu_memory_utilization", "served_model_name"}
    fields = config.get("fields", {})
    observed = (config.get("image_id") == container.get("image_id") and config.get("container") == container.get("container")
                and expected <= fields.keys() and all(isinstance(fields[k], dict) and fields[k].get("value") is not None
                and fields[k].get("value") != "UNKNOWN" and fields[k].get("source") in {"observed_runtime", "observed_process_argv"}
                and isinstance(fields[k].get("evidence_file"), str)
                and (config_path.parent / fields[k]["evidence_file"]).is_file() for k in expected))
    loaded = discovery.get("loaded_runtime", {})
    signatures = (discovery.get("runtime_introspection") == "SIGNATURES_OBSERVED_REVIEW_REQUIRED"
                  and discovery.get("compatibility") == "VERSION_PATH_MATCH_NO_ADAPTER_INSTALLED"
                  and loaded.get("vllm_version") == runtime.get("packages", {}).get("vllm")
                  and discovery.get("distributions", {}).get("vllm-ascend") == runtime.get("packages", {}).get("vllm-ascend")
                  and discovery.get("python") == runtime.get("python"))
    okay = bool(observed and signatures)
    report["gates"]["D1-G6"] = {"status": "PASS" if okay else "BLOCKED",
                                  "evidence": [str(discovery_path), str(config_path)],
                                  "reason": "recorded_module_signature_and_explicit_configuration_review" if okay else "missing_or_mismatched_recorded_evidence"}
    report["status"] = "DAY1_REAL_PASS" if all(g["status"] == "PASS" for g in report["gates"].values()) else "REAL_ACCEPTANCE_PENDING"
    write_private_text(output / "day1_acceptance.json", json.dumps(report, indent=2) + "\n")
    text = ["# DAY1_ACCEPTANCE", "", report["status"], "", "| Gate | Status | Evidence |", "|---|---|---|"]
    text += [f"| {k} | {v['status']} | {v['evidence']} |" for k, v in report["gates"].items()]
    text += ["", "Finalization reviewed existing evidence only. Configuration sources are operator-supplied; inspect their cited evidence.",
             "No model request, Docker operation or hook injection was performed during finalization."]
    write_private_text(output / "DAY1_ACCEPTANCE.md", "\n".join(text) + "\n")
    return report


def main(argv=None):
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--socket", default=os.environ.get("V41_SOCKET", "unix:///run/docker-v41.sock"))
    p.add_argument("--container", default=os.environ.get("V41_CONTAINER", "dsv41-smoke-256k"))
    p.add_argument("--container-python", default="/usr/local/python3.12.13/bin/python3")
    p.add_argument("--client-python", default=os.environ.get("PY", sys.executable))
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--timeout", type=float, default=20)
    p.add_argument("--smoke-timeout", type=float, default=300)
    p.add_argument("--live", action="store_true")
    p.add_argument("--finalize", action="store_true", help="review an existing run only; requires two evidence files")
    p.add_argument("--runtime-discovery", type=Path)
    p.add_argument("--config-evidence", type=Path)
    p.add_argument("--confirm-exclusive-window", action="store_true", help="operator confirms no other active model requests/NPU tasks; not proof of scheduler isolation")
    args = p.parse_args(argv)
    if args.finalize:
        if args.live or not args.runtime_discovery or not args.config_evidence:
            p.error("--finalize requires --runtime-discovery and --config-evidence; cannot combine --live")
        report = finalize(args.run_dir.resolve(), args.runtime_discovery, args.config_evidence)
        print(f"DAY1_STATUS={report['status']}; NO_RUNTIME_ACTION=True")
        return 0 if report["status"] == "DAY1_REAL_PASS" else 2
    if args.live and not args.confirm_exclusive_window:
        p.error("--live requires operator --confirm-exclusive-window")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.container) or not args.socket.startswith("unix:///"):
        p.error("use an explicit container name and local unix Docker socket")
    if args.timeout <= 0 or args.smoke_timeout <= 0:
        p.error("timeouts must be positive")
    report = run(args)
    print(f"DAY1_STATUS={report['status']}; OUTPUT={args.run_dir.resolve()}")
    return 0 if report["gates"]["D1-G1"]["status"] == "PASS" and report["gates"]["D1-G2"]["status"] == "PASS" and (not args.live or report["smoke"]["smoke_pass"] and report["smoke"]["evidence_pass"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
