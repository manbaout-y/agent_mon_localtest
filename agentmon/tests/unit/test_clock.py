import time
import unittest

from agentmon.clock import ClockAnchor


class ClockAnchorTests(unittest.TestCase):
    def test_clock_anchor_contains_wall_and_monotonic_time(self) -> None:
        before_wall = time.time_ns()
        before_mono = time.monotonic_ns()

        anchor = ClockAnchor.capture()

        after_wall = time.time_ns()
        after_mono = time.monotonic_ns()
        self.assertGreaterEqual(anchor.wall_time_ns, before_wall)
        self.assertLessEqual(anchor.wall_time_ns, after_wall)
        self.assertGreaterEqual(anchor.monotonic_time_ns, before_mono)
        self.assertLessEqual(anchor.monotonic_time_ns, after_mono)

        data = anchor.to_dict()
        self.assertEqual(data["schema_version"], "1.0")
        self.assertIsInstance(data["wall_time_ns"], int)
        self.assertIsInstance(data["monotonic_time_ns"], int)
