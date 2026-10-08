import unittest

from agentmon.analysis.intervals import merge_intervals


class IntervalTests(unittest.TestCase):
    def test_merge_intervals_uses_half_open_ranges(self) -> None:
        merged, total = merge_intervals([(10, 20), (15, 30), (30, 40), (50, 45)])

        self.assertEqual(merged, [(10, 40)])
        self.assertEqual(total, 30)
