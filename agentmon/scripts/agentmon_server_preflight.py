#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmon.script_compat import install_module_facade
from agentmon.server import preflight as _implementation


install_module_facade(globals(), _implementation)


if __name__ == "__main__":
    raise SystemExit(main())
