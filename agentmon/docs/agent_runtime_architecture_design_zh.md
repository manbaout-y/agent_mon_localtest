# AgentMon Agent Runtime 架构设计方案

本文是一份面向改造落地的完整架构设计。目标是把当前 AgentMon 从“Agent 负载监控与分析工具”
演进为一个框架无关、推理框架解耦、负载感知、可插拔调度的单机 Agent Runtime。

设计重点不是重写已有能力，而是把当前 P0/P1/P2 观测资产沉淀为 runtime 的 telemetry 基座，
再补齐任务编排、资源调度、控制面、策略插件和配置体系。

## 1. 总体结论

这个项目适合演进成 Agent Runtime，但核心必须重新分层：

```text
Agent Frameworks
  mini-SWE / OpenHands / LangGraph / AutoGen / 自研 Agent
        |
        v
Agent Backend Adapters
  启动、事件翻译、artifact/checkpoint、可选协作控制
        |
        v
AgentMon Runtime Core
  Run/Step/Model/Tool 的中立生命周期、状态、事件、预算
        |
        +----------------------+----------------------+
        |                      |                      |
        v                      v                      v
Scheduler              Telemetry Aggregator      Control Plane
  准入、队列、并发、路由    P0/P1/P2/系统快照          model gate/tool pool/agent control
        |                      ^                      ^
        v                      |                      |
Resource Optimizer -------------+----------------------+
  负载判断、策略建议、风险信号
```

必须守住三条边界：

1. **Runtime core 不感知具体 Agent 框架。**
   mini-SWE、OpenHands、LangGraph、AutoGen 都只能通过 backend adapter 接入。

2. **Runtime core 不强绑定推理框架。**
   vLLM、SGLang、TGI、云 API 都是 provider 或 telemetry adapter。深度推理指标可以增强优化，
   但不能成为 runtime 正常运行的前提。

3. **资源优化器不直接控制外部模块。**
   Optimizer 只输出决策，Scheduler 校验 policy 和 capability 后通过 Control Plane 执行。

第一阶段推荐目标：

```text
动态控制进入 vLLM 的请求流
  + 基于任务画像做 admit/defer
  + 对 Agent tool 做 tool pool 并发和 CPU 资源控制
  + 所有策略先支持 advise/replay，再进入 enforce
```

当前仓库已落地一个可运行的第一阶段切片：

```text
runtime/
  RunSpec、Backend 协议、ProcessBackend、RuntimeService、TaskState、持久化队列、daemon、heartbeat/recovery、audit replay

scheduler/
  fixed_concurrency 准入、workload_aware 准入、scheduler snapshot、策略 registry

optimizer/
  QueueSnapshot、ModelServiceSnapshot fixture、static_threshold advise、workload_aware advise、策略 registry、snapshot replay

experiments + optimizer/harness/
  batch evidence graph、critical path、workload-normalized metrics、snapshot timeline、多策略 advice replay、risk audit、advice-proxy mechanism evaluation、Analysis SOP packaging

control/
  model gate target/acquire、OpenAI-compatible gated provider、tool pool acquire/exec、best-effort resource actuator、统一 audit events

adapters/
  agent_frameworks.mini_swe 与 inference.vllm_018 新 namespace、adapter capability catalog、mini-SWE runtime opt-in hooks
```

当前控制面已具备本地 `enforce` 原语：它可以在请求进入 OpenAI-compatible/vLLM endpoint 前进行
model gate，也可以通过 pooled tool executor 执行本地工具命令，并对子进程施加 best-effort 的
nice、rlimit、CPU affinity 请求。尚未完成的是 cgroups v2 隔离，以及把这些 wrapper 深度嵌入具体
Agent 框架 SDK 的真实构造路径。

## 2. 设计目标与边界

### 2.1 目标

1. 支持多种 Agent 框架。
   - Runtime 只理解 `Run`、`Step`、`ModelRequest`、`ToolCall`、`Observation`。
   - 框架知识放在 adapter 中。
   - 黑盒进程、trajectory 导入、语义事件、协作式 SDK 都可以共存。

2. 支持多种推理后端。
   - 请求路径通过 `ModelProvider` 抽象。
   - 观测路径通过 `InferenceTelemetryProvider` 抽象。
   - vLLM P2 hook 是增强 telemetry，不是 runtime core 的依赖。

3. 支持负载感知整机优化。
   - 观测 CPU、内存、I/O、NPU、HBM、vLLM queue、KV、TTFT、prefill/decode、tool 资源。
   - 形成 `NodeSnapshot`、`ModelServiceSnapshot`、`RunProfile`、`QueueSnapshot`。
   - Optimizer 输出准入、并发、路由、预算、延迟、降级等建议。

4. 支持 Agent 任务编排。
   - 任务从命令字符串升级为 `WorkloadSpec` 和 `RunSpec`。
   - 支持优先级、重试、分类、任务画像、模型 target、tool pool、资源预算。

5. 支持策略即插即用。
   - 调度算法、优化算法、任务分类器、模型路由、资源 actuator 都作为插件。
   - 插件只能输出声明式 plan/decision，不能直接改 runtime 状态。

6. 保持当前 AgentMon 能力可用。
   - 现有 `agentmon run` 兼容。
   - 现有事件、报告、summary、timeline、P1/P2 分析逐步迁移，不做一次性推倒。

### 2.2 非目标

