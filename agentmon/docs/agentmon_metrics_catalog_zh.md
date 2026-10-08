# AgentMon 监测指标中文手册

本文按指标族汇总 AgentMon 当前采集、派生、聚合和校验的指标。为避免把
`mean/p50/p90/p95/max/total` 等同类统计拆成大量重复字段，表中将同一指标族合并说明。

## P0、P1、P2 分别是什么

P0、P1、P2 最初表示 AgentMon 的实施优先级，现在也用于区分三层采集链路。它们不是
三个互相替代的版本，而是从操作系统外部观测逐步深入到 Agent 和模型服务内部的三层数据。

| 层级 | 采集位置 | 主要回答的问题 | 典型数据 | 重要边界 |
|---|---|---|---|---|
| P0 | Agent 和 vLLM 进程外部 | 整个任务用了多少 CPU、内存、NPU、KV Cache，服务计数器如何变化？ | Host/Proc 采样、Ascend HBM/AI Core、vLLM `/metrics`、KV 快照 | 通常只能说明某段时间内的系统状态，不能精确归因到某个 Agent Step 或模型请求。 |
| P1 | Coding Agent 进程内部 | Agent 执行了多少 Step、模型调用和工具调用？每次调用在客户端看来耗时多久？ | `agent_step`、`model_request`、`tool_call` Span，Tool PID/CPU/RSS/I/O，trajectory | P1 Model Span 是客户端观察的完整等待时间，包含请求准备、网络、模型服务和客户端解析，不等于纯推理时间。 |
| P2 | vLLM API、Scheduler、EngineCore 和 KV Cache 内部 | 一个模型请求在服务内部排队、Prefill、Decode 和 KV 操作分别花了多少时间？ | request lifecycle、Scheduler iteration、Token、KV block、Prefix Cache、EngineCore token boundary | P2 默认关闭并需要在 vLLM 启动环境安装 hook；服务级资源事件可能被多个并发任务共享。 |

### P1 应该怎样理解

P1 从 Coding Agent 的视角记录业务执行路径：

```text
Agent Step
  -> Model Request
  -> Tool Call
  -> 下一 Agent Step
```

- `p1_model_span_duration_s`：Agent 发起模型调用到收到并处理响应的总等待时间。
- `tool_call_duration_s`：工具调用从开始到结束的时间。
- `agent_steps/model_requests/tool_calls`：Agent 路径长度和复杂度。
- Tool 的 CPU、RSS/PSS 和 I/O 只有使用原生 P1 Environment adapter 时才能精确采集；仅靠
  trajectory fallback 通常只能恢复命令、返回码和近似时长。

因此，P1 可以回答“Agent 在等模型还是在跑工具”，但不能单独回答模型内部为什么慢。

### P2 应该怎样理解

P2 从 vLLM 服务内部记录一次模型请求的生命周期：

```text
received -> queued -> first_scheduled -> prefill_completed
         -> first_token -> engine_finished -> finished
```

- `queue`：进入调度队列到首次被 Scheduler 选中。
- `prefill`：首次调度到 Prompt Prefill 执行完成。
- `TTFT`：服务收到请求到 EngineCore 产生首 Token。
- `decode`：首 Token 到 EngineCore 完成生成。
- `server E2E`：服务收到请求到 API 响应完成。
- Scheduler、Token、KV Block 和 Prefix Cache 指标用于解释排队、批处理、上下文计算和缓存压力。

`proxy_ttft/proxy_decode` 使用 API 输出边界，只是代理指标；只有带 `exact` 且质量检查通过的
EngineCore 边界，才适合用于精确的 Prefill、Decode 和 TPOT 分析。

### P1 和 P2 如何配合

同一次模型调用通过以下 ID 链路关联：

```text
model_call_id -> client_request_id -> server_request_id/api_request_id
              -> engine_request_id/vllm_request_id
```

关联成功后可以把 P1 客户端耗时拆为：

```text
P1 客户端模型总耗时
= 请求准备/发送
+ P2 服务端排队、Prefill、Decode
+ 响应传输/客户端解析
```

P1 存在但 P2 缺失时，仍然可以分析 Agent 路径和模型总等待时间，但不能可靠地拆分服务内部
瓶颈。P2 存在但没有 P1 关联时，该请求会保留为 `model_extra`，不能强行归入某个 Agent Step。

