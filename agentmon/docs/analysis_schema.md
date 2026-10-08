# AgentMon Analysis Schema

The analysis SOP writes a versioned bundle:

```text
analysis_bundle/
  manifest/
  00_inventory/
  01_normalized/
  02_validation/
  02_semantics/
  03_analysis/
  04_evidence/
  05_handoff/
  figures/
  model_input_pack/
```

## Manifest

`manifest/experiment_manifest.json` records discovered configuration and field provenance. Missing fields are explicit, and inferred fields are listed under `inferred_fields`.

Field source classes:

- `EXPLICIT_MANIFEST`
- `CONFIG`
- `LOG`
- `ENVIRONMENT`
- `SOURCE`
- `INFERRED`
- `UNKNOWN`

## Normalized Tables

`01_normalized/` separates levels:

- experiment/trial/batch: `experiments`, `trials`, `batches`
- worker/pipeline: `workers`, `worker_intervals`
- run: `runs`
- step/span: `steps`, `spans`
- request: `requests`, `request_lifecycle`, `p1_request_attempts`, `p2_requests`, `request_id_aliases`
- scheduler/resource: `scheduler_iterations`, `resource_samples`
- evidence support: `trajectories`, `task_results`, `event_inventory`

Missing values remain null/empty. The analyzer must not fill missing metric values with zero.

## Inventory And Contract Tables

`00_inventory/` includes discovered files/runs/batches plus the standard
analysis data-foundation contract:

- `experiment_contract.json`
- `contract_validation`
- `research_questions`
- `expected_artifacts.json`

When no explicit contract is supplied, the generated contract remains
`PARTIAL`; fields that cannot be discovered are not imputed.

## Validation Tables

`02_validation/` includes:

- `integrity_issues`
- `metric_coverage`
- `time_conservation`
- `lifecycle_conservation`
- `little_law_validation`
- `pipeline_reconciliation`
- `conservation_checks`
- `request_id_consistency`
- `request_conservation`
- `profile_detection`
- `resource_sampling_validation`
- `data_quality_gate.json`
- `analysis_ready.json`

## Semantics Tables

`02_semantics/` materializes Schema Profile semantics:

- `metric_dictionary`
- `entity_set_definitions`
- `clock_domain_audit`
- `semantics_manifest.json`

## Handoff Tables

`05_handoff/` is a basic-analysis handoff package:

- `observation_register`
- `anomaly_register`
- `contradiction_register`
- `unresolved_question_register`
- `analysis_backlog`

These registers organize deterministic observations and guardrails. They are
not a customized deep-analysis planner.

## Metric Registry

Metric profiles live in `src/agentmon/analysis/schemas/*.yaml`. They are JSON-compatible YAML files with required fields:

- `canonical_name`
- `aliases`
- `source_event_types`
- `data_level`
- `unit`
- `clock_domain`
- `definition`
- `required_fields`
- `nullable`
- `aggregation_rules`
- `time_weighted`
- `summable`
- `quantile_allowed`
- `normalization`
- `validation_rules`
- `source_code_reference`
- `version_range`
- `deprecation_status`