1. 第一阶段不做多机集群调度。
2. 第一阶段不要求所有 Agent 框架都支持 pause/resume/checkpoint。
3. 第一阶段不直接热改 vLLM 内部 scheduler。
4. 第一阶段不把自动 cancel/抢占作为默认策略。
5. 不把 mini-SWE 或 vLLM 写进 runtime core。

## 3. 现有资产如何进入新架构

| 现有模块 | 当前能力 | 新架构位置 |
|---|---|---|
| `RunController` | 包装子进程、run 目录、采样、轨迹导入 | `ProcessBackend` 和 runtime lifecycle 基础 |
| `events.py` | 通用事件结构 | canonical event v2 的起点 |
| `storage/jsonl.py`、`storage/sqlite.py` | 事件落盘 | Runtime Event Store |
| `storage/event_queue.py` | 异步写入 | EventBus / Telemetry pipeline |
| `sampling/*` | proc/host/vLLM metrics/Ascend 采样 | Telemetry collectors |
| `adapters/mini_swe/*` | P1 step/model/tool span | `agent_frameworks.mini_swe` adapter |
| `adapters/vllm_018/*` | vLLM P2 深度 hook | `inference_telemetry.vllm_018` |
| `scripts/agentmon_batch_runtime.py` | 兼容 CLI 门面 | 调用 `agentmon.batch.*` 的 Orchestrator/Scheduler 原型 |
| `scripts/agentmon_step_sequence.py` | 兼容 CLI 门面 | 调用 `agentmon.step_sequence.*` 的 per-step 分析入口 |
| `scripts/agentmon_server_preflight.py` | 兼容 CLI 门面 | 调用 `agentmon.server.preflight` 的服务器冒烟预检入口 |
| `scripts/agentmon_existing_data_analysis.py` | 兼容 CLI 门面 | 调用 `agentmon.analysis.existing_data` 的已完成 batch 离线分析入口 |
| `scripts/agentmon_analyze_steps.py` | 兼容 CLI 门面 | 调用 `agentmon.analysis.step_csv` 的 step CSV 自动分析入口 |
| `agentmon.batch.*` | batch 编排、workload、分析 runner、policy、manifest、artifact、resource、summary | Orchestrator/Analysis Runtime 边界 |
| `agentmon.step_sequence.*` | P1 span、P2 projection、resource window、trajectory fallback、formatters | 标准 step-level evidence builder |
| `agentmon.server.preflight` | 服务器环境、vLLM/CANN/ACL、P2 hook、active probe 检查 | Server smoke gate |
| `agentmon.analysis.existing_data` / `agentmon.analysis.step_csv` | 已有数据分析和 CSV 负载初判 | Analysis Runtime 工具层 |
| `reporting/*` | report、summary、timeline、canonical、event_log、P1/P2/quality facade | Analysis/Reporting |
| `analysis/stages.py` | stage catalog、stage spec、stage protocol | Analysis Runtime 插件边界 |

迁移原则：

```text
先包一层，不先重写。
先保证兼容，不先追求抽象纯度。
先做 advise/replay，不先强制控制。
```

当前代码组织已经按该原则完成职责拆分：旧脚本路径只保留命令兼容和 monkeypatch 兼容，
批处理实现进入 `agentmon.batch`，step sequence 实现进入 `agentmon.step_sequence`，服务器预检
进入 `agentmon.server.preflight`，已有数据和 step CSV 分析进入 `agentmon.analysis.*`，reporting
保留 `agentmon.reporting.report` 公开门面并新增领域入口模块。后续新增 batch 策略、step 证据字段、
server smoke 检查、reporting domain analyzer 或 analysis stage 时，应优先放入这些包内模块，
避免继续扩张脚本或单一门面文件。

## 4. 总体模块设计

建议目录结构：

```text
src/agentmon/
  runtime/
    types.py              # RunSpec、RunState、Budget、Capability
    core.py               # RuntimeService，生命周期和状态机
    backend.py            # AgentRuntimeBackend 协议
    model.py              # ModelProvider 协议
    tools.py              # ToolExecutor 协议
    events.py             # canonical events
    state_store.py        # 状态索引和 checkpoint 元数据
    registry.py           # backend/provider/actuator/strategy registry

  orchestrator/
    workload.py           # WorkloadSpec、任务集加载、任务画像
    service.py            # 批任务编排、重试、收尾分析

  scheduler/
    queue.py              # ready/deferred/running/draining 队列
    strategy.py           # SchedulerStrategy 协议
    admission.py          # 准入和 slot 分配
    decisions.py          # SchedulingPlan、ControlAction
    service.py            # SchedulerService

  optimizer/
    snapshots.py          # NodeSnapshot、ModelServiceSnapshot、RunProfile
    strategy.py           # OptimizerStrategy 协议
    policies.py           # guardrail 和目标策略
    service.py            # OptimizerService

  control/
    model_gate.py         # 模型请求准入
    router.py             # 多模型服务路由
    tool_pool.py          # 工具池并发
    cgroup.py             # cgroups/cpuset/nice/rlimit
    agent_control.py      # pause/resume/cancel/budget actuator

  telemetry/
    collectors/           # host/proc/device/model service 采样
    aggregate.py          # 快照和画像构建
    quality.py            # 数据质量标注
    replay.py             # 历史事件回放

  adapters/
    agent_frameworks/
      process/
      mini_swe/
      langgraph/
      autogen/
    inference/
      openai_compatible/
      litellm/
      vllm_018/
      sglang/
      tgi/
```

