from __future__ import annotations

from collections import Counter
from typing import Any

from agentmon.optimizer.harness.schemas import FORBIDDEN_CONCLUSIONS, SCHEMA_VERSION, UNKNOWN, UNRESOLVED


def audit_decision_timeline(decision_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in decision_rows:
        by_strategy.setdefault(str(row.get("strategy") or UNKNOWN), []).append(row)
    strategy_audits = [
        audit_strategy(name, sorted(rows, key=lambda item: int(item.get("snapshot_index") or 0)))
        for name, rows in sorted(by_strategy.items())
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "result_type": "agentmon.optimizer_experiment.risk_audit",
        "audit_mode": "offline_advice_replay",
        "strategy_audits": strategy_audits,
        "global_limitations": [
            "Advice replay does not prove online runtime benefit.",
            "would_defer and would_limit are inferred from decision output and are not enforced actions.",
            "Starvation risk is unresolved without a policy threshold and enforced queue history.",
            *FORBIDDEN_CONCLUSIONS,
        ],
    }


def audit_strategy(strategy: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    pressures = [str(row.get("admission_pressure") or UNKNOWN) for row in rows]
    pressure_transitions = sum(
        1 for previous, current in zip(pressures, pressures[1:]) if previous != current
    )
    alternating_patterns = [
        {
            "start_snapshot_index": int(rows[index].get("snapshot_index") or 0),
            "pattern": [pressures[index], pressures[index + 1], pressures[index + 2]],
        }
        for index in range(0, max(0, len(pressures) - 2))
        if pressures[index] == pressures[index + 2] and pressures[index] != pressures[index + 1]
    ]
    risk_flags = Counter(
        flag
        for row in rows
        for flag in str(row.get("risk_flags") or "").split(";")
        if flag
    )
    would_defer = [bool(row.get("would_defer")) for row in rows]
    would_limit = [bool(row.get("would_limit")) for row in rows]
    would_relax = [bool(row.get("would_relax")) for row in rows]
    return {
        "strategy": strategy,
        "decision_count": len(rows),
        "pressure_counts": dict(Counter(pressures)),
        "pressure_transition_count": pressure_transitions,
        "alternating_pressure_patterns": alternating_patterns,
        "oscillation_status": (
            "ALTERNATING_PATTERN_OBSERVED"
            if alternating_patterns
            else "PRESSURE_TRANSITIONS_OBSERVED"
            if pressure_transitions
            else "NO_PRESSURE_TRANSITIONS_OBSERVED"
        ),
        "would_defer_count": sum(would_defer),
        "would_limit_count": sum(would_limit),
        "would_relax_count": sum(would_relax),
        "max_consecutive_would_defer_snapshots": max_consecutive(would_defer),
        "max_consecutive_would_limit_snapshots": max_consecutive(would_limit),
        "risk_flag_counts": dict(sorted(risk_flags.items())),
        "starvation_risk_status": starvation_status(would_defer, rows),
        "over_limit_status": over_limit_status(would_limit),
    }


def starvation_status(would_defer: list[bool], rows: list[dict[str, Any]]) -> str:
    if not rows:
        return UNKNOWN
    if all(would_defer):
        return "PERSISTENT_WOULD_DEFER_OBSERVED"
    if any(would_defer):
        return UNRESOLVED
    return "NO_WOULD_DEFER_OBSERVED"


def over_limit_status(would_limit: list[bool]) -> str:
    if not would_limit:
        return UNKNOWN
    if all(would_limit):
        return "PERSISTENT_WOULD_LIMIT_OBSERVED"
    if any(would_limit):
        return "INTERMITTENT_WOULD_LIMIT_OBSERVED"
    return "NO_WOULD_LIMIT_OBSERVED"


def max_consecutive(values: list[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
