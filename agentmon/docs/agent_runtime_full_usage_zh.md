# AgentMon Agent Runtime 完整使用教程

本文用于昇腾服务器上的完整实验流程：多个 mini-SWE 任务共享同一个
vLLM/vLLM-Ascend 服务，并采集 P0、P1、P2、工具进程、KV Cache 和 NPU 指标。

## 1. 解压与环境变量

```bash
export AGENTMON_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/agentmon/bin/python
export AGENTMON_HOME=/data/node0_disk3/chenyang/agentmon
export AGENTMON_REPO=/absolute/path/to/agentmon

mkdir -p "$AGENTMON_REPO"
tar -xzf /path/to/agentmon-server-ready-p2-deep-workload-20260714-src.tar.gz \
  -C "$AGENTMON_REPO" \
  --strip-components=1

export PYTHONPATH="$AGENTMON_REPO/src"
```

先验证源码和 CLI：

```bash
"$AGENTMON_PY" -m compileall -q \
  "$AGENTMON_REPO/src" \
  "$AGENTMON_REPO/scripts" \
  "$AGENTMON_REPO/tests"

PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" -m unittest discover \
  -s "$AGENTMON_REPO/tests" \
  -v

PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" -m agentmon --help
```

## 2. 启动完整 P2 vLLM 服务

P2 Hook 安装默认关闭，`AGENTMON_VLLM_P2_ENABLE=1` 必须在启动 vLLM API 和
EngineCore 子进程之前设置。Hook 安装后 Deep P2 默认开启，不再要求额外设置
`AGENTMON_VLLM_P2_DEEP=1`；下面保留显式值用于实验配置留痕：

```bash
export P2_ROOT=/data/node0_disk3/chenyang/agentmon/p2/$(date +%Y%m%d-%H%M%S)

umask 077
mkdir -m 700 -p "$P2_ROOT"

export AGENTMON_VLLM_P2_ENABLE=1
export AGENTMON_VLLM_P2_DEEP=1
export AGENTMON_EVENTS_PATH="$P2_ROOT/events.jsonl"
export PYTHONPATH="$AGENTMON_REPO/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1
```

deep 模式用于采集 scheduler iteration、execution-anchored prefill、
cached/uncached/computed-prefill token、带 quality 的 recomputed token、KV before/after
和 block allocate/free/evict。
它有额外观测开销；正式对比实验应保持开关一致。
只有兼容性或低开销对照需要在服务启动前设置
`AGENTMON_VLLM_P2_DEEP=false`。关闭后相关精细字段保持 UNKNOWN/PARTIAL，不用代理值
冒充 Deep P2 数据。

使用原来能启动成功的完整 vLLM 命令，不要把文档中的省略号当成命令。启动前检查：

```bash
npu-smi info
ps -eo pid,ppid,cmd | grep -E '[v]llm|[a]pi_server'
ss -ltnp | grep -E ':8000|:8001|:8002'
```

保留完整启动命令，供 batch manifest 记录：

```bash
printf '%s\n' '<完整 vLLM 启动命令>' > "$P2_ROOT/vllm-command.txt"
chmod 600 "$P2_ROOT/vllm-command.txt"
```

服务启动后确认 capability 事件：

```bash
grep -m1 '"event_type":"vllm.adapter.capability"' \
  "$AGENTMON_EVENTS_PATH" \
  | "$AGENTMON_PY" -m json.tool
```

应看到 vLLM `0.18.0`、vLLM-Ascend 版本、真实 hook 目标、可见 NPU 和两个
hot-path 开关。

### 2.1 可选 MoE 路由监测

MoE 路由默认关闭。只采集负载均衡摘要时，在启动服务前增加：

```bash
export AGENTMON_VLLM_P2_MOE=summary
```

短时诊断需要保存每个 token position、每层、top-k 的逻辑专家 ID 时使用：

```bash
export AGENTMON_VLLM_P2_MOE=trace
export AGENTMON_VLLM_P2_MOE_CHUNK_TOKENS=256
```

两种模式都必须在原 vLLM serve 命令中增加：

```text
--enable-return-routed-experts
```

服务启动后检查：

```bash
grep -m1 '"event_type":"vllm.moe.routing.capability"' \
  "$AGENTMON_EVENTS_PATH" | "$AGENTMON_PY" -m json.tool
```

