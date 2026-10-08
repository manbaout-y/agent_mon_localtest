from __future__ import annotations

import csv
import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .collector import TraceCollector, TraceLevel
from .forensic import FORENSIC_FIELDS
from .schema import TRACE_SCHEMA_VERSION, trace_schema_document


def _json_line(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"


class ObservabilityWriter:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(_json_line(row))
        os.replace(temporary, path)

    @staticmethod
    def _write_gzip_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            for row in rows:
                handle.write(_json_line(row))
        os.replace(temporary, path)

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]], fields: Iterable[str] | None = None) -> None:
        fields = list(fields or dict.fromkeys(key for row in rows for key in row))
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)

    def write(self, collector: TraceCollector) -> dict[str, Any]:
        if collector.level is TraceLevel.OFF:
            return {"level": TraceLevel.OFF.value, "summary_event_count": 0, "detail_event_count": 0, "files": []}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        summary = collector.summary_events()
        detail = collector.detail_events()
        costs = collector.cost_rows()
        forensic = collector.forensic_rows()
        schema_path = self.output_dir / "trace_schema.json"
        summary_path = self.output_dir / "trace_summary.jsonl"
        cost_path = self.output_dir / "cost_accounting.csv"
        forensic_path = self.output_dir / "forensic_summary.csv"
        manifest_path = self.output_dir / "run_manifest.json"
        self._write_json(schema_path, trace_schema_document())
        self._write_jsonl(summary_path, summary)
        self._write_csv(cost_path, costs)
        self._write_csv(forensic_path, forensic, FORENSIC_FIELDS)
        files = [schema_path, summary_path, cost_path, forensic_path]
        if collector.level is TraceLevel.DETAIL:
            detail_path = self.output_dir / "trace_detail.jsonl.gz"
            self._write_gzip_jsonl(detail_path, detail)
            files.append(detail_path)
        manifest = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": collector.run_id,
            "trace_level": collector.level.value,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "summary_event_count": len(summary),
            "detail_event_count": len(detail),
            "cost_record_count": len(costs),
            "forensic_record_count": len(forensic),
            "duration_semantics": "null unless a real measured duration is supplied; no latency estimation",
            "files": [path.name for path in files] + [manifest_path.name],
        }
        self._write_json(manifest_path, manifest)
        files.append(manifest_path)
        return {**manifest, "files": [str(path) for path in files]}
