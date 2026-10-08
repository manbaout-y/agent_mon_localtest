# Metric Dictionary

The runtime metric registry is loaded from:

- `src/agentmon/analysis/schemas/agentmon_default.yaml`
- `src/agentmon/analysis/schemas/agentmon_v4.yaml`
- `src/agentmon/analysis/schemas/vllm_default.yaml`
- `src/agentmon/analysis/schemas/vllm_0_18.yaml`
- `src/agentmon/analysis/schemas/trajectory_default.yaml`

Important distinctions:

- `workload_task_throughput`: attempted tasks divided by workload phase wall time.
- `pipeline_task_throughput`: attempted tasks divided by full pipeline wall time.
- `successful_workload_task_throughput`: successful workload tasks divided by workload phase wall time.
- `publishable_pipeline_task_throughput`: publishable tasks divided by full pipeline wall time.
- `task_throughput_per_hour`: legacy per-hour task rate; check the row's `workload_window_s` and `pipeline_window_s` before comparing.
- `request_throughput_per_second`: request attempts divided by the matching request-set window.
- `whole_workload_generation_tok_s`: generated tokens divided by whole workload window.
- `per_request_generation_tok_s`: request-level generation tokens divided by request service time.
- `scheduled_token_count`: scheduler work item count; never generated tokens.
- `actual_generated_tokens_delta`: generated-token delta from runtime/P2 telemetry; required for steady-state generated-token throughput.
- `active_model_spans`: observed client/model request concurrency; not configured concurrency.
- `resource_aicore_util_pct`: resource sample; means require time weighting when intervals exist.
- `npu_mapping_evidence`: LEVEL_1/2/3/NONE mapping evidence for PID/rank/logical/physical device attribution.

Conclusion classes:

- `OBSERVED_FACT`
- `STATISTICAL_ASSOCIATION`
- `MECHANISM_INFERENCE`
- `UNRESOLVED_HYPOTHESIS`
