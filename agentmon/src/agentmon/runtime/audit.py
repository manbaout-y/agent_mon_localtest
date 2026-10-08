from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import uuid4

from agentmon.config import AgentMonConfig
from agentmon.paths import chmod_private_file, make_private_dir, output_root


class RuntimeAuditLog:
    def __init__(self, cfg: AgentMonConfig) -> None:
        self.path = output_root(cfg) / "_runtime" / "events.jsonl"

    def emit(
        self,
        event_type: str,
        *,
        payload: dict[str, Any],
        quality: str = "exact",
        source: str = "runtime",
    ) -> None:
        make_private_dir(self.path.parent)
        row = {
            "schema_version": "1.0",
            "event_id": uuid4().hex,
            "source": source,
            "event_type": event_type,
            "wall_time_ns": time.time_ns(),
            "quality": quality,
            "payload": payload,
        }
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        chmod_private_file(self.path)
