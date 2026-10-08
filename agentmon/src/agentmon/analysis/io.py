from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator

from agentmon.paths import chmod_private_file, make_private_dir, open_private_text, write_private_text


SCHEMA_VERSION = "1.0"
CSV_GZ_SUFFIX = ".csv.gz"


def ensure_dir(path: str | Path) -> Path:
    return make_private_dir(Path(path))


def write_json(path: str | Path, payload: Any) -> None:
    write_private_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def read_json(path: str | Path, default: Any = None) -> Any:
    target = Path(path)
    if not target.exists():
        if default is not None:
            return default
        raise FileNotFoundError(target)
    return json.loads(target.read_text(encoding="utf-8"))


def write_markdown(path: str | Path, text: str) -> None:
    write_private_text(path, text.rstrip() + "\n")


def write_plain_csv(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    """Write a small interoperability/audit CSV with private permissions."""
    target = Path(path)
    ensure_dir(target.parent)
    normalized = normalize_rows(rows)
    fields = sorted({str(key) for row in normalized for key in row})
    with open_private_text(target, newline="") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in normalized:
                writer.writerow({key: csv_scalar(row.get(key)) for key in fields})
    chmod_private_file(target)
    return target


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_source_hash(paths: Iterable[str | Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(str(Path(item)) for item in paths):
        target = Path(path)
        digest.update(path.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        if target.is_file():
            digest.update(file_sha256(target).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def stable_metadata_hash(paths: Iterable[str | Path], *, root: str | Path | None = None) -> str:
    """Hash file identity and metadata without reading file contents."""
    digest = hashlib.sha256()
    base = Path(root).resolve() if root is not None else None
    for raw in sorted((Path(item).resolve() for item in paths), key=str):
        try:
            stat = raw.stat()
            identity = str(raw.relative_to(base)) if base is not None and raw.is_relative_to(base) else str(raw)
            digest.update(identity.encode("utf-8", errors="replace"))
            digest.update(b"\0")
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(b"\n")
        except OSError:
            digest.update(str(raw).encode("utf-8", errors="replace"))
            digest.update(b"\0missing\n")
    return digest.hexdigest()


def line_count(path: str | Path) -> int:
    count = 0
    with open_maybe_compressed(path, "rt") as handle:
        for _ in handle:
            count += 1
    return count


def open_maybe_compressed(path: str | Path, mode: str = "rt"):
    target = Path(path)
    if target.suffix == ".gz":
        return gzip.open(target, mode, encoding="utf-8", errors="replace")  # type: ignore[call-overload]
    if target.suffix == ".zst":
        try:
            import zstandard as zstd  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError("zstd input requires the optional zstandard package") from exc
        raw = target.open("rb")
        reader = zstd.ZstdDecompressor().stream_reader(raw)
        if "b" in mode:
            return reader
        import io

        return io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
    return target.open(mode, encoding="utf-8", errors="replace") if "b" not in mode else target.open(mode)


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    with open_maybe_compressed(path, "rt") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                item["_source_path"] = str(Path(path))
                item["_source_line"] = line_no
                yield item


def inspect_jsonl(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    total_lines = 0
    valid_lines = 0
    invalid_lines = 0
    duplicate_event_ids = 0
    sequence_regressions = 0
    timestamp_regressions = 0
    missing_required = 0
    last_event_seq: int | None = None
    last_mono: int | None = None
    seen_event_ids: set[str] = set()
    error: str | None = None
    last_line_had_newline = True
    try:
        with open_maybe_compressed(target, "rt") as handle:
            for line_no, line in enumerate(handle, start=1):
                total_lines += 1
                last_line_had_newline = line.endswith("\n")
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
                valid_lines += 1
                event_id = item.get("event_id")
                if isinstance(event_id, str):
                    if event_id in seen_event_ids:
                        duplicate_event_ids += 1
                    seen_event_ids.add(event_id)
                if not item.get("run_id") or not item.get("event_type"):
                    missing_required += 1
                seq = integer_or_none(item.get("sequence") or item.get("seq"))
                if seq is not None and last_event_seq is not None and seq < last_event_seq:
                    sequence_regressions += 1
                if seq is not None:
                    last_event_seq = seq
                mono = integer_or_none(item.get("monotonic_time_ns"))
                if mono is not None and last_mono is not None and mono < last_mono:
                    timestamp_regressions += 1
                if mono is not None:
                    last_mono = mono
    except Exception as exc:  # noqa: BLE001 - surfaced as data quality evidence
        error = f"{type(exc).__name__}: {exc}"
    return {
        "path": str(target),
        "size_bytes": target.stat().st_size if target.exists() else None,
        "sha256": file_sha256(target) if target.is_file() else None,
        "total_lines": total_lines,
        "valid_lines": valid_lines,
        "invalid_lines": invalid_lines,
        "duplicate_event_ids": duplicate_event_ids,
        "sequence_regressions": sequence_regressions,
        "timestamp_regressions": timestamp_regressions,
        "missing_required_fields": missing_required,
        "last_line_missing_newline": bool(total_lines and not last_line_had_newline),
        "status": "FAIL" if error or invalid_lines else "PASS",
        "error": error,
    }


def write_table(
    output_dir: str | Path,
    name: str,
    rows: list[dict[str, Any]],
    *,
    output_format: str = "parquet",
) -> dict[str, Any]:
    output = ensure_dir(output_dir)
    normalized = normalize_rows(rows)
    if output_format == "parquet":
        try:
            return write_parquet(output, name, normalized)
        except ModuleNotFoundError:
            artifact = write_csv_gz(output / f"{name}{CSV_GZ_SUFFIX}", normalized)
            return {
                "format": "csv.gz",
                "path": str(artifact),
                "row_count": len(normalized),
                "parquet_status": "unavailable",
                "parquet_missing_reason": "pyarrow_not_installed",
            }
    artifact = write_csv_gz(output / f"{name}{CSV_GZ_SUFFIX}", normalized)
    return {"format": "csv.gz", "path": str(artifact), "row_count": len(normalized)}


def write_parquet(output: Path, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = output / f"{name}.parquet"
    table = pa.Table.from_pylist(rows) if rows else pa.table({"schema_version": pa.array([], type=pa.string())})
    pq.write_table(table, path)
    chmod_private_file(path)
    return {"format": "parquet", "path": str(path), "row_count": len(rows), "parquet_status": "complete"}


def write_csv_gz(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with gzip.open(target, "wt", encoding="utf-8", newline="") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: csv_scalar(row.get(key)) for key in fields})
    chmod_private_file(target)
    return target


def read_table(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if target.suffix == ".parquet":
        import pyarrow.parquet as pq

        return [dict(row) for row in pq.read_table(target).to_pylist()]
    if target.name.endswith(CSV_GZ_SUFFIX):
        with gzip.open(target, "rt", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    with target.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_bundle_table(bundle_root: str | Path, section: str, name: str) -> list[dict[str, Any]]:
    section_path = Path(bundle_root) / section
    for suffix in (".parquet", CSV_GZ_SUFFIX, ".csv"):
        path = section_path / f"{name}{suffix}"
        if path.exists():
            return read_table(path)
    return []


def write_table_manifest(path: str | Path, tables: dict[str, dict[str, Any]]) -> None:
    enriched: dict[str, dict[str, Any]] = {}
    for name, artifact in tables.items():
        item = dict(artifact)
        artifact_path = Path(str(item.get("path") or ""))
        if artifact_path.is_file():
            stat = artifact_path.stat()
            item["size_bytes"] = stat.st_size
            item["mtime_ns"] = stat.st_mtime_ns
        enriched[name] = item
    write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "tables": enriched,
        },
    )


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {str(key): normalize_scalar(value) for key, value in sorted(row.items())}
        for row in rows
    ]


def normalize_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def csv_scalar(value: Any) -> str:
    value = normalize_scalar(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"na", "n/a", "none", "null", "nan"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def parse_json_text(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def hardlink_or_copy(src: str | Path, dst: str | Path) -> None:
    source = Path(src)
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if target.exists():
            target.unlink()
        os.link(source, target)
    except OSError:
        import shutil

        shutil.copy2(source, target)
    chmod_private_file(target)
