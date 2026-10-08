from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


def _safe_number(value: Any) -> str:
    if value in (None, ""):
        return "N/A"
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return str(value)


def render_capsule(
    *,
    regression: Iterable[Mapping[str, Any]],
    audit: Iterable[Mapping[str, Any]],
    tournament: Iterable[Mapping[str, Any]],
    provider_plan: Mapping[str, Any],
    output_files: Iterable[str],
    failures: Iterable[str] = (),
) -> str:
    regression = list(regression); audit = list(audit); tournament = list(tournament)
    statuses = Counter(str(row.get("status")) for row in regression)
    classes = Counter(str(row.get("classification")) for row in audit)
    lines = [
        "REAL-OFFLINE-PIPELINE v1 CAPSULE",
        "Representation-level results only; no logits, next-token, generation, latency, or reuse-safety claim.",
        "",
        "REGRESSION_GATE " + " ".join(f"{key}={value}" for key, value in sorted(statuses.items())),
    ]
    for row in regression:
        if row.get("status") in {"FAIL", "NUMERIC_WARN", "SEMANTIC_SCOPE_DIFFERENCE"}:
            lines.append(
                "REG " + " ".join(
                    str(value) for value in (
                        row.get("status"), row.get("experiment"), row.get("model"), row.get("condition"),
                        row.get("state"), row.get("scope"), _safe_number(row.get("historical_value")),
                        _safe_number(row.get("new_value")), _safe_number(row.get("abs_diff")),
                    )
                )
            )
    lines.extend(["", "PAPER_CAPABILITY " + " ".join(f"{key}={value}" for key, value in sorted(classes.items()))])
    for row in audit:
        lines.append(f"PAPER {row['paper_id']} {row['paper_name']} :: {row['classification']} :: component={row.get('component') or '-'} :: missing={row.get('missing_providers') or '-'}")
    lines.extend(["", "TOURNAMENT_TOP"])
    valid = [row for row in tournament if row.get("residual_energy_explained") not in (None, "")]
    valid.sort(key=lambda row: float(row["residual_energy_explained"]), reverse=True)
    for row in valid[:40]:
        lines.append(
            f"{row.get('method')}|{row.get('variant')}|{row.get('model')}|{row.get('condition')}|{row.get('state')}|"
            f"scope={row.get('scope')}|R={_safe_number(row.get('residual_energy_explained'))}|class={row.get('method_class','PAPER-COMPONENT')}|target={row.get('oracle_target_usage')}"
        )
    lines.extend(["", "NEXT_NPU_SIGNALS"])
    for provider in provider_plan.get("providers", []):
        if provider.get("required_next_npu"):
            lines.append(f"{provider['signal']} :: {provider['granularity']} :: {', '.join(provider['papers'])}")
    lines.extend(["", "OUTPUT_FILES", *sorted(output_files)])
    failures = list(failures)
    lines.extend(["", "FAILURES", *(failures or ["none"])])
    return "\n".join(lines) + "\n"


def write_bounded_capsule(path: str | Path, text: str, max_bytes: int = 45000) -> int:
    if max_bytes < 256:
        raise ValueError("capsule byte limit must be at least 256")
    payload = text.encode("utf-8")
    if len(payload) > max_bytes:
        marker = b"\n[TRUNCATED_TO_BYTE_LIMIT]\n"
        prefix = payload[: max_bytes - len(marker)]
        while prefix and (prefix[-1] & 0xC0) == 0x80:
            prefix = prefix[:-1]
        payload = prefix.decode("utf-8", errors="ignore").encode("utf-8") + marker
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    if target.stat().st_size > max_bytes:
        raise AssertionError("capsule exceeds configured byte limit")
    return target.stat().st_size

