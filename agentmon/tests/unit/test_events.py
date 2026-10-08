import json
import math
import unittest

from agentmon.events import Event


class EventTests(unittest.TestCase):
    def test_event_serializes_to_valid_json(self) -> None:
        event = Event(
            run_id="run-1",
            source="controller",
            event_type="lifecycle",
            quality="exact",
            labels={"phase": "start"},
            payload={"ok": True},
        )

        raw = event.to_json()
        data = json.loads(raw)

        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["run_id"], "run-1")
        self.assertEqual(data["source"], "controller")
        self.assertEqual(data["event_type"], "lifecycle")
        self.assertTrue(data["event_id"])
        self.assertIsInstance(data["wall_time_ns"], int)
        self.assertIsInstance(data["monotonic_time_ns"], int)

    def test_nan_and_inf_are_safely_converted_to_null(self) -> None:
        event = Event(
            run_id="run-1",
            source="controller",
            event_type="sample",
            quality="sampled",
            labels={"bad": float("nan")},
            payload={
                "nan": float("nan"),
                "inf": float("inf"),
                "neg_inf": float("-inf"),
                "nested": [1, float("inf")],
            },
        )

        raw = event.to_json()
        data = json.loads(raw)

        self.assertNotIn("NaN", raw)
        self.assertNotIn("Infinity", raw)
        self.assertIsNone(data["labels"]["bad"])
        self.assertIsNone(data["payload"]["nan"])
        self.assertIsNone(data["payload"]["inf"])
        self.assertIsNone(data["payload"]["neg_inf"])
        self.assertIsNone(data["payload"]["nested"][1])
        self.assertIn("_json_sanitization", data["payload"])
        self.assertFalse(math.isnan(data["payload"]["nested"][0]))