## 5. 核心领域模型

### 5.1 对象层级

```text
Workload
  一批任务和编排策略。

Task
  用户提交的一项工作，包含输入、workspace、优先级、标签、预算。

Run
  Task 的一次执行尝试。一个 Task 可以有多次 Run。

Step
  Agent 的一次语义步骤。黑盒 backend 可以缺失。

ModelRequest
  一次模型请求。可以来自 Agent adapter、Provider wrapper 或推理 telemetry。

ToolCall
  一次工具调用。runtime-controlled ToolExecutor 时可精确控制资源。

Artifact / Checkpoint
  输出、轨迹、日志、状态快照和可恢复点。
```

### 5.2 Run 状态机

```text
submitted
  -> queued
  -> admitted
  -> starting
  -> running
  -> draining
  -> succeeded

running -> cancelling -> cancelled
running -> failed
queued/running -> deferred -> queued
running -> pausing -> paused -> resuming -> running
```

`paused/resuming/checkpoint` 只在 backend capability 支持时可用。黑盒进程模式只保证
start、poll、cancel、collect artifacts。

### 5.3 RunSpec

```python
@dataclass
class RunSpec:
    task_id: str
    backend: str
    input: dict[str, Any]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    priority: int = 0
    resource_budget: ResourceBudget | None = None
    model_targets: list[ModelTarget] = field(default_factory=list)
    timeout_seconds: float | None = None
    retry_policy: RetryPolicy | None = None
```

### 5.4 WorkloadSpec

```yaml
workload:
  name: swe-benchmark
  task_source: tasks.tsv
  task_id_column: instance_id
  backend: mini-swe

  defaults:
    priority: 0
    timeout_seconds: 3600
    model_targets: [local_vllm]

  orchestration:
    max_retries: 1
    continue_on_error: true

  classification:
    rules:
      - class: long_context
        when: prompt_tokens_estimate > 32000
      - class: tool_heavy
        when: expected_tool_calls > 10
```

## 6. 核心协议

### 6.1 AgentRuntimeBackend

Agent 框架通过 backend 接入。Runtime core 不关心框架内部对象。

```python
class AgentRuntimeBackend(Protocol):
    name: str

    def capabilities(self) -> BackendCapabilities:
        ...

    def prepare(self, spec: RunSpec, context: RuntimeContext) -> PreparedRun:
        ...

    def start(self, prepared: PreparedRun) -> RunHandle:
        ...

    def poll(self, handle: RunHandle) -> BackendStatus:
        ...

    def cancel(self, handle: RunHandle, reason: str) -> None:
        ...

    def collect_artifacts(self, handle: RunHandle) -> list[ArtifactRef]:
        ...

    # Optional, only when capability says supported.
    def pause(self, handle: RunHandle, reason: str) -> None:
        ...

    def resume(self, handle: RunHandle) -> None:
        ...

    def checkpoint(self, handle: RunHandle) -> CheckpointRef | None:
        ...

    def update_budget(self, handle: RunHandle, budget: ResourceBudget) -> None:
        ...
```

能力声明示例：

```json
{
  "backend": "mini-swe",
  "start": true,
  "cancel": true,
  "semantic_events": ["run", "step", "model_request", "tool_call"],
  "tool_pid_attribution": true,
  "pause": false,
  "checkpoint": "trajectory_only"
}
```

### 6.2 ModelProvider

`ModelProvider` 处理请求路径，不等同于推理内部观测。

```python
class ModelProvider(Protocol):
    name: str

    def capabilities(self) -> ModelProviderCapabilities:
        ...

    def complete(self, request: ModelRequest) -> ModelResponse:
        ...

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        ...
```

Provider 负责：

1. 注入 `run_id`、`model_call_id`、`client_request_id`。
2. 记录 `model_request.start/end/error`。
3. 处理 timeout、retry、usage、streaming。
4. 把响应中的 request id 暴露给 telemetry 关联。

### 6.3 InferenceTelemetryProvider

`InferenceTelemetryProvider` 处理观测路径。

```python
class InferenceTelemetryProvider(Protocol):
    name: str

    def capabilities(self) -> InferenceTelemetryCapabilities:
        ...

    def snapshot(self) -> ModelServiceSnapshot:
        ...

    def iter_events(self) -> Iterable[CanonicalEvent]:
        ...
```

vLLM P2 hook、SGLang metrics、TGI Prometheus、云 API latency 都通过这个接口进入 telemetry。

### 6.4 ToolExecutor

```python
class ToolExecutor(Protocol):
    name: str

    def capabilities(self) -> ToolExecutorCapabilities:
        ...

    def execute(self, request: ToolCallRequest, context: RuntimeContext) -> ToolCallResult:
        ...
```

当前 mini-SWE local environment 中基于 `Popen`、PID/PGID、timeout、RSS/PSS/I/O 的逻辑，
适合抽成默认本地 ToolExecutor。

## 7. 与 Agent 框架协同

### 7.1 接入等级

| 等级 | 接入方式 | Runtime 能力 |
|---|---|---|
| L0 黑盒进程 | CLI subprocess | start/cancel/exit/proc 采样 |
| L1 轨迹导入 | CLI + trajectory parser | run 后恢复 step/model/tool 近似信息 |
| L2 语义 adapter | mixin/hook/SDK callback | 实时 step/model/tool span、request id 注入 |
| L3 协作控制 | framework SDK/plugin | pause/resume/checkpoint/budget/model gate |

