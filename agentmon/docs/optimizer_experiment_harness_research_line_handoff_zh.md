# AgentMon Optimization Experiment Harness 研究线交接

日期：2026-08-06

研究线语义分支：`feature/optimizer-experiment-harness`

本地目录：

```text
/Users/yang/Desktop/project_acs/infra/load_monitor_optimizer_harness
```

说明：原项目目录 `/Users/yang/Desktop/project_acs/infra/load_monitor` 当前不是 git 仓库，
因此本研究线不是 `git checkout -b` 形成的真实分支，而是从当前稳定代码复制出的独立工作目录。
稳定冒烟线继续使用原目录和已打包源码；本目录用于实现 optimizer experiment harness，
避免影响下一轮服务器冒烟。

## 1. 当前稳定线基线

稳定线源码包：

```text
artifacts/packages/agentmon-server-test-ready-20260728-pre-smoke-refactor-src.tar.gz
```

SHA256：

```text
703b7f00e2b113b56ad8b5cfd618b08fcb1af1c19bb546373fa36bae0f681717
```

稳定线验证：

```text
364 passed, 1 skipped
```

跳过项是本地缺少真实 vLLM 环境的集成测试。

## 1.1 当前研究线交付包

本次研究线源码包：

```text
artifacts/packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz
```

源码包旁边应同时存在：

```text
artifacts/packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.sha256
artifacts/packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.BUILD_MANIFEST.json
artifacts/packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.summary.json
artifacts/packages/agentmon-server-test-ready-20260806-project-audit-hardening-test-summary.txt
```

使用时以同目录 `.sha256` 文件为校验依据。quick/full `TEST_MANIFEST`、provenance 和
test summary 应由服务器上的 release gate 生成真实结果，不在本机伪造。研究线包包含 M1/M2/M3
optimizer experiment harness、experiment request/contract 生成、mechanism registry
和统一实验阶段入口。稳定冒烟线目录仍不应被修改。

当前本地验证：

```text
pipeline/replay/quick-smoke/release targeted pytest: 55 passed
full workspace pytest: 444 passed, 1 skipped
clean package full release gate: 444 passed, 1 skipped
quick source gate: PASS
full source gate: PASS, eligible_for_server_preflight=true
source/package clean check: PASS
```

本机 Python 为 3.9.6，不是服务器 release gate 使用的 Python 3.11/vLLM/CANN 环境；
全量 source gate 需在服务器上按标准流程重新生成真实 quick/full 报告。

## 2. 已有 optimizer 能力边界

当前项目已经支持 optimizer 策略插件，但还不是完整实验底座。

已有能力：

- `src/agentmon/optimizer/registry.py`
  - `register_optimizer_strategy(...)`
  - `unregister_optimizer_strategy(...)`
  - `optimizer_strategy_catalog()`
- `src/agentmon/optimizer/types.py`
  - `QueueSnapshot`
  - `ModelServiceSnapshot`
  - `OptimizerDecision`
- `src/agentmon/optimizer/strategies.py`
  - `static_threshold`
  - `workload_aware`
- CLI：
  - `python -m agentmon strategy list --domain optimizer`
  - `python -m agentmon optimizer decisions --config ...`
  - `python -m agentmon optimizer replay --snapshot ...`
- Replay：
  - `src/agentmon/replay/simulator.py` 支持单个 scheduler/runtime snapshot 的 optimizer replay。

当前缺口：

