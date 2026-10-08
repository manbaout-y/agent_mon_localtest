"""Content-free, evidence-only Day 2 joins using the existing P2 envelope.

This module installs no hook. Old P2 quality labels alone never prove a full
API -> EngineCore -> Scheduler chain. No time-nearest matching is performed.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from agentmon.adapters.inference.vllm_018.p2_index import is_p2_event, p2_alias_ids
from agentmon.paths import make_private_dir, write_private_text

MAX_LINE_BYTES = 16384
STAGES = {"api", "async_llm", "engine_core", "scheduler"}
TERMINALS = {"vllm.request.finished": "finished", "vllm.request.engine_finished": "finished",
             "vllm.request.cancelled": "cancelled", "vllm.request.timeout": "timeout"}


def identity(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        return None
    return value


def read_records(path: Path) -> tuple[list[dict], list[dict]]:
    records, errors = [], []
    with path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError("oversized")
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("not_object")
                records.append(record)
            except (ValueError, UnicodeError):
                errors.append({"file": str(path), "line": number, "status": "INVALID_RECORD"})
    return records, errors


def analyze_binding(clients: list[dict], events: list[dict], *, live_evidence: bool = False,
                    client_smoke_pass: bool = False) -> tuple[list[dict], dict]:
    """Strict explicit joins. `direct` can be tested synthetically, never live-promoted.

    Minimal future producer contract: existing Event(source=vllm_p2), payload
    stage/process_role/source_pid/source_host/clock_domain/evidence_source;
    API headers explicitly observed; async and scheduler object aliases explicit.
    The contract is an input requirement, not an installed v0.27 adapter.
    """
    issues, valid, seen = [], [], {}
    for event in events:
        eid, p = identity(event.get("event_id")), event.get("payload")
        if (not is_p2_event(event) or not eid or not identity(event.get("event_type")) or not isinstance(p, dict) or event.get("schema_version") != "1.0"
                or any(type(event.get(k)) is not int or event[k] < 0 for k in ("wall_time_ns", "monotonic_time_ns"))):
            issues.append({"status": "INVALID_ENVELOPE", "event_id": eid})
            continue
        if eid in seen:
            if event != seen[eid]:
                issues.append({"status": "CONFLICT_EVENT_ID", "event_id": eid})
            continue
        seen[eid] = event
        valid.append(event)

    def scope(e):
        p = e["payload"]
        # Clock domain scopes identities; it is never used to subtract times.
        return tuple(identity(v) for v in (e.get("run_id"), p.get("source_host"), p.get("clock_domain")))

    def proved(e, stage):
        p = e["payload"]
        role = "api" if stage in {"api", "async_llm"} else "engine_core"
        boundary_types = {"vllm.request.received", "vllm.request.queued", "vllm.request.id_alias",
                          "vllm.request.api_received", "vllm.request.engine_received", "vllm.request.scheduler_received"}
        return (all(scope(e)) and e.get("event_type") in boundary_types
                and e.get("quality") == "exact" and p.get("stage") == stage and p.get("process_role") == role
                and type(p.get("source_pid")) is int and p["source_pid"] > 0
                and identity(p.get("evidence_source")) in {"runtime", "synthetic"}
                and p.get("mapping_basis") == ("observed_header" if stage == "api" else "observed_request_object"))

    groups = defaultdict(list)
    for event in valid:
        if all(scope(event)):
            groups[scope(event)].append(event)
    attempts = defaultdict(list)
    for c in clients:
        a = c.get("agentmon", {})
        if not isinstance(a, dict):
            a = {}
        attempts[identity(a.get("request_attempt_id"))].append(c)
    for aid, cs in attempts.items():
        if aid is None or len(cs) != 1:
            issues.append({"status": "INVALID_OR_DUPLICATE_ATTEMPT", "request_attempt_id": aid})

    results, used = [], set()
    for c in clients:
        a = c.get("agentmon", {}) if isinstance(c.get("agentmon"), dict) else {}
        run, call, attempt, api = [identity(a.get(k)) for k in
                                  ("agent_run_id", "model_request_id", "request_attempt_id", "api_request_id")]
        base = {"agent_run_id": run, "model_request_id": call, "request_attempt_id": attempt,
                "api_request_id": api, "engine_request_id": None, "scheduler_request_id": None,
                "correlation_quality": "unavailable", "validation_status": "BLOCKED",
                "terminal_status": "unobserved", "evidence_event_ids": [], "request_id_aliases": [],
                "evidence_class": "RUNTIME_DECLARED" if live_evidence else "OFFLINE"}
        if not run or not call or not attempt or len(attempts[attempt]) != 1:
            results.append({**base, "quality_reason": "invalid_client_identity"})
            continue
        candidates = []
        for key, es in groups.items():
            # A missing response (timeout) needs an explicitly observed attempt header.
            for e in es:
                p = e["payload"]
                same_attempt = (identity(p.get("agentmon_run_id")) == run
                                and identity(p.get("agentmon_model_request_id")) == call
                                and identity(p.get("request_attempt_id")) == attempt)
                if ((api and api in p2_alias_ids(p)) or (not api and same_attempt)):
                    candidates.append((key, e))
        if not candidates:
            results.append({**base, "quality_reason": "no_explicit_server_identity; no_time_matching"})
            continue
        scopes = {key for key, _ in candidates}
        if len(scopes) != 1:
            results.append({**base, "quality_reason": "ambiguous_service_scope"})
            continue
        key = next(iter(scopes))
        es = groups[key]
        api_events = [e for _, e in candidates if proved(e, "api")
                      and identity(e["payload"].get("agentmon_run_id")) == run
                      and identity(e["payload"].get("agentmon_model_request_id")) == call
                      and isinstance(e["payload"].get("observed_header_fields"), list)
                      and "agentmon_run_id" in e["payload"]["observed_header_fields"]
                      and "agentmon_model_request_id" in e["payload"]["observed_header_fields"]
                      and identity(e["payload"].get("api_request_id"))
                      and (not e["payload"].get("request_attempt_id") or e["payload"]["request_attempt_id"] == attempt)]
        if not api_events:
            weak = [e for _, e in candidates]
            quality = "heuristic" if any(e["payload"].get("mapping_basis") == "time_nearest" for e in weak) else "server_generated"
            used.update(e["event_id"] for e in weak)
            results.append({**base, "service_run_id": key[0], "correlation_quality": quality,
                            "quality_reason": "server_id_only; observed_client_headers_missing",
                            "evidence_event_ids": [e["event_id"] for e in weak]})
            continue
        observed_apis = {e["payload"]["api_request_id"] for e in api_events}
        if len(observed_apis) != 1:
            results.append({**base, "quality_reason": "conflicting_api_ids_for_attempt"})
            continue
        api = next(iter(observed_apis))
        base["api_request_id"] = api
        header_owners = {(identity(e["payload"].get("agentmon_run_id")), identity(e["payload"].get("agentmon_model_request_id")))
                         for e in es if proved(e, "api") and e["payload"].get("api_request_id") == api}
        if header_owners != {(run, call)}:
            results.append({**base, "quality_reason": "conflicting_server_api_ownership"})
            continue
        # Retried calls are disambiguated by response ID or observed attempt, never call order.
        owners = [other for other in clients if other is not c and isinstance(other.get("agentmon"), dict)
                  and identity(other["agentmon"].get("api_request_id")) == api]
        if owners:
            results.append({**base, "quality_reason": "ambiguous_client_api_ownership"})
            continue
        async_events = [e for e in es if proved(e, "async_llm") and e["payload"].get("api_request_id") == api
                        and identity(e["payload"].get("engine_request_id"))]
        engines = sorted({e["payload"]["engine_request_id"] for e in async_events})
        if not engines:
            used.update(e["event_id"] for e in api_events)
            results.append({**base, "quality_reason": "missing_explicit_api_engine_alias",
                            "evidence_event_ids": [e["event_id"] for e in api_events]})
        for engine in engines:
            related = [e for e in es if e["payload"].get("engine_request_id") == engine]
            alias = [e for e in async_events if e["payload"]["engine_request_id"] == engine]
            conflict = any(proved(e, "async_llm") and e["payload"].get("api_request_id") != api for e in related)
            core = [e for e in related if proved(e, "engine_core")]
            sched = [e for e in related if proved(e, "scheduler") and identity(e["payload"].get("scheduler_request_id"))]
            for sid in sorted({e["payload"]["scheduler_request_id"] for e in sched}) or [None]:
                scheduler_conflict = bool(sid and any(proved(e, "scheduler") and e["payload"].get("scheduler_request_id") == sid
                                                    and e["payload"].get("engine_request_id") != engine for e in es))
                selected = api_events + alias + core + [e for e in sched if e["payload"]["scheduler_request_id"] == sid]
                terminal_events = [e for e in related if e.get("event_type") in TERMINALS]
                terminals = {TERMINALS[e["event_type"]] for e in terminal_events}
                reason = ("conflicting_engine_ownership" if conflict else "conflicting_scheduler_ownership" if scheduler_conflict else "conflicting_terminal_states" if len(terminals) > 1
                          else "missing_engine_core_or_scheduler_evidence" if not core or not sid else "explicit_headers_and_object_aliases")
                complete = not conflict and not scheduler_conflict and len(terminals) <= 1 and bool(core and sid)
                ids = sorted({e["event_id"] for e in selected + terminal_events})
                used.update(ids)
                results.append({**base, "service_run_id": key[0], "source_host": key[1], "clock_domain": key[2],
                                "engine_request_id": engine, "scheduler_request_id": sid,
                                "correlation_quality": "direct" if complete else "unavailable", "quality_reason": reason,
                                "validation_status": "OFFLINE_PASS" if complete and not live_evidence else "EVIDENCE_READY" if complete else "BLOCKED",
                                "terminal_status": next(iter(terminals)) if len(terminals) == 1 else "conflict" if terminals else "unobserved",
                                "evidence_event_ids": ids,
                                "request_id_aliases": [{"from_kind": "api", "from_id": api, "to_kind": "engine", "to_id": engine,
                                                        "evidence_event_ids": [e["event_id"] for e in alias]},
                                                       {"from_kind": "engine", "from_id": engine, "to_kind": "scheduler", "to_id": sid,
                                                        "evidence_event_ids": [e["event_id"] for e in sched if e["payload"]["scheduler_request_id"] == sid]}]})

    for index, row in enumerate(results):
        if len(json.dumps(row).encode("utf-8")) > MAX_LINE_BYTES:
            issues.append({"status": "BINDING_EVIDENCE_LIMIT_EXCEEDED", "request_attempt_id": row["request_attempt_id"]})
            results[index] = {k: row[k] for k in ("agent_run_id", "model_request_id", "request_attempt_id", "api_request_id",
                                                  "engine_request_id", "scheduler_request_id", "terminal_status", "evidence_class")}
            results[index].update(correlation_quality="unavailable", validation_status="BLOCKED",
                                  quality_reason="binding_evidence_limit_exceeded", evidence_event_ids=[], request_id_aliases=[])

    # Capability evidence must cover actual emitting processes, not just a role label.
    process_coverage = []
    for key, es in groups.items():
        processes = {(e["payload"].get("process_role"), e["payload"].get("source_pid")) for e in es
                     if isinstance(e["payload"].get("stage"), str) and e["payload"]["stage"] in STAGES
                     and isinstance(e["payload"].get("process_role"), str) and type(e["payload"].get("source_pid")) is int}
        for role, pid in sorted(processes, key=str):
            loaded = any(e.get("event_type") == "vllm.adapter.capability" and e["payload"].get("process_role") == role
                         and e["payload"].get("source_pid") == pid and e["payload"].get("status") == "installed"
                         and e["payload"].get("adapter_namespace") == "vllm_0271"
                         and identity(e["payload"].get("loaded_vllm_version")) in {"0.27.1", "0.27.1+empty"}
                         and e["payload"].get("signature_validation") == "PASS"
                         and type(e["payload"].get("dropped_events")) is int
                         and e["payload"].get("dropped_events") == 0 for e in es)
            process_coverage.append({"service_run_id": key[0], "source_host": key[1], "clock_domain": key[2],
                                     "process_role": role, "source_pid": pid, "hook_loaded": loaded})
    request_events = [e for e in valid if e.get("event_type", "").startswith("vllm.request.")]
    orphan = sorted(e["event_id"] for e in request_events if e["event_id"] not in used)
    full = bool(results) and all(r["correlation_quality"] == "direct" for r in results) and not issues
    runtime_sources = all(c.get("source") == "http_interception" and c.get("http_status") == 200 for c in clients) and all(
        e["payload"].get("evidence_source") == "runtime" for e in valid if e["event_id"] in used or e.get("event_type") == "vllm.adapter.capability")
    coverage = bool(process_coverage) and all(p["hook_loaded"] for p in process_coverage)
    scheduler_c1 = any(e["payload"].get("stage") == "scheduler" for e in valid if e["event_id"] in used) and all(
        type(e["payload"].get("active_request_count")) is int and e["payload"].get("active_request_count") == 1 for e in valid
        if e["event_id"] in used and e["payload"].get("stage") == "scheduler")
    terminal_success = bool(results) and all(r["terminal_status"] == "finished" for r in results)
    live_pass = bool(live_evidence and full and runtime_sources and coverage and not orphan
                     and client_smoke_pass and scheduler_c1 and terminal_success)
    audit = {"schema_version": "agentmon-request-binding-v1", "status": "LIVE_BINDING_PASS" if live_pass else
             "OFFLINE_BINDING_PASS" if full and not live_evidence else "REAL_ACCEPTANCE_PENDING" if full else "BLOCKED",
             "real_acceptance": "PASS" if live_pass else "PENDING", "issues": issues,
             "client_attempts": len(clients), "binding_rows": len(results),
             "unmatched_p1_attempts": sorted({r["request_attempt_id"] for r in results if r["correlation_quality"] != "direct"}, key=str),
             "orphan_p2_event_ids": orphan, "process_coverage": process_coverage,
             "runtime_provenance_complete": runtime_sources, "clock_policy": "NO_CROSS_PROCESS_TIME_SUBTRACTION",
             "client_smoke_pass": client_smoke_pass, "scheduler_c1_observed": scheduler_c1,
             "terminal_success_observed": terminal_success,
             "model_executed_by_analyzer": False, "hook_installed_by_analyzer": False}
    return results, audit


def write_analysis(output: Path, rows: list[dict], audit: dict) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be new/empty")
    make_private_dir(output)
    write_private_text(output / "request_binding.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    write_private_text(output / "request_identity_audit.json", json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    write_private_text(output / "binding_acceptance.json", json.dumps({"status": audit["status"], "real_acceptance": audit["real_acceptance"]}, indent=2) + "\n")