Runtime 根据 capability 自动降级：

```text
没有 semantic events:
  只构建 process-level RunProfile。

没有 pause:
  Optimizer 不能建议 soft_pause，只能 defer 新任务。

没有 checkpoint:
  Scheduler 不做抢占式取消，只做准入控制。
```

### 7.2 启动协同

```text
1. Orchestrator 读取 WorkloadSpec，生成 RunSpec
2. Scheduler 判断是否 admit
3. Runtime 创建 run_id、run_dir、RuntimeContext
4. Backend 根据 RunSpec 生成启动命令或 SDK 调用
5. Runtime 注入：
   AGENTMON_RUN_ID
   AGENTMON_RUN_DIR
   AGENTMON_EVENTS_PATH
   AGENTMON_ROOT_SPAN_ID
   AGENTMON_MODEL_PROVIDER
6. Backend 启动 Agent
7. Adapter 写 canonical events
8. Runtime 收尾 artifacts/checkpoints/reports
```

### 7.3 事件协同

Agent adapter 不传框架对象，只写事件：

```text
agent_step.start/end/error
model_request.start/end/error
tool_call.start/process_start/end/error
checkpoint.created
artifact.created
```

这些事件构建 `RunProfile`：

```text
agent_steps
model_requests
tool_calls
model_wait_s
tool_wall_s
prompt_tokens
tool_cpu_s
failure_count
current_phase
```

### 7.4 控制协同

控制动作必须经过 capability：

| 动作 | 要求 |
|---|---|
| `defer` | 不需要 Agent 支持，只是不启动新任务 |
| `cancel` | backend 支持 cancel，黑盒进程可用 SIGTERM/SIGKILL |
| `pause_at_step_boundary` | backend 支持 pause |
| `resume` | backend 支持 resume |
| `checkpoint` | backend 支持 checkpoint |
| `budget_update` | backend、Provider 或 ToolExecutor 支持预算 gate |

## 8. 与推理框架协同

### 8.1 两条路径

```text
ModelProvider
  请求路径：发送请求、接收响应、streaming、usage、错误、timeout。

InferenceTelemetryProvider
  观测路径：queue、prefill、decode、KV、scheduler、device 负载。
```

两者可以来自不同模块：

```text
ModelProvider: OpenAI-compatible HTTP
InferenceTelemetryProvider: vLLM P2 events + /metrics
```

### 8.2 请求 ID 链路

推荐链路：

```text
model_call_id
  -> client_request_id
  -> api_request_id / server_request_id
  -> engine_request_id / backend_request_id
```

Provider 注入 headers：

```text
X-AgentMon-Run-Id
X-AgentMon-Request-Id
X-AgentMon-Model-Call-Id
```

Telemetry 用这些 ID 关联 P1/P2。没有精确 ID 时，只能保留服务窗口观测：

```text
request_attribution = not_request_attributed
observation_scope = shared_service_observation
```

不得把服务级 queue/KV/HBM 指标强行归因到某个 run 或 step。

### 8.3 推理能力声明

```json
{
  "provider": "vllm-openai-compatible",
  "telemetry": "vllm-p2",
  "request_correlation": "exact_header",
  "usage_tokens": "exact",
  "streaming": true,
  "queue_latency": true,
  "prefill_latency": true,
  "decode_latency": true,
  "kv_cache": true,
  "scheduler_iterations": true,
  "device_memory": "sampled"
}
```

Optimizer 按 capability 降级：

| 能力 | 可用策略 |
|---|---|
| P1 latency | 粗粒度模型等待限流 |
| usage tokens | token budget 和长上下文分类 |
| queue latency | 动态降低 inflight |
| prefill/decode | 区分长 prompt 和长输出瓶颈 |
| KV metrics | 延迟 long_context，避免 preemption |
| per-device metrics | endpoint 或设备池诊断 |

## 9. Telemetry 与事件规范

### 9.1 Canonical Event

```json
{
  "schema_version": "2.0",
  "event_id": "...",
  "run_id": "...",
  "task_id": "...",
  "source": "runtime|adapter|scheduler|optimizer|model|tool|proc|host|inference",
  "event_type": "model_request.start",
  "wall_time_ns": 0,
  "monotonic_time_ns": 0,
  "quality": "exact",
  "trace": {
    "trace_id": "...",
    "span_id": "...",
    "parent_span_id": "...",
    "step_id": "..."
  },
  "labels": {},
  "payload": {}
}
```

### 9.2 事件族

| 事件族 | 示例 |
|---|---|
| Runtime | `run.submitted`、`run.queued`、`run.admitted`、`run.started`、`run.finished` |
| Agent | `agent_step.start`、`agent_step.end` |
| Model | `model_request.start`、`model_request.token`、`model_request.end` |
| Tool | `tool_call.start`、`tool_call.process_start`、`tool_call.end` |
| Scheduler | `scheduler.plan`、`scheduler.action`、`scheduler.defer` |
| Optimizer | `optimizer.snapshot`、`optimizer.decision` |
| Control | `control.action.started`、`control.action.succeeded`、`control.action.failed` |
| System | `host.sample`、`proc.sample`、`device.sample`、`inference.snapshot` |

### 9.3 Telemetry Aggregator

Aggregator 只产出事实，不做控制：

```text
输入：
  runtime events
  agent events
  model provider events
  inference telemetry events
  host/proc/device samples
  tool events

输出：
  NodeSnapshot
  ModelServiceSnapshot
  RunRuntimeProfile
  QueueSnapshot
  HistoricalProfile
  DataQualityReport
```

