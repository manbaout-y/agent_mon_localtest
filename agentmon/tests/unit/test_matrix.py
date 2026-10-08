from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from agentmon.experiments.matrix import run_repeated_matrix


class MatrixExperimentTests(unittest.TestCase):
    def test_repeats_command_in_isolated_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "matrix"
            result = run_repeated_matrix(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path(r'{repeat_dir}/marker-{repeat}.txt').write_text('ok')",
                ],
                repeat=3,
                output_root=root,
                label="C5",
            )

            self.assertEqual(result["repeat_completed"], 3)
            self.assertEqual(result["success_count"], 3)
            self.assertTrue((root / "repeat-001" / "marker-1.txt").is_file())
            self.assertTrue((root / "repeat-003" / "marker-3.txt").is_file())
            stored = json.loads((root / "matrix-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["statistics"]["wall_s"]["count"], 3)


if __name__ == "__main__":
    unittest.main()
