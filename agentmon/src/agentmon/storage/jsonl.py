from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Any

from agentmon.events import Event, safe_json_dumps


class JsonlWriter:
    """Thread-safe append-only JSONL writer for normalized events."""

    def __init__(self, path: str | Path, *, fsync: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        self._fsync = fsync
        self._lock = threading.Lock()
        self._closed = False

    def write_event(self, event: Event | dict[str, Any]) -> None:
        if isinstance(event, Event):
            line = event.to_json()
        else:
            line = safe_json_dumps(event)
        with self._lock:
            if self._closed:
                raise ValueError("cannot write to a closed JsonlWriter")
            os.write(self._fd, (line + "\n").encode("utf-8"))
            if self._fsync:
                os.fsync(self._fd)

    def flush(self) -> None:
        """Make the append-only fd visible to readers.

        The writer uses unbuffered ``os.write`` calls, so there is no Python
        buffer to flush. This method exists to give finalization code an
        explicit barrier API shared with fsync/close.
        """
        with self._lock:
            if self._closed:
                return

    def fsync(self) -> None:
        with self._lock:
            if not self._closed:
                os.fsync(self._fd)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                if self._fsync:
                    os.fsync(self._fd)
                os.close(self._fd)
                self._closed = True

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