`status=SUPPORTED` 且 `enable_return_routed_experts=true` 才表示配置路径已确认。
首个真实请求结束后还应出现 `vllm.moe.routing.summary`。`summary` 适合长实验；
`trace` 会额外写压缩原始路由，应先用短任务评估事件体积和性能开销。

这里的 expert ID 是逻辑路由 ID，不是物理 NPU/rank，也不是专家算子耗时。
完整字段、算法和禁止结论见 `docs/p2_vllm_adapter.md`。

## Runtime 优化总开关

Runtime 的调度、Optimizer、Model Gate、Tool Pool 和工具子进程资源约束由同一
总开关控制：

```toml
[runtime_optimization]
enabled = true
```

临时关闭所有主动优化动作：

```bash
export AGENTMON_RUNTIME_OPTIMIZATION_ENABLED=false
```

关闭后采集和 TaskState 仍继续，但 Scheduler 全放行，Optimizer 不产生压力动作，
Model Gate/Tool Pool 不拒绝请求，上下文压缩不改写 prompt，nice/内存/CPU affinity
不施加。这样可在相同观测
口径下做 Runtime 优化 A/B。各策略原理和子开关见
`docs/runtime_speed_optimization_algorithms_zh.md`。

### Coding Agent 上下文压缩

目标服务器示例已显式启用请求前上下文压缩：

```toml
[context_compression]
enabled = true
trigger_strategy = "token_ratio"
strategy = "model_summary"
plugin_modules = []
trigger_ratio = 0.80
target_ratio = 0.20
window_tokens = 0
estimator_chars_per_token = 4.0
trigger_options_json = '{}'
strategy_options_json = '{"min_recent_groups":1}'
```

`window_tokens=0` 表示使用 `[model].max_model_len`。也可以设置正整数覆盖它。当估算
上下文达到 `ceil(window_tokens * trigger_ratio)` 时，默认 `token_ratio` 触发插件会启动
压缩，算法以
`floor(window_tokens * target_ratio)` 为预算。内置 `model_summary` 保留
system/developer 指令、初始任务，以及最近的完整消息组；assistant 与其后的
tool/observation 消息作为原子组处理。更早的完整消息组由一个独立的模型请求生成
continuation summary，再以一条摘要消息插回原位置。默认 Summary Provider 由 mini-SWE
Adapter 提供并复用当前模型，也可以在 `strategy_options_json` 中指定兼容的模型名。
它只修改本次发给模型的消息副本，mini-SWE 内部完整 trajectory 不变。

摘要请求使用独立 request ID 和 `request_purpose=context_summary`，会像其他模型请求一样
进入 P1/P2 关联，但不会与主请求混为同一个请求。压缩决策记录压缩开始、结束、耗时，
以及摘要输入、输出、Cached、Uncached Prompt Token 和 token quality；事件中不保存旧
上下文或摘要正文。如果摘要调用失败、返回空文本或插件异常，状态为 `ERROR_BYPASS`，
主请求原样放行，不退回静默截断。若必保留消息与摘要合计超过目标，则保留正确性并标记
`PARTIAL`。上下文计数默认是序列化字符数除以已配置比例，摘要调用 token 优先采用 API
usage。没有窗口或计数时状态保持 `UNRESOLVED`。

旧的确定性结构裁剪仍以 `strategy="recent_groups"` 保留，主要用于显式对照实验，不再是
默认压缩算法。

触发策略和压缩算法是两个独立插件点。外部模块在导入时可分别调用
`register_context_compression_trigger(...)` 和
`register_context_compression_strategy(...)`，然后配置：

```toml
[context_compression]
enabled = true
trigger_strategy = "low_progress_or_tool_growth"
strategy = "my_semantic_summary"
plugin_modules = ["my_agentmon_plugins.context_summary"]
trigger_ratio = 0.80
target_ratio = 0.20
trigger_options_json = '{"low_progress_steps":3}'
strategy_options_json = '{"summary_model":"local-summary-model"}'
```

`trigger_strategy` 可以实现回合数、低进展、工具输出增长或显式标记等触发方式；
`strategy` 可以实现抽取、摘要、去重或检索式记忆。它们可单独更换，不要把框架角色
或模型名写进插件通用契约。

查看当前进程已注册策略：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon strategy list \
  --domain context_compression
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon strategy list \
  --domain context_compression_trigger