- 已有 M1/M2 最小实现：
  - `src/agentmon/experiments/evidence.py` 读取 batch 的 manifest、summary、results、step CSV 和 run `events.jsonl`，生成 evidence graph、critical path、bottleneck attribution、workload-normalized metrics 和 data quality；同时提供 source-only batch index 与 step CSV iterator，Harness 在 M1 后用它进入 M2/M3；
  - `src/agentmon/experiments/snapshots.py` 从 batch results 与 step CSV 构造 optimizer replay 时间序列 snapshot；
  - `src/agentmon/experiments/replay.py` 支持多个 optimizer 策略的离线 advice replay；其 prediction future observation 使用阶段私有 SQLite 有序索引，避免构造完整排序副本；
  - `src/agentmon/experiments/prediction.py` 输出候选预测变量的滞后观测可用性和关联验证；
  - `src/agentmon/optimizer/harness/audit.py` 输出 would-defer / would-limit / would-relax、压力转移、A-B-A 交替模式和持久 advice 风险审计；
  - `src/agentmon/experiments/evaluation.py` 输出 M3 离线 advice-proxy 机制比较、消融表、贡献分解、冲突审计和 evaluation report；
  - `src/agentmon/experiments/request.py` 支持由简洁 request 生成完整 experiment contract；
  - `src/agentmon/experiments/mechanisms/` 提供 `model_gate`、`scheduler`、`tool_pool`、`context_compression`、`prefix_cache` 的 mechanism registry profile；
  - `src/agentmon/experiments/experiment_cli.py` 提供 `prepare`、`smoke`、`pilot`、`formal`、`analyze` 统一阶段入口；
  - `src/agentmon/optimizer/harness/runner.py` 统一串联 M1/M2；
  - `scripts/agentmon_optimizer_experiment.py` 和 `agentmon optimizer experiment` 是 optimizer harness 兼容入口；
  - `scripts/agentmon_experiment.py` 与 `scripts/agentmon_experiment_init.py` 是实验编排入口。

剩余缺口：

- 已有统一机制消融和组合效果评价的离线 advice-proxy 版本，但还没有基于真实多 variant batch 的因果收益估计。
- harness 输出已经接入 `analysis run-all` 的 `optimizer_harness_build` stage，输出位于
  `04_evidence/optimizer_experiment/`。
- 在线 M4 enforce 仍未接入；这是刻意保留的边界。

## 3. 研究目标

本研究线要支撑三个方向：

1. Agent 系统性能瓶颈归因分析
   - 面向 Agent Workflow、软件执行阶段和硬件资源建立统一表征。
   - 通过跨层监测、性能评测和因果/反事实分析回答“为什么慢、瓶颈在哪里、影响多大”。
   - 产出后续预测变量、调控对象和优化目标。

2. 运行时预测与动态联合调控
   - 基于动态执行图和实时资源状态预测后继节点激活、阶段计算开销、KV/权重需求和数据传输压力。
   - 将离线瓶颈认知转化为在线决策。
   - 优化任务完成时间、SLO、资源利用率、能耗和公平性。

3. 多优化机制的系统化融合与效果评价
   - 比较调度、缓存、资源分配、数据搬运和上下文压缩等机制。
   - 支持统一 baseline、消融实验、组合策略、冲突仲裁和端到端收益验证。
   - 避免局部优化互相抵消。

## 4. 推荐总体设计

不要把 harness 做成单纯 optimizer replay。应做成三层：

```text
Evidence Layer
  统一事件、标准表、动态图、关键路径、工作量归一化、瓶颈归因

Replay / Prediction Layer
  时间序列 snapshot、策略 advice replay、预测变量验证、策略风险审计

Evaluation / Fusion Layer
  baseline、消融、组合机制、冲突仲裁、收益归因、SOP 输出
```

## 5. 建议代码组织

优先新增包，避免继续扩张旧脚本和 runtime core。

```text
src/agentmon/experiments/
  evidence.py
  graph.py
  snapshots.py
  replay.py
  evaluation.py
  reports.py

src/agentmon/optimizer/harness/
  runner.py
  audit.py
  schemas.py

src/agentmon/experiments/mechanisms/
  model_gate.yaml
  scheduler.yaml
  tool_pool.yaml
  context_compression.yaml
  prefix_cache.yaml

scripts/agentmon_optimizer_experiment.py
scripts/agentmon_experiment.py
scripts/agentmon_experiment_init.py
```

已有模块应复用：

- `src/agentmon/analysis/*`
- `src/agentmon/step_sequence/*`
- `src/agentmon/batch/*`
- `src/agentmon/optimizer/*`
- `src/agentmon/replay/simulator.py`
- `src/agentmon/adapters/inference/vllm_018/p2_index.py`

