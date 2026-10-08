from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agentmon.sampling.host import read_diskstats, read_schedstat


class HostSamplingTests(unittest.TestCase):
    def test_diskstats_preserves_units_and_io_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diskstats"
            path.write_text(
                "8 0 sda 10 2 100 4 20 3 200 5 1 6 7\n",
                encoding="utf-8",
            )

            result = read_diskstats(str(path))["sda"]

        self.assertEqual(result["read_bytes"], 100 * 512)
        self.assertEqual(result["write_bytes"], 200 * 512)
        self.assertEqual(result["reads_completed"], 10)
        self.assertEqual(result["io_time_ms"], 6)
        self.assertEqual(result["sector_unit_bytes"], 512)

    def test_schedstat_aggregates_per_cpu_runqueue_delay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedstat"
            path.write_text(
                "version 15\ncpu0 100 20 3\ncpu1 200 40 5\n",
                encoding="utf-8",
            )

            result = read_schedstat(str(path))

        self.assertEqual(result["runtime_ns"], 300)
        self.assertEqual(result["runqueue_delay_ns"], 60)
        self.assertEqual(result["timeslices"], 8)


if __name__ == "__main__":
    unittest.main()