```

## 3. 准备任务表

任务表可以是 TSV、CSV 或 JSONL。TSV 示例：

```text
instance_id\tproblem_statement\trepo
py-001\t修复 calculator 测试\t/path/to/worktree/py-001
py-002\t修复 parser 测试\t/path/to/worktree/py-002
```

列名可以在 `--command-template` 中以 `{列名}` 引用。每个任务必须有唯一
`task_id`、`id` 或 `instance_id`。

## 4. 跑 5 并发或 10 并发

5 并发和 10 并发使用同一个脚本，只修改 `--concurrency`。所有任务共享同一个
vLLM endpoint 和 0,1,2,3 四张卡。

`scripts/agentmon_batch_runtime.py` 现在是兼容入口；实际实现位于
`agentmon.batch` 包内。排查时优先看 `agentmon.batch.runtime`、`workload`、
`analysis_runner`、`policy`、`artifacts`、`manifest`、`resources` 和 `summary`，
不要把新逻辑继续写进脚本 wrapper。

```bash
export TASKS=/path/to/tasks.tsv
export AGENTMON_ENV=/data/node0_disk3/chenyang/new_version/agentmon-latest-20260708.env
export BATCH_ROOT="$AGENTMON_HOME/batches/$(date +%Y%m%d-%H%M%S)-dist50-c5"

PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_batch_runtime.py" \
  --batch-root "$BATCH_ROOT" \
  --task-file "$TASKS" \
  --task-id-column instance_id \
  --expected-task-count 50 \
  --concurrency 5 \
  --analysis-workers 8 \
  --p1-mode required \
  --agentmon-python "$AGENTMON_PY" \
  --agentmon-config "$AGENTMON_REPO/examples/config.target.toml" \
  --output-root "$AGENTMON_HOME/runs" \
  --env-file "$AGENTMON_ENV" \
  --p2-events "$AGENTMON_EVENTS_PATH" \
  --p2-filter-mode auto \
  --p2-quality direct \
  --min-p2-match-rate 0.95 \
  --min-p2-lifecycle-rate 0.95 \
  --ascend-devices 0,1,2,3 \
  --task-package /path/to/task-package-manifest-or-archive \
  --model-path /path/to/qwen3-coder-30b \
  --vllm-launch-command-file "$P2_ROOT/vllm-command.txt" \
  --temperature 0 \
  --seed 1 \
  --metadata benchmark=dist50 \
  --metadata experiment=c5-repeat1 \
  --command-template '/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/mini -m hosted_vllm/qwen3-coder-30b -t "{problem_statement}" -o {workspace}/mini.traj.json' \
  --continue-on-error