不要把新逻辑塞回：

- `scripts/agentmon_batch_runtime.py`
- `scripts/agentmon_step_sequence.py`
- `src/agentmon/runtime/core.py`
- `src/agentmon/optimizer/service.py`

## 6. 里程碑

### M1：离线证据底座

目标：从已有 batch/run 数据生成统一 evidence graph 和瓶颈初判。

输入：

- batch root
- run dirs
- `events.jsonl`
- step sequence CSV/JSON
- P2 service events 或 P2 index
- batch summary/results/manifest

输出：

- `experiment_manifest.json`
- `evidence_graph.json`
- `critical_path.csv`
- `bottleneck_attribution.json`
- `workload_normalized_metrics.csv`
- `data_quality.json`

约束：

- 缺失数据保持 `UNKNOWN`、`PARTIAL`、`UNRESOLVED`。
- Prompt、摘要正文、原始工具输出和完整代码 diff 默认不进入 harness 输出。
- API first output 不能冒充 EngineCore first token。
- 总 Prompt Tokens 不能冒充实际 Prefill Tokens。
- 请求属于某个 batch 不代表资源由该请求独占。

### M2：Optimizer Advice Replay

目标：基于 evidence graph 生成时间序列 snapshot，并让多个 optimizer 策略在 advice 模式下回放。

正式 replay 前先运行 optimizer 专用输入预检：

```bash
PYTHONPATH=src python -m agentmon optimizer experiment \
  --batch-root /path/to/concrete-batch \
  --output-dir /path/to/optimizer-experiment \
  --strategy static_threshold \
  --preflight-only
```

预检只接受具体 batch 目录。历史 `batch-results.tsv` 中的 `run_dir` 已失效时，使用一个或多个
显式搜索根；搜索结果为多个候选时保持 `UNRESOLVED`，不会自动选择：

```bash
PYTHONPATH=src python -m agentmon optimizer experiment \
  --batch-root /path/to/concrete-batch \
  --run-search-root /path/to/historical-run-search-root \
  --output-dir /path/to/optimizer-experiment \
  --strategy static_threshold \
  --preflight-only
```

预检写出 `optimizer_preflight.json`。没有任何可观测时间证据时，命令退出码为 `3`，
snapshot 和 decision 数量均为零；M1 仍可输出缺失数据说明，M2/M3 不运行。实现不得为缺失时间线补造
零时刻 snapshot。

建议 CLI：

```bash
PYTHONPATH=src python -m agentmon optimizer experiment \
  --batch-root /path/to/batch \
  --strategy static_threshold \
  --strategy workload_aware \
  --schema-profile /path/to/schema-profile.json \
  --output-dir /path/to/optimizer-experiment \
  --status-file /path/to/optimizer-experiment/status.json \
  --progress-interval-seconds 5
```

`--schema-profile` 是可选参数，用来覆盖 step CSV 字段映射和 P2 service
event 的 source / event_type / payload 字段名。策略只消费标准化后的
snapshot，不应在策略代码里写 vLLM、mini-SWE、固定任务 ID、并发档位或实验路径。
`--status-file` 也是可选参数；阶段切换一定更新，指定 `--progress-interval-seconds` 后，
snapshot build 和 decision replay 会按墙钟间隔写入当前进度。该间隔只控制状态输出，不参与策略判断。

当前 harness 生成 `pipeline_manifest.json`、`pipeline_dag.json`、`pipeline_status.json`、
`source_identity.json`、`artifact_provenance.json` 和 `stages/<stage>/stage-status.json`。Replay 使用 P2 排序分片和
snapshot/decision 流；`events.jsonl` 始终是权威输入，分片只是可重建派生物。可用
`--memory-budget-bytes` 调整此运行的内存预算，或用 `--resume` 复用已完成 replay。
复用条件同时绑定输入文件 hash、源码树、策略集合及顺序、完整 AgentMon 配置、Schema Profile、内存预算，
并复核 decision/snapshot/summary/audit/prediction 文件及 snapshot part 的 hash。任一项变化或损坏都会从 M2 重跑，
不会沿用旧建议。
该预算没有隐式最小值；`stages/m2_decision_replay/snapshot_manifest.json` 会记录单 snapshot 与单 decision group 的
驻留契约，并记录 `static_timepoint_ordering=external_merge_unique_parts`。中断的未完成 replay 不发布对外文件，当前会从 replay stage 重跑，不会伪称 offset 续跑。

