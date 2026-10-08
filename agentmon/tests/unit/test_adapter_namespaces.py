import unittest
import io
import json
from contextlib import redirect_stdout

from agentmon.cli import main as agentmon_main


class AdapterNamespaceTests(unittest.TestCase):
    def test_mini_swe_new_namespace_reexports_existing_adapter(self) -> None:
        from agentmon.adapters.agent_frameworks.mini_swe.agent import InstrumentedAgentMixin
        from agentmon.adapters.mini_swe.agent import InstrumentedAgentMixin as Existing

        self.assertIs(InstrumentedAgentMixin, Existing)
        self.assertTrue(InstrumentedAgentMixin.__module__.startswith("agentmon.adapters.agent_frameworks."))

    def test_mini_swe_legacy_namespace_reexports_adapter_entrypoint(self) -> None:
        from agentmon.adapters.agent_frameworks.mini_swe.adapter import MiniSweAgentFrameworkAdapter
        from agentmon.adapters.mini_swe.adapter import MiniSweAgentFrameworkAdapter as Existing

        self.assertIs(MiniSweAgentFrameworkAdapter, Existing)

    def test_vllm_new_namespace_reexports_existing_adapter(self) -> None:
        from agentmon.adapters.inference.vllm_018.events import VllmP2EventSink
        from agentmon.adapters.vllm_018.events import VllmP2EventSink as Existing

        self.assertIs(VllmP2EventSink, Existing)
        self.assertTrue(VllmP2EventSink.__module__.startswith("agentmon.adapters.inference."))

    def test_vllm_legacy_namespace_reexports_p2_index(self) -> None:
        from agentmon.adapters.inference.vllm_018.p2_index import build_p2_batch_index
        from agentmon.adapters.vllm_018.p2_index import build_p2_batch_index as Existing

        self.assertIs(build_p2_batch_index, Existing)

    def test_adapter_registry_uses_contract_capability_type(self) -> None:
        from agentmon.adapters import AdapterCapability
        from agentmon.contracts import AdapterCapability as ContractAdapterCapability

        self.assertIs(AdapterCapability, ContractAdapterCapability)

    def test_mini_swe_adapter_prepares_p1_command(self) -> None:
        from agentmon.adapters.agent_frameworks.mini_swe import MINI_SWE_P1_CLASSES, MiniSweAgentFrameworkAdapter

        command, status = MiniSweAgentFrameworkAdapter().prepare_command("mini-swe-agent -t task.md")

        self.assertEqual(status, "injected")
        for flag, class_path in MINI_SWE_P1_CLASSES.items():
            self.assertIn(flag, command)
            self.assertIn(class_path, command)

    def test_vllm_provider_exposes_inference_descriptor(self) -> None:
        from agentmon.adapters.inference.vllm_018 import provider

        descriptor = provider().descriptor()

        self.assertEqual(descriptor.domain, "inference")
        self.assertEqual(descriptor.namespace, "agentmon.adapters.inference.vllm_018")
        self.assertIn("engine_request_events", {item.name for item in descriptor.capabilities})

    def test_adapter_capability_catalog_reports_agent_and_inference_adapters(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = agentmon_main(["adapter", "capabilities"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["result_type"], "agentmon.adapter.capability_catalog")
        adapters = {row["name"]: row for row in payload["adapters"]}
        self.assertEqual(adapters["mini_swe"]["domain"], "agent_framework")
        self.assertEqual(adapters["mini_swe"]["namespace"], "agentmon.adapters.agent_frameworks.mini_swe")
        self.assertIn("agent_step_events", adapters["mini_swe"]["capabilities"])
        self.assertEqual(adapters["vllm_018"]["domain"], "inference")
        self.assertIn("kv_cache_accounting", adapters["vllm_018"]["capabilities"])

    def test_adapter_capability_catalog_filters_by_domain(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = agentmon_main(["adapter", "capabilities", "--domain", "inference"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual([row["name"] for row in payload["adapters"]], ["vllm_018"])

    def test_adapter_capability_catalog_reports_resource_provider(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = agentmon_main(["adapter", "capabilities", "--domain", "resource"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual([row["name"] for row in payload["adapters"]], ["npu_process_mapping"])
        self.assertEqual(payload["adapters"][0]["namespace"], "agentmon.sampling.npu_mapping")
