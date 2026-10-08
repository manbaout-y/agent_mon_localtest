from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Iterator

from agentmon.analysis.config import AnalysisConfig
from agentmon.analysis.io import open_maybe_compressed, read_json, write_json
from agentmon.analysis.profiles import available_profiles, values_for_keys


P2_INDEX_SCHEMA_VERSION = "1.2"


@dataclass(frozen=True)
class P2IndexResult:
    status: str
    index_path: str | None
    manifest_path: str
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "index_path": self.index_path,
            "manifest_path": self.manifest_path,
            "metrics": self.metrics,
        }


def build_or_reuse_p2_index(
    source_paths: Iterable[str | Path],
    *,
    cache_root: str | Path,
    cfg: AnalysisConfig,
    force_rebuild: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> P2IndexResult:
    sources = sorted({Path(item).expanduser().resolve() for item in source_paths if Path(item).expanduser().is_file()})
    cache = Path(cache_root).expanduser().resolve() / "p2_index"
    cache.mkdir(parents=True, exist_ok=True)
    index_path = cache / "events.sqlite3"
    manifest_path = cache / "p2_index_manifest.json"
    fingerprints = [source_fingerprint(path) for path in sources]
    profiles = tuple(available_profiles(cfg).values())
    request_id_keys = sorted({key for profile in profiles for key in profile.request_id_keys})
    run_id_keys = sorted({key for profile in profiles for key in profile.run_id_keys})
    task_id_keys = sorted({key for profile in profiles for key in profile.task_id_keys})
    batch_id_keys = sorted({key for profile in profiles for key in profile.batch_id_keys})
    compatibility = {
        "schema_version": P2_INDEX_SCHEMA_VERSION,
        "sources": fingerprints,
        "request_id_keys": request_id_keys,
        "run_id_keys": run_id_keys,
        "task_id_keys": task_id_keys,
        "batch_id_keys": batch_id_keys,
    }
    existing = read_json(manifest_path, default={}) if manifest_path.exists() else {}
    index_ready = index_path.is_file() and sqlite_index_ready(index_path)
    if (
        not force_rebuild
        and index_ready
        and existing.get("completed") is True
        and existing.get("compatibility") == compatibility
    ):
        metrics = dict(existing.get("metrics") or {})
        metrics.update(
            {
                "source_file_open_count": 0,
                "full_scan_count": 0,
                "decompression_count": 0,
                "JSON_parse_count": 0,
                "bytes_read": 0,
                "events_parsed": 0,
                "events_indexed_delta": 0,
                "index_reused": True,
                "index_incrementally_updated": False,
                "index_partial_resume": False,
                "index_query_wall_s": 0.0,
            }
        )
        return P2IndexResult("CACHED", str(index_path), str(manifest_path), metrics)
    if not sources:
        metrics = empty_metrics(index_reused=False)
        payload = {"schema_version": P2_INDEX_SCHEMA_VERSION, "completed": True, "compatibility": compatibility, "metrics": metrics}
        write_json(manifest_path, payload)
        return P2IndexResult("NOT_APPLICABLE", None, str(manifest_path), metrics)

    incremental_sources = None
    if not force_rebuild and index_ready and existing.get("completed") is True:
        incremental_sources = incremental_append_sources(existing.get("compatibility"), compatibility)
    incremental = incremental_sources is not None
    partial_resume = bool(
        not force_rebuild
        and index_ready
        and existing.get("completed") is False
        and existing.get("compatibility") == compatibility
    )
    completed_source_paths = {
        str(path)
        for path in existing.get("completed_sources", [])
        if partial_resume and str(path) in {str(source) for source in sources}
    }
    started = time.monotonic()
    connection = sqlite3.connect(index_path)
    events_parsed = 0
    events_indexed_delta = 0
    committed_indexed_delta = 0
    invalid_lines = 0
    bytes_read = 0
    decompression_count = 0
    full_scan_count = 0
    source_files_opened = 0
    try:
        initialize_schema(connection)
        if not incremental and not partial_resume:
            connection.execute("DELETE FROM aliases")
            connection.execute("DELETE FROM events")
            connection.commit()
        scan_sources = (
            incremental_sources
            if incremental_sources is not None
            else [(source, 0) for source in sources if str(source) not in completed_source_paths]
        )
        completed_bytes = 0
        for source_index, (source, start_offset) in enumerate(scan_sources, start=1):
            source_bytes = max(source.stat().st_size - start_offset, 0)
            source_files_opened += 1
            decompression_count += int(source.suffix in {".gz", ".zst"})
            full_scan_count += int(start_offset == 0)
            batch: list[tuple[dict[str, Any], int, int | None]] = []
            start_line = indexed_source_line_count(connection, source) if start_offset else 0
            iterator = iter_jsonl_events_from_offset(source, start_offset, start_line) if start_offset else iter_jsonl_events(source)
            for event, line_number, byte_offset, valid in iterator:
                events_parsed += 1
                if not valid:
                    invalid_lines += 1
                    continue
                batch.append((event, line_number, byte_offset))
                if len(batch) >= 1000:
                    events_indexed_delta += insert_batch(
                        connection,
                        source,
                        batch,
                        request_id_keys,
                        run_id_keys,
                        task_id_keys,
                        batch_id_keys,
                    )
                    if progress_callback:
                        progress_callback(
                            {
                                "source_file": str(source),
                                "source_files_completed": source_index - 1,
                                "source_file_count": len(scan_sources),
                                "processed_bytes": completed_bytes + offset_progress(batch, start_offset),
                                "events_parsed": events_parsed,
                                "events_indexed": events_indexed_delta,
                            }
                        )
                    batch.clear()
            if batch:
                events_indexed_delta += insert_batch(
                    connection,
                    source,
                    batch,
                    request_id_keys,
                    run_id_keys,
                    task_id_keys,
                    batch_id_keys,
                )
            connection.commit()
            committed_indexed_delta = events_indexed_delta
            completed_source_paths.add(str(source))
            completed_bytes += source_bytes
            bytes_read += source_bytes
            checkpoint_metrics = p2_build_metrics(
                sources=sources,
                scan_sources=scan_sources,
                events_parsed=events_parsed,
                events_indexed=int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
                events_indexed_delta=events_indexed_delta,
                invalid_lines=invalid_lines,
                bytes_read=bytes_read,
                decompression_count=decompression_count,
                full_scan_count=full_scan_count,
                source_files_opened=source_files_opened,
                started=started,
                index_reused=incremental or partial_resume,
                incrementally_updated=incremental,
                partial_resume=partial_resume,
            )
            write_json(
                manifest_path,
                {
                    "schema_version": P2_INDEX_SCHEMA_VERSION,
                    "completed": False,
                    "completed_sources": sorted(completed_source_paths),
                    "compatibility": compatibility,
                    "metrics": checkpoint_metrics,
                },
            )
            if progress_callback:
                progress_callback(
                    {
                        "source_file": str(source),
                        "source_files_completed": source_index,
                        "source_file_count": len(scan_sources),
                        "processed_bytes": completed_bytes,
                        "events_parsed": events_parsed,
                        "events_indexed": events_indexed_delta,
                    }
                )
        events_indexed_total = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        metrics = p2_build_metrics(
            sources=sources,
            scan_sources=scan_sources,
            events_parsed=events_parsed,
            events_indexed=events_indexed_total,
            events_indexed_delta=events_indexed_delta,
            invalid_lines=invalid_lines,
            bytes_read=bytes_read,
            decompression_count=decompression_count,
            full_scan_count=full_scan_count,
            source_files_opened=source_files_opened,
            started=started,
            index_reused=incremental or partial_resume,
            incrementally_updated=incremental,
            partial_resume=partial_resume,
        )
        payload = {
            "schema_version": P2_INDEX_SCHEMA_VERSION,
            "completed": True,
            "completed_sources": sorted(str(source) for source in sources),
            "compatibility": compatibility,
            "metrics": metrics,
        }
        write_json(manifest_path, payload)
        return P2IndexResult("PASS", str(index_path), str(manifest_path), metrics)
    except Exception:
        connection.rollback()
        events_indexed_delta = committed_indexed_delta
        events_indexed_total = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        write_json(
            manifest_path,
            {
                "schema_version": P2_INDEX_SCHEMA_VERSION,
                "completed": False,
                "completed_sources": sorted(completed_source_paths),
                "compatibility": compatibility,
                "metrics": p2_build_metrics(
                    sources=sources,
                    scan_sources=scan_sources if "scan_sources" in locals() else [],
                    events_parsed=events_parsed,
                    events_indexed=events_indexed_total,
                    events_indexed_delta=events_indexed_delta,
                    invalid_lines=invalid_lines,
                    bytes_read=bytes_read,
                    decompression_count=decompression_count,
                    full_scan_count=full_scan_count,
                    source_files_opened=source_files_opened,
                    started=started,
                    index_reused=incremental or partial_resume,
                    incrementally_updated=incremental,
                    partial_resume=partial_resume,
                ),
            },
        )
        raise
    finally:
        connection.close()


def p2_build_metrics(
    *,
    sources: list[Path],
    scan_sources: list[tuple[Path, int]],
    events_parsed: int,
    events_indexed: int,
    events_indexed_delta: int,
    invalid_lines: int,
    bytes_read: int,
    decompression_count: int,
    full_scan_count: int,
    source_files_opened: int,
    started: float,
    index_reused: bool,
    incrementally_updated: bool,
    partial_resume: bool,
) -> dict[str, Any]:
    return {
        "source_file_count": len(sources),
        "source_file_open_count": source_files_opened,
        "full_scan_count": full_scan_count,
        "incremental_scan_count": sum(1 for _, offset in scan_sources if offset > 0),
        "decompression_count": decompression_count,
        "JSON_parse_count": events_parsed,
        "bytes_read": bytes_read,
        "events_parsed": events_parsed,
        "events_indexed": events_indexed,
        "events_indexed_delta": events_indexed_delta,
        "invalid_lines": invalid_lines,
        "index_build_wall_s": time.monotonic() - started,
        "index_query_wall_s": 0.0,
        "index_reused": index_reused,
        "index_incrementally_updated": incrementally_updated,
        "index_partial_resume": partial_resume,
        "bytes_scanned_per_run": 0,
        "events_scanned_per_run": 0,
    }


def query_p2_events(
    index_path: str | Path | None,
    *,
    run_id: str | None,
    task_id: str | None,
    aliases: Iterable[str],
    batch_id: str | None = None,
    wall_start_ns: int | None = None,
    wall_end_ns: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not index_path or not Path(index_path).is_file():
        return [], {"index_query_wall_s": 0.0, "events_scanned_per_run": 0, "bytes_scanned_per_run": 0}
    started = time.monotonic()
    values = sorted({str(item) for item in aliases if str(item)})
    identity_clauses: list[str] = []
    parameters: list[Any] = []
    if run_id:
        identity_clauses.append("e.run_id = ?")
        parameters.append(run_id)
    if task_id:
        identity_clauses.append("e.task_id = ?")
        parameters.append(task_id)
    if batch_id:
        identity_clauses.append("e.batch_id = ?")
        parameters.append(batch_id)
    if values:
        placeholders = ",".join("?" for _ in values)
        identity_clauses.append(f"(e.run_id IS NULL AND e.task_id IS NULL AND a.alias_value IN ({placeholders}))")
        parameters.extend(values)
    if not identity_clauses and wall_start_ns is None and wall_end_ns is None:
        return [], {"index_query_wall_s": 0.0, "events_scanned_per_run": 0, "bytes_scanned_per_run": 0}
    clauses = [f"({' OR '.join(identity_clauses)})"] if identity_clauses else []
    if wall_start_ns is not None:
        clauses.append("e.wall_time_ns >= ?")
        parameters.append(int(wall_start_ns))
    if wall_end_ns is not None:
        clauses.append("e.wall_time_ns <= ?")
        parameters.append(int(wall_end_ns))
    sql = f"""
        SELECT DISTINCT e.event_json
        FROM events e
        LEFT JOIN aliases a ON a.event_rowid = e.id
        WHERE {' AND '.join(clauses)}
        ORDER BY e.monotonic_time_ns, e.source_file, e.line_number
    """
    with sqlite3.connect(Path(index_path)) as connection:
        rows = [json.loads(row[0]) for row in connection.execute(sql, parameters)]
    return rows, {
        "index_query_wall_s": time.monotonic() - started,
        "events_scanned_per_run": len(rows),
        "bytes_scanned_per_run": 0,
    }


def event_aliases(events: Iterable[dict[str, Any]], cfg: AnalysisConfig) -> set[str]:
    keys = tuple(sorted({key for profile in available_profiles(cfg).values() for key in profile.request_id_keys}))
    return {value for event in events for value in values_for_keys(event, keys)}


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            source_file TEXT NOT NULL,
            line_number INTEGER NOT NULL,
            byte_offset INTEGER,
            event_id TEXT,
            source TEXT,
            event_type TEXT,
            run_id TEXT,
            task_id TEXT,
            batch_id TEXT,
            wall_time_ns INTEGER,
            monotonic_time_ns INTEGER,
            sequence INTEGER,
            event_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aliases (
            event_rowid INTEGER NOT NULL,
            alias_type TEXT NOT NULL,
            alias_value TEXT NOT NULL,
            FOREIGN KEY(event_rowid) REFERENCES events(id)
        );
        CREATE INDEX IF NOT EXISTS idx_p2_run ON events(run_id);
        CREATE INDEX IF NOT EXISTS idx_p2_task ON events(task_id);
        CREATE INDEX IF NOT EXISTS idx_p2_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_p2_wall ON events(wall_time_ns);
        CREATE INDEX IF NOT EXISTS idx_p2_mono ON events(monotonic_time_ns);
        CREATE INDEX IF NOT EXISTS idx_p2_alias ON aliases(alias_value, alias_type);
        """
    )
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(events)")}
    if "batch_id" not in columns:
        connection.execute("ALTER TABLE events ADD COLUMN batch_id TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p2_batch ON events(batch_id)")


def insert_batch(
    connection: sqlite3.Connection,
    source: Path,
    batch: list[tuple[dict[str, Any], int, int | None]],
    request_id_keys: list[str],
    run_id_keys: list[str],
    task_id_keys: list[str],
    batch_id_keys: list[str],
) -> int:
    count = 0
    for event, line_number, byte_offset in batch:
        event = dict(event)
        event.setdefault("_source_path", str(source))
        event.setdefault("_source_line", line_number)
        event.setdefault("_source_offset", byte_offset)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        run_id = first_value(event, payload, tuple(run_id_keys))
        task_id = first_value(event, payload, tuple(task_id_keys))
        batch_id = first_value(event, payload, tuple(batch_id_keys))
        cursor = connection.execute(
            """
            INSERT INTO events(source_file,line_number,byte_offset,event_id,source,event_type,run_id,task_id,batch_id,wall_time_ns,monotonic_time_ns,sequence,event_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(source),
                line_number,
                byte_offset,
                text_value(event.get("event_id")),
                text_value(event.get("source")),
                text_value(event.get("event_type")),
                run_id,
                task_id,
                batch_id,
                int_value(event.get("wall_time_ns")),
                int_value(event.get("monotonic_time_ns")),
                int_value(event.get("sequence") or event.get("seq")),
                json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )
        event_rowid = int(cursor.lastrowid)
        alias_rows = []
        for key in request_id_keys:
            for container in (event, payload):
                value = text_value(container.get(key))
                if value:
                    alias_rows.append((event_rowid, key, value))
        if alias_rows:
            connection.executemany("INSERT INTO aliases(event_rowid,alias_type,alias_value) VALUES(?,?,?)", sorted(set(alias_rows)))
        count += 1
    return count


def iter_jsonl_events(path: Path) -> Iterator[tuple[dict[str, Any], int, int | None, bool]]:
    if path.suffix not in {".gz", ".zst"}:
        with path.open("rb") as handle:
            line_number = 0
            while True:
                offset = handle.tell()
                raw = handle.readline()
                if not raw:
                    return
                line_number += 1
                event, _, _, valid = parse_line(raw.decode("utf-8", errors="replace"))
                yield event, line_number, offset, valid
    else:
        with open_maybe_compressed(path, "rt") as handle:
            for line_number, line in enumerate(handle, start=1):
                event, _, _, valid = parse_line(line)
                yield event, line_number, None, valid


def iter_jsonl_events_from_offset(
    path: Path,
    start_offset: int,
    start_line_number: int,
) -> Iterator[tuple[dict[str, Any], int, int | None, bool]]:
    if path.suffix in {".gz", ".zst"}:
        raise ValueError("incremental byte-offset indexing is unsupported for compressed P2 streams")
    with path.open("rb") as handle:
        handle.seek(start_offset)
        line_number = start_line_number
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                return
            line_number += 1
            event, _, _, valid = parse_line(raw.decode("utf-8", errors="replace"))
            yield event, line_number, offset, valid


def parse_line(line: str) -> tuple[dict[str, Any], int, int | None, bool]:
    if not line.strip():
        return {}, 0, None, False
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        return {}, 0, None, False
    return (item, 0, None, True) if isinstance(item, dict) else ({}, 0, None, False)


def offset_progress(batch: list[tuple[dict[str, Any], int, int | None]], start_offset: int) -> int:
    offsets = [offset for _, _, offset in batch if offset is not None]
    return max(max(offsets) - start_offset, 0) if offsets else 0


def source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    with path.open("rb") as handle:
        head = handle.read(64 * 1024)
        tail = head
        if stat.st_size > len(head):
            handle.seek(max(stat.st_size - 64 * 1024, 0))
            tail = handle.read(64 * 1024)
    digest = hashlib.sha256()
    digest.update(head)
    digest.update(tail)
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "head_sha256": hashlib.sha256(head).hexdigest(),
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
        "tail_size_bytes": len(tail),
        "ends_with_newline": stat.st_size == 0 or tail.endswith(b"\n"),
        "boundary_sha256": digest.hexdigest(),
    }


def incremental_append_sources(
    previous_compatibility: Any,
    current_compatibility: dict[str, Any],
) -> list[tuple[Path, int]] | None:
    if not isinstance(previous_compatibility, dict):
        return None
    if previous_compatibility.get("schema_version") != current_compatibility.get("schema_version"):
        return None
    if previous_compatibility.get("request_id_keys") != current_compatibility.get("request_id_keys"):
        return None
    for key in ("run_id_keys", "task_id_keys", "batch_id_keys"):
        if previous_compatibility.get(key) != current_compatibility.get(key):
            return None
    previous_sources = {
        str(item.get("path")): item
        for item in previous_compatibility.get("sources", [])
        if isinstance(item, dict) and item.get("path")
    }
    current_sources = {
        str(item.get("path")): item
        for item in current_compatibility.get("sources", [])
        if isinstance(item, dict) and item.get("path")
    }
    if not previous_sources or not set(previous_sources).issubset(current_sources):
        return None
    scans: list[tuple[Path, int]] = []
    for path_text, current in current_sources.items():
        path = Path(path_text)
        previous = previous_sources.get(path_text)
        if previous is None:
            scans.append((path, 0))
            continue
        if previous == current:
            continue
        old_size = int(previous.get("size_bytes") or 0)
        new_size = int(current.get("size_bytes") or 0)
        if path.suffix in {".gz", ".zst"} or new_size <= old_size or not previous.get("ends_with_newline"):
            return None
        if not source_prefix_matches(path, previous):
            return None
        scans.append((path, old_size))
    return scans


def source_prefix_matches(path: Path, previous: dict[str, Any]) -> bool:
    old_size = int(previous.get("size_bytes") or 0)
    tail_size = int(previous.get("tail_size_bytes") or 0)
    expected_head = previous.get("head_sha256")
    expected_tail = previous.get("tail_sha256")
    if expected_head is None or expected_tail is None or tail_size < 0 or tail_size > old_size:
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(min(64 * 1024, old_size))
            handle.seek(max(old_size - tail_size, 0))
            tail = handle.read(tail_size)
    except OSError:
        return False
    return hashlib.sha256(head).hexdigest() == expected_head and hashlib.sha256(tail).hexdigest() == expected_tail


def indexed_source_line_count(connection: sqlite3.Connection, source: Path) -> int:
    row = connection.execute("SELECT MAX(line_number) FROM events WHERE source_file = ?", (str(source),)).fetchone()
    return int(row[0] or 0)


def sqlite_index_ready(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as connection:
            return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except (sqlite3.Error, OSError, TypeError):
        return False


def empty_metrics(*, index_reused: bool) -> dict[str, Any]:
    return {
        "source_file_count": 0,
        "source_file_open_count": 0,
        "full_scan_count": 0,
        "incremental_scan_count": 0,
        "decompression_count": 0,
        "JSON_parse_count": 0,
        "bytes_read": 0,
        "events_parsed": 0,
        "events_indexed": 0,
        "events_indexed_delta": 0,
        "index_build_wall_s": 0.0,
        "index_query_wall_s": 0.0,
        "index_reused": index_reused,
        "index_incrementally_updated": False,
        "index_partial_resume": False,
        "bytes_scanned_per_run": 0,
        "events_scanned_per_run": 0,
    }


def first_value(event: dict[str, Any], payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for container in (event, payload):
        for key in keys:
            value = text_value(container.get(key))
            if value:
                return value
    return None


def text_value(value: Any) -> str | None:
    return None if value in {None, ""} else str(value)


def int_value(value: Any) -> int | None:
    try:
        return None if value in {None, ""} else int(value)
    except (TypeError, ValueError):
        return None
