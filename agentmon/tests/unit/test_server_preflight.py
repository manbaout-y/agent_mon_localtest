from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agentmon_server_preflight.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agentmon_server_preflight", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ServerPreflightTests(unittest.TestCase):
    def test_stream_parser_keeps_usage_from_final_empty_choices_chunk(self) -> None:
        module = load_module()
        parsed = module.parse_streaming_response_lines(
            [
                'data: {"id":"chatcmpl-stream","choices":[{"delta":{"content":"1"}}]}\n',
                'data: {"id":"chatcmpl-stream","choices":[{"delta":{"content":" 2"}}]}\n',
                'data: {"id":"chatcmpl-stream","choices":[],"usage":{"completion_tokens":81}}\n',
                "data: [DONE]\n",
            ]
        )

        self.assertEqual(parsed["api_request_id"], "chatcmpl-stream")
        self.assertEqual(parsed["api_generation_tokens"], 81)
        self.assertEqual(parsed["stream_chunk_count"], 3)

    def test_stream_probe_requires_api_engine_and_finished_token_agreement(self) -> None:
        module = load_module()
        events = release_lifecycle_events("chatcmpl-stream", "engine-stream", generation_tokens=81)

        result = module.audit_stream_probe(
            {
                "status": "pass",
                "api_request_id": "chatcmpl-stream",
                "api_generation_tokens": 81,
                "stream_chunk_count": 82,
                "failures": [],
            },
            events,
        )

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["vllm_request_id"], "engine-stream")
        self.assertEqual(result["p2_generation_tokens"], 81)
        self.assertEqual(result["first_token_count"], 1)
        self.assertEqual(result["engine_finished_count"], 1)
        self.assertTrue(result["last_token_ns_present"])

    def test_concurrency_probe_accepts_four_exact_request_mappings(self) -> None:
        module = load_module()
        api_results = concurrent_api_results(4)
        events = []
        for index in range(4):
            events.extend(
                release_lifecycle_events(
                    f"chatcmpl-{index}",
                    f"engine-{index}",
                    generation_tokens=100,
                    base_ns=1_000 + index * 100,
                )
            )
            events.append(scheduler_event(f"engine-{index}", batch_request_count=4))

        result = module.audit_concurrency_probe(api_results, events)

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["succeeded"], 4)
        self.assertEqual(result["unique_api_request_ids"], 4)
        self.assertEqual(result["unique_vllm_request_ids"], 4)
        self.assertEqual(result["max_batch_request_count"], 4)
        self.assertTrue(result["continuous_batching_observed"])

    def test_concurrency_probe_rejects_one_api_id_mapping_to_multiple_engines(self) -> None:
        module = load_module()
        api_results = concurrent_api_results(4)
        events = []
        for index in range(4):
            events.extend(
                release_lifecycle_events(
                    f"chatcmpl-{index}",
                    f"engine-{index}",
                    generation_tokens=100,
                    base_ns=1_000 + index * 100,
                )
            )
            events.append(scheduler_event(f"engine-{index}", batch_request_count=4))
        events.append(
            release_event(
                "vllm.request.received",
                "chatcmpl-0",
                "engine-crossed",
                9_000,
            )
        )

        result = module.audit_concurrency_probe(api_results, events)

        self.assertEqual(result["status"], "fail")
        self.assertGreater(result["correlation_failure_count"], 0)
        self.assertIn("request_id_correlation_failure", result["failures"])

    def test_request_audit_rejects_missing_and_duplicate_lifecycle_events(self) -> None:
        module = load_module()
        events = release_lifecycle_events("chatcmpl-life", "engine-life", generation_tokens=100)
        without_queue = [event for event in events if event["event_type"] != "vllm.request.queued"]
        missing = module.request_event_audit(without_queue, "chatcmpl-life")
        duplicated = module.request_event_audit(
            [*events, next(event for event in events if event["event_type"] == "vllm.request.first_token")],
            "chatcmpl-life",
        )

        self.assertIn("missing_queued", missing["lifecycle_failures"])
        self.assertIn("duplicate_first_token", duplicated["lifecycle_failures"])

    def test_stream_probe_rejects_duplicate_engine_finished(self) -> None:
        module = load_module()
        events = release_lifecycle_events("chatcmpl-dup", "engine-dup", generation_tokens=81)
        events.append(
            next(event for event in events if event["event_type"] == "vllm.request.engine_finished")
        )

        result = module.audit_stream_probe(
            {
                "status": "pass",
                "api_request_id": "chatcmpl-dup",
                "api_generation_tokens": 81,
                "stream_chunk_count": 82,
                "failures": [],
            },
            events,
        )

        self.assertEqual(result["status"], "fail")
        self.assertIn("duplicate_engine_finished", result["failures"])

    def test_event_stream_integrity_detects_invalid_duplicate_and_sequence_regression(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = [
                integrity_event("same", sequence=1),
                integrity_event("same", sequence=1),
                integrity_event("third", sequence=0),
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows) + "{invalid\n",
                encoding="utf-8",
            )

            result = module.event_stream_integrity(module.read_p2_event_stream(path))

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["invalid_json_lines"], 1)
        self.assertEqual(result["duplicate_event_ids"], 1)
        self.assertEqual(result["sequence_duplicates"], 1)
        self.assertEqual(result["sequence_regressions"], 1)

    def test_concurrency_probe_fails_when_requests_do_not_overlap(self) -> None:
        module = load_module()
        api_results = concurrent_api_results(4)
        events = []
        for index in range(4):
            events.extend(
                release_lifecycle_events(
                    f"chatcmpl-{index}",
                    f"engine-{index}",
                    generation_tokens=100,
                    base_ns=1_000 + index * 100,
                )
            )
            events.append(scheduler_event(f"engine-{index}", batch_request_count=1))

        result = module.audit_concurrency_probe(api_results, events)

        self.assertEqual(result["status"], "fail")
        self.assertIn("concurrent_requests_did_not_overlap", result["failures"])

    def test_old_command_does_not_execute_active_runtime_probes(self) -> None:
        module = load_module()
        args = module.build_parser().parse_args(
            [
                "--agentmon-repo",
                str(ROOT),
                "--agentmon-python",
                sys.executable,
            ]
        )

        with patch.object(module, "run_active_runtime_probes") as active_probe:
            report = module.build_report(args)

        active_probe.assert_not_called()
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["runtime_stream_probe"]["status"], "skipped")
        self.assertEqual(report["runtime_concurrency_probe"]["status"], "skipped")

    def test_active_runtime_gate_runs_prefix_stream_and_concurrency_probes(self) -> None:
        module = load_module()
        prefix_results = [
            api_result("chatcmpl-prefix-1", 1),
            api_result("chatcmpl-prefix-2", 1),
        ]
        stream_result = api_result("chatcmpl-stream", 81, stream_chunks=82)
        concurrent_results = concurrent_api_results(4)
        active_events = []
        for api_id, engine_id, tokens in (
            ("chatcmpl-prefix-1", "engine-prefix-1", 1),
            ("chatcmpl-prefix-2", "engine-prefix-2", 1),
            ("chatcmpl-stream", "engine-stream", 81),
            *[
                (f"chatcmpl-{index}", f"engine-{index}", 100)
                for index in range(4)
            ],
        ):
            active_events.extend(
                release_lifecycle_events(api_id, engine_id, generation_tokens=tokens)
            )
            if engine_id.startswith("engine-") and engine_id[-1:].isdigit():
                active_events.append(scheduler_event(engine_id, batch_request_count=4))
        active_snapshot = clean_snapshot(active_events)
        full_snapshot = clean_snapshot(
            [
                {
                    "source": "vllm_p2",
                    "event_type": "vllm.adapter.capability",
                    "payload": {
                        "status": "supported",
                        "details": {
                            "capability_phase": "scheduler_runtime_configuration",
                            "prefix_cache_enabled": False,
                        },
                    },
                },
                *active_events,
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            events_path.write_text("", encoding="utf-8")
            with (
                patch.object(
                    module,
                    "send_chat_completion",
                    side_effect=[*prefix_results, stream_result],
                ) as send,
                patch.object(
                    module,
                    "run_concurrent_api_requests",
                    return_value=concurrent_results,
                ) as concurrent,
                patch.object(module, "wait_for_probe_events", return_value=active_snapshot),
                patch.object(module, "read_p2_event_stream", return_value=full_snapshot),
            ):
                result = module.run_active_runtime_probes(
                    base_url="http://127.0.0.1:8001/v1",
                    model="test-model",
                    events_path=events_path,
                    stream_probe=True,
                    stream_max_tokens=128,
                    concurrency=4,
                    concurrent_max_tokens=192,
                    prefix_cache_expectation="disabled",
                    timeout=30,
                )

        self.assertEqual(result["status"], "pass")
        self.assertEqual(send.call_count, 3)
        self.assertEqual(send.call_args_list[-1].kwargs["stream"], True)
        concurrent.assert_called_once()
        self.assertEqual(result["runtime_prefix_cache_probe"]["status"], "pass")
        self.assertEqual(result["runtime_stream_probe"]["status"], "pass")
        self.assertEqual(result["runtime_concurrency_probe"]["status"], "pass")
        self.assertEqual(result["event_stream_integrity"]["status"], "pass")

    def test_strict_active_runtime_argument_failure_returns_json_and_nonzero(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            events_path.write_text("", encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(
                    [
                        "--agentmon-repo",
                        str(ROOT),
                        "--agentmon-python",
                        sys.executable,
                        "--events-path",
                        str(events_path),
                        "--runtime-base-url",
                        "http://127.0.0.1:8001/v1",
                        "--strict",
                    ]
                )

        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("runtime_probe_arguments", report["required_failures"])
        self.assertEqual(report["runtime_stream_probe"]["status"], "skipped")

    def test_local_source_tree_and_agentmon_python_are_ready(self) -> None:
        module = load_module()
        args = module.build_parser().parse_args(
            [
                "--agentmon-repo",
                str(ROOT),
                "--agentmon-python",
                sys.executable,
            ]
        )

        report = module.build_report(args)

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["static_server_preflight_status"], "PASS")
        self.assertTrue(report["eligible_for_runtime_p2_gate"])
        self.assertTrue(report["eligible_for_next_stage"])
        self.assertEqual(report["next_stage"], "runtime_p2_gate")
        self.assertEqual(report["required_failures"], [])
        statuses = {item["name"]: item["status"] for item in report["checks"]}
        self.assertEqual(statuses["source_tree"], "ok")
        self.assertEqual(statuses["agentmon_cli"], "ok")
        self.assertEqual(statuses["sitecustomize_disabled"], "ok")
        self.assertEqual(statuses["vllm_runtime"], "skipped")

    def test_build_report_preserves_symlinked_venv_python_path(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            venv_bin = Path(tmp) / "mini-venv" / "bin"
            venv_bin.mkdir(parents=True)
            python_link = venv_bin / "python"
            python_link.symlink_to(Path(sys.executable))
            resolved_target = python_link.resolve()
            args = module.build_parser().parse_args(
                [
                    "--agentmon-repo",
                    str(ROOT),
                    "--agentmon-python",
                    sys.executable,
                    "--mini-swe-python",
                    str(python_link),
                ]
            )
            with patch.object(
                module,
                "check_mini_swe",
                return_value=module.make_check("mini_swe_p1", "ok", required=True),
            ) as mini_check:
                report = module.build_report(args)

        supplied = mini_check.call_args.args[1]
        self.assertEqual(supplied, python_link.absolute())
        self.assertNotEqual(supplied, resolved_target)
        self.assertEqual(
            report["python_identities"]["mini_swe"]["supplied_path"],
            str(python_link.absolute()),
        )
        self.assertEqual(
            report["python_identities"]["mini_swe"]["resolved_interpreter"],
            str(Path(sys.executable).resolve()),
        )

    def test_mini_binary_is_resolved_next_to_supplied_symlinked_python(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            venv_bin = Path(tmp) / "mini-venv" / "bin"
            venv_bin.mkdir(parents=True)
            python_link = venv_bin / "python"
            python_link.symlink_to(Path(sys.executable))
            mini = venv_bin / "mini"
            mini.write_text("#!/bin/sh\n", encoding="utf-8")
            commands = [
                {
                    "status": "ok",
                    "returncode": 0,
                    "stdout": json.dumps({"agent": "a", "model": "m", "environment": "e"}),
                    "stderr": "",
                    "duration_seconds": 0.1,
                },
                {
                    "status": "ok",
                    "returncode": 0,
                    "stdout": "--agent-class --model-class --environment-class",
                    "stderr": "",
                    "duration_seconds": 0.1,
                },
            ]
            with patch.object(module, "run_command", side_effect=commands) as run:
                result = module.check_mini_swe(ROOT, python_link, 1.0)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["detail"]["python"], str(python_link))
        self.assertEqual(result["detail"]["mini_binary"], str(mini))
        self.assertEqual(run.call_args_list[0].args[0][0], str(python_link))
        self.assertEqual(run.call_args_list[1].args[0][0], str(mini))

    def test_vllm_probe_preserves_cann_environment_and_appends_acl_paths(self) -> None:
        module = load_module()
        payload = complete_vllm_payload()
        payload["imports"]["acl"]["file"] = "/opt/ascend/python/site-packages/acl.so"
        captured: dict[str, object] = {}

        def fake_run(argv, *, env, timeout):
            captured["argv"] = argv
            captured["env"] = env
            return {
                "status": "ok",
                "returncode": 0,
                "stdout": json.dumps(payload) + "\n",
                "stderr": "",
                "duration_seconds": 0.1,
            }

        caller_env = {
            "PYTHONPATH": "/caller/python",
            "LD_LIBRARY_PATH": "/opt/ascend/lib64",
            "ASCEND_HOME_PATH": "/opt/ascend",
            module.ACL_PYTHON_PATH_ENV: "/env/acl-python",
        }
        with patch.dict(os.environ, caller_env, clear=True):
            acl_paths = module.configured_acl_python_paths(["/cli/acl-python"])
            with patch.object(module, "run_command", side_effect=fake_run):
                result = module.check_vllm(
                    ROOT,
                    Path("/venv/bin/python"),
                    1.0,
                    False,
                    acl_python_paths=acl_paths,
                )

        probe_env = captured["env"]
        self.assertEqual(probe_env["LD_LIBRARY_PATH"], "/opt/ascend/lib64")
        self.assertEqual(probe_env["ASCEND_HOME_PATH"], "/opt/ascend")
        self.assertEqual(
            probe_env["PYTHONPATH"].split(os.pathsep),
            [
                str(ROOT / "src"),
                "/caller/python",
                "/cli/acl-python",
                "/env/acl-python",
            ],
        )
        self.assertEqual(result["detail"]["acl_file"], "/opt/ascend/python/site-packages/acl.so")
        self.assertEqual(
            result["detail"]["acl_python_paths"],
            ["/cli/acl-python", "/env/acl-python"],
        )

    def test_deep_probe_accepts_result_after_15_seconds_with_60_second_default(self) -> None:
        module = load_module()
        args = module.build_parser().parse_args(["--deep"])
        timeout = module.effective_timeout_seconds(args)
        captured: dict[str, object] = {}

        def fake_run(argv, *, env, timeout):
            captured["timeout"] = timeout
            return {
                "status": "ok",
                "returncode": 0,
                "stdout": json.dumps(complete_vllm_payload()) + "\n",
                "stderr": "",
                "duration_seconds": 20.0,
            }

        with patch.object(module, "run_command", side_effect=fake_run):
            result = module.check_vllm(ROOT, Path("/venv/bin/python"), timeout, True)

        self.assertEqual(timeout, 60.0)
        self.assertEqual(captured["timeout"], 60.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["detail"]["probe_status"], "ok")
        self.assertFalse(result["detail"]["probe_timeout"])
        explicit = module.build_parser().parse_args(["--deep", "--timeout", "37"])
        self.assertEqual(module.effective_timeout_seconds(explicit), 37.0)

    def test_vllm_probe_timeout_is_explicit_and_does_not_invent_missing_capabilities(self) -> None:
        module = load_module()
        command = {
            "status": "timeout",
            "duration_seconds": 60.1,
            "timeout_seconds": 60.0,
            "stdout": "",
            "stderr": "",
        }
        caller_env = {
            "PYTHONPATH": "/caller/python",
            "LD_LIBRARY_PATH": "/opt/ascend/lib64",
            "ASCEND_HOME_PATH": "/opt/ascend",
        }
        supplied_python = Path("/venv/verl-cann90/bin/python")
        acl_paths = [Path("/opt/ascend/python/site-packages")]
        with (
            patch.dict(os.environ, caller_env, clear=True),
            patch.object(module, "run_command", return_value=command),
        ):
            result = module.check_vllm(
                ROOT,
                supplied_python,
                60.0,
                True,
                acl_python_paths=acl_paths,
            )

        detail = result["detail"]
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(detail["probe_timeout"])
        self.assertEqual(detail["probe_status"], "timeout")
        self.assertEqual(detail["python"], str(supplied_python))
        self.assertEqual(detail["python_identity"]["supplied_path"], str(supplied_python))
        self.assertEqual(detail["acl_python_paths"], [str(acl_paths[0])])
        self.assertEqual(detail["runtime_environment"]["LD_LIBRARY_PATH"], "/opt/ascend/lib64")
        self.assertEqual(detail["runtime_environment"]["ASCEND_HOME_PATH"], "/opt/ascend")
        for misleading_field in (
            "acl_file",
            "import_failures",
            "missing_base_targets",
            "missing_deep_required_targets",
            "engine_core_step_with_batch_queue_present",
            "true_async_llm_implementation",
        ):
            self.assertNotIn(misleading_field, detail)

    def test_deep_mode_requires_actual_vllm_python(self) -> None:
        module = load_module()
        args = module.build_parser().parse_args(
            [
                "--agentmon-repo",
                str(ROOT),
                "--agentmon-python",
                sys.executable,
                "--deep",
            ]
        )

        report = module.build_report(args)

        self.assertEqual(report["status"], "failed")
        self.assertIn("vllm_runtime", report["required_failures"])

    def test_vllm_probe_audits_true_async_target_and_optional_deep_methods(self) -> None:
        module = load_module()
        payload = complete_vllm_payload()
        payload["targets"]["vllm.v1.core.kv_cache_manager:KVCacheManager"]["methods"][
            "evict_blocks"
        ]["present"] = False
        command = {
            "status": "ok",
            "returncode": 0,
            "stdout": json.dumps(payload) + "\n",
            "stderr": "",
            "duration_seconds": 0.1,
        }

        with patch.object(module, "run_command", return_value=command):
            result = module.check_vllm(ROOT, Path(sys.executable), 1.0, True)

        self.assertEqual(result["status"], "warning")
        self.assertTrue(result["detail"]["true_async_llm_implementation"])
        self.assertEqual(result["detail"]["missing_deep_required_targets"], [])
        self.assertEqual(
            result["detail"]["missing_deep_optional_targets"],
            ["vllm.v1.core.kv_cache_manager:KVCacheManager.evict_blocks"],
        )
        self.assertTrue(result["detail"]["engine_core_step_with_batch_queue_signature_valid"])
        self.assertTrue(result["detail"]["engine_core_step_with_batch_queue_hook_declared"])

    def test_vllm_probe_fails_when_async_path_hook_is_not_declared(self) -> None:
        module = load_module()
        payload = complete_vllm_payload()
        payload["agentmon_hook_targets"]["vllm.v1.engine.core:EngineCore"].remove(
            "step_with_batch_queue"
        )
        command = {
            "status": "ok",
            "returncode": 0,
            "stdout": json.dumps(payload) + "\n",
            "stderr": "",
            "duration_seconds": 0.1,
        }

        with patch.object(module, "run_command", return_value=command):
            result = module.check_vllm(ROOT, Path(sys.executable), 1.0, True)

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["detail"]["engine_core_step_with_batch_queue_hook_declared"])
        self.assertEqual(
            result["detail"]["asynchronous_scheduling_collection_status"],
            "required_failure",
        )

    def test_prefix_cache_launch_validation_uses_discovered_cli_option(self) -> None:
        module = load_module()
        payload = complete_vllm_payload()
        command = {
            "status": "ok",
            "returncode": 0,
            "stdout": json.dumps(payload) + "\n",
            "stderr": "",
            "duration_seconds": 0.1,
        }

        with patch.object(module, "run_command", return_value=command):
            result = module.check_vllm(
                ROOT,
                Path(sys.executable),
                1.0,
                True,
                launch_command="vllm serve model --disable-prefix-cache-local",
                prefix_cache_expectation="disabled",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["detail"]["prefix_cache_launch_validation"], "passed")
        self.assertEqual(
            result["detail"]["prefix_cache_launch_setting"]["matched_option"],
            "--disable-prefix-cache-local",
        )

    def test_runtime_p2_check_fails_on_async_hook_missing_and_disabled_cache_hit(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            rows = [
                {
                    "source": "vllm_p2",
                    "event_type": "vllm.adapter.capability",
                    "payload": {
                        "status": "partial",
                        "details": {
                            "capability_phase": "scheduler_runtime_configuration",
                            "async_scheduling_enabled": True,
                            "engine_core_step_with_batch_queue_hooked": False,
                            "engine_token_boundary_collection": "unsupported",
                            "prefix_cache_enabled": False,
                        },
                    },
                },
                p2_cached_event("req-1", 0),
                p2_cached_event("req-2", 1280),
            ]
            events_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            result = module.check_runtime_p2_events(
                events_path,
                required=True,
                prefix_cache_expectation="disabled",
                minimum_prefix_probe_requests=2,
            )

        self.assertEqual(result["status"], "failed")
        self.assertIn("async_engine_core_hook_missing", result["detail"]["failures"])
        self.assertIn("prefix_cache_hit_while_disabled", result["detail"]["failures"])

    def test_runtime_p2_check_accepts_hooked_async_path_and_zero_cached_tokens(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            rows = [
                {
                    "source": "vllm_p2",
                    "event_type": "vllm.adapter.capability",
                    "payload": {
                        "status": "supported",
                        "details": {
                            "capability_phase": "scheduler_runtime_configuration",
                            "async_scheduling_enabled": True,
                            "engine_core_step_with_batch_queue_hooked": True,
                            "engine_token_boundary_collection": "supported",
                            "prefix_cache_enabled": False,
                            "prefix_cache_expectation_status": "passed",
                        },
                    },
                },
                *p2_lifecycle_events("req-1", 0),
                *p2_lifecycle_events("req-2", 0),
            ]
            events_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            result = module.check_runtime_p2_events(
                events_path,
                required=True,
                prefix_cache_expectation="disabled",
                minimum_prefix_probe_requests=2,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["detail"]["failures"], [])
        self.assertEqual(result["detail"]["prefix_cache_cached_tokens_max"], 0)

    def test_events_path_checks_write_and_private_permissions(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.chmod(root, 0o700)
            private = module.check_events_path(root / "events.jsonl")
            os.chmod(root, 0o755)
            public = module.check_events_path(root / "events.jsonl")

        self.assertEqual(private["status"], "ok")
        self.assertEqual(private["detail"]["probe_mode"], "0o600")
        self.assertEqual(public["status"], "warning")
        self.assertFalse(public["detail"]["private_permissions"])


def complete_vllm_payload() -> dict[str, object]:
    target_methods = {
        "vllm.v1.engine.async_llm:AsyncLLM": ["add_request", "generate", "abort"],
        "vllm.v1.engine.llm_engine:LLMEngine": ["add_request", "step"],
        "vllm.v1.engine.core:EngineCore": ["add_request", "step", "step_with_batch_queue"],
        "vllm.v1.core.sched.scheduler:Scheduler": [
            "__init__",
            "add_request",
            "schedule",
            "make_stats",
            "update_from_output",
        ],
        "vllm.v1.core.kv_cache_manager:KVCacheManager": [
            "__init__",
            "make_prefix_cache_stats",
            "get_computed_blocks",
            "allocate_slots",
            "cache_blocks",
            "free",
            "evict_blocks",
            "reset_prefix_cache",
        ],
    }
    targets = {}
    for target, methods in target_methods.items():
        module_name, class_name = target.split(":", 1)
        targets[target] = {
            "status": "ok",
            "class_name": class_name,
            "class_module": module_name,
            "methods": {
                method: {
                    "present": True,
                    "signature": "(self)",
                    "signature_valid": True if method in {"step", "step_with_batch_queue"} else None,
                    "signature_error": None,
                }
                for method in methods
            },
        }
    return {
        "imports": {
            "vllm": {"status": "ok", "version": "0.18.0"},
            "vllm_ascend": {"status": "ok", "version": "0.18.0"},
            "acl": {"status": "ok", "version": None},
        },
        "targets": targets,
        "alias": {"present": True, "same_object": True},
        "agentmon_hook_targets": target_methods,
        "engine_args": {
            "enable_prefix_caching_default": True,
            "async_scheduling_default": True,
            "enable_prefix_caching_cli": {
                "option_strings": ["--enable-prefix-cache-local", "--disable-prefix-cache-local"],
                "action_class": "BooleanOptionalAction",
                "default": True,
                "const": None,
                "nargs": 0,
            },
            "async_scheduling_cli": {
                "option_strings": ["--async-on", "--async-off"],
                "action_class": "BooleanOptionalAction",
                "default": True,
                "const": None,
                "nargs": 0,
            },
        },
    }


def p2_cached_event(request_id: str, cached_tokens: int) -> dict[str, object]:
    return {
        "source": "vllm_p2",
        "event_type": "vllm.request.finished",
        "payload": {
            "vllm_request_id": request_id,
            "cached_prompt_tokens": cached_tokens,
        },
    }


def p2_lifecycle_events(request_id: str, cached_tokens: int) -> list[dict[str, object]]:
    event_types = [
        "vllm.request.received",
        "vllm.request.queued",
        "vllm.request.first_scheduled",
        "vllm.request.prefill_completed",
        "vllm.request.first_token",
        "vllm.request.first_output",
        "vllm.request.engine_finished",
        "vllm.request.finished",
    ]
    return [
        {
            "source": "vllm_p2",
            "event_type": event_type,
            "payload": {
                "vllm_request_id": request_id,
                **(
                    {"cached_prompt_tokens": cached_tokens}
                    if event_type == "vllm.request.finished"
                    else {}
                ),
            },
        }
        for event_type in event_types
    ]


def concurrent_api_results(count: int) -> list[dict[str, object]]:
    return [
        {
            "status": "pass",
            "api_request_id": f"chatcmpl-{index}",
            "api_generation_tokens": 100,
            "failures": [],
        }
        for index in range(count)
    ]


def api_result(
    api_request_id: str,
    generation_tokens: int,
    *,
    stream_chunks: int | None = None,
) -> dict[str, object]:
    return {
        "status": "pass",
        "api_request_id": api_request_id,
        "api_generation_tokens": generation_tokens,
        "stream_chunk_count": stream_chunks,
        "failures": [],
    }


def clean_snapshot(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "events": events,
        "start_offset": 0,
        "bytes_read": 0,
        "invalid_json_lines": 0,
        "duplicate_event_ids": 0,
        "sequence_duplicates": 0,
        "sequence_regressions": 0,
    }


def release_lifecycle_events(
    api_request_id: str,
    engine_request_id: str,
    *,
    generation_tokens: int,
    base_ns: int = 1_000,
) -> list[dict[str, object]]:
    definitions = [
        ("vllm.request.received", "received_ns", base_ns),
        ("vllm.request.queued", "queued_ns", base_ns + 10),
        ("vllm.request.first_scheduled", "first_scheduled_ns", base_ns + 20),
        ("vllm.request.prefill_completed", "prefill_completed_ns", base_ns + 30),
        ("vllm.request.first_token", "first_token_ns", base_ns + 40),
        ("vllm.request.first_output", "first_output_ns", base_ns + 45),
        ("vllm.request.engine_finished", "engine_finished_ns", base_ns + 60),
        ("vllm.request.finished", "finished_ns", base_ns + 70),
    ]
    rows = []
    for event_type, timestamp_key, timestamp in definitions:
        row = release_event(event_type, api_request_id, engine_request_id, timestamp)
        row["payload"][timestamp_key] = timestamp
        if event_type == "vllm.request.engine_finished":
            row["payload"].update(
                {
                    "last_token_ns": base_ns + 55,
                    "generation_tokens": generation_tokens,
                }
            )
        if event_type == "vllm.request.finished":
            row["payload"].update(
                {
                    "generation_tokens": generation_tokens,
                    "cached_prompt_tokens": 0,
                }
            )
        rows.append(row)
    return rows


def release_event(
    event_type: str,
    api_request_id: str,
    engine_request_id: str,
    timestamp: int,
) -> dict[str, object]:
    return {
        "source": "vllm_p2",
        "event_id": f"{engine_request_id}-{event_type}-{timestamp}",
        "event_type": event_type,
        "monotonic_time_ns": timestamp,
        "payload": {
            "api_request_id": api_request_id,
            "vllm_request_id": engine_request_id,
        },
    }


def scheduler_event(engine_request_id: str, *, batch_request_count: int) -> dict[str, object]:
    return {
        "source": "vllm_p2",
        "event_id": f"scheduler-{engine_request_id}",
        "event_type": "vllm.request.scheduler_iteration",
        "payload": {
            "vllm_request_id": engine_request_id,
            "batch_request_count": batch_request_count,
            "continuous_batching_observed": batch_request_count >= 2,
        },
    }


def integrity_event(event_id: str, *, sequence: int) -> dict[str, object]:
    return {
        "source": "vllm_p2",
        "event_id": event_id,
        "event_type": "vllm.request.received",
        "payload": {
            "source_pid": 123,
            "source_starttime_ticks": "456",
            "sequence": sequence,
        },
    }


if __name__ == "__main__":
    unittest.main()
