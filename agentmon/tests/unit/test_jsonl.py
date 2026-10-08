import json
import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path

from agentmon.events import Event
from agentmon.storage.jsonl import JsonlWriter


def write_jsonl_events_for_process(path: str, process_index: int, event_count: int) -> None:
    writer = JsonlWriter(path)
    try:
        for event_index in range(event_count):
            writer.write_event(
                Event(
                    run_id="run-mp",
                    source="controller",
                    event_type="sample",
                    quality="sampled",
                    labels={"process": process_index},
                    payload={"event": event_index},
                )
            )
    finally:
        writer.close()


class JsonlWriterTests(unittest.TestCase):
    def test_concurrent_event_writes_are_valid_json_lines(self) -> None:
        thread_count = 8
        events_per_thread = 40

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            writer = JsonlWriter(path)

            def write_events(thread_index: int) -> None:
                for event_index in range(events_per_thread):
                    writer.write_event(
                        Event(
                            run_id="run-1",
                            source="controller",
                            event_type="sample",
                            quality="sampled",
                            labels={"thread": thread_index},
                            payload={"event": event_index},
                        )
                    )

            threads = [
                threading.Thread(target=write_events, args=(idx,))
                for idx in range(thread_count)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            writer.close()

            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), thread_count * events_per_thread)
        event_ids = set()
        for line in lines:
            data = json.loads(line)
            event_ids.add(data["event_id"])
            self.assertEqual(data["run_id"], "run-1")
        self.assertEqual(len(event_ids), len(lines))

    def test_multi_process_writes_are_valid_json_lines(self) -> None:
        process_count = 4
        events_per_process = 30

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            ctx = multiprocessing.get_context("spawn")
            processes = [
                ctx.Process(
                    target=write_jsonl_events_for_process,
                    args=(str(path), idx, events_per_process),
                )
                for idx in range(process_count)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            lines = path.read_text(encoding="utf-8").splitlines()
            file_mode = path.stat().st_mode & 0o777

        self.assertEqual(len(lines), process_count * events_per_process)
        self.assertEqual(file_mode, 0o600)
        event_ids = set()
        for line in lines:
            data = json.loads(line)
            self.assertEqual(data["run_id"], "run-mp")
            event_ids.add(data["event_id"])
        self.assertEqual(len(event_ids), len(lines))
