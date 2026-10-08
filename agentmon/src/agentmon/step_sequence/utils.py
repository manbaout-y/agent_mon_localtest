from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def text_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def list_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def run_id_from_events(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        value = event.get("run_id")
        if value not in {None, ""}:
            return str(value)
    return None


def sort_time(row: dict[str, Any]) -> int:
    value = row.get("start_mono_ns") or row.get("end_mono_ns")
    return int(value) if isinstance(value, int) else 2**63 - 1


def ns_to_s(value: Any) -> float | None:
    if not isinstance(value, int):
        return None
    return value / 1_000_000_000


def ns_delta(start: Any, end: Any) -> int | None:
    if isinstance(start, int) and isinstance(end, int):
        return end - start
    return None


def first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def first_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def min_int(values: Any) -> int | None:
    nums = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return min(nums) if nums else None


def max_int(values: Any) -> int | None:
    nums = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return max(nums) if nums else None


def integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def parse_timestamp_seconds(value: Any) -> float | None:
    number = number_or_none(value)
    if number is not None:
        return float(number)
    if not isinstance(value, str) or not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def byte_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="replace"))
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8", errors="replace"))


def cached_tokens_from_usage(usage: dict[str, Any]) -> int | None:
    cached = integer_or_none(usage.get("cached_tokens"))
    if cached is not None:
        return cached
    details = as_dict(usage.get("prompt_tokens_details"))
    return integer_or_none(details.get("cached_tokens"))


def sum_present(values: Any) -> int | None:
    total = 0
    found = False
    for value in values:
        number = integer_or_none(value)
        if number is not None:
            total += number
            found = True
    return total if found else None


def sum_numeric(values: Any) -> int | float | None:
    total: int | float = 0
    found = False
    for value in values:
        number = number_or_none(value)
        if number is not None:
            total += number
            found = True
    return total if found else None


def mean_present(values: Any) -> float | None:
    clean = [number for value in values if (number := number_or_none(value)) is not None]
    return sum(clean) / len(clean) if clean else None


def max_present(values: Any) -> float | int | None:
    clean = [number for value in values if (number := number_or_none(value)) is not None]
    return max(clean) if clean else None


def min_present(values: Any) -> float | int | None:
    clean = [number for value in values if (number := number_or_none(value)) is not None]
    return min(clean) if clean else None


def count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        label = str(value) if value not in {None, ""} else "missing"
        counts[label] = counts.get(label, 0) + 1
    return counts


def ratio_or_none(numerator: Any, denominator: Any) -> float | None:
    top = number_or_none(numerator)
    bottom = number_or_none(denominator)
    if top is None or bottom in {None, 0}:
        return None
    return float(top) / float(bottom)


def nonzero(value: Any) -> bool:
    number = integer_or_none(value)
    return number is not None and number != 0


def fmt_s(value: Any) -> str:
    number = number_or_none(value)
    return "N/A" if number is None else f"{number:.3f}s"


def fmt_na(value: Any) -> str:
    return "N/A" if value is None else str(value)


def fmt_pct(value: Any) -> str:
    number = number_or_none(value)
    return "N/A" if number is None else f"{number * 100:.2f}%"


def fmt_pct_value(value: Any) -> str:
    number = number_or_none(value)
    return "N/A" if number is None else f"{number:.2f}%"


def fmt_bytes(value: Any) -> str:
    number = number_or_none(value)
    if number is None:
        return "N/A"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(number)
    unit = units[0]
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            break
        size /= 1024
    return f"{size:.2f} {unit}"