```

要跑 10 并发：

```bash
--concurrency 10
```

代码没有 5 并发上限。是否能跑 10、15、20 并发由 vLLM queue、KV、HBM、
max_num_seqs、max_num_batched_tokens 和任务失败率决定。每组实验要使用新的
`BATCH_ROOT`。

`--p1-mode required` 会给简单的 `mini`/`mini-swe-agent` 命令自动补上 AgentMon
三层 adapter，并在原生 P1 spans 缺失时把 pipeline 标记为失败。复杂管道、重定向
命令不会被自动改写，此时应在 mini 命令中显式加入三个 class 参数。

### 4.1 批跑工作区与源码防污染门禁

正式批跑保持以下默认值，不需要额外填写：

```text
--workspace-policy isolated
--runtime-source-isolation snapshot
--runtime-source-integrity strict
```

runner 会在第一个任务启动前解析全部 `workspace` 和 `cwd`，包括符号链接的真实目标。
默认要求每个任务使用唯一的
`<batch-root>/workspaces/<task-id>`，`cwd` 必须位于该 workspace 内，并拒绝任何与
AgentMon 源码目录相同、位于其内部或包含该源码目录的路径。`BATCH_ROOT` 和
`AGENTMON_HOME` 必须是实验数据目录，不能指向解包后的 `AGENTMON_REPO`。

如果任务已经在 batch root 外物化，必须显式声明：

```bash
--workspace-policy external \
--workspace-template '{repo}' \
--cwd-template '{workspace}'
```

`external` 只允许 workspace 位于 batch root 外，不会放开 AgentMon 源码保护，也不会
允许 `cwd` 逃出 workspace。两个 task 指向同一 repo 仍会在执行前失败。

每个 worker 实际从 batch 内的只读 Runtime 源码快照导入 AgentMon，原源码路径会从
worker 的 `PYTHONPATH` 和相关 `AGENTMON_*` 环境变量中移除。workload 前后同时校验
原源码和快照哈希。证据位于：

```text
$BATCH_ROOT/workspace-contracts.json
$BATCH_ROOT/runtime-source-isolation.json
$BATCH_ROOT/runtime-source-integrity.json
```

路径门禁失败时不会启动任何任务。源码哈希变化时 strict 模式停止后续分析并返回非零；
报告会列出新增、修改和删除的文件。不要使用 `warn`/`off` 绕过正式实验门禁。

批次是否有效不能再由 TSV 行数判断。`batch-summary.json.completion` 和 manifest
分别保存：

```text
controller_finished
result_table_complete
workload_attempt_complete
analysis_complete
experiment_valid
validity_status = VALID | INVALID | UNRESOLVED
```

只要 `agentmon_run_id_missing > 0`，即使命令返回 0、结果行数等于任务数，批次也会是
`INVALID`，禁止用于吞吐、时延、A/B、瓶颈或 optimizer 效果结论。排查顺序固定为
`workspace-contracts.json`、`runtime-source-integrity.json`、
`batch-summary.json.completion`、最后是对应任务的 stderr 和 run dir。

工具进程树默认每 0.1 秒采一次 RSS/PSS/I/O：

```bash
export AGENTMON_TOOL_SAMPLE_INTERVAL_SECONDS=0.1
```

更短间隔覆盖率更高，但采集开销也更大；设置为 `0` 时只保留初始采样。工具 CPU
另由 rusage 前后增量采集，短命令不会仅因 P0 采样周期较长而丢失 CPU time。

### 执行和分析分离

Deep P2 会产生很大的 service `events.jsonl`。正式长跑建议先只跑 workload，避免
P2 index、report、step sequence 和标准分析占用 vLLM 实验窗口：

```bash
# 在上面的完整 runner 命令中增加：
--defer-analysis
```

`--defer-analysis` 是 `--execution-only` 的更直观别名；两者行为相同。runner 会写出
`batch-results.tsv`、`batch-manifest.json`、`batch-summary.json` 和
`pipeline-profile.json`，并标记 `analysis_deferred=true`。这不是分析失败，只表示结果要
后续离线补齐。

也可以设置一个显式保护阈值，防止 P2 文件过大时误触发同步分析：

```bash
--defer-analysis-if-p2-over-bytes 5000000000
```

该阈值必须由实验配置显式给出；AgentMon 不会把某个固定 GB 数写死为通用规则。

任务结束后，在相同 `BATCH_ROOT`、task file、env、P2 source 和分析参数下执行：

```bash
export P2_INDEX_CACHE_ROOT="$AGENTMON_HOME/p2-index-cache"
mkdir -p "$P2_INDEX_CACHE_ROOT"

PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_batch_runtime.py" \
  --batch-root "$BATCH_ROOT" \
  --task-file "$TASKS" \
  --task-id-column instance_id \
  --analyze-existing \
  --analysis-preset standard \
  --analysis-workers 1 \
  --p1-mode required \
  --agentmon-python "$AGENTMON_PY" \
  --output-root "$AGENTMON_HOME/runs" \
  --env-file "$AGENTMON_ENV" \
  --p2-events "$AGENTMON_EVENTS_PATH" \
  --p2-index-cache-root "$P2_INDEX_CACHE_ROOT" \
  --ascend-devices 0,1,2,3