### P1/P2 状态含义

| 状态 | 含义 |
|---|---|
| `complete` | 该层所需事件存在且完整性检查通过。 |
| `partial` | 有数据，但生命周期、资源字段、能力或请求关联不完整。 |
| `missing` | 当前 Run 没有找到该层数据；不表示对应工作没有发生。 |
| `unsupported` | 当前版本、运行模式或 hook 目标不支持该项能力。 |
| `unavailable` | 理论上支持，但本次没有有效样本或无法证明数据质量。 |

例如 `p1_status=missing` 常见于未加载 mini-SWE adapter 或只导入了 trajectory；
`p2_status=missing` 常见于 vLLM 未启用 P2 hook、没有导入服务事件，或请求 ID 无法与 Run 关联。

## 指标目录

| 层级 | 指标或字段 | 含义与分析用途 |
|---|---|---|
| Run | `run_id`, `label`, `task_id` | 一次运行、实验标签和任务的唯一标识，用于跨批次配对。 |
| Run | `run_status`, `exit_code`, `agent_exit_status`, `term_signal` | AgentMon 子进程及 Agent 的结束状态。 |
| Run | `task_success`, `execution_success` | 工作负载是否真正成功，不等同于监控流程成功。 |
| Run | `monitoring_success`, `analysis_pipeline_success`, `overall_pipeline_success` | 分别表示采集、分析及完整流水线是否成功。 |
| Run | `duration_seconds`, `run_wall_s` | 任务墙钟时间，用于端到端性能和吞吐比较。 |
| Run | `mini_swe_version`, `agentmon_version` | Agent 和采集器版本，用于实验复现和跨版本比较。 |
| Agent | `api_calls`, `assistant_turns`, `agent_steps` | API 调用、Assistant 回合和 Agent Step 数量。 |
| Agent | `model_requests`, `tool_calls` | 模型请求和工具调用数量，反映 Agent 执行路径长度。 |
| Agent | `context_message_count`, `context_input_chars` | 每次模型调用携带的消息数和上下文字符数。 |
| Agent | `context_token_estimate`, `context_prompt_tokens` | 上下文估算 Token 和服务返回的实际 Prompt Token。 |
| Agent | `context_*_delta` | 相邻模型请求之间的上下文增长量。 |
| Agent | `context_truncation/rewrite/summarization_count` | 上下文截断、重写和摘要次数。 |
| Agent | `context_compression_status` | 请求前压缩状态。 |
| Agent | `context_compression_trigger_strategy` | 决定何时压缩的触发插件；默认为 `token_ratio`。 |
| Agent | `context_compression_trigger_reason` | 触发插件对本次 triggered/not-triggered/unresolved 决定的内容无关原因码。 |
| Agent | `context_compression_strategy` | 决定如何压缩的算法插件；默认为模型摘要 `model_summary`，`recent_groups` 仅用于显式裁剪对照。 |
| Agent | `context_compression_original/compressed/removed_tokens` | 压缩前、压缩后和移除的 Token；可信度由 measurement quality 决定。 |
| Agent | `context_compression_trigger/target_tokens` | 本次窗口计算出的触发线和目标预算，不是固定平台常数。 |
| Agent | `context_compression_applied`, `context_compression_target_achieved` | 是否真实减少上下文，以及是否达到目标预算。 |
| Agent | `context_compression_measurement_quality` | `estimated_*`、插件定义精度或 `UNRESOLVED`；估算值不得冒充 tokenizer 精确值。 |
| Agent | `context_compression_limitations/prohibited_conclusions` | 必保留组超预算、摘要语义未验证、计量不完整等限制及禁止结论。 |
| Agent | `context_compression_started/ended_mono_ns`, `context_compression_duration_s` | 本次实际压缩操作的单调时钟边界与耗时；未触发时为空。 |
| Agent | `context_summary_request_id`, `context_summary_model`, `context_summary_status` | 摘要模型请求的独立身份、模型和状态，可与 P1/P2 请求关联。 |
| Agent | `context_summary_input/output/total_tokens` | 摘要请求自身的输入、输出和总 Token，不与主请求 Token 混合。 |
| Agent | `context_summary_cached/uncached_prompt_tokens` | 摘要请求自身的缓存与未缓存 Prompt Token。 |
| Agent | `context_summary_duration_s`, `context_summary_token_quality` | 摘要模型调用耗时及 Token 计量质量。 |
| Agent | `tool_result_chars`, `tool_result_tokens_estimated` | 工具结果注入上下文的大小；Token 可能是估算值。 |
| Agent | `semantic_phase` | 当前 Step 或模型调用所属阶段，如探索、修改、测试。 |
| Agent | `path_expansion_count`, `step_action_count` | 一个 Step 展开的动作数量，用于识别 Agent 路径膨胀。 |
| Agent | `transport_attempt_count`, `retry_count` | 模型传输尝试及重试次数。 |
| Token | `prompt_tokens` | 输入模型的 Prompt Token 数。 |
| Token | `generation_tokens`, `completion_tokens` | 模型生成 Token 数。 |
| Token | `cached_tokens` | 从 Prefix Cache 命中的 Prompt Token 数。 |
| Token | `uncached_prompt_tokens` | `prompt_tokens - cached_tokens`。 |
| Token | `computed_prefill_tokens` | 本次请求实际进入 Prefill 计算的 Token 数。 |
| Token | `recomputed_prompt_tokens` | 已出现过但又被重新计算的 Prompt Token 数。 |
| Token | `token_source`, `token_quality`, `token_missing_reason` | Token 数据来源、可信度及缺失原因。 |
| Token | `instance_cost` | 模型调用成本，仅在上游提供价格或费用时有效。 |
| P1 Model | `p1_model_span_duration_s`, `model_call_e2e_s` | Agent 客户端观察到的完整模型调用时间。 |
| 请求关联 | `model_call_id`, `client_request_id` | Agent/P1 侧模型调用和客户端请求 ID。 |
| 请求关联 | `server_request_id`, `api_request_id` | OpenAI API/vLLM 服务入口请求 ID。 |
| 请求关联 | `engine_request_id`, `vllm_request_id` | Scheduler/EngineCore 内部请求 ID。 |
| 请求关联 | `canonical_request_id`, `request_id`, `request_id_aliases` | 分析层稳定请求 ID 及所有观测到的别名；不表示各层原生 ID 必然相同。 |
| 请求关联 | `p2_match_status/method/confidence/missing_reason` | P1 与 P2 请求的匹配结果、方法、置信度和缺失原因。 |
| 请求关联 | `request_identity_audit`, `p2_identity_audit` | 已匹配、P1 未匹配和孤儿 P2 请求的审计摘要，用于排查关联质量。 |
| P2 时延 | `p2_e2e_s`, `server_e2e_s` | `server_received -> API finished`，服务端总耗时。 |
| P2 时延 | `p2_queue_s` | `queued -> first_scheduled`，调度排队时间。 |
| P2 时延 | `p2_engine_ttft_s` | `queued -> EngineCore first_token`。 |
| P2 时延 | `p2_exact_ttft_s` | `server_received -> EngineCore first_token`。 |
| P2 时延 | `p2_exact_prefill_s` | `first_scheduled -> prefill_completed`，深度采集时才可用。 |
| P2 时延 | `p2_exact_decode_s` | `first_token -> engine_finished`。 |
| P2 时延 | `p2_exact_tpot_s` | 首 Token 到末 Token 的时间除以生成间隔数，即每 Token 时间。 |
| P2 时延 | `engine_residence_s` | `first_scheduled -> engine_finished`，不包含首次调度前排队。 |
| P2 时延 | `p2_proxy_ttft_s` | `received -> API first_output`，代理指标，不是精确 TTFT。 |
| P2 时延 | `p2_proxy_decode_s` | `API first_output -> API finished`，代理 Decode 时间。 |
| P2 时延 | `client_model_call_to_server_received_s` | 客户端准备、HTTP 传输和服务入口处理时间。 |
| P2 时延 | `server_finished_to_client_return_s` | 服务输出、传输及客户端解析时间。 |
| P2 时延 | `client_non_server_overhead_s` | 客户端 E2E 中未被服务端 E2E 覆盖的开销。 |
| P2 生命周期 | `received`, `queued`, `first_scheduled`, `prefill_completed` | 请求接收、入队、首次调度及 Prefill 完成时间点。 |
| P2 生命周期 | `first_token`, `first_output`, `engine_finished`, `finished` | 引擎首 Token、API 首输出、引擎完成及 API 完成时间点。 |
| P2 生命周期 | `finish_reason`, `last_token_ns` | 请求结束原因及最后一个生成 Token 的时间。 |
| Scheduler | `scheduler_iteration_count` | 请求经历的 Scheduler 迭代次数。 |
| Scheduler | `queue_depth_on_arrival`, `running_requests_on_arrival` | 请求到达时的等待队列和运行请求数。 |
| Scheduler | `running/waiting_requests_after_schedule` | 一轮调度后的运行和等待请求数。 |
| Scheduler | `max_batch_request_count` | Continuous batching 单轮包含的最大请求数。 |
| Scheduler | `scheduled_prompt/decode/total_tokens` | 每轮调度的 Prompt、Decode 和总 Token 数。 |
| Scheduler | `batch_enter_ns`, `batch_exit_ns`, `batch_residence_s` | 一个请求在一次 scheduler iteration 中从调度选择完成到执行完成的边界和耗时。 |
| Scheduler | `prefill/decode/chunked_prefill_iteration_count` | 请求经历的各类调度迭代数。 |
| Scheduler | `mixed_batch_iteration_count` | 同一批次同时包含 Prefill 和 Decode 的次数。 |
| Scheduler | `preemption_count` | 请求被抢占的次数；升高通常表示 KV 或调度压力。 |
| KV Cache | `kv_usage_before/after/delta` | 请求操作前后的 KV Cache 使用率及变化。 |
| KV Cache | `allocated/cached/cache_hit_block_count` | 分配、缓存及命中的 KV Block 数。 |
| KV Cache | `freed/evicted_block_count` | 释放和淘汰的 KV Block 数。 |
| KV Cache | `kv_residency_s` | 请求 KV Block 从分配到释放的驻留时间。 |
| KV Cache | `kv_pool_allocated_bytes_per_rank` | 每个 TP rank 的 KV Pool 分配大小。 |
| KV Cache | `kv_snapshot_usage_ratio` | 采样时的服务级 KV Cache 使用率。 |
| Prefix Cache | `prefix_cache_status` | Prefix Cache 状态：`enabled/disabled/unknown`。 |
| Prefix Cache | `prefix_cache_hit_rate`, `cache_reuse_rate` | Prompt Cache 命中比例；禁用时必须为 N/A。 |
| Prefix Cache | `recomputed_prompt_rate` | Prompt 被重新计算的比例，越高表示缓存复用越差。 |
| MoE 路由 | `moe_routing_status`, `moe_routing_mode` | 路由数据的完整性和 `summary/trace` 采集模式。 |
| MoE 路由 | `moe_routed_token_count`, `moe_assignment_count` | 实际捕获的路由 token position 数与 top-k 逻辑专家分配总数。 |
| MoE 路由 | `moe_max_expert_share` | 各层中单一逻辑专家最大分配占比；高值表示路由集中。 |
| MoE 路由 | `moe_min_normalized_entropy` | 各层归一化路由熵的最小值；低值表示分布偏斜。 |
| MoE 路由 | `moe_max_load_coefficient_of_variation` | 各层专家分配变异系数的最大值。 |
| MoE 路由 | `moe_token_alignment_counts` | 捕获 shape 与请求 token 记账的 `MATCH/MISMATCH/UNKNOWN` 计数。 |
| Tool | `tool_name`, `command`, `executable`, `command_hash` | 工具类型、命令、可执行程序及脱敏后的命令标识。 |
| Tool | `tool_call_duration_s`, `approx_tool_duration_s` | 原生或 trajectory 推算的工具执行时间。 |
| Tool | `returncode`, `tool_status`, `timed_out` | 命令返回码、工具状态和超时状态。 |
| Tool | `stdout/stderr/output_sha256`, `error_fingerprint` | 不保存正文时仍可用于结果去重和错误集合归并的内容哈希。 |
| Tool | `test_passed/failed/error/skipped_count` | 测试命令输出中可确定解析出的结果计数；无法识别时为 null。 |
| Tool | `code_diff_observed`, `code_diff_sha256/bytes/changed_file_count` | 显式 `git diff` 工具调用的内容无关摘要；不代表所有工作区改动都已捕获。 |
| Tool | `timeout_limit_s`, `termination_signal`, `exception_type` | 超时阈值、终止信号和异常类型。 |
| Tool | `tool_error_count` | 工具执行或监控本身发生异常的次数。 |
| Tool | `command_nonzero_return_count` | 命令非零返回次数，不自动代表整个任务失败。 |
| Tool | `pid`, `pgid`, `pid_starttime_ticks` | 工具进程、进程组及 PID 身份校验信息。 |
| Tool | `output/stdout/stderr_bytes` | 工具输出规模。 |
| Tool 资源 | `tool_cpu_time_s`, `cpu_user/system_time_s` | 工具进程树实际消耗的 CPU 时间。 |
| Tool 资源 | `tool_cpu_wall_ratio` | CPU 时间除以墙钟时间；接近 1 表示单核 CPU 密集。 |
| Tool 资源 | `tool_peak_rss/pss_bytes` | 工具进程树峰值物理内存和按比例分摊内存。 |
| Tool 资源 | `tool_read/write_bytes_delta` | 工具调用期间磁盘读取和写入量。 |
| Tool 资源 | `minor/major_faults_delta` | 工具进程缺页次数。 |
| Tool 资源 | `voluntary/involuntary_context_switches_delta` | 主动和被动上下文切换次数。 |
| Process | `step_agent_cpu_time_s`, `step_vllm_cpu_time_s` | Step 窗口内 Agent 和 vLLM 进程 CPU 时间。 |
| Process | `*_cpu_wall_ratio` | CPU 时间除以窗口墙钟时间，用于判断 CPU 是否为瓶颈。 |
| Process | `peak_rss_bytes`, `peak_pss_bytes` | Agent、vLLM 或 Tool 进程峰值内存。 |
| Process | `agent_process_count_max/active_last` | 运行期最大进程数及最后活跃窗口的进程数。 |
| Host | `cpu_util_pct`, `cpu_per_core`, `load1_per_cpu`, `run_queue` | Host CPU 利用率、负载及可运行任务压力。 |
| Host | `context_switches_per_s`, `scheduler_latency` | Host 调度开销和调度延迟。 |
| Host | `memory_available_bytes`, `page_faults`, `swap_in/out` | 可用内存、缺页和交换压力。 |
| Host | `cpu/memory/io_psi` | Linux PSI 压力，表示任务因资源不足而被阻塞的程度。 |
| Host | `numa_local/remote/miss_pages_per_s` | NUMA 本地、远端访问及 miss 情况。 |
| Host | `disk_iops`, `iowait` | 磁盘操作及 CPU I/O 等待。 |
| Ascend | `device_id`, `rank_id` | NPU 设备和 TP rank 标识。 |
| Ascend | `hbm_used_bytes`, `peak/avg_hbm_used_bytes` | NPU 显存使用量及窗口峰值、平均值。 |
| Ascend | `aicore_util_pct`, `max/avg_aicore_util_pct` | AI Core 利用率。 |
| Ascend | `temperature_c`, `power_w`, `frequency_mhz` | 温度、功耗和频率。 |
| Ascend | `hbm_bandwidth_bytes_per_s` | HBM 带宽；取决于当前 `npu-smi` 是否提供。 |
| Ascend | `target_*`, `host_*` | 目标设备和整机设备指标分开，防止错误归因。 |
| Ascend | `observation_scope`, `request_attribution` | `shared_service_observation` 等资源观测和归因范围。 |
| Ascend | `parser_confidence`, `hbm/aicore_quality` | `npu-smi` 解析可信度；未解析到时为 N/A，不是 0。 |
| vLLM P0 | `metric_count`, `semantic_matches`, `counter_deltas` | `/metrics` 中识别到的指标数、语义匹配和计数器增量。 |
| vLLM P0 | `vllm_request_count`, `preemption_count` | 服务请求数和抢占次数。 |
| 数据质量 | `invalid_json_lines`, `duplicate_event_ids` | 损坏 JSONL 行和重复事件 ID 数。 |
| 数据质量 | `sequence_regressions` | 同一进程事件序列重复或倒退次数。 |
| 数据质量 | `p0_jsonl_sqlite_difference`, `event_count_mismatch` | JSONL 与 SQLite 事件数量差异。 |
| 数据质量 | `open/abandoned/orphan_end/negative_duration_spans` | 未闭合、遗弃、孤立结束及负时长 Span。 |
| 数据质量 | `p2_lifecycle_complete_rate`, `missing_events` | P2 生命周期完整率和缺失阶段。 |
| 数据质量 | `adapter_error_count`, `capability_required_failure_count` | P2 适配器错误和必需能力检查失败数。 |
| 数据质量 | `quality`, `source`, `clock_domain`, `measurement_boundary` | 指标精确度、来源、时钟域及测量边界。 |
| 数据质量 | `coverage`, `valid_sample_count` | 指标有效样本数和覆盖率；缺失样本不会按 0 聚合。 |
| 发布门禁 | `publishability_status` | `PUBLISHABLE/UNPUBLISHABLE`，决定能否发布确定性结论。 |
| Batch | `run/success/failure_count`, `success_rate` | 批次任务规模和成功率。 |
| Batch | 各时延的 `total/mean/p50/p90/p95/max` | 批次时延分布和长尾情况。 |
| Batch | Token/API/Model/Tool 的 `total/mean` | 批次工作量和平均任务复杂度。 |
| Batch 吞吐 | `pipeline_attempt_throughput` | attempted 除以完整流水线墙钟时间。 |
| Batch 吞吐 | `execution_attempt_throughput` | attempted 除以 workload 阶段墙钟时间。 |
| Batch 吞吐 | `observed_success_throughput` | 成功任务数除以 workload 阶段墙钟时间。 |
| Batch 吞吐 | `counterfactual_success_only_throughput` | 排除失败开销后的反事实吞吐，不能当作实测值。 |
| Batch 吞吐 | `generation_tokens_per_second` | 批次生成 Token 总数除以执行时间。 |
| Timeline | `active_run/model/tool_count` | 各时间点并行运行的任务、模型请求和工具数。 |
| Timeline | `phase_summary`, `tail_summary`, `transition_gap` | 批次阶段、Drain/tail 以及 Model/Tool 转换空隙。 |
| Timeline | `concurrency_integral_check` | Model/Tool 时间轴积分与逐任务时长之和是否守恒。 |
| 并发分析 | `inflight_model_requests`, `inflight_uncached_tokens` | 实际在途请求数及未缓存 Token 工作量。 |
| 自动分析 | `tail_cause`, `evidence`, `confidence` | 长上下文、排队、重计算、工具超时、Host 压力等长尾分类。 |
| 对比分析 | `workload_amplification_s`, `system_slowdown_s`, `unexplained_residual_s` | 同任务跨并发的工作量增长、系统减速和未解释残差。 |
| 自监控 | `trajectory_import/p1_analysis/p2_matching/report_render/*_wall_s` | AgentMon 后处理各阶段耗时，用于发现分析流水线自身开销。 |