数据质量必须保留：

```text
exact / sampled / estimated / inferred / missing / unsupported
```

## 10. Orchestrator、Scheduler 与 Optimizer

### 10.1 责任边界

```text
Orchestrator:
  读取任务、生成 RunSpec、处理批任务、重试、收尾分析。

Scheduler:
  队列、准入、slot、并发、路由动作、控制动作执行。

Optimizer:
  读取 telemetry 快照，输出负载判断和调度建议。

Control Plane:
  把 Scheduler 动作翻译成 model gate、tool pool、cgroup、agent control。
```

### 10.2 Scheduler 队列

```text
ready_queue
  可运行任务，按 priority/fairness/score 排序。

deferred_queue
  因负载或策略延迟，带 next_eligible_time。

running_set
  已 admission 的 run。

draining_set
  正在收尾、导入事件、生成报告的 run。
```

准入同时检查：

1. 全局 run slot。
2. model request slot。
3. tool pool slot。
4. 任务 class limit。
5. CPU/内存/NPU/HBM guardrail。
6. 公平性和优先级。
7. backend/provider/actuator capability。

### 10.3 Optimizer 输入与输出

输入：

```python
@dataclass
class OptimizerInput:
    node: NodeSnapshot
    queue: QueueSnapshot
    running: list[RunRuntimeProfile]
    model_services: list[ModelServiceSnapshot]
    policies: ResourcePolicy
    history: HistoricalProfiles
```

输出：

```python
@dataclass
class OptimizerDecision:
    decision_id: str
    target_concurrency: dict[str, int]
    admission_pressure: str
    task_class_limits: dict[str, int]
    route_preferences: dict[str, float]
    budget_recommendations: dict[str, ResourceBudget]
    risk_flags: list[str]
    reason: str
    confidence: float
```

Optimizer 不直接启动、暂停、取消或 kill 进程。

### 10.4 第一阶段策略

先用可解释规则和 AIMD：

```text
vLLM queue 高:
  降低 model_gate inflight。

KV/HBM 高:
  限制 long_context。

CPU/IO 高:
  限制 cpu_heavy tool pool。

系统健康:
  AIMD 慢慢增加并发。

全局高压:
  停止 admit 新 run，让已有 run drain。
```

## 11. 控制面设计

### 11.1 ControlAction

```python
@dataclass
class ControlAction:
    action_id: str
    target_type: Literal[
        "run",
        "model_service",
        "model_request_gate",
        "tool_pool",
        "tool_process",
        "agent_backend",
    ]
    target_id: str
    action_type: str
    parameters: dict[str, Any]
    reason: str
    expires_at_ns: int | None = None
```

执行前检查：

1. policy 是否允许。
2. actuator capability 是否支持。
3. 当前权限是否满足。
4. 目标对象是否仍存在。
5. action 是否过期。
6. 是否需要 rollback。

### 11.2 控制 vLLM batch 的方式

vLLM continuous batching 是服务内部行为。Runtime 默认不直接改 vLLM scheduler，
而是控制进入 vLLM 的请求流。

分层控制：

1. **请求准入控制**
   - 通过 `model_request_gate.max_inflight` 控制进入 vLLM 的请求数量。

2. **任务画像编排**
   - `long_context`、`decode_heavy`、`short_interactive` 分开限流。

3. **请求参数塑形**
   - 调整 `max_tokens`、timeout、retry budget。

4. **多服务池路由**
   - long-context 和 short-context 可以路由到不同 vLLM endpoint。

5. **推理框架控制插件**
   - 只有 vLLM 提供明确控制 API 或受控插件时，才允许设置 scheduler 级参数。

示例：

```text
输入信号：
  waiting_requests = 28
  queue_latency_p95_s = 6.4
  kv_usage_ratio = 0.91
  preemption_rate = 0.05

决策：
  max_inflight_requests: 16 -> 8
  long_context_max_inflight: 4 -> 1
  defer task_class=long_context 120s
  short_interactive 保留 2 个 slot

结果：
  已进入 vLLM 的请求自然 drain
  新 long-context 请求在 runtime gate 排队
```

### 11.3 控制 Agent Tool CPU

工具资源比 vLLM batch 更适合直接控制，因为 runtime 通常能拿到 PID/PGID。

建议引入 tool pool：

```text
tool_pool.default
tool_pool.cpu_heavy
tool_pool.io_heavy
tool_pool.high_priority
```

Linux 控制手段：

| 手段 | 用途 |
|---|---|
| cgroups v2 `cpu.max` | CPU quota |
| cgroups v2 `cpuset.cpus` | CPU 亲和池 |
| cgroups v2 `memory.max` | 内存上限 |
| cgroups v2 `io.max` | I/O 限速 |
| `nice` / `ionice` | 软优先级 |
| `resource.setrlimit` | 进程级限制 |

ToolExecutor 启动流程：

```text
1. 根据 ToolCallRequest 选择 tool_pool
2. Scheduler 检查 tool_pool slot
3. ToolExecutor 创建进程
4. 加入 cgroup 或设置 affinity/nice/rlimit
5. ToolProcessObserver 采样
6. 写 tool_call.process_start
7. 结束后释放 slot，写资源用量
```

如果工具由 Agent 框架自己启动，runtime 只能在 adapter 捕捉 PID 后 best-effort 移入 cgroup，
数据质量应标记为 `best_effort`。