```

GB 级 P2 JSONL 优先使用 `--analysis-workers 1` 或 `2`；并发分析主要争抢磁盘 I/O
和 JSON 解析，worker 过多可能更慢。离线分析时才会构建 `_p2_index/` 缓存，并复用
该缓存做每任务 P2 过滤。

`--p2-index-cache-root` 可让多个 batch root 复用同一个 service P2 source 的索引；
如果 300-400 组实验来自同一个或重复使用的 P2 文件，后续组可以直接命中缓存，避免
重复解析 GB 级 JSONL。每个不同 P2 文件仍需首次建索引一次。

分析 preset：

```text
full      兼容旧流程，写完整 p2-service-global.events.jsonl，生成 all-steps.csv 和 timeline
standard  推荐长跑离线分析；不复制完整 service-global P2，不在缺 task 窗口时 fallback 全局流，仍生成每任务 report/step/timeline
quick     快速巡检；在 standard 基础上跳过 all-steps.csv 合并和 batch timeline，不作为最终发布分析
```

恢复分析不需要 `--command-template`；它读取已有 `batch-results.tsv`。task file 和
关键实验参数仍要保持一致，便于重建任务元数据和做可比性检查。

## 5. P2 并发关联为什么不用 byte offset

并发任务的 P2 事件在同一个 service `events.jsonl` 中交错，不能按每个任务的
起止 byte offset 归因。runner 优先使用：

```text
payload.agentmon_run_id == AgentMon run_id
```

如果 header/run-id 没传到服务，则使用：

```text
mini trajectory assistant.extra.response.id
== P2 payload.api_request_id
```

每个 run 只导入 request-keyed 事件。KV snapshot、capability、NPU/HBM 等服务级
观测保留在 service timeline 中，step CSV 会标记：

```text
observation_scope = shared_service_observation
request_attribution = not_request_attributed
```

不能把它们解释成某个任务独占消耗。

`raw/p2_import.json` 会保存 matched/unmatched response ID、时间窗 extras 和以下拆分
计数：

```text
p2_request_count
p2_request_event_count
p2_request_keyed_event_count
p2_global_event_count
p2_sample_event_count
p2_total_event_count
multiple_p2_api_request_id_count
multiple_p2_api_request_ids
```

`p2_requests_in_task_window_not_in_trajectory_count` 只是共享窗口诊断，可能包含其他
并发任务，不能强行塞入相邻 step。

如果一个 `api_request_id` 对应多个不同的 engine request ID，则状态为 `multiple`，
不会计入 exact match，也不会任选一条归因。原始请求行仍保留为 `model_extra` 供审计，
受影响 ID 会写入 `batch-results.tsv` 和 `batch-summary.json`。

`report.json.vllm_p2.requests[]` 与 `step_sequence.json` 的模型 item 都提供稳定请求
身份字段：`canonical_request_id`、`canonical_request_id_kind`、`request_id`、
`request_id_kind`、`request_id_aliases`、`response_id` 和
`trajectory_request_id`。其中 `request_id` 是分析层规范 ID，不表示 P1、API 入口和
EngineCore 的原生 ID 必然相同。`report.json.client_server.request_identity_audit`
和 `step_sequence.json.p2_identity_audit` 会列出已匹配请求、P1 有但 P2 缺失的请求，
以及 P2 有但未匹配 P1 的孤儿请求。共享服务里的孤儿 P2 只能作为诊断证据，不能按时间邻近
强行归入某个 task。

## 6. Batch 输出

```text
$BATCH_ROOT/batch-manifest.json
$BATCH_ROOT/batch-summary.json
$BATCH_ROOT/batch-results.tsv
$BATCH_ROOT/p2-service-global.events.jsonl        # full preset 或显式写全局副本时生成
$BATCH_ROOT/step-sequence/all-steps.csv           # quick preset 会跳过
$BATCH_ROOT/step-sequence/<task_id>/step_sequence.json
$BATCH_ROOT/step-sequence/<task_id>/step_sequence.csv
$BATCH_ROOT/step-sequence/<task_id>/step_sequence.txt
$BATCH_ROOT/timeline/timeline_report.md           # quick preset 会跳过
$BATCH_ROOT/timeline/batch_timeline_1s.csv        # quick preset 会跳过
$BATCH_ROOT/timeline/concurrency_integral_check.csv # quick preset 会跳过
```

不要把 `success` 混为一个概念。结果表分别提供：

```text
execution_success
monitoring_success
trajectory_import_success
p1_native_status
p2_import_success
report_publishability_status
analysis_pipeline_success
usable_run
judge_success
overall_pipeline_success
```

`pipeline_success`/`analysis_pipeline_success` 表示监控与分析链路可用；
`overall_pipeline_success` 还要求 workload 执行成功且 judge 未失败。runner 最终退出码
使用后者，因此 execution failure 不会再被“分析成功”掩盖。失败任务如果数据完整，仍可
作为性能样本保留，但必须在结果中明确 `execution_success=false`。

如果需要 benchmark judge：

```bash
--judge-command-template 'python /path/to/judge.py --task {task_id} --workspace {workspace}'
```

工具命令 `returncode != 0` 不会自动判定整个 Agent run 失败。报告会分别统计：

```text
tool_instrumentation_error_count
tool_timeout_count
command_nonzero_return_count
```

### 单 Run Finalization Barrier

`agentmon run` 自动生成 report/summary 前会先完成统一 finalization：

```text
child_done
p1_adapter_done
collectors_joined
event_queue_drained
event_writer_closed
trajectory_decision_final
p2_import_finished
```

trajectory 不再单次 `exists()` 判定。对显式 `--trajectory`、命令行 `-o/--output`
检测路径或 `agent.trajectory_glob` 发现路径，控制器会等待文件存在、非空、连续
stat 稳定并可解析 JSON 后再导入；超时后才记录 `MISSING_AFTER_TIMEOUT`。没有配置
trajectory 时记录 `NOT_CONFIGURED`，不写最终的假缺失 error。

如配置 `[finalization].p2_events_path`，控制器会记录服务 P2 JSONL 的
`p2_start_offset` / `p2_end_offset`，在 trajectory 成功导入后优先按
`assistant.extra.response.id == payload.api_request_id` 筛选请求事件，再写入 run
本地 `events.jsonl`。无法排他归属时保留 service-window 低置信质量标记，不补造精确
关联。`report.json` 的 `event_snapshot` 和 `finalization` 字段可用于验证 report
是否基于 writer close 后的最终 JSONL。`finalization.trajectory_status` 表示 runtime
结束时的快照；如果 batch runner 在后处理阶段补导入 trajectory，最终可分析状态以
`report.json.analysis_state.trajectory.status` 和 `summary.json.analysis_trajectory_status`
为准，同时保留 `runtime_finalization_trajectory_status` 供审计。

## 7. 自动分析瓶颈

runner 已自动生成合并 CSV：

```bash
mkdir -p "$BATCH_ROOT/analysis"

PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_analyze_steps.py" \
  "$BATCH_ROOT/step-sequence/all-steps.csv" \
  --out-dir "$BATCH_ROOT/analysis" \
  --min-p2-match-rate 0.95 \
  --min-p2-lifecycle-rate 0.95 \
  --strict \
  --format report \
  | tee "$BATCH_ROOT/analysis/report.txt"
```

输出包括：

```text
analysis.json
run_summary.csv
model_hotspots.csv
tool_hotspots.csv
metric_definition.json
data_quality.json
confidence.json
limitations.json
validation_checks.json
report.txt
```

分析器可以识别：

* scheduler queue 压力；
* TTFT、显式 prefill、prefill-to-first-token 代理窗口和 decode 谁占主导；
* model wait 与 tool wait；
* prompt cache reuse 和 recompute；
* KV 使用率、分配、释放、驱逐和 preemption；
* Agent/tool/vLLM CPU 与 wall ratio；
* 工具 RSS/PSS/I/O 和 timeout 类型；
* 目标卡及整机 HBM/AI Core；
* scheduler running/waiting（共享服务窗口）、model_extra、ordinal fallback、低
  coverage 和低质量时钟数据。

`--strict` 在请求匹配率、P2 lifecycle、时钟域或输入完整性低于门槛时返回 1，
但仍会写出全部诊断文件。`UNPUBLISHABLE` 表示不可直接发布确定性结论，不表示原始
数据会被删除。

## 8. P2 时间定义

```text
queue        queued -> first_scheduled
engine TTFT  queued -> first_token
TTFT         received -> first_token
prefill proxy first_scheduled -> first_token
prefill exact first_scheduled -> prefill_completed（仅 deep 模式）
decode       first_token -> engine_finished
token decode first_token -> last_token
exact TPOT   (last_token - first_token) / (generation_tokens - 1)
E2E          received -> API finished
first output received -> API RequestOutput 首次可见
```

`first_token` 来自 EngineCore 的新 token 输出。非流式 `FINAL_ONLY` 的 API 返回可能
接近请求结束，因此只记为 `first_output`，不能冒充 first token。每个时间点保存 PID、
host 和 `CLOCK_MONOTONIC` domain。旧事件缺少来源时标记 `legacy_unverified`，分析器
不会据此下强结论。

`last_token` 是 EngineCore 最近一次产生新 token 的时间；请求完成、abort、取消或异常
后关联状态会清理。`exact_tpot_s` 只在 first/last token 同一可验证时钟域且 generation
tokens 大于 1 时输出。

`KVCacheManager.get_computed_blocks()` 在真正 prefill 之前运行，只用于得到 cached、
computed 和 recomputed prompt token。它现在发出
`vllm.request.prompt_cache_accounted`，不能当作 `prefill_completed`。因此
显式 `AGENTMON_VLLM_P2_DEEP=false` 的低开销模式下 `p2_prefill_s` 保持 null；默认
Deep 模式中，只有匹配的
`Scheduler.update_from_output()` 成功后才写入真实执行锚定边界。没有该边界时可参考
`p2_prefill_to_first_token_s`，但该值包含首 token 计算，不是纯 prefill。

## 9. 空值与 Prefix Cache 语义

空值、`null`、N/A 不按 0 聚合，同时输出有效样本数和 coverage。

Prefix cache 关闭时必须是：

```text
prefix_cache_status = disabled
prefix_cache_hit_rate = null / N/A
```

不能显示 0%。只有 enabled 且分母有效的 run 才参与 hit-rate 平均。

NPU HBM 或 AI Core 解析不到时保持 null，并保留 parser confidence/quality；不能把
解析失败写成 0。

## 10. 批次时间轴与吞吐

runner 会自动生成 timeline，也可以原位重建并严格校验：

```bash
PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" -m agentmon timeline "$BATCH_ROOT" \
  --bucket-seconds 1 \
  --bucket-seconds 5 \
  --strict \
  --format table