## 统一判读规则

1. `*_s` 表示秒，`*_ns` 表示纳秒，`*_bytes` 表示字节，`*_pct` 表示百分比，`*_ratio` 和 `*_rate` 通常为 `0` 到 `1`。
2. `null` 或 `N/A` 表示没有有效观测，不能按 `0` 参与求和、平均或比例计算。
3. Prefix Cache 为 `disabled` 时，命中率必须显示 `N/A`，不能显示 `0%`。
4. `exact` 可用于定量结论；`derived` 是派生值；`proxy` 只适合趋势分析；`unavailable` 不应参与结论。
5. Step/Model 级 NPU、Scheduler 和全局 KV 指标通常是共享服务时间窗观测，不表示某个请求独占这些资源。
6. MoE expert ID 是逻辑 router ID；没有同时间窗 expert map 时不能映射为物理 rank，也不能从路由 ID 推断专家执行时间。
7. `command_nonzero_return_count` 只表示工具命令返回非零，不能单独判定整个 Agent 任务失败。
8. 比较不同批次前，应确认任务集、模型配置、vLLM/AgentMon 版本、Prefix Cache、MoE 采集模式和 P2 关联方式一致。

## 对应源码和文档

- 单 Run 与聚合字段：`src/agentmon/reporting/summary.py`
- Step CSV 完整字段：`scripts/agentmon_step_sequence.py`
- Canonical 指标定义：`src/agentmon/reporting/canonical.py`
- 完整运行分析说明：`docs/agent_runtime_full_usage_zh.md`
- P2 深度采集说明：`docs/p2_deep_agent_workload_zh.md`
