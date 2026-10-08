from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any

from agentmon.events import Event
from agentmon.storage.jsonl import JsonlWriter
from agentmon.storage.sqlite import SqliteEventWriter


class EventSink:
    def __init__(
        self,
        run_id: str,
        jsonl_path: str | Path,
        sqlite_path: str | Path | None,
        *,
        max_items: int,
        flush_interval_seconds: float,
    ) -> None:
        self.run_id = run_id
        self.dropped_samples_total = 0
        self.rejected_events_total = 0
        self.sqlite_errors_total = 0
        self.writer_errors_total = 0
        self.writer_failures: list[dict[str, Any]] = []
        self._queue: queue.Queue[Event | dict[str, Any] | None] = queue.Queue(maxsize=max_items)
        self._jsonl = JsonlWriter(jsonl_path)
        self._sqlite = SqliteEventWriter(sqlite_path) if sqlite_path is not None else None
        self._flush_interval_seconds = flush_interval_seconds
        self._state_lock = threading.Lock()
        self._accepting = True
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="agentmon-writer", daemon=True)
        self._thread.start()

    def emit(
        self,
        source: str,
        event_type: str,
        quality: str,
        *,
        labels: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        critical: bool = False,
    ) -> None:
        event = Event(
            run_id=self.run_id,
            source=source,
            event_type=event_type,
            quality=quality,
            labels=labels or {},
            payload=payload or {},
        )
        self.emit_event(event, critical=critical)

    def emit_event(self, event: Event | dict[str, Any], *, critical: bool = False) -> bool:
        with self._state_lock:
            accepting = self._accepting and not self._closed
        if not accepting:
            self.rejected_events_total += 1
            return False
        try:
            if critical:
                self._queue.put(event, timeout=1)
            else:
                self._queue.put_nowait(event)
        except queue.Full:
            self.dropped_samples_total += 1
            return False
        return True

    def stop_accepting(self) -> None:
        with self._state_lock:
            self._accepting = False

    def pending_queue_size(self) -> int:
        return self._queue.qsize()

    def drain(self, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + max(0.0, timeout_seconds)
        drained = True
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    drained = False
                    break
                self._queue.all_tasks_done.wait(remaining)
        return {
            "drained": drained,
            "timeout_seconds": timeout_seconds,
            "pending_queue_size": self.pending_queue_size(),
            "writer_alive": self._thread.is_alive(),
            "writer_errors_total": self.writer_errors_total,
            "duration_seconds": time.monotonic() - started,
        }

    def flush(self) -> None:
        self._jsonl.flush()
        if self._sqlite is not None:
            try:
                self._sqlite.commit()
            except Exception as exc:
                self.sqlite_errors_total += 1
                self._record_writer_failure("sqlite.commit", exc)

    def fsync(self) -> None:
        self._jsonl.fsync()

    def close(self, *, timeout_seconds: float = 10.0, fsync: bool = True) -> dict[str, Any]:
        with self._state_lock:
            if self._closed:
                return {
                    "closed": True,
                    "already_closed": True,
                    "drained": True,
                    "writer_joined": not self._thread.is_alive(),
                    "pending_queue_size": self.pending_queue_size(),
                    "writer_errors_total": self.writer_errors_total,
                    "rejected_events_total": self.rejected_events_total,
                }
            self._accepting = False
            self._closed = True

        started = time.monotonic()
        sentinel_enqueued = False
        try:
            self._queue.put(None, timeout=max(0.0, timeout_seconds))
            sentinel_enqueued = True
        except queue.Full:
            self._record_writer_failure(
                "event_queue.close",
                TimeoutError("event writer queue did not accept sentinel before timeout"),
            )

        drain_timeout = max(0.0, timeout_seconds - (time.monotonic() - started))
        drain_result = self.drain(timeout_seconds=drain_timeout)
        join_timeout = max(0.0, timeout_seconds - (time.monotonic() - started))
        self._thread.join(timeout=join_timeout)
        writer_joined = not self._thread.is_alive()
        try:
            self.flush()
            if fsync:
                self.fsync()
        finally:
            self._jsonl.close()
            if self._sqlite is not None:
                self._sqlite.close()
        return {
            "closed": True,
            "already_closed": False,
            "sentinel_enqueued": sentinel_enqueued,
            "drained": bool(drain_result.get("drained")),
            "writer_joined": writer_joined,
            "pending_queue_size": self.pending_queue_size(),
            "writer_alive": self._thread.is_alive(),
            "writer_errors_total": self.writer_errors_total,
            "writer_failures": list(self.writer_failures),
            "dropped_samples_total": self.dropped_samples_total,
            "rejected_events_total": self.rejected_events_total,
            "sqlite_errors_total": self.sqlite_errors_total,
            "duration_seconds": time.monotonic() - started,
        }

    def _run(self) -> None:
        last_commit = time.monotonic()
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    break
                try:
                    self._jsonl.write_event(item)
                except Exception as exc:
                    self._record_writer_failure("jsonl.write_event", exc)
                    continue
                if self._sqlite is not None and isinstance(item, Event):
                    try:
                        self._sqlite.write_event(item)
                        if time.monotonic() - last_commit >= self._flush_interval_seconds:
                            self._sqlite.commit()
                            last_commit = time.monotonic()
                    except Exception as exc:
                        self.sqlite_errors_total += 1
                        self._record_writer_failure("sqlite.write_event", exc)
            finally:
                self._queue.task_done()
        if self._sqlite is not None:
            try:
                self._sqlite.commit()
            except Exception as exc:
                self.sqlite_errors_total += 1
                self._record_writer_failure("sqlite.commit", exc)

    def _record_writer_failure(self, phase: str, exc: BaseException) -> None:
        self.writer_errors_total += 1
        if len(self.writer_failures) < 20:
            self.writer_failures.append(
                {
                    "phase": phase,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
