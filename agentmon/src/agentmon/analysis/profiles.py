from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from typing import Any, Iterable

from agentmon.analysis.config import AnalysisConfig


PROFILE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class SchemaProfile:
    name: str
    source_signals: tuple[str, ...]
    event_type_signals: tuple[str, ...]
    event_prefix_signals: tuple[str, ...]
    model_request_event_types: tuple[str, ...]
    p1_event_types: tuple[str, ...]
    p2_event_prefixes: tuple[str, ...]
    p2_event_types: tuple[str, ...]
    request_id_keys: tuple[str, ...]
    run_id_keys: tuple[str, ...]
    task_id_keys: tuple[str, ...]
    batch_id_keys: tuple[str, ...]
    p2_eligibility_fields: tuple[str, ...]


from agentmon.analysis.domain_profiles import builtin_agentmon_profiles


BUILTIN_PROFILES: dict[str, SchemaProfile] = builtin_agentmon_profiles()


def available_profiles(cfg: AnalysisConfig) -> dict[str, SchemaProfile]:
    profiles = dict(BUILTIN_PROFILES)
    for name, raw in cfg.profile_detection.definitions.items():
        if not isinstance(raw, dict):
            continue
        base = profiles.get(str(name), BUILTIN_PROFILES["agentmon"])
        profiles[str(name)] = SchemaProfile(
            name=str(name),
            source_signals=tuple_text(raw.get("source_signals"), base.source_signals),
            event_type_signals=tuple_text(raw.get("event_type_signals"), base.event_type_signals),
            event_prefix_signals=tuple_text(raw.get("event_prefix_signals"), base.event_prefix_signals),
            model_request_event_types=tuple_text(raw.get("model_request_event_types"), base.model_request_event_types),
            p1_event_types=tuple_text(raw.get("p1_event_types"), base.p1_event_types),
            p2_event_prefixes=tuple_text(raw.get("p2_event_prefixes"), base.p2_event_prefixes),
            p2_event_types=tuple_text(raw.get("p2_event_types"), base.p2_event_types),
            request_id_keys=tuple_text(raw.get("request_id_keys"), base.request_id_keys),
            run_id_keys=tuple_text(raw.get("run_id_keys"), base.run_id_keys),
            task_id_keys=tuple_text(raw.get("task_id_keys"), base.task_id_keys),
            batch_id_keys=tuple_text(raw.get("batch_id_keys"), base.batch_id_keys),
            p2_eligibility_fields=tuple_text(raw.get("p2_eligibility_fields"), base.p2_eligibility_fields),
        )
    return profiles


def resolve_profile(cfg: AnalysisConfig, events: Iterable[dict[str, Any]], *, run_id: str) -> tuple[SchemaProfile | None, dict[str, Any]]:
    rows = list(events)
    profiles = available_profiles(cfg)
    if cfg.profile != "auto":
        selected = profiles.get(cfg.profile)
        return selected, {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "run_id": run_id,
            "requested_profile": cfg.profile,
            "detected_profile": selected.name if selected else "UNKNOWN",
            "status": "PASS" if selected else "AMBIGUOUS",
            "confidence": 1.0 if selected else 0.0,
            "detection_signals": json.dumps(["explicit_cli_or_config"], sort_keys=True),
            "fallback_reason": None if selected else "unknown_explicit_profile",
            "schema_versions": json.dumps(sorted(schema_versions(rows))),
        }

    source_counts = Counter(str(row.get("source") or "unknown") for row in rows)
    type_counts = Counter(str(row.get("event_type") or "unknown") for row in rows)
    scored: list[tuple[float, int, SchemaProfile, list[str]]] = []
    for profile in profiles.values():
        signals: list[str] = []
        matches = 0
        opportunities = max(len(profile.source_signals) + len(profile.event_type_signals) + len(profile.event_prefix_signals), 1)
        for source in profile.source_signals:
            if source_counts.get(source):
                matches += 1
                signals.append(f"source:{source}={source_counts[source]}")
        for event_type in profile.event_type_signals:
            if type_counts.get(event_type):
                matches += 1
                signals.append(f"event_type:{event_type}={type_counts[event_type]}")
        for prefix in profile.event_prefix_signals:
            count = sum(value for key, value in type_counts.items() if key.startswith(prefix))
            if count:
                matches += 1
                signals.append(f"event_prefix:{prefix}={count}")
        scored.append((matches / opportunities, matches, profile, signals))
    scored.sort(key=lambda item: (item[0], item[1], item[2].name), reverse=True)
    best_score, best_matches, best_profile, signals = scored[0] if scored else (0.0, 0, BUILTIN_PROFILES["agentmon"], [])
    tied = [item for item in scored if item[0] == best_score and item[1] == best_matches]
    ambiguous = best_matches == 0 or best_score < cfg.profile_detection.auto_min_confidence or len(tied) > 1
    selected = None if ambiguous else best_profile
    return selected, {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "run_id": run_id,
        "requested_profile": "auto",
        "detected_profile": selected.name if selected else "AMBIGUOUS",
        "status": "AMBIGUOUS" if ambiguous else "PASS",
        "confidence": best_score,
        "detection_signals": json.dumps(signals, sort_keys=True),
        "fallback_reason": "insufficient_or_tied_signals" if ambiguous else None,
        "schema_versions": json.dumps(sorted(schema_versions(rows))),
    }


def is_event_type(event_type: str, exact: tuple[str, ...], prefixes: tuple[str, ...] = ()) -> bool:
    return event_type in exact or any(event_type.startswith(prefix) for prefix in prefixes)


def values_for_keys(event: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    values: list[str] = []
    for source in (event, payload):
        for key in keys:
            value = source.get(key)
            if value not in {None, ""} and str(value) not in values:
                values.append(str(value))
    return values


def schema_versions(events: Iterable[dict[str, Any]]) -> set[str]:
    return {str(event.get("schema_version") or "unknown") for event in events}


def tuple_text(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return default