执行顺序由真实 DAG 保证：M1 与 M2 完成后运行 M3，随后生成报告，最后执行 artifact validation 和
`artifact_provenance.json`。`FAILED` 优先于 preflight 状态向顶层传播并返回非零；`UNRESOLVED` 返回独立非零状态；
`PARTIAL` 表示有可用但受限的离线证据，不能解释成在线收益。

最小形状示例：

```json
{
  "p2_service_event_profile": {
    "event_sources": ["custom_p2"],
    "scheduler_event_types": ["custom.scheduler.sample"],
    "kv_event_types": ["custom.kv.sample"],
    "running_request_fields": ["active"],
    "waiting_request_fields": ["queued"],
    "kv_usage_ratio_fields": ["cache_pressure"]
  },
  "step_csv_profile": {
    "request_ttft_fields": ["engine_ttft_s"]
  }
}
```

或兼容脚本：

```bash
PYTHONPATH=src python scripts/agentmon_optimizer_experiment.py \
  --batch-root /path/to/batch \
  --strategy static_threshold \
  --strategy workload_aware \
  --output-dir /path/to/optimizer-experiment
```

输出：

- `optimizer_preflight.json`
- `optimizer_decision_timeline.csv`
- `optimizer_decisions.jsonl`
- `optimizer_strategy_summary.json`
- `optimizer_risk_audit.json`
- `prediction_variable_validation.json`
- `prediction_variable_validation.csv`
- `optimizer_replay_report.md`
- `artifact_provenance.json`

### 服务器历史数据 qualification

服务器上不应只运行本地工作树的 harness。使用干净包解出的目录、该目录实际被导入的目标 Python 和真实 batch
目录执行以下入口；它会先运行 full source gate，并强制 Harness 子进程优先从该已验证目录导入。Harness 回报的
`packaged_verified` source tree hash 必须与 gate 一致；否则只写 `NOT_QUALIFIED`，不会把该次分析标为 qualified：

```bash
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_optimizer_qualification.py" \
  --package "$RELEASE" \
  --installed-source-root "$AGENTMON_REPO" \
  --agentmon-python "$AGENTMON_PY" \
  --vllm-python "$VLLM_PY" \
  --batch-root /path/to/historical-batch \
  --run-search-root /path/to/historical-run-search-root \
  --output-dir /path/to/qualification-output \
  --strategy static_threshold \
  --strategy workload_aware
```

只在 `qualification_manifest.json` 的 `status=QUALIFIED`，且其中 full source gate、`artifact_provenance.json`
和 harness result 都存在时，才可声明该**次运行**具备服务器历史数据分析资格。它不表示在线 enforce、独占资源归属或策略收益已被证明。

仓库测试会对两次独立 M1/M2/M3 输出做规范化 JSON/CSV/JSONL hash 比较；生成时间、绝对路径和运行时资源字段会被剔除，
数据质量、限制和决策顺序不会被剔除。

当前最小可用实现已落地。推荐命令：

```bash
PYTHONPATH=src python -m agentmon optimizer experiment \
  --batch-root /path/to/batch \
  --strategy static_threshold \
  --strategy workload_aware \
  --output-dir /path/to/optimizer-experiment
```

兼容脚本入口：

```bash
PYTHONPATH=src python scripts/agentmon_optimizer_experiment.py \
  --batch-root /path/to/batch \
  --strategy static_threshold \
  --strategy workload_aware \
  --output-dir /path/to/optimizer-experiment
```

