import unittest

from agentmon.sampling.procfs import parse_proc_stat_line


class ProcfsTests(unittest.TestCase):
    def test_parse_proc_stat_handles_comm_with_space_and_parens(self) -> None:
        line = "123 (cmd name (x)) S 1 123 123 0 -1 4194304 10 0 2 0 100 25 0 0 20 0 3 0 9999 123456 42 0 0 0"

        parsed = parse_proc_stat_line(line)

        self.assertEqual(parsed["pid"], 123)
        self.assertEqual(parsed["comm"], "cmd name (x)")
        self.assertEqual(parsed["ppid"], 1)
        self.assertEqual(parsed["pgrp"], 123)
        self.assertEqual(parsed["utime"], 100)
        self.assertEqual(parsed["stime"], 25)
        self.assertEqual(parsed["starttime_ticks"], 9999)