```

主要产物：

```text
run_timeline.csv
service_resource_windows.csv
batch_timeline_1s.csv
batch_timeline_5s.csv
phase_summary.csv
tail_summary.csv
transition_gap.csv
concurrency_integral_check.csv
timeline_report.md
validation_checks.json
```

`concurrency_integral_check.csv` 比较原始 Model/Tool 区间总时长与 bucket 时间轴积分，
误差必须小于 1%。跨 clock domain、并发数越界、run 数不一致、tail percentile
逆序或 usable run 缺失都会令 timeline 变为 `UNPUBLISHABLE`。

`service_resource_windows.csv` 按 step 导出 scheduler、KV、目标卡 HBM/AI Core 和
每卡 JSON。它明确标记 `shared_service_observation / not_request_attributed`，用于观察
同一 vLLM 服务在任务窗口内的状态，不能解释为该任务独占资源。

`batch-summary.json.throughput.standard_definitions` 同时给出 formula、分子、分母、
时间窗和 confidence，不要混用：

```text
workload_task_throughput                attempted / workload phase wall
pipeline_task_throughput                attempted / 全 pipeline wall
successful_workload_task_throughput     execution success / workload phase wall
publishable_pipeline_task_throughput    publishable task / 全 pipeline wall
pipeline_attempt_throughput             attempted / 全 pipeline wall
execution_attempt_throughput            attempted / workload phase wall
observed_success_throughput             execution success / workload phase wall
counterfactual_success_only_throughput  理想打包下的反事实估算
steady_state_completion_rate            t10 到 t90 的观测完成率
```

`batch-phase-events.jsonl` 记录 pipeline、preprocessing、workload、P2 indexing、
analysis 和 finalization 的 start/finish。`phase_timings` 分开记录 batch startup、
workload、P2 indexing、analysis、CSV export、timeline build、file scan、
finalization 和 postprocessing；`pipeline_phase_validation` 校验分阶段和 full pipeline
wall time 是否闭合。`pipeline-profile.json` 还汇总
每任务 trajectory import、P2 matching/import、report、step analysis 和 judge 的 wall、
CPU、RSS、I/O。目录文件扫描只在末尾执行一次，避免 profiler 本身反复遍历大批次。

批次根目录还会写 `p2-index-metrics.json`、`npu-process-mapping.json` 和
`npu-mapping-evidence.csv`。其中 P2 索引和 request 归因由
`agentmon.adapters.inference.vllm_018.p2_index` 提供，NPU/procfs 进程映射由
`agentmon.sampling.npu_mapping` 提供，batch runtime 只负责编排和汇总。稳态
generated-token 吞吐只使用 actual generated token delta；`scheduled_token_count`
只能作为调度工作量指标，不能当作已生成 token。

step sequence 的实现也已经库化到 `agentmon.step_sequence`：`core` 负责整体编排，
`spans` 负责 P1 span 重建，`p2` 负责 P2 lifecycle/token/KV 投影，`resources`
负责 scheduler/proc/NPU 窗口归因，`formatters` 负责 JSON/CSV/table 输出。
`scripts/agentmon_step_sequence.py` 仅保留旧命令路径。

`scripts/agentmon_server_preflight.py` 也仅保留旧命令路径；服务器环境、vLLM/CANN/ACL、
P2 hook 和 active probe 检查实现位于 `agentmon.server.preflight`。冒烟命令不用改，
新增预检逻辑应进入包内模块。

batch 收尾阶段使用 `_p2_index/` 下的紧凑 P2 v2 cache：每条事件只序列化一次，
各类 ID 索引只保存整数引用。request 与 service-global 事件均建立单调时间索引，
任务窗口通过二分查询获得，不再让每个任务扫描完整 P2 流。每个任务会生成
`p2-filtered/<task_id>.service-global.events.jsonl`，其中只包含任务请求窗口内的
全局事件，以及窗口开始前最近的 KV、scheduler、capability 状态。任务时间窗
缺失时会明确回退到完整 global 流，避免为了提速丢失分析上下文。

内置 batch analyzer 还会直接导入已经筛选好的 P2 事件，并让 report 与
step-sequence 复用 P2 导入后的一次 run event 读取；独立 CLI 的行为不变。
`batch-results.tsv` 中的 `p2_window_time_index_used`、
`p2_window_candidate_event_count`、`p2_service_global_filter_mode` 及 global
source/written/scanned 计数可以确认是否命中快速路径；cache 大小、扫描次数和
parse/build/load 时间记录在 `p2-index-metrics.json`。

## 11. Canonical 表、配对比较和重复实验

单 run 的 `agentmon report` 会写入 `canonical/`。也可合并多个 run：

```bash
PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" -m agentmon canonical \
  --runs-root "$AGENTMON_HOME/runs" \
  --output-dir "$BATCH_ROOT/canonical" \
  --format jsonl --format csv
