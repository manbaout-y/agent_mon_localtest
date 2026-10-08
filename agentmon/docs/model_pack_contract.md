# Model Input Pack Contract

`agentmon analysis build-model-pack` writes a compact `model_input_pack/` with deterministic inputs for local report generation.

Required files include:

- `README.md`
- `experiment_manifest.json`
- `experiment_contract.json`
- `expected_artifacts.json`
- `metric_dictionary.md`
- `semantics_manifest.json`
- `validation_status.json`
- `analysis_ready.json`
- `data_quality_gate.json`
- `handoff_manifest.json`
- `verified_facts.json`
- `statistical_associations.json`
- `mechanism_inferences.json`
- `unresolved_questions.json`
- `excluded_hypotheses.json`
- `allowed_conclusions.json`
- `forbidden_conclusions.json`
- core analysis CSV/Parquet tables
- `request_details`: normalized request identity, token accounting, exact/proxy
  lifecycle fields, compression, summary, scheduler, and KV totals;
- `request_lifecycle_details`;
- `request_batch_membership` and `scheduler_iteration_details` when Deep P2 is
  available;
- `kv_block_events` when KV lifecycle hooks are available;
- `tool_call_details`, `error_event_details`, and `task_result_details`;
- `research_questions` and `contract_validation`;
- `metric_dictionary`, `entity_set_definitions`, and `clock_domain_audit`;
- `conservation_checks`;
- `observation_register`, `anomaly_register`, `contradiction_register`,
  `unresolved_question_register`, and `analysis_backlog`;
- `evidence_index`
- `report_prompt.md`

The model must:

- use only the pack;
- not recalculate raw JSONL metrics;
- cite evidence for important numbers;
- keep request/run/batch/pipeline levels separate;
- preserve distinction between task result and observability quality;
- respect `data_quality_gate.json` and handoff guardrails;
- avoid forbidden conclusions.

Detail tables contain normalized, content-free evidence and may be empty. An
empty or absent Deep P2/KV field is not zero. Prompt text, generated summary
text, raw tool output, and full code diffs are not added to the model pack by
this contract.

The handoff registers are basic-analysis outputs only. They summarize observed
facts, deterministic anomaly flags, active guardrails, and unresolved evidence
requirements from the standard pipeline. They are not customized deep-analysis
plans and do not authorize causal mechanism claims.