### 11.4 模型请求与工具请求联合编排

Agent 任务通常在 model phase 和 tool phase 之间切换：

```text
vLLM queue 高，CPU 空闲:
  延迟新的 model request，继续运行 tool phase。

CPU/IO 高，vLLM 空闲:
  限制 cpu_heavy tool pool，允许 model phase 进入。

KV/HBM 高:
  延迟 long-context model phase，允许短 prompt 或工具阶段。

全局高压:
  停止 admit 新 run，让已有任务 drain。
```

Runtime 通过最近事件维护 run phase：

```text
model_request.start -> model_phase
model_request.end -> agent_phase
tool_call.start -> tool_phase
tool_call.end -> agent_phase
```

## 12. 策略、配置与插件化

### 12.1 配置分层

```text
runtime config
  状态目录、事件存储、默认 backend。

adapter config
  mini-SWE/OpenAI/vLLM/SGLang 接入参数。

resource config
  本机设备、模型 endpoint、tool pool。

policy config
  准入阈值、动作权限、优先级、公平性。

strategy config
  启用哪些 scheduler/optimizer/classifier/router 插件。
```

示例：

```toml
[runtime_optimization]
enabled = true

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

[scheduler]
enabled = true
strategy = "workload_aware"
max_running_runs = 8
workload_prompt_tokens_p90_budget = 120000
workload_generation_tokens_p90_budget = 12000
long_context_token_threshold = 32000
long_context_limit = 2
abnormal_path_risk_threshold = 0.8

[optimizer]
enabled = true
mode = "advise"
strategy = "workload_aware"
high_utilization_ratio = 0.8
model_queue_latency_high_s = 2.0
model_kv_high_ratio = 0.9
model_waiting_requests_high = 1

[control]
enabled = true
mode = "advise"

[model_gate]
enabled = true
service_id = "runtime.default"
max_inflight_requests = 16
moderate_pressure_target = 8
high_pressure_target = 4
min_inflight_requests = 1

[tool_pool]
enabled = true
default_max_concurrent = 8
cpu_heavy_max_concurrent = 4
io_heavy_max_concurrent = 2
high_priority_max_concurrent = 2
nice_increment = 0
cpu_affinity_cpus = []
```

`runtime_optimization.enabled` 是主动优化总开关。关闭时保留采集、审计和
TaskState，但 Scheduler 全放行，Optimizer、Model Gate、Tool Pool 和
Resource Actuator 不产生控制效果，上下文压缩也不改写 prompt。`context_compression`
的 `window_tokens=0` 表示运行时使用 `model.max_model_len`。所有数值仅为配置形态示例，服务器实际值必须
由 workload、硬件和 A/B 证据确定，不能写入通用策略逻辑。

### 12.2 插件扩展点

```text
RuntimeBackendPlugin
ModelProviderPlugin
InferenceTelemetryPlugin
ToolExecutorPlugin
SchedulerStrategyPlugin
OptimizerStrategyPlugin
ContextCompressionTriggerPlugin
ContextCompressionStrategyPlugin
WorkloadClassifierPlugin
ModelRouterPlugin
ResourceActuatorPlugin
PolicyPlugin
ReporterPlugin
```

插件 manifest：

```json
{
  "name": "agentmon.optimizer.aimd_model_gate",
  "kind": "OptimizerStrategyPlugin",
  "version": "1.0.0",
  "schema_version": "1.0",
  "entrypoint": "agentmon.optimizer.strategies.aimd:AimdModelGate",
  "capabilities": {
    "dynamic_concurrency": true,
    "task_class_limits": true
  },
  "requires": {
    "runtime_api": ">=1.0,<2.0",
    "telemetry_fields": [
      "model.waiting_requests",
      "model.queue_latency_p95_s"
    ]
  },
  "failure_mode": "disable_and_fallback"
}
```

### 12.3 调度算法插件

调度算法拆成：

```text
Filter -> Score -> Allocate -> Act
```

接口：

```python
class SchedulerStrategy(Protocol):
    name: str

    def plan(self, context: SchedulingContext) -> SchedulingPlan:
        ...
```

`SchedulingPlan` 只能描述动作，Scheduler core 负责校验和执行：

```python
@dataclass
class SchedulingPlan:
    admits: list[RunAdmission]
    defers: list[RunDefer]
    control_actions: list[ControlAction]
    reason: str
    confidence: float
```

### 12.4 Optimizer 算法插件

可选策略：

| 策略 | 用途 |
|---|---|
| StaticThreshold | 阈值保护，第一版默认 |
| AIMD | 动态并发控制，稳定且易解释 |
| PIDController | 控制 queue latency 或 TTFT |
| TokenBudget | 按 token/HBM/KV 预算调度 |
| FairShare | 多用户或多任务组公平性 |
| DeadlineAware | SLA/deadline 任务 |
| LearnedClassifier | 历史画像预测 |
| BanditRouter | 多 endpoint 路由探索 |

### 12.5 上下文压缩插件

上下文压缩拆成四个责任面：

1. Runtime 服务处理窗口、目标预算、状态、审计和失败回退。
2. Agent Framework Adapter 把框架消息映射为通用上下文及协议原子组。
3. `ContextCompressionTriggerPlugin` 决定何时压缩。
4. `ContextCompressionStrategyPlugin` 决定如何测量和压缩。

插件协议为：

