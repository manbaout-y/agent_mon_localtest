# Adding A New Analysis Profile

Add a profile when a new AgentMon, vLLM, trajectory, or serving schema changes metric semantics.

1. Add a JSON-compatible YAML file under `src/agentmon/analysis/schemas/`.
2. Define each metric with unit, level, clock domain, aggregation rule, and validation rule.
3. Add profile metadata under `src/agentmon/analysis/profiles/` if needed.
4. Add tests that load the profile and verify semantic flags such as `summable`, `time_weighted`, and `quantile_allowed`.
5. Do not reuse a metric name for a different level or unit. Mark old fields deprecated instead.

For request/P2 profiles, declare `request_id_keys`, `run_id_keys`, `task_id_keys`, `batch_id_keys`, event signals, event sets, and `p2_eligibility_fields`. The shared index and eligibility core consume these mappings and must not be modified for a new framework version.

Profiles should make unknown fields explicit instead of silently reusing incompatible meanings.
