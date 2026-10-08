from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from agentmon.events import Event, safe_json_dumps
from agentmon.paths import chmod_private_file


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,
  event_type TEXT NOT NULL,
  wall_time_ns INTEGER NOT NULL,
  mono_time_ns INTEGER NOT NULL,
  quality TEXT NOT NULL,
  labels_json TEXT,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run_time ON events(run_id, mono_time_ns);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(run_id, source, event_type);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  start_wall_ns INTEGER NOT NULL,
  start_mono_ns INTEGER NOT NULL,
  end_wall_ns INTEGER,
  end_mono_ns INTEGER,
  exit_code INTEGER,
  term_signal INTEGER,
  command_hash TEXT,
  cwd TEXT,
  manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capabilities (
  run_id TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  detail_json TEXT,
  PRIMARY KEY (run_id, name)
);
"""


class SqliteEventWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch(mode=0o600)
        chmod_private_file(self.path)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
        chmod_private_file(self.path)

    def write_event(self, event: Event | dict[str, Any]) -> None:
        data = event.to_dict() if isinstance(event, Event) else dict(event)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO events
                (event_id, run_id, source, event_type, wall_time_ns, mono_time_ns,
                 quality, labels_json, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["event_id"],
                    data["run_id"],
                    data["source"],
                    data["event_type"],
                    data["wall_time_ns"],
                    data["monotonic_time_ns"],
                    data["quality"],
                    safe_json_dumps(data.get("labels", {})),
                    safe_json_dumps(data.get("payload", {})),
                ),
            )

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()
