"""Phases 2/3 offline tools. No server access, model loading, resource enforcement or paper recipe changes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agentmon import correlate_events
from .io import load_records, write_csv, write_json, write_jsonl
from .lifecycle import EVENT_SCHEMA_VERSION, StateEvent, analyze_lifecycle
from .policy_replay import POLICIES, replay_demands
from .recovery import CostProfiles, plan_recovery
from .scenarios import constructed_schedule
from .state_compare import compare_npz, dependency_impact
from .state_inventory import capture_requirements


OFFLINE_SUITE_VERSION = "state-research-offline-v1.0.1"
CAPSULE_MAX_BYTES = 45000


def bounded_capsule(report: dict, max_bytes=45000) -> str:
    if type(max_bytes) is not int or not 512 <= max_bytes <= CAPSULE_MAX_BYTES:
        raise ValueError("capsule budget must be an integer from 512 to 45000 bytes")
    lines = [f"VERSION={OFFLINE_SUITE_VERSION}", f"STATUS={report['status']}",
             "MODE=OFFLINE_ANALYSIS_ONLY; MODEL_EXECUTION=NOT_RUN",
             "REAL_ACCEPTANCE=PENDING; WORK_PROXY_IS_NOT_LATENCY",
             "RESEARCH_METHODS=BASELINES_AND_PLANNING_TOOLS_NOT_VALIDATED_CONTROL_POLICY"]
    for name, gate in report["stages"].items():
        lines.append(f"{name}: {gate['status']} {gate.get('reason', '')}")
    for item in report.get("policy_summary", []):
        lines.append(f"{item['policy']}: hits={item['hits']}/{item['access_units']}; miss_bytes_proxy={item['miss_bytes']}")
    for name, sources in report.get("evidence_sources", {}).items():
        lines.append(f"EVIDENCE_SOURCE[{name}]={','.join(sources) or 'UNAVAILABLE'}")
    lines.extend(f"OUTPUT={p}" for p in report.get("files", []))
    text = ""
    for line in lines:
        if len((text + line + '\n[TRUNCATED; see report.json]\n').encode('utf-8')) > max_bytes:
            text += "[TRUNCATED; see report.json]\n"
            break
        text += line + "\n"
    return text


def run_suite(config: dict, base: Path, output: Path) -> dict:
    # Validate the report limit before producing any output: never finish with an oversized capsule.
    capsule_budget = config.get("capsule_max_bytes", CAPSULE_MAX_BYTES)
    bounded_capsule({"status": "PREFLIGHT", "stages": {}}, capsule_budget)
    if output.exists() and any(output.iterdir()):
        raise ValueError("use a new/empty output directory to preserve prior acceptance evidence")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "run_config.json", {"version": OFFLINE_SUITE_VERSION,
                                            "input_base": str(base.resolve()), "config": config})
    stages, evidence_sources = {}, {}

    def run(name, input_keys, callback):
        missing = [key for key in input_keys if not config.get(key)]
        if missing:
            stages[name] = {"status": "INPUT_MISSING", "reason": ", ".join(missing)}
            return None
        try:
            result = callback()
            stages[name] = {"status": "OFFLINE_CHECK_PASS"}
            return result
        except (ValueError, KeyError, TypeError, OSError) as exc:
            stages[name] = {"status": "FAIL", "reason": str(exc)}
            return None

    def records(key):
        rows = load_records(base / config[key])
        if not rows:
            raise ValueError(f"{key}: empty input has no acceptance evidence")
        return rows

    def request_records():
        rows = records("requests")
        ids = [row["request_id"] for row in rows]
        if any(not isinstance(rid, str) or not rid for rid in ids) or len(ids) != len(set(ids)):
            raise ValueError("requests manifest requires unique nonempty request_id values")
        return rows

    def lifecycle():
        events = [StateEvent(**row) for row in records("state_events")]
        if config.get("requests"):
            known = {row["request_id"]: row for row in request_records()}
            unknown = sorted({event.request_id for event in events} - set(known))
            if unknown:
                raise ValueError(f"state events refer to requests absent from manifest: {unknown}")
            for event in events:
                if event.action in {"state.create", "state.share", "state.restore"}:
                    state, request = event.data["state"], known[event.request_id]
                    for field in ("model_family", "context_version"):
                        if request.get(field) is not None and state[field] != request[field]:
                            raise ValueError(f"event {event.event_id}: state {field} conflicts with request manifest")
        return analyze_lifecycle(events)

    timeline = run("lifecycle", ["state_events"], lifecycle)
    policies = []
    if timeline is not None:
        evidence_sources["lifecycle"] = timeline["sources"]
        unvalidated = sum(bool(c.get("dependency_revalidation_required_state_ids")) for c in timeline["consumes"])
        if unvalidated:
            stages["lifecycle"] = {"status": "PARTIAL_OR_BLOCKED", "reason": "consumed dependencies changed without explicit refresh",
                                   "revalidation_required_consumptions": unvalidated}
        if timeline.get("legacy_context_update_event_ids"):
            stages["lifecycle"] = {"status": "PARTIAL_OR_BLOCKED", "reason": "legacy update conflates context and content versions",
                                   "legacy_context_update_event_ids": timeline["legacy_context_update_event_ids"]}
        write_json(output / "state_timeline.json", timeline)
        write_jsonl(output / "state_demands.jsonl", timeline["demands"])
        if not timeline["demands"]:
            stages["policy_replay"] = {"status": "INPUT_MISSING", "reason": "no state.consume evidence; allocation is not access"}
        elif config.get("capacities"):
            def replay():
                configured = config.get("policies", ["shared_lru", "dependency_bundle_lru"])
                if not isinstance(configured, list) or not configured or len(set(configured)) != len(configured):
                    raise ValueError("policies must be a nonempty unique list")
                rows, errors = [], []
                for policy in configured:
                    try:
                        if policy not in POLICIES:
                            raise ValueError(f"unknown policy {policy}")
                        rows.append(replay_demands(timeline["demands"], config["capacities"], policy=policy,
                                                  type_budgets=config.get("type_budgets")))
                    except (ValueError, KeyError, TypeError) as exc:
                        errors.append({"policy": policy, "status": "FAIL", "reason": str(exc)})
                return rows, errors
            result = run("policy_replay", ["capacities"], replay)
            if result is not None:
                policies, errors = result
                write_json(output / "policy_replay.json", policies)
                if errors:
                    write_json(output / "policy_errors.json", errors)
                    stages["policy_replay"] = {"status": "FAIL", "reason": "policy failures; successful policies retained", "failed_count": len(errors)}
        else:
            stages["policy_replay"] = {"status": "INPUT_MISSING", "reason": "explicit capacities"}
    else:
        stages["policy_replay"] = {"status": "BLOCKED_DEPENDENCY", "reason": "lifecycle"}
    summary = [{k: v for k, v in p.items() if k != "history"} for p in policies]
    write_csv(output / "policy_comparison.csv", summary,
              ["policy", "experiment_class", "sources", "access_units", "hits", "misses", "hit_fraction",
               "miss_bytes", "bypass_bytes", "evictions", "peak_resident_bytes", "timing_status", "semantics"])
    def recover():
        profiles = CostProfiles(records("cost_trials"))
        cases = records("recovery_cases")
        ids = [case["case_id"] for case in cases]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate recovery case_id")
        rows = []
        for case in cases:
            try:
                rows.extend(plan_recovery([case], profiles, set(config.get("available_capabilities", []))))
            except (ValueError, KeyError, TypeError) as exc:
                rows.append({"case_id": case["case_id"], "status": "FAIL", "reason": str(exc),
                             "source": case.get("source"), "selected_path": None, "execution_status": "NOT_EXECUTED"})
        return rows
    recovery = run("recovery", ["cost_trials", "recovery_cases"], recover)
    if recovery is not None:
        evidence_sources["recovery"] = sorted({r["source"] for r in recovery if r.get("source")})
        write_json(output / "recovery_plans.json", recovery)
        write_csv(output / "recovery_comparison.csv", recovery,
                  ["case_id", "sample_id", "source", "selected_path", "selected_variant", "selected_parameters",
                   "status", "execution_status", "functional_safety", "reason"])
        if any(row["status"] == "FAIL" for row in recovery):
            stages["recovery"] = {"status": "FAIL", "reason": "invalid recovery cases; other case results retained"}
        elif not recovery or any(row["status"] != "PLAN_READY" for row in recovery):
            stages["recovery"] = {"status": "PARTIAL_OR_BLOCKED", "reason": "see recovery_plans.json; no uncosted optimum claimed"}
    def compare():
        cases = records("state_comparisons")
        seen, splits = set(), {}
        for case in cases:
            identity = case["identity"]
            key = tuple(identity[k] for k in ("model_family", "condition", "state_kind", "pair_id", "layer_id"))
            if key in seen:
                raise ValueError("duplicate state comparison identity; compare variants in separate runs")
            seen.add(key)
            base_pair = identity["model_family"], identity["pair_id"]
            if base_pair in splits and splits[base_pair] != identity["split"]:
                raise ValueError(f"split leakage for {base_pair}")
            splits[base_pair] = identity["split"]
        rows, errors = [], []
        for case in cases:
            try:
                rows.append(compare_npz(case, base))
            except (ValueError, KeyError, TypeError, OSError) as exc:
                errors.append({"identity": case["identity"], "status": "FAIL", "reason": str(exc)})
        return rows, errors
    comparison = run("state_comparison", ["state_comparisons"], compare)
    if comparison is not None:
        comparison, errors = comparison
        evidence_sources["state_comparison"] = sorted({r["source"] for r in comparison})
        write_json(output / "state_comparison.json", comparison)
        if errors:
            write_json(output / "state_comparison_errors.json", errors)
            stages["state_comparison"] = {"status": "FAIL", "reason": "invalid state cases; successful comparisons retained", "failed_count": len(errors)}
        elif any(row["status"] == "ZERO_BASELINE_DEGRADED" for row in comparison):
            stages["state_comparison"] = {"status": "DEGRADATION_RECORDED", "reason": "true corrected SSE retained"}
        elif any(not row.get("canonical_mapping_verified", False) for row in comparison):
            stages["state_comparison"] = {"status": "PARTIAL_OR_BLOCKED", "reason": "legacy synthetic corrected coordinates unverified"}
    def impact():
        graph = json.loads((base / config["dependency_graph"]).read_text())
        return dependency_impact(graph["nodes"], graph["changed_state_ids"])
    affected = run("dependency_impact", ["dependency_graph"], impact)
    if affected is not None:
        write_json(output / "dependency_impact.json", affected)
    joins = run("agentmon_correlation", ["requests", "agentmon_events"],
                lambda: correlate_events(request_records(), records("agentmon_events")))
    if joins is not None:
        write_json(output / "agentmon_correlation.json", joins)
        if joins["conflicts"] or joins["ambiguous"] or joins["unmatched"]:
            stages["agentmon_correlation"] = {"status": "PARTIAL_OR_BLOCKED", "reason": "unresolved ID correlation; see detailed rows"}
    schedule = run("constructed_schedule", ["requests", "schedule"],
                   lambda: constructed_schedule(request_records(), **config["schedule"]))
    if schedule is not None:
        write_json(output / "workload_schedule.json", schedule)
    requirements = capture_requirements()
    requirements["acceptance_sequence"] = [
        "1. Verify runtime identity/schema and tokenizer alignment; no V4.1 schema inferred from V4",
        "2. Capture one request's state instances, producer/consumer graph and allocation lifetimes",
        "3. Capture sequential and pause/resume traces, AgentMon IDs and actual consumption",
        "4. Calibrate isolated recovery costs on disjoint samples with a fixed timing protocol",
        "5. Supply explicit validity evidence and mapped A/B/repaired states; run offline suite",
        "6. Separately validate logits/next-token/generation, then resource enforcement and end-to-end timing",
    ]
    write_json(output / "runtime_acceptance_requirements.json", requirements)
    report = {"version": OFFLINE_SUITE_VERSION, "event_schema": EVENT_SCHEMA_VERSION,
              "status": "FAIL" if any(v["status"] == "FAIL" for v in stages.values()) else
                        "OFFLINE_CHECKS_PASS" if all(v["status"] == "OFFLINE_CHECK_PASS" for v in stages.values()) else "PARTIAL",
              "stages": stages, "policy_summary": summary, "real_acceptance": "PENDING",
              "recovery_summary": {"case_count": len(recovery or []),
                                   "plan_ready_count": sum(r["status"] == "PLAN_READY" for r in recovery or []),
                                   "blocked_or_partial_count": sum(r["status"] not in {"PLAN_READY", "FAIL"} for r in recovery or []),
                                   "failure_count": sum(r["status"] == "FAIL" for r in recovery or [])},
              "evidence_sources": evidence_sources,
              "research_status": {
                  "resource_optimization": "FIXED_TRACE_BASELINES_ONLY_NOT_DYNAMIC_BENEFIT_OPTIMIZER",
                  "joint_reuse": "DEPENDENCY_ANALYSIS_AND_EVICTION_BASELINE_NOT_RUNTIME_INHERITANCE",
                  "selective_repair": "EXTERNAL_VALIDITY_GATED_PLAN_NOT_REPAIR_IMPLEMENTATION",
                  "model_functional_safety": "NOT_EVALUATED",
                  "v4_1_runtime": "SCHEMA_AND_CAPTURE_NOT_IMPLEMENTED"},
              "model_execution": "NOT_RUN", "files": sorted(p.name for p in output.iterdir())}
    report["files"] += ["report.json", "capsule.txt"]
    write_json(output / "report.json", report)
    (output / "capsule.txt").write_text(bounded_capsule(report, capsule_budget), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline state lifecycle, fixed-trace policies and evidence-gated recovery plans")
    parser.add_argument("--config", required=True, help="JSON; input paths resolved relative to config")
    parser.add_argument("--output", required=True, help="new/empty directory")
    args = parser.parse_args(argv)
    path = Path(args.config).resolve()
    report = run_suite(json.loads(path.read_text(encoding="utf-8")), path.parent, Path(args.output))
    print(f"OFFLINE_SUITE_STATUS={report['status']}")
    print("REAL_ACCEPTANCE=PENDING; MODEL_EXECUTION=NOT_RUN")
    print(f"OUTPUT={Path(args.output).resolve()}")
    return 1 if report["status"] == "FAIL" else 0