当前 M2 是离线 advice replay，不执行 enforce，不回写 runtime，也不声明策略收益。
完全相同的跨 run service sample 会去重；同一时间同一 service 指标出现冲突值时，相关字段保持未知，
预检状态降为 `PARTIAL` 并保留冲突证据，不按 run 顺序选取任意值。
M3 顺序读取 `optimizer_decision_timeline.csv`，只保留当前 snapshot 的 strategy decision group；
`conflict_audit.json` 的 `streaming_contract` 记录该上界，冲突明细先写阶段私有 JSONL 再顺序组装兼容 JSON。
这不意味着 step CSV、M1 evidence graph 或精确 percentile 已经完全磁盘化。

应包含：

- 每个时间点的 queue/model/resource snapshot。
- 每个策略的 advice decision。
- `would_defer`、`would_limit`、`would_relax`、`risk_flags`。
- 数据质量和不可得字段。
- 策略振荡、过度限流、长期饥饿风险。

### M3：机制评测与消融

目标：统一比较单机制和组合机制的实际贡献。

机制示例：

- runtime scheduler
- model gate
- tool pool
- context compression
- prefix cache
- KV-aware admission
- tool-timeout backpressure
- AIMD/PID/queue-target optimizer

输出：

- `mechanism_comparison.json`
- `ablation_table.csv`
- `contribution_decomposition.csv`
- `conflict_audit.json`
- `evaluation_report.md`

当前离线 M3 已落地：

- `mechanism_comparison.json` 汇总观测 workload/task/request/token/resource/tool 指标，以及各策略 advice proxy；
- `ablation_table.csv` 以 baseline strategy 为参照比较策略 advice 行为差异；
- `contribution_decomposition.csv` 把观测 duration component 与 advice risk flag 候选机制分开列出；
- `conflict_audit.json` 检测同一 snapshot 上多个策略的 pressure、would-defer、would-limit、would-relax 和 target concurrency 分歧；
- `evaluation_report.md` 明确写出数据质量、限制和禁止结论。

M3 当前不声明因果收益。真实收益仍需要多 variant batch、统一 baseline、端到端任务结果和消融实验。

评价必须区分：

- task-level throughput / latency
- request-level Queue / TTFT / TPOT / Decode
- workload-normalized token 指标
- resource utilization
- tool timeout / error rate
- 数据质量和禁止结论

### M4：在线控制接入

目标：只把经过 M1-M3 验证的策略接回 Runtime。

要求：

- 先 `advise`。
- 再小规模 `enforce`。
- 所有控制动作进入 audit。
- Control Plane 做权限、冷却、边界和冲突仲裁。
- 不允许 Optimizer 直接 kill 任务或直接改推理框架内部状态。

## 7. 新对话建议开场 Prompt

```text
你现在接手 AgentMon/AGENTRUN 的 feature/optimizer-experiment-harness 研究线。

工作目录：
/Users/yang/Desktop/project_acs/infra/load_monitor_optimizer_harness

这是从稳定冒烟线复制出来的独立研究目录，原目录不是 git 仓库，因此本目录语义上对应 feature/optimizer-experiment-harness。

请先阅读：
1. README.md
2. docs/optimizer_experiment_harness_research_line_handoff_zh.md
3. docs/agent_runtime_architecture_design_zh.md
4. docs/runtime_speed_optimization_algorithms_zh.md
5. src/agentmon/optimizer/
6. src/agentmon/replay/simulator.py
7. src/agentmon/analysis/
8. src/agentmon/step_sequence/
9. src/agentmon/batch/

目标：
先实现 M1/M2 的最小可用版本，即从已有 batch 数据构造 evidence/snapshot，并对多个 optimizer 策略做 advice replay，输出统一报告。

要求：
- 不影响稳定冒烟线。
- 不改旧 batch runtime、step sequence 和 server preflight 的 CLI 用法。
- 新增代码优先放在 src/agentmon/experiments/ 和 src/agentmon/optimizer/harness/。
- 缺失数据保持 UNKNOWN/PARTIAL/UNRESOLVED。
- 所有结论必须带证据来源、数据质量和禁止结论。
- 增加确定性单元测试。
```