```

标准表为 `runs`、`requests`、`tools`、`events`、`system_samples`。安装
`pyarrow` 后增加 `--format parquet`；使用 `--require-parquet` 可把缺少 Parquet 依赖
变成非零退出码。`canonical_manifest.json` 记录 schema version、行数和缺失原因。

同任务同 step 配对：

```bash
"$AGENTMON_PY" -m agentmon compare \
  --baseline /path/to/C1 \
  --target /path/to/C25 \
  --pair-by task,step > paired-comparison.json
```

重复实验：

```bash
"$AGENTMON_PY" -m agentmon matrix --repeat 3 \
  --output-root /path/to/matrix -- \
  bash /path/to/run-one-batch.sh '{repeat_dir}'
```

每次运行使用独立目录，并输出 mean、median、standard deviation 和 p95。跨 batch
比较器进一步输出 bootstrap CI、配对 delta 与 effect size；缺失样本不按 0 参与。

## 12. 跨批次比较

```bash
PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_compare_batches.py" \
  /path/to/c5-repeat1 \
  /path/to/c10-repeat1 \
  --expected-task-count 50 \
  --strict \
  --format json \
  > /path/to/c5-vs-c10.json
```

比较前自动检查 task ID/package hash、AgentMon/runner、vLLM/vLLM-Ascend/CANN、
model config、vLLM launch scheduler 参数、P1/P2 模式、temperature/seed 和自定义
metadata。并发数允许不同。输出按 task ID 配对的 delta、percentile 和 95% CI。

已有的 C1/C5/C10 等 batch 还可以直接做只读根因分析，不需要重跑任务：

```bash
PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_existing_data_analysis.py" \
  --batch C1=/path/to/c1-batch \
  --batch C5=/path/to/c5-batch \
  --batch C10=/path/to/c10-batch \
  --baseline C1 \
  --strict \
  --output-dir /path/to/offline-analysis
```

该脚本同样是兼容入口；实际实现位于 `agentmon.analysis.existing_data`。
Step CSV 自动分析的旧命令 `scripts/agentmon_analyze_steps.py` 对应实现位于
`agentmon.analysis.step_csv`。

该脚本会重新审计 P2 候选和 lifecycle、校验绝对 monotonic 时间轴、按 task/model
call 配对并发实验，并区分 workload amplification 与单位工作变慢。完整公式、产物和
`UNPUBLISHABLE` 条件见 `docs/existing_data_offline_analysis_zh.md`。

## 13. 完整验收标准

```text
batch result count == expected task count
monitoring_success == task count
trajectory_import_success == task count
p1_native_complete == task count
p2_import_success == task count
p2 match rate >= 0.95（或实验声明的门槛）
p2 lifecycle complete rate >= 0.95
report_publishability_status == PUBLISHABLE
timeline publishability status == PUBLISHABLE
invalid JSON == 0
duplicate event IDs == 0
open spans == 0
abandoned spans == 0
orphan end spans == 0
negative durations == 0
concurrency integral error < 1%
```

`judge_success` 是独立的 benchmark 质量门槛，不等于监控链路成功。
