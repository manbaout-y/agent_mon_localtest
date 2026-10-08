from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
from typing import Any

def load_events(run_dir: str | Path) -> list[dict[str, Any]]:
    return read_event_log(run_dir)["events"]


def read_event_log(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    path = next((root / name for name in ("events.jsonl", "events.jsonl.gz", "events.jsonl.zst") if (root / name).exists()), root / "events.jsonl")
    events: list[dict[str, Any]] = []
    invalid_lines = 0
    total_lines = 0
    if not path.exists():
        return {
            "events": events,
            "total_lines": 0,
            "valid_lines": 0,
            "invalid_lines": 0,
            "path": str(path),
            "file_size_bytes": None,
        }
    file_size_bytes = path.stat().st_size
    with open_event_log(path) as handle:
        for total_lines, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if not isinstance(item, dict):
                invalid_lines += 1
                continue
            item.setdefault("_source_path", str(path))
            item.setdefault("_source_line", total_lines)
            events.append(item)
    return {
        "events": events,
        "total_lines": total_lines,
        "valid_lines": len(events),
        "invalid_lines": invalid_lines,
        "path": str(path),
        "file_size_bytes": file_size_bytes,
    }


def open_event_log(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    if path.suffix == ".zst":
        try:
            import zstandard as zstd  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError("zstd event logs require the optional zstandard package") from exc
        raw = path.open("rb")
        return io.TextIOWrapper(zstd.ZstdDecompressor().stream_reader(raw), encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")
