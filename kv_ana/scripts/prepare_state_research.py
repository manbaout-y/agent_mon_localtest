#!/usr/bin/env python3
"""Standalone entrypoint; use the project's existing Python environment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kvreuse_b5.state_research.prepare import main

if __name__ == "__main__":
    raise SystemExit(main())