```python
class ContextCompressionTrigger(Protocol):
    name: str

    def evaluate(self, request: ContextCompressionRequest) -> ContextCompressionTriggerResult:
        ...

class ContextCompressionStrategy(Protocol):
    name: str

    def measure(self, context, adapter_metadata) -> ContextMeasurement:
        ...

    def compress(self, request: ContextCompressionRequest) -> ContextCompressionResult:
        ...
```

内置默认 `model_summary` 通过 Adapter 提供的 Summary Provider 生成 continuation
summary；Runtime Core 不感知具体模型客户端、Agent 消息模板或推理服务。摘要请求有独立
身份和 usage 证据。`recent_groups` 仍作为显式的确定性结构裁剪对照插件存在。外部模块可
注册其他语义摘要、抽取式压缩、工具结果去重、检索式长期记忆或模型专用 tokenizer
策略。配置的 `plugin_modules` 负责显式导入，`strategy` 选择一个已注册名称，
`strategy_options_json` 只传给该插件。新增或移除算法不修改 Runtime Core，也不要求修改
mini-SWE Adapter。

触发策略与压缩算法是两个可独立更换的概念。内置 `token_ratio` 按
`trigger_ratio` 工作；其他触发插件可使用回合数、低进展、工具输出增长、模型反馈或显式标记。
`target_ratio` 作为预算传给压缩算法。两类插件分别由 `trigger_strategy`/
`trigger_options_json` 和 `strategy`/`strategy_options_json` 选择，不得把触发条件
硬编码进 Agent Adapter。

### 12.6 决策合并

多个插件输出建议时按优先级合并：

```text
Policy Guardrail
  最高优先级，例如内存/HBM 保护。

Safety Decision
  停止 admit 或降低并发。

Fairness Decision
  防止某类任务饿死。

Throughput Decision
  在安全和公平内最大化吞吐。

Experiment Decision
  只在灰度或实验组生效。
```

规则：

1. 更保守的资源上限优先。
2. 安全风险覆盖吞吐建议。
3. capability 不支持的动作丢弃。
4. 过期 decision 丢弃。
5. 同一 target 的冲突动作只保留最高优先级。

### 12.7 Replay 与 Conformance

新算法上线流程：

```text
实现插件
  -> conformance test
  -> replay 历史批次
  -> advise 模式观察
  -> 小流量 enforce
  -> 默认启用
```

Replay 输入：

```text
events.jsonl
batch-results.tsv
p2-service-global.events.jsonl
policy.toml
candidate strategy
```

Replay 检查：

1. 是否频繁振荡。
2. 是否饿死某类任务。
3. 是否在数据质量不足时做强动作。
4. 是否违反 policy guardrail。
5. 决策是否可解释。

## 13. API 与 CLI

### 13.1 CLI

```bash
agentmon runtime serve --config runtime.toml
agentmon runtime submit --workload workload.yaml
agentmon runtime status <run-id>
agentmon runtime queue
agentmon runtime cancel <run-id>
agentmon optimizer decisions --since 10m
agentmon runtime replay --events <batch-dir> --strategy aimd_model_gate
```

兼容：

```bash
agentmon run -- <command>
```

可以实现为 `ProcessBackend` 的快捷入口。

### 13.2 Python SDK

```python
from agentmon.runtime import RuntimeClient, RunSpec

client = RuntimeClient.from_config("runtime.toml")
run = client.submit(
    RunSpec(
        task_id="calc-001",
        backend="mini-swe",
        input={"problem_statement": "修复 calculator 测试"},
        cwd="/path/to/workspace",
        priority=10,
    )
)
client.wait(run.run_id)
```

## 14. 安全、降级与运维

### 14.1 降级策略

| 失败点 | 降级方式 |
|---|---|
| Agent semantic adapter 不可用 | 降级到黑盒进程观测 |
| ModelProvider request id 不可用 | 保留 P1 span，不做 P2 精确归因 |
| Inference telemetry 不可用 | 使用 latency/error/resource 黑盒策略 |
| ToolExecutor 资源采样失败 | 保留 returncode/duration，资源字段 unavailable |
| Optimizer 异常 | Scheduler 使用静态 policy |
| Scheduler 动态决策异常 | 保持当前并发，不执行危险动作 |
| EventBus 写入失败 | 增加 dropped/error counter，关键事件重试 |

### 14.2 安全红线

1. Runtime core 不得 import 具体 Agent adapter。
2. Runtime core 不得 import 具体推理 telemetry adapter。
3. Optimizer 不得直接执行控制动作。
4. Scheduler 执行动作前必须检查 capability 和 policy。
5. 插件不得直接写 runtime state store。
6. 插件不得绕过 Scheduler kill 进程或改模型服务。
7. sampled/inferred 数据不得当 exact 使用。
8. 无 checkpoint 能力时默认不抢占。
9. 自动 cancel 默认关闭。
10. 所有控制动作必须写审计事件。

### 14.3 进程与工具隔离

延续当前做法：

1. 每个 run 独立 run directory。
2. batch task 独立 HOME/XDG_CONFIG_HOME/workspace。
3. 默认清理 `PYTHONPATH`、`PYTHONHOME`。
4. stdout/stderr 写私有日志。
5. 默认不保存 prompt 全文和 tool output 全文。
6. 命令保存 hash 和摘要。

## 15. 落地路线

### Phase 1：Runtime Core 骨架

目标：

