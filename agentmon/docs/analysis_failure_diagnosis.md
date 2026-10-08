# Analysis Failure Diagnosis

No server failure bundle or raw server trace is present in this workspace, so the direct and root cause of a specific server execution is not verified locally.

The analysis runtime now creates diagnosis artifacts from the evidence available at the point of failure:

- `analysis_failure_ledger.json`
- `analysis_failure_stages.csv`
- `analysis_failure_diagnosis.md`
- `run_summary.json`
- `analysis_status.json`
- `stages/<stage>/stage-status.json`

The ledger distinguishes data, schema, semantic, implementation, memory, disk, timeout, index invalidation and unknown failures. It records only evidence observed by the process and does not infer a server-data root cause from local synthetic tests.

For a server incident, preserve the output bundle and the console log. The first failed stage, traceback, stage runtime summary, preflight result, input inventory, resume status and request conservation tables are sufficient for source-level diagnosis without exporting raw JSONL.
