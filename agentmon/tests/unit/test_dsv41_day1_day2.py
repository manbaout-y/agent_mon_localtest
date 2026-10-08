from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from agentmon.analysis.request_binding import analyze_binding, read_records, write_analysis
from agentmon.adapters.inference.vllm_018.version import is_supported_vllm_version
from agentmon.events import Event

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def client(n=1, *, attempt=None, api=None, source="synthetic"):
    return {"source": source, "http_status": 200, "served_model": "deepseek-v41",
            "effective_controls": {"chat_template_kwargs": {"thinking": False}},
            "agentmon": {"agent_run_id": "agent-run", "model_request_id": f"model-{n}",
                         "request_attempt_id": attempt or f"attempt-{n}", "api_request_id": api or f"chatcmpl-{n}"}}


def event(stage, n=1, *, engine=None, scheduler=None, source="synthetic", etype=None):
    payload = {"stage": stage, "process_role": "api" if stage in {"api", "async_llm"} else "engine_core",
               "source_pid": 100 if stage in {"api", "async_llm"} else 200,
               "source_host": "test-host", "clock_domain": "test-host:boot-A:monotonic",
               "evidence_source": source,
               "mapping_basis": "observed_header" if stage == "api" else "observed_request_object"}
    if stage == "api":
        payload.update(agentmon_run_id="agent-run", agentmon_model_request_id=f"model-{n}", api_request_id=f"chatcmpl-{n}",
                       observed_header_fields=["agentmon_run_id", "agentmon_model_request_id"])
    else:
        payload["engine_request_id"] = engine or f"engine-{n}"
        if stage == "async_llm":
            payload["api_request_id"] = f"chatcmpl-{n}"
        if stage == "scheduler":
            payload["scheduler_request_id"] = scheduler or (f"{engine}-scheduler" if engine else f"scheduler-{n}")
    return Event(run_id="service-run", source="vllm_p2", event_type=etype or "vllm.request.received",
                 quality="exact", payload=payload).to_dict()


def chain(n=1, *, engine=None, source="synthetic"):
    return [event(stage, n, engine=engine, source=source) for stage in ("api", "async_llm", "engine_core", "scheduler")]


def capability(role, pid, source="runtime"):
    e = event("api" if role == "api" else "engine_core", source=source, etype="vllm.adapter.capability")
    e["payload"].update(process_role=role, source_pid=pid, status="installed", adapter_namespace="vllm_0271",
                        loaded_vllm_version="0.27.1+empty", signature_validation="PASS", dropped_events=0)
    return e