1. 新增 `runtime/types.py`、`runtime/backend.py`、`runtime/core.py`。
2. 把当前 `RunController` 包成 `ProcessBackend`。
3. 保持 `agentmon run` 兼容。

验收：

1. shell command 可通过 runtime submit 执行。
2. run directory、manifest、events 与现有兼容。
3. 现有测试不回退。

### Phase 2：mini-SWE Adapter 解耦

目标：

1. mini-SWE P1 迁入 `adapters/agent_frameworks/mini_swe`。
2. core 不出现 mini-SWE 名称。
3. P1 span 输出 canonical events。

验收：

1. step/model/tool 报告不回退。
2. 黑盒 process backend 不依赖 mini-SWE。

### Phase 3：Telemetry 快照与 Optimizer Advise

目标：

1. 构建 `NodeSnapshot`、`ModelServiceSnapshot`、`RunProfile`。
2. 实现 `static_threshold` 和 `aimd_model_gate` advise。
3. 输出 `optimizer.snapshot` 和 `optimizer.decision`。

验收：

1. 不改变 workload 行为。
2. 决策原因和数据质量可解释。

### Phase 4：Scheduler 与 Model Gate

目标：

1. 从 batch runner 抽出队列和准入。
2. 实现 `model_request_gate`。
3. 支持动态 admit/defer。

验收：

1. 可替代固定 `--concurrency`。
2. vLLM queue/KV 高时降低新请求准入。
3. 所有动作有审计事件。

### Phase 5：Tool Pool 与资源控制

目标：

1. 实现 tool pool max_concurrent。
2. 支持 nice/affinity。
3. 支持 cgroups v2 CPU/memory 基础控制。

验收：

1. CPU-heavy tool 不挤占 Agent 主进程。
2. tool 资源分配写入事件。

### Phase 6：推理 Provider/Telemetry 分离

目标：

1. 新增 OpenAI-compatible Provider。
2. vLLM P2 迁为 InferenceTelemetryProvider。
3. 支持无 P2 的后端。

验收：

1. 不启用 P2 时 runtime 可运行。
2. 启用 P2 时 optimizer 使用深度指标。
3. capability 降级可测试。

### Phase 7：Replay、插件和灰度

目标：

1. 实现 Strategy Registry。
2. 实现 replay simulator。
3. 插件 conformance tests。

验收：

1. 新调度算法可只改配置启用。
2. 新算法先 advise，再 enforce。

### Phase 8：协作式 Agent 控制

目标：

1. 定义 pause/resume/checkpoint 协议。
2. 提供一个自研 SDK 示例 backend。
3. 支持 step 边界 soft pause。

验收：

1. 不支持 checkpoint 的 backend 不会被抢占。
2. 支持 checkpoint 的 backend 可恢复。

## 16. 最小 POC

最小 POC 不追求完整 pause/resume，先验证核心闭环：

1. `ProcessBackend` 接入 Runtime Core。
2. `MiniSweBackend` 输出 P1 canonical events。
3. `OpenAICompatibleProvider` 注入 request id。
4. `Vllm018TelemetryProvider` 读取 P2 events 和 metrics。
5. `ResourceOptimizer` advise 模式判断 queue/KV 压力。
6. `Scheduler` enforce 模式只做动态 admit/defer。
7. `model_request_gate` 控制进入 vLLM 的请求。
8. `tool_pool_semaphore` 控制工具并发。

成功标准：

```text
同一 runtime 可以跑 mini-SWE 和普通 shell agent。
没有 vLLM P2 时仍可运行和报告。
启用 vLLM P2 时能识别 queue/KV 压力。
高压时延迟 long_context/model-heavy 任务。
CPU-heavy tool 被限制在 tool pool。
所有决策和控制动作可 replay 和审计。
```

## 17. 测试策略

### 17.1 单元测试

1. Run state machine。
2. canonical event schema。
3. backend capability。
4. scheduler plan validation。
5. optimizer decision。
6. control action policy check。
7. plugin manifest 和 conformance。
8. data quality 降级。

### 17.2 集成测试

1. process backend 执行简单命令。
2. fake mini-SWE child 输出 P1 events。
3. fake vLLM telemetry 关联 model request。
4. optimizer advise 不改变执行结果。
5. scheduler enforce 动态降低准入。
6. tool pool 限制并发。

### 17.3 压力与回放测试

1. 50 个短任务固定并发对比动态并发。
2. long-context 任务触发 KV/HBM 保护。
3. tool-heavy 任务触发 CPU/IO 保护。
4. replay 检查策略振荡和饥饿。

## 18. 推荐第一批插件

```text
RuntimeBackend:
  process
  mini_swe

ModelProvider:
  openai_compatible

InferenceTelemetry:
  generic_openai_latency
  vllm_018_p2

SchedulerStrategy:
  fixed_concurrency
  priority_fair

OptimizerStrategy:
  static_threshold
  aimd_model_gate

WorkloadClassifier:
  rule_based

ModelRouter:
  weighted_round_robin
  least_queue_pressure

ResourceActuator:
  model_request_gate
  tool_pool_semaphore
  cgroup_v2_basic
```

第一批闭环：

```text
任务入队
  -> rule_based 分类
  -> static_threshold/AIMD 判断负载
  -> priority_fair 调度
  -> model_request_gate 控制 vLLM 入口
  -> tool_pool_semaphore 控制工具并发
  -> cgroup_v2_basic 控制工具 CPU/内存
  -> 所有决策可 replay
```
