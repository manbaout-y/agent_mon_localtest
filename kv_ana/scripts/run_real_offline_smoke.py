#!/usr/bin/env python3
from __future__ import annotations

import sys

from run_real_offline_pipeline import main


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if "--max-pairs" not in arguments:
        arguments.extend(("--max-pairs", "3"))
    raise SystemExit(main(arguments))

