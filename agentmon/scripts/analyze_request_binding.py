#!/usr/bin/env python3
"""Read existing client/P2 files; never contact a service or install a hook."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentmon.analysis.request_binding import analyze_binding, read_records, write_analysis


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-evidence", required=True, type=Path)
    parser.add_argument("--server-events", action="append", default=[], type=Path,
                        help="repeat for per-process P2 files; no file means client-only analysis")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live-evidence", action="store_true", help="evaluate declared runtime provenance and process-coverage gates")
    parser.add_argument("--client-report", type=Path, help="existing client_smoke.json; required for live acceptance")
    args = parser.parse_args(argv)
    clients, errors = read_records(args.client_evidence)
    events = []
    for path in args.server_events:
        records, invalid = read_records(path)
        events.extend(records)
        errors.extend(invalid)
    client_pass = False
    if args.client_report:
        report = json.loads(args.client_report.read_text(encoding="utf-8"))
        expected = ("FIRST_ACTION_VALID", "TOOL_EXECUTION", "SECOND_ACTION_VALID", "WIRE_THINKING_AND_REQUEST_IDS")
        client_pass = (report.get("status") == "PASS" and report.get("concurrency") == 1
                       and report.get("model_requests") == 2 and isinstance(report.get("checks"), dict)
                       and all(report["checks"].get(k) is True for k in expected))
    rows, audit = analyze_binding(clients, events, live_evidence=args.live_evidence, client_smoke_pass=client_pass)
    if errors:
        audit["issues"].extend(errors)
        audit.update(status="BLOCKED", real_acceptance="PENDING")
    for row in rows:
        row["evidence_files"] = [str(args.client_evidence), *(str(p) for p in args.server_events)]
    write_analysis(args.output, rows, audit)
    print(f"BINDING_STATUS={audit['status']}; REAL_ACCEPTANCE={audit['real_acceptance']}")
    return 0 if audit["status"] in {"OFFLINE_BINDING_PASS", "LIVE_BINDING_PASS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
