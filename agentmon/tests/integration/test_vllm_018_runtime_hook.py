from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest

from agentmon.adapters.vllm_018 import hook as p2_hook
from agentmon.adapters.vllm_018.events import VllmP2EventSink


RUN_INTEGRATION_ENV = "AGENTMON_RUN_VLLM_018_INTEGRATION"


@unittest.skipUnless(
    os.environ.get(RUN_INTEGRATION_ENV) == "1",
    f"set {RUN_INTEGRATION_ENV}=1 in the real vLLM 0.18 service environment",
)
class Vllm018RuntimeHookIntegrationTests(unittest.TestCase):
    def tearDown(self) -> None:
        p2_hook.uninstall_for_tests()

    def test_real_engine_core_contract_and_hook_installation(self) -> None:
        from vllm.v1.engine.async_llm import AsyncLLM
        from vllm.v1.engine.core import EngineCore

        self.assertEqual(AsyncLLM.__name__, "AsyncLLM")
        self.assertEqual(AsyncLLM.__module__, "vllm.v1.engine.async_llm")
        for method_name in ("step", "step_with_batch_queue"):
            method = getattr(EngineCore, method_name)
            parameters = list(inspect.signature(method).parameters.values())
            self.assertTrue(parameters)
            self.assertEqual(parameters[0].name, "self")

        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            sink = VllmP2EventSink(run_id="vllm-018-integration", events_path=events_path)
            try:
                result = p2_hook.install(sink=sink, force=True)
            finally:
                sink.close()

            self.assertEqual(result["detected_vllm_version"].split("+", 1)[0], "0.18.0")
            self.assertTrue(result["engine_core_step_hooked"])
            self.assertTrue(result["engine_core_step_with_batch_queue_hooked"])
            self.assertTrue(getattr(EngineCore.step, p2_hook.INSTALLED_ATTR, False))
            self.assertTrue(
                getattr(EngineCore.step_with_batch_queue, p2_hook.INSTALLED_ATTR, False)
            )

            events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            capability = next(
                event for event in events if event["event_type"] == "vllm.adapter.capability"
            )
            details = capability["payload"]["details"]
            self.assertTrue(details["engine_core_step_hooked"])
            self.assertTrue(details["engine_core_step_with_batch_queue_hooked"])
            self.assertEqual(details["engine_token_boundary_collection"], "supported")


if __name__ == "__main__":
    unittest.main()
