# P2 深度采集与 Agent Workload 使用说明

本文说明 AgentMon 的 P2 深度采集和 mini-SWE Agent workload 指标。两部分都是
附加观测，不修改 vLLM 或 mini-SWE 安装。

## 1. 开关与兼容性

P2 Hook 安装仍是显式动作：

```bash
export AGENTMON_VLLM_P2_ENABLE=1
```

Hook 安装成功后，Deep P2 现在默认开启，自动尝试采集真实执行锚定的 prefill
边界、scheduler iteration、continuous batching 构成和完整 KV lifecycle。正式实验
可以显式写出下列值作为配置留痕，但不再是启用 Deep 的必要条件：

```bash
export AGENTMON_VLLM_P2_DEEP=1
```

只有需要兼容旧版目标、定位 Hook 开销或做低开销对照时才关闭：

```bash
export AGENTMON_VLLM_P2_DEEP=false
```

显式假值支持 `0/false/no/off`。Deep 关闭时只保留基础生命周期采集，Prefill 完成、
逐请求 Batch 成员关系、逐轮 Prefill/Decode Token 和 KV Block 生命周期保持
UNKNOWN/PARTIAL，不从 API 输出或缓存查询结果猜测。

深度模式会同时尝试 hook：

```text
Scheduler.update_from_output
KVCacheManager.get_computed_blocks
KVCacheManager.allocate_slots
KVCacheManager.cache_blocks
KVCacheManager.free
KVCacheManager.evict_blocks
KVCacheManager.reset_prefix_cache
```

其中 `Scheduler.update_from_output` 是执行锚定 prefill 边界的必要目标；KV 方法按
服务器实际类逐项安装。缺少某个 KV 方法时 capability 为 `partial`，对应 operation
保持不可用而不是填 0，也不会改变 vLLM 原调用结果。只有全部 KV 方法均存在时，
才能把本次实验称为完整 KV lifecycle 采集。

旧开关 `AGENTMON_VLLM_P2_HOOK_GET_COMPUTED_BLOCKS=1` 和
`AGENTMON_VLLM_P2_HOOK_KV_BLOCKS=1` 仍兼容。深度模式已经包含它们，不需要重复设置。
它会增加事件量和 scheduler/KV 热路径上的 Python 观测开销，应先用独立实验服务冒烟。

可限制单个 scheduler aggregate 事件中保存的 request summary 数量：

```bash
export AGENTMON_VLLM_P2_DEEP_MAX_BATCH_REQUESTS=256
```

该限制只截断 aggregate 事件中的列表；request-keyed scheduler 事件仍逐请求发出。

## 2. 真实 prefill 边界

`Scheduler.schedule()` 返回时，prompt token 只是被排入该轮，不能视为执行完成。
深度 hook 保存该 `SchedulerOutput` 的对象关联，等同一个对象进入
`Scheduler.update_from_output()` 且原函数成功返回后，才发出：

```text
vllm.request.prefill_completed
```

关键字段：

```text
prefill_boundary_source=scheduler.update_from_output
prefill_boundary_quality=engine_execution_observed
scheduler_iteration_id=<iteration>
```

它表示该轮模型执行结果已回到 EngineCore，并跨过 prompt completion boundary；
不是单个 NPU kernel 的 profiler 时间戳。

## 3. Scheduler 与 Continuous Batching

每轮可产生：

```text
vllm.scheduler.iteration.scheduled
vllm.request.scheduler_iteration
vllm.request.scheduler_iteration_completed
vllm.scheduler.iteration.completed
```

`scheduler_iteration` 记录请求被选入该轮时的 phase、位置、token 和 schedule 边界；
`scheduler_iteration_completed` 用相同 request/iteration ID 记录 `batch_enter_ns`、
`batch_exit_ns` 和 `batch_residence_ns`。可分析：batch request 数、总 scheduled tokens、
逐轮 scheduled prefill/decode tokens、新请求/恢复请求、prefill/decode 请求数、chunked
prefill、混合 prefill+decode batch、preemption，以及 schedule 到执行完成耗时。
这些是共享服务观测。aggregate 事件列出 request ID，但服务资源不能据此解释为某个请求独占。

## 4. KV Lifecycle

深度模式记录 `lookup/allocate/cache/free/evict/reset`。request-keyed 事件可聚合：

```text
cached_block_count
cache_hit_block_count
allocated_block_count
freed_block_count
evicted_block_count
kv_usage_before / kv_usage_after
kv_lifecycle_operation_counts
```

Prefix cache disabled 时，hit rate 必须保持 `null / N/A`，不能解释为 0%。

## 5. Agent Workload

P1 mini-SWE adapters 自动记录以下内容，无需新开关：

```text
context message/character/token-estimate delta
exact prompt token delta（provider usage 可用时）
tool-result message/character/token estimate
context truncation/rewrite/summarization signal
LiteLLM transport attempt/retry count
step/model semantic phase
action fan-out/path expansion
format retry signal
```

`tool_result_tokens_estimated` 默认使用 `ceil(chars / 4)`，并明确标记
`tool_result_token_quality=estimated_chars_div_4`。可通过
`AGENTMON_TOOL_RESULT_CHARS_PER_TOKEN` 调整估算因子。没有 tokenizer 时不得将它当作精确 token。

AgentMon 只持久化结构、计数和 quality，不写模型消息、tool output 或命令正文。

## 6. Client / Server 分段

当 P1 model span 与 P2 request 精确关联且时钟域一致时，报告和 step CSV 增加：

```text
client_model_call_to_server_received_s
server_finished_to_client_return_s
client_observed_model_e2e_s
client_non_server_overhead_s
exact_tpot_s
kv_residency_s
kv_residency_quality
```

第一项包含客户端准备、HTTP 传输和服务入口处理；第二项包含服务输出、传输和客户端解析。
它们不是纯网络时延。跨主机或 clock domain 不一致时对应值为 null，并保留 quality。
`exact_tpot_s` 使用 EngineCore first/last token 边界；`kv_residency_s` 只在同一请求同时
存在 allocate/free block event 时计算。

## 7. 服务器启动与检查

启动 vLLM 前：

```bash
export PYTHONPATH="$AGENTMON_REPO/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1
export AGENTMON_VLLM_P2_ENABLE=1
export AGENTMON_VLLM_P2_DEEP=1
export AGENTMON_EVENTS_PATH=/secure/path/vllm-p2/events.jsonl
umask 077
```

冒烟后检查：

```bash
grep -o '"event_type":"[^"]*"' "$AGENTMON_EVENTS_PATH" | sort | uniq -c
```

至少应看到 lifecycle；深度请求完成后还应看到 scheduler iteration、
`prefill_completed` 和 KV lifecycle 事件。若 capability 中某个方法在服务器 fork 不存在，
hook 会记录 skipped/error 并保持 vLLM 原行为。

生成最终文件：

```bash
"$AGENTMON_PY" -m agentmon report "$RUN_ID"

"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" \
  "$AGENTMON_HOME/runs/$RUN_ID" \
  --format csv \
  > "$AGENTMON_HOME/runs/$RUN_ID/step_sequence.csv"
```

详细字段位于 `report.json`、`summary.json` 和 `step_sequence.csv`；简洁表格不会打印
完整 scheduler request list 或 vLLM counter deltas。
