from __future__ import annotations

import csv
import json
from pathlib import Path


def load_records(path: str | Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, dict):
        containers = [k for k in ("records", "data", "items") if isinstance(value.get(k), list)]
        if len(containers) > 1:
            raise ValueError("ambiguous input record-list container")
        value = value[containers[0]] if containers else [value]
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise ValueError("input must contain a JSON record, record list or JSONL objects")
    return value


def merge_sidecar(records: list[dict], sidecar: list[dict]) -> list[dict]:
    merged = [dict(r) for r in records]
    indices = set()
    for row in sidecar:
        index = row["record_index"]
        if type(index) is not int or not 0 <= index < len(records) or index in indices:
            raise ValueError("sidecar record_index must be unique and in input bounds")
        indices.add(index)
        existing = merged[index].get("research", {})
        overlap = set(existing) & set(row["research"])
        if any(existing[k] != row["research"][k] for k in overlap):
            raise ValueError(f"sidecar conflicts with existing research fields at record {index}")
        merged[index]["research"] = {**existing, **row["research"]}
    return merged


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list, tuple))
                                 else "" if v is None else v) for k, v in row.items() if k in fields})