class BindingTests(unittest.TestCase):
    def test_importing_offline_reader_does_not_load_old_hook_or_procfs(self):
        result = subprocess.run([sys.executable, "-c", "import sys; import agentmon.analysis.request_binding; assert 'agentmon.adapters.inference.vllm_018.hook' not in sys.modules; assert 'agentmon.sampling.procfs' not in sys.modules"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lazy_p2_public_exports_preserve_pure_helpers(self):
        from agentmon.adapters.inference import vllm_018
        from agentmon.adapters.inference.vllm_018.kv import calculate_kv_pool
        self.assertIs(vllm_018.calculate_kv_pool, calculate_kv_pool)
        self.assertIs(vllm_018.is_supported_vllm_version, is_supported_vllm_version)
        self.assertIn("install_vllm_p2_hooks_from_env", vllm_018.__all__)

    def test_analyzer_cli_client_only_is_blocked_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "clients.jsonl"
            source.write_text(json.dumps(client()) + "\n")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/analyze_request_binding.py"),
                                     "--client-evidence", str(source), "--output", str(root / "out")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            audit = json.loads((root / "out/request_identity_audit.json").read_text())
            self.assertEqual(audit["status"], "BLOCKED")
            self.assertEqual(audit["unmatched_p1_attempts"], ["attempt-1"])

    def test_direct_keeps_three_distinct_native_ids_and_original_inputs(self):
        cs, es = [client()], chain()
        originals = deepcopy((cs, es))
        rows, audit = analyze_binding(cs, es)
        self.assertEqual(rows[0]["correlation_quality"], "direct")
        self.assertEqual((rows[0]["api_request_id"], rows[0]["engine_request_id"], rows[0]["scheduler_request_id"]),
                         ("chatcmpl-1", "engine-1", "scheduler-1"))
        self.assertEqual(audit["status"], "OFFLINE_BINDING_PASS")
        self.assertEqual(audit["real_acceptance"], "PENDING")
        self.assertEqual((cs, es), originals)

    def test_timestamps_and_direct_label_do_not_prove_binding(self):
        es = chain()
        es[0]["payload"].update(mapping_basis="time_nearest", correlation_quality="direct")
        rows, _ = analyze_binding([client()], es)
        self.assertEqual(rows[0]["correlation_quality"], "heuristic")

    def test_server_generated_without_observed_client_headers(self):
        es = chain()
        es[0]["payload"].pop("observed_header_fields")
        rows, _ = analyze_binding([client()], es)
        self.assertEqual(rows[0]["correlation_quality"], "server_generated")

    def test_same_string_without_explicit_alias_is_unavailable(self):
        es = chain(engine="chatcmpl-1")
        es[1]["payload"].pop("mapping_basis")
        rows, _ = analyze_binding([client()], es)
        self.assertEqual(rows[0]["correlation_quality"], "unavailable")

    def test_concurrent_retry_fanout_and_orphan(self):
        c1, c2 = client(1), client(2)
        retry = client(1, attempt="retry-1", api="chatcmpl-retry")
        retry_es = chain(3)
        for e in retry_es:
            if "api_request_id" in e["payload"]:
                e["payload"]["api_request_id"] = "chatcmpl-retry"
        retry_es[0]["payload"]["agentmon_model_request_id"] = "model-1"
        es = chain(1) + chain(2) + retry_es + chain(1, engine="engine-1-child") + [event("engine_core", 9)]
        rows, audit = analyze_binding([c1, c2, retry], es)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["correlation_quality"] == "direct" for r in rows))
        self.assertEqual({r["engine_request_id"] for r in rows if r["request_attempt_id"] == "retry-1"}, {"engine-3"})
        self.assertEqual(len(audit["orphan_p2_event_ids"]), 1)

    def test_cross_service_scope_is_ambiguous(self):
        es = chain() + chain()
        for e in es[4:]:
            e["run_id"] = "other-service"
        rows, _ = analyze_binding([client()], es)
        self.assertEqual(rows[0]["quality_reason"], "ambiguous_service_scope")

    def test_duplicate_attempt_and_conflicting_engine_are_blocked(self):
        rows, audit = analyze_binding([client(), client()], chain())
        self.assertTrue(audit["issues"])
        self.assertTrue(all(r["correlation_quality"] == "unavailable" for r in rows))

    def test_scheduler_and_api_reverse_ownership_conflicts_are_blocked(self):
        es = chain(1) + chain(2)
        es[-1]["payload"]["scheduler_request_id"] = "scheduler-1"
        rows, _ = analyze_binding([client(1), client(2)], es)
        self.assertTrue(all(r["correlation_quality"] != "direct" for r in rows))
        es = chain(1) + [event("api", 2)]
        es[-1]["payload"]["api_request_id"] = "chatcmpl-1"
        rows, _ = analyze_binding([client(1)], es)
        self.assertEqual(rows[0]["quality_reason"], "conflicting_server_api_ownership")
        es = chain(1) + chain(2, engine="engine-1")
        rows, _ = analyze_binding([client(1), client(2)], es)
        self.assertTrue(all(r["correlation_quality"] == "unavailable" for r in rows))

    def test_missing_core_and_missing_scheduler_do_not_pass(self):
        for missing in ("engine_core", "scheduler"):
            rows, audit = analyze_binding([client()], [e for e in chain() if e["payload"]["stage"] != missing])
            self.assertEqual(audit["status"], "BLOCKED")
            self.assertNotEqual(rows[0]["correlation_quality"], "direct")

    def test_capability_and_terminal_do_not_replace_add_request_boundary(self):
        es = [e for e in chain() if e["payload"]["stage"] != "engine_core"]
        es += [capability("engine_core", 200), event("engine_core", etype="vllm.request.finished")]
        rows, audit = analyze_binding([client()], es)
        self.assertNotEqual(rows[0]["correlation_quality"], "direct")
        self.assertEqual(audit["status"], "BLOCKED")

    def test_wrong_metadata_types_degrade_instead_of_raising(self):
        es = chain()
        es[0]["payload"].update(evidence_source=[], observed_header_fields=None)
        rows, audit = analyze_binding([client()], es)
        self.assertNotEqual(rows[0]["correlation_quality"], "direct")
        self.assertEqual(audit["status"], "BLOCKED")

    def test_synthetic_cannot_be_live_promoted(self):
        rows, audit = analyze_binding([client()], chain() + [capability("api", 100), capability("engine_core", 200)], live_evidence=True)
        self.assertEqual(rows[0]["correlation_quality"], "direct")
        self.assertEqual(audit["status"], "REAL_ACCEPTANCE_PENDING")

    def test_runtime_contract_requires_both_processes_and_zero_drops(self):
        cs = [client(source="http_interception")]
        es = chain(source="runtime") + [event("engine_core", source="runtime", etype="vllm.request.finished")]
        es[3]["payload"]["active_request_count"] = 1
        _, audit = analyze_binding(cs, es + [capability("api", 100)], live_evidence=True, client_smoke_pass=True)
        self.assertEqual(audit["status"], "REAL_ACCEPTANCE_PENDING")
        caps = [capability("api", 100), capability("engine_core", 200)]
        _, audit = analyze_binding(cs, es + caps, live_evidence=True, client_smoke_pass=True)
        self.assertEqual(audit["status"], "LIVE_BINDING_PASS")  # simulated acceptance logic, not a live experiment
        caps[1]["payload"]["dropped_events"] = 1
        _, audit = analyze_binding(cs, es + caps, live_evidence=True, client_smoke_pass=True)
        self.assertNotEqual(audit["status"], "LIVE_BINDING_PASS")

    def test_live_requires_successful_client_scheduler_isolation_and_terminal(self):
        cs = [client(source="http_interception")]
        es = chain(source="runtime") + [capability("api", 100), capability("engine_core", 200)]
        _, audit = analyze_binding(cs, es, live_evidence=True)
        self.assertNotEqual(audit["status"], "LIVE_BINDING_PASS")
        self.assertFalse(audit["scheduler_c1_observed"])
        self.assertFalse(audit["terminal_success_observed"])

    def test_cancel_timeout_and_terminal_conflict(self):
        for etype, expected in (("vllm.request.cancelled", "cancelled"), ("vllm.request.timeout", "timeout")):
            rows, _ = analyze_binding([client()], chain() + [event("engine_core", etype=etype)])
            self.assertEqual(rows[0]["terminal_status"], expected)
        rows, audit = analyze_binding([client()], chain() + [event("engine_core", etype="vllm.request.finished"), event("engine_core", etype="vllm.request.cancelled")])
        self.assertEqual(rows[0]["terminal_status"], "conflict")
        self.assertEqual(audit["status"], "BLOCKED")

    def test_timeout_without_api_response_needs_observed_attempt(self):
        c = client()
        c["agentmon"]["api_request_id"] = None
        rows, _ = analyze_binding([c], chain())
        self.assertEqual(rows[0]["correlation_quality"], "unavailable")
        es = chain()
        es[0]["payload"]["request_attempt_id"] = "attempt-1"
        rows, _ = analyze_binding([c], es)
        self.assertEqual(rows[0]["correlation_quality"], "direct")

    def test_conflicting_event_id_and_invalid_envelope_block_acceptance(self):
        es = chain()
        conflict = deepcopy(es[0]); conflict["payload"]["api_request_id"] = "different"
        _, audit = analyze_binding([client()], es + [conflict, {"source": "vllm_p2", "payload": {}}])
        self.assertEqual(audit["status"], "BLOCKED")
        self.assertEqual(len(audit["issues"]), 2)

    def test_private_output_whitelist_size_and_writer_failure(self):
        c, es = client(), chain()
        c["prompt"] = "PRIVATE_PROMPT"
        es[0]["payload"].update(prompt="PRIVATE_PROMPT", authorization="PRIVATE_TOKEN", completion="PRIVATE_COMPLETION")
        rows, audit = analyze_binding([c], es)
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "binding"
            write_analysis(output, rows, audit)
            saved = "".join(p.read_text() for p in output.iterdir())
            self.assertNotIn("PRIVATE_", saved)
            self.assertLess(len(json.dumps(rows[0])), 5000)
            with self.assertRaises(ValueError):
                write_analysis(output, rows, audit)
            with patch("agentmon.analysis.request_binding.write_private_text", side_effect=OSError("disk failed")):
                with self.assertRaises(OSError):
                    write_analysis(Path(td) / "failed", rows, audit)
        self.assertEqual(c["prompt"], "PRIVATE_PROMPT")

    def test_malformed_oversized_records_not_exported(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "input.jsonl"
            p.write_text('{bad json}\n' + json.dumps({"prompt": "p" * 20000}) + '\n' + json.dumps(client()) + '\n')
            rows, errors = read_records(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(errors), 2)
            self.assertNotIn("prompt", json.dumps(errors))


class DiscoveryTests(unittest.TestCase):
    def test_old_gate_unchanged_and_new_eligibility_uses_loaded_path(self):
        m = script("dsv41_request_discovery")
        self.assertFalse(is_supported_vllm_version("0.27.1+empty"))
        self.assertTrue(is_supported_vllm_version("0.18.0+empty"))
        self.assertTrue(m.compatible_runtime("0.27.1+empty", "0.27.1+empty", "/real/vllm/__init__.py", "/real/vllm"))
        self.assertFalse(m.compatible_runtime("0.18.0", "0.27.1+empty", "/real/vllm/__init__.py", "/real/vllm"))
        self.assertFalse(m.compatible_runtime("0.27.1+empty", "0.27.1+empty", "/other/vllm/__init__.py", "/real/vllm"))

    def test_ast_discovery_never_imports_runtime_and_reports_actual_lines(self):
        m = script("dsv41_request_discovery")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "v1/engine/async_llm.py"
            p.parent.mkdir(parents=True)
            p.write_text('raise RuntimeError("MUST_NOT_IMPORT")\nclass AsyncLLM:\n    async def add_request(self, request_id, trace_headers=None):\n        return request_id\n')
            with patch.object(m.importlib, "import_module", side_effect=AssertionError("unexpected import")):
                snapshot = m.collect(root)
            o = snapshot["objects"][0]
            self.assertEqual(o["line"], 3)
            self.assertIn("trace_headers", o["static_signature"])
            self.assertEqual(snapshot["runtime_introspection"], "NOT_RUN")
            m.render(snapshot, root / "report")
            self.assertIn("NOT_RUN", (root / "report/REQUEST_CALL_CHAIN.md").read_text())

    def test_runtime_import_failure_is_data_and_never_installs_hook(self):
        m = script("dsv41_request_discovery")
        with tempfile.TemporaryDirectory() as td, patch.object(m.importlib, "import_module", side_effect=ImportError("PRIVATE_ERROR")):
            snapshot = m.collect(Path(td), runtime_introspection=True)
        self.assertEqual(snapshot["compatibility"], "BLOCKED_IMPORT")
        self.assertFalse(snapshot["hook_installed"])
        self.assertNotIn("PRIVATE_ERROR", json.dumps(snapshot))


class Day1Tests(unittest.TestCase):
    def run_fake(self, *, running=True, host=True, live=False):
        m = script("dsv41_day1_acceptance")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        args = type("Args", (), {"run_dir": Path(tmp.name) / "run", "socket": "unix:///run/docker-v41.sock",
                "container": "dsv41-smoke-256k", "container_python": "/usr/local/python3.12.13/bin/python3",
                "client_python": "client-python", "timeout": 1, "smoke_timeout": 2,
                "live": live, "confirm_exclusive_window": live})()
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            text = ""
            if "inspect" in argv:
                text = json.dumps({"State": {"Status": "running" if running else "exited", "ExitCode": 0},
                                   "Image": "sha256:test", "Config": {"Image": "test-image", "Env": ["PRIVATE_TOKEN"]}})
            elif m.CONTAINER_API_PROGRAM in argv:
                text = json.dumps({"data": [{"id": "deepseek-v41", "max_model_len": 262144, "private": "PRIVATE_TOKEN"}]})
            elif m.RUNTIME_PROGRAM in argv:
                text = json.dumps({"python": args.container_python, "packages": {"vllm": "0.27.1+empty", "vllm-ascend": "target"}, "launch_settings": []})
            elif "client-python" == argv[0]:
                text = json.dumps({"packages": m.EXPECTED_CLIENT})
            return {"status": "ok", "returncode": 0, "stdout": text, "stderr": "", "duration_seconds": 0}
        probe = lambda: {"reachable": host, "expected_model": host, "http_status": 200 if host else None}
        report = m.run(args, runner=runner, host_probe=probe)
        return m, args, report, calls

    def test_default_read_only_no_model_or_mutating_commands(self):
        m, args, report, calls = self.run_fake()
        self.assertEqual(report["gates"]["D1-G3"]["status"], "NOT_RUN")
        self.assertFalse(any(any(v in a for v in ("start", "restart", "stop", "kill", "rm")) for a in calls))
        self.assertFalse(any("deepseek_v41_client_smoke.py" in " ".join(a) for a in calls))
        saved = "".join(p.read_text() for p in args.run_dir.rglob("*") if p.is_file())
        self.assertNotIn("PRIVATE_TOKEN", saved)

    def test_exited_container_never_executes_or_starts(self):
        _, _, report, calls = self.run_fake(running=False, live=True)
        self.assertEqual(report["gates"]["D1-G1"]["status"], "BLOCKED")
        self.assertFalse(any("exec" in a or "start" in a for a in calls))

    def test_container_only_api_is_not_host_client_pass(self):
        _, _, report, calls = self.run_fake(host=False, live=True)
        self.assertTrue(report["gates"]["D1-G2"]["container_route_only"])
        self.assertEqual(report["gates"]["D1-G2"]["status"], "BLOCKED")
        self.assertFalse(any("deepseek_v41_client_smoke.py" in " ".join(a) for a in calls))

    def test_live_requires_explicit_window_before_any_operations(self):
        m = script("dsv41_day1_acceptance")
        with patch.object(m, "run", side_effect=AssertionError("must not run")), self.assertRaises(SystemExit):
            m.main(["--run-dir", "not-used", "--live"])

    def test_smoke_zero_exit_alone_is_not_pass(self):
        m = script("dsv41_day1_acceptance")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "client_smoke.json").write_text(json.dumps({"status": "PASS", "checks": {}}))
            (root / "request_evidence.jsonl").write_text(json.dumps(client(source="http_interception")) + "\n")
            self.assertFalse(m.audit_smoke(root, 0)["smoke_pass"])

    def test_live_reuses_original_smoke_and_saves_exit_code(self):
        _, args, report, calls = self.run_fake(live=True)
        smoke_calls = [a for a in calls if any(v.endswith("deepseek_v41_client_smoke.py") for v in a)]
        self.assertEqual(len(smoke_calls), 1)
        self.assertIn("--kvreuse-root", smoke_calls[0])
        self.assertEqual((args.run_dir / "logs/live-smoke-exit-code.txt").read_text().strip(), "0")
        self.assertEqual(report["gates"]["D1-G4"]["status"], "FAIL")  # no actual smoke artifacts in this simulation

    def test_filtered_logs_do_not_preserve_payload_or_secret(self):
        m = script("dsv41_day1_acceptance")
        text = 'prompt=PRIVATE_PROMPT\nAuthorization: PRIVATE_TOKEN\nINFO Application startup complete.\nRuntimeError PRIVATE_COMPLETION\n'
        filtered = m.filtered_log(text)
        self.assertNotIn("PRIVATE_", filtered)
        self.assertIn("Application startup complete", filtered)

    def test_finalize_unknown_config_never_passes_and_runs_no_command(self):
        m, args, _, _ = self.run_fake()
        discovery = args.run_dir / "source/discovery.json"
        discovery.write_text(json.dumps({"runtime_introspection": "NOT_RUN"}))
        with patch.object(m, "run_command", side_effect=AssertionError("no command during finalize")):
            report = m.finalize(args.run_dir, discovery, args.run_dir / "env/config-review.example.json")
        self.assertEqual(report["gates"]["D1-G6"]["status"], "BLOCKED")
        self.assertEqual(report["status"], "REAL_ACCEPTANCE_PENDING")


if __name__ == "__main__":
    unittest.main()
