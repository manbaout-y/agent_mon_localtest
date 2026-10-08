from __future__ import annotations

from dataclasses import dataclass
import threading
import time
import traceback
from typing import Callable

from agentmon.storage.event_queue import EventSink


@dataclass
class SamplerStopStatus:
    source: str
    stop_requested: bool
    joined: bool
    timeout_seconds: float
    thread_name: str
    thread_ident: int | None
    alive: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "stop_requested": self.stop_requested,
            "joined": self.joined,
            "timeout_seconds": self.timeout_seconds,
            "thread_name": self.thread_name,
            "thread_ident": self.thread_ident,
            "alive": self.alive,
        }


class PeriodicSampler:
    source = "sampler"

    def __init__(self, sink: EventSink, interval_seconds: float) -> None:
        self.sink = sink
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"agentmon-{self.source}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, *, timeout_seconds: float | None = None) -> SamplerStopStatus:
        self._stop.set()
        timeout = max(0.0, timeout_seconds if timeout_seconds is not None else max(5.0, self.interval_seconds * 3))
        self._thread.join(timeout=timeout)
        return SamplerStopStatus(
            source=self.source,
            stop_requested=True,
            joined=not self._thread.is_alive(),
            timeout_seconds=timeout,
            thread_name=self._thread.name,
            thread_ident=self._thread.ident,
            alive=self._thread.is_alive(),
        )

    def sample(self) -> None:
        raise NotImplementedError

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.sample()
            except Exception as exc:
                self.sink.emit(
                    self.source,
                    "error",
                    "sampled",
                    payload={
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "traceback_tail": traceback.format_exc(limit=4),
                    },
                    critical=True,
                )
            elapsed = time.monotonic() - started
            delay = max(0.0, self.interval_seconds - elapsed)
            self._stop.wait(delay)
