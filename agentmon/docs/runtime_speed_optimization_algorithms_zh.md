# AGENTRUN Runtime 速度优化策略算法说明

本文说明 AgentMon/AGENTRUN 当前已经落地的 Runtime 层速度优化策略。重点是算法原理和适用边界，而不是代码实现。

这里的“速度优化”指在不修改 vLLM、CANN、Ascend Runtime、LMCache、OS/cgroup 管理器等推理引擎或底层系统软件的前提下，通过 Runtime 层的调度、准入、限流、资源隔离和低效任务识别来改善：

- 并发任务的端到端完成时间；
- 模型请求的排队时间和 TTFT；
- 批量任务的有效吞吐；
- 工具调用对模型请求路径的干扰；
- 长尾任务、低进展任务和异常路径对整体 workload 的拖累。

## 1. 总体性能模型

一个 Agent 任务的端到端耗时可以抽象为：

```text
T_task =
  T_runtime_queue
+ Σ step(
    T_agent_decision
  + T_model_client_wait
  + T_model_service_queue
  + T_prefill
  + T_decode
  + T_tool_queue
  + T_tool_exec
  + T_context_growth_cost
  )
+ T_finalization
```

在不改推理引擎和底层系统的条件下，Runtime 不能直接压缩单个请求的 `T_prefill` 或 `T_decode` 内核执行时间，但可以控制以下量：

- 同时运行多少 Agent 任务；
- 哪类任务可以同时运行；
- 模型请求进入推理服务的速率；
- CPU/IO-heavy 工具命令是否会和模型请求客户端、采样器、Agent 主进程争抢资源；
- 低进展任务是否继续消耗模型请求、上下文和工具资源；
- 是否让长上下文、高 token 预算任务集中爆发，造成 KV、HBM 或服务队列压力。

因此，当前策略的核心思想是：

```text
用 Runtime 层控制 arrival rate、workload mix 和 local contention，
让推理服务和本地执行环境尽量工作在低排队、低尾延迟、高有效吞吐区间。
```

这类策略对“单请求裸推理速度”的帮助有限，但对并发 Agent workload 的端到端速度很关键。

## 2. 优化目标

当前策略采用多目标优化，而不是只追求最大并发：

1. 降低服务排队和 TTFT。
2. 降低 p95/p99 端到端完成时间。
3. 提高单位时间完成的有效任务数。
4. 避免低价值任务长期占用模型、工具和上下文预算。
5. 在缺失观测数据时保持保守，不用猜测值驱动强控制。
6. 保持 Runtime core 与具体 Agent 框架和推理框架解耦。

典型目标函数可以写成：

```text
minimize:
  α * p95(T_task)
+ β * p95(TTFT)
+ γ * p95(model_service_queue)
+ δ * wasted_model_requests
+ ε * failed_or_unpublishable_runs

subject to:
  success_rate 不下降到不可接受范围
  data_quality 满足策略要求
  control action 只作用于 Runtime 可控对象
```

其中权重 `α...ε` 不在通用逻辑中硬编码，应由实验目标、配置和策略 Profile 决定。

## 3. 策略总览

当前已经落地的速度相关策略可以分为六层：

| 层级 | 策略 | 直接控制对象 | 主要改善指标 |
|---|---|---|---|
| 任务准入 | 固定并发准入 | 同时运行的 run 数 | workload makespan、尾延迟 |
| 任务准入 | Workload-aware 调度 | 任务组合、长上下文数量、预测 token 预算 | TTFT、KV 压力、端到端尾延迟 |
| 反馈优化 | Static-threshold / workload-aware optimizer | admission pressure 和预算建议 | 拥塞恢复、低进展治理 |
| 请求控制 | Model Gate | 进入推理服务前的模型请求 inflight | vLLM queue、TTFT、请求 E2E |
| Prompt 控制 | 上下文压缩策略 | coding agent 发给模型的消息副本 | Prefill、KV 占用、请求 E2E、任务 E2E |
| 本地资源 | Tool Pool 和工具资源控制 | 本地工具并发、CPU/IO 干扰 | tool latency、模型客户端等待、端到端时间 |

这几层不是互相替代，而是串联工作：

```text
任务提交
  -> Runtime 队列
  -> 任务级 Scheduler admit/defer
  -> Agent 执行
  -> Context Compression 评估/压缩
  -> 模型请求 Model Gate
  -> 工具请求 Tool Pool
  -> 事件反馈
  -> Optimizer 决策和 TaskState 更新
```

### 3.1 Runtime 优化总开关

所有会改变任务准入、请求准入、工具并发或子进程资源约束的策略，共享一个总开关：

```toml
[runtime_optimization]
enabled = true
```

也可以使用环境变量覆盖：

```bash
export AGENTMON_RUNTIME_OPTIMIZATION_ENABLED=false
```

总开关默认开启，以保持既有配置的行为兼容。关闭时，各子策略自己的 `enabled`
配置仍被保留，但不产生主动控制效果：

| 组件 | 总开关关闭后的确定性行为 |
|---|---|
| Scheduler | 所有候选任务 `admit`，原因标记为 `runtime_optimization_disabled`。 |
| Optimizer | 返回 `admission_pressure=none`，不产生降并发或预算建议。 |
| Model Gate | 不拒绝模型请求，也不占用本地 gate slot。 |
| Tool Pool | 不因 pool 容量拒绝工具命令。 |
| Resource Actuator | 不施加 nice、内存上限或 CPU affinity。 |
| Context Compression | 不改写模型请求上下文，原消息直接放行。 |
| P0/P1/P2 与 TaskState | 继续采集和派生，只作为证据，不执行优化动作。 |

保留观测而只关闭动作，是为了让同一采集口径支持 A/B：控制组关闭总开关，实验组开启
总开关和选定子策略。不要通过关闭 P1/P2 来构造控制组，否则两组的数据质量和测量开销
同时变化，无法把差异归因于调度策略。

总开关与子开关的有效关系为：

```text
effective_feature_enabled =
  runtime_optimization.enabled AND feature.enabled
```

其中 `feature` 可以是 scheduler、optimizer、control、model_gate、tool_pool 或
context_compression。

## 4. 固定并发准入

### 4.1 算法思想

固定并发准入是最基础的 overload protection。它假设在某个硬件、模型和任务分布下，系统存在一个合理的并发上限 `Cmax`。当 active runs 达到该上限时，新任务不立即启动，而是进入 deferred/queued 状态。

准入规则：

```text
active = count(runs whose state in active_states)

if active >= Cmax:
  defer(candidate)
else:
  admit(candidate)
```

### 4.2 为什么能提速

Agent workload 常见的问题是“并发越高，服务内排队越长，TTFT 和尾延迟增长快于吞吐增长”。超过系统拐点后，新任务会让所有任务都变慢：

- vLLM 内部 waiting queue 增大；
- KV/HBM 压力升高；
- prompt prefill 互相堆叠；
- 客户端等待和超时增加；
- 工具进程、采样、日志写入也会争抢 CPU/IO。

固定并发上限通过牺牲一部分瞬时并发，避免系统进入拥塞区，通常能改善 p95/p99 完成时间，也能提高“成功完成任务/单位时间”的有效吞吐。

### 4.3 调参原则

`Cmax` 不应该来自某次实验的硬编码结论，而应通过服务器基准实验确定。推荐从低并发开始扫描，观察：

- workload task throughput；
- successful workload throughput；
- p50/p95/p99 TTFT；
- vLLM queue latency；
- KV/HBM 使用和 eviction；
- task timeout、模型错误、publishable 比例。

合理的 `Cmax` 通常在“吞吐开始趋平但尾延迟尚未陡增”的位置附近。

### 4.4 局限性

固定并发不理解任务差异。一个短上下文、少请求任务和一个长上下文、高 token 任务在这个策略中都占一个 slot。因此它是安全基线，不是最终的最优调度策略。

## 5. Workload-aware 任务调度

### 5.1 算法思想

Workload-aware 调度在固定并发准入之上加入任务画像。它不只问“现在有几个任务在跑”，还问：

- 这些任务预计还会消耗多少 prompt tokens；
- 预计还会生成多少 tokens；
- 是否有长上下文任务正在运行；
- 新任务是否本身就有异常路径风险；
- 当前任务状态是否有可用的预测信息。

候选任务 `j` 只有在固定并发和 workload budget 都通过时才会被 admit。

### 5.2 P90 token budget 检查

对 prompt token 和 generation token 分别建立预算。

```text
S_prompt =
  Σ remaining_prompt_tokens_p90(active_i)
+ remaining_prompt_tokens_p90(candidate_j)

S_generation =
  Σ remaining_generation_tokens_p90(active_i)
+ remaining_generation_tokens_p90(candidate_j)
```

如果候选任务的预测值可用，且加入候选后超过配置预算：

```text
S_prompt > B_prompt_p90      -> defer
S_generation > B_gen_p90     -> defer
```

P90 的含义是保守控制尾部风险，而不是只看平均值。Agent 任务往往长尾明显，平均 token 预算会低估拥塞风险。

### 5.3 Long-context 限制

长上下文任务对 TTFT 和 KV/HBM 压力更敏感。策略使用两个参数：

```text
long_context_token_threshold = L
long_context_limit = K
```

若候选任务的 active context tokens 达到阈值 `L`，并且当前 active long-context 任务数已经达到 `K`：

```text
defer(candidate)
```

这样做的目标是避免多个长上下文任务同时触发大 prefill、KV 分配和缓存压力。

### 5.4 Abnormal-path 风险限制

某些任务在开始前或运行中可被预测为高风险路径，例如：

- 可能反复失败；
- 可能进入大量工具重试；
- 可能消耗过多上下文；
- 可能长时间无有效进展。

策略使用异常路径概率：

```text
if abnormal_path_probability >= risk_threshold:
  defer(candidate)
```

这里的 `defer` 不是说任务永远不执行，而是避免它在资源紧张或混部不利时拖累其他任务。

### 5.5 缺失预测的处理

如果预测缺失，策略不能猜测 token 或风险。当前原则是：

```text
known facts drive decisions;
unknown stays unknown;
fallback to fixed-concurrency safety baseline.
```

这避免了把不可靠预测变成强控制依据。

### 5.6 为什么能提速

Workload-aware 调度通过控制 workload mix 改善速度：

- 减少同时发生的大 prompt prefill；
- 降低长上下文任务对 KV/HBM 的集中冲击；
- 避免高风险任务在高负载时扩大尾延迟；
- 让短任务和正常任务更容易快速完成；
- 降低 batch 中“少数异常任务拖慢整体收尾”的概率。

它优化的是整体 workload，而不是单个任务的绝对公平等待时间。

## 6. 优先级与队列选择

Runtime 队列支持任务优先级。队列选择遵循：

```text
优先级高的任务先被选择；
同优先级下，提交时间更早的任务先被选择。
```

该策略适合在混合任务中保护高价值或交互式任务。例如短 SLA 任务、验证任务、回归 smoke task 可以获得更低的等待时间。

优先级策略本身不会提高系统总算力，但可以改善关键任务的端到端延迟。使用时需要避免长期饿死低优先级任务。

## 7. Optimizer 反馈控制

### 7.1 算法角色

Scheduler 决定“是否启动任务”。Optimizer 判断“当前系统压力如何，以及下游控制面应该采用什么目标”。

Optimizer 的输出是一个决策对象，核心字段包括：

- admission pressure：`none`、`moderate`、`high`；
- target concurrency；
- risk flags；
- budget recommendations；
- confidence；
- data quality。

它本身不直接修改推理引擎，也不直接 kill 任务。强控制必须经过 Control Plane 的权限和模式检查。

### 7.2 Static-threshold 压力判断

Static-threshold 策略基于服务和队列快照判断压力。

典型规则：

```text
if model waiting requests >= threshold:
  pressure = high

if model queue latency p95 >= threshold:
  pressure = high

if KV usage ratio >= threshold:
  pressure = high

if running_count / max_running_runs >= high_utilization_ratio:
  pressure = moderate
```

这些阈值来自配置。策略只使用观测到的字段；如果某个字段不可用，不会用猜测值代替。

### 7.3 Workload-aware Optimizer

Workload-aware Optimizer 在基础压力判断上叠加任务进展状态：

```text
if any active task is STALLED:
  add risk flag: stalled_runtime_tasks_present
  recommend: defer_or_degrade_future_model_requests

if any active task is LOW_PROGRESS:
  add risk flag: low_progress_runtime_tasks_present
  recommend: compact_context_and_replan
```

如果系统资源看似没有满，但存在停滞或低进展任务，策略会把压力提高到至少 moderate。原因是低进展任务会持续消耗模型请求和工具资源，对整体 makespan 有隐性伤害。

### 7.4 为什么能提速

Optimizer 的价值在于把“被动观测”变成“可执行的控制目标”：

- 服务队列过高时，后续 Model Gate 可以降低 inflight；
- KV 压力过高时，调度可减少长上下文并发；
- 低进展任务存在时，可建议压缩上下文、重规划或限制后续模型请求；
- 通过 replay 可以在历史事件上验证策略，不必一开始就 enforce。

## 8. Model Gate 请求限流

### 8.1 算法思想

Model Gate 控制模型请求进入推理服务前的 inflight 数量。它是 Runtime 在不改 vLLM scheduler 的情况下影响 vLLM queue 和 TTFT 的主要在线控制点。

策略根据 Optimizer pressure 选择目标 inflight：

```text
pressure = none      -> target = max_inflight
pressure = moderate  -> target = moderate_pressure_target
pressure = high      -> target = high_pressure_target
```

准入规则：

```text
if control mode is advise:
  allow request, record decision

if control mode is enforce:
  if current_inflight < target:
    admit request
  else:
    reject or defer request according to caller policy
```

### 8.2 为什么能提速

推理服务的内部队列不是越长越好。当请求到达速率长期超过服务处理速率时：

```text
arrival_rate > service_rate
```

排队时间会快速增大，TTFT 和 E2E 进入长尾。Model Gate 在服务外部调节 arrival rate，使系统更接近：

```text
arrival_rate <= sustainable_service_rate
```

这样可以降低服务内排队，减少 timeout 和重试，也让已经进入服务的请求更快得到 first token。

### 8.3 对端到端时间的影响

Model Gate 可能增加某些请求在 Runtime 侧的等待或重试，但如果系统已处于拥塞区，它通常会降低整体端到端时间，因为：

- 服务内等待下降；
- TTFT 下降；
- 请求超时减少；
- 长尾任务不再把所有请求一起拖慢；
- 批量任务更少出现“最后几个任务长时间卡住”的现象。

### 8.4 使用边界

Model Gate 只影响经过 Runtime/Provider wrapper 的请求。绕过 Runtime 直接访问模型 endpoint 的请求不受控制。

## 9. Tool Pool 工具资源调度

### 9.1 算法思想

Agent 任务不仅消耗模型服务，还会运行本地工具，例如测试、构建、压缩、解压、文件复制、仓库操作等。工具命令可能占用 CPU、内存和 IO，间接拖慢模型请求客户端、Agent 主进程和采样写入。

Tool Pool 将工具按资源形态分池：

```text
default
cpu_heavy
io_heavy
high_priority
```

每个池有自己的并发上限：

```text
if running_count(pool) < max_concurrent(pool):
  admit tool call
else:
  reject or defer according to control mode
```

### 9.2 命令分类原则

分类依据应来自配置和可观察命令形态，而不是硬编码某次实验。常见策略：

- 编译、测试、Python 脚本等归入 CPU-heavy；
- 压缩、解压、复制、同步等归入 IO-heavy；
- 未识别命令进入 default；
- 特殊任务可通过策略或 adapter 标注 high-priority。

### 9.3 为什么能提速

Tool Pool 的主要收益来自降低本地资源争抢：

- 避免大量测试/构建同时占满 CPU；
- 避免压缩/复制类任务造成 IO 抖动；
- 降低 Agent 主进程等待工具返回的尾延迟；
- 降低模型请求客户端被本地 CPU/IO 抢占的概率；
- 减少工具风暴导致的整体 workload 抖动。

它不让单个工具命令本身更快，但让多个 Agent 并发时的工具等待更可控。

## 10. 工具子进程资源约束

### 10.1 算法思想

在 Tool Pool 之外，Runtime 还可以为受控工具子进程生成资源控制计划：

- nice increment；
- 内存上限；
- CPU affinity。

这些控制是 best-effort，本质是把工具进程限制在更可预测的本地资源范围内。

### 10.2 为什么能提速

资源约束的目标不是提高工具峰值性能，而是降低工具对关键路径的干扰：

- 避免某个测试或构建进程吃掉过多 CPU；
- 避免异常工具进程占用过多内存；
- 把工具负载限制在指定 CPU 集合，减少和模型客户端或采样线程的争抢；
- 让端到端任务时间更稳定。

### 10.3 使用边界

这类控制只作用于 Runtime 启动并管理的工具子进程。外部 shell、手工启动的进程或 Agent 框架内部未接入 Runtime 的工具执行路径不受影响。

## 11. TaskState 低进展治理

### 11.1 算法思想

TaskState 从事件流派生任务进展状态。它不是用固定任务 ID 或实验结论判断，而是使用通用信号：

正向信号包括：

- 测试结果改善；
- 有效 diff；
- 子目标推进；
- 新证据产生；
- 模型请求或工具调用带来可解释进展。

负向信号包括：

- 重复工具调用；
- 重复错误；
- 上下文快速增长；
- token waste；
- 预算超限。

进展分数可以抽象为：

```text
progress_score =
  base_score
+ positive_signal_count * positive_weight
- negative_signal_count * negative_weight
```

然后根据阈值分类：

```text
score >= normal_threshold      -> NORMAL
stalled_threshold < score < normal_threshold -> LOW_PROGRESS
score <= stalled_threshold     -> STALLED
hard_budget_exceeded           -> STALLED
missing evidence               -> UNKNOWN or PARTIAL
```

### 11.2 上下文治理建议

TaskState 还会识别上下文增长和重复内容风险，给出建议：

- compact dynamic context；
- 去重或外置工具输出；
- 重建 stable prefix layout；
- replan or review；
- defer/degrade future model requests。

这些建议默认不自动终止任务，而是作为 Optimizer 和 Control Plane 的输入。

### 11.3 为什么能提速

Agent 任务的慢，有时不是推理服务慢，而是任务进入了低效循环：

- 反复运行同一个工具；
- 反复生成无效 patch；
- 上下文越来越长但有效信息密度下降；
- 错误模式重复；
- 模型请求数量增加但成功概率下降。

低进展治理通过减少 wasted model requests 和 wasted tool calls 改善端到端时间。对批量 workload 来说，少数 STALLED 任务往往决定最后的 makespan，因此及时识别它们很重要。

## 12. 主动上下文压缩

### 12.1 触发与目标

设模型窗口为 `W`，本次请求发送前测得的上下文 token 数为 `C`，触发比例为
`r_trigger`，目标比例为 `r_target`。当前目标配置是：

```text
r_trigger = 0.80
r_target  = 0.20
T_trigger = ceil(W * r_trigger)
T_target  = floor(W * r_target)
```

内置 `token_ratio` 触发策略只在 `C >= T_trigger` 时调用压缩算法。
`window_tokens=0` 时从
`model.max_model_len` 取 `W`；两者都缺失时状态为 `UNRESOLVED`，不猜测模型窗口，
也不修改请求。80% 和 20% 是配置默认值，不写入算法分支，可通过 TOML、环境变量或
运行时策略配置改变。其他 Trigger Plugin 可以不使用 `T_trigger`，但仍必须返回显式
的 triggered/not-triggered/unresolved 结果和限制证据。

mini-SWE 请求前无法从服务端提前得到本次真实 `prompt_tokens`。内置策略因此使用消息
序列化字符数与 `estimator_chars_per_token` 计算确定性估算，并把质量标为
`estimated_serialized_chars_div_*`。估算可以用于控制，但报告禁止把它当作 tokenizer
精确计数。需要精确边界时，应由插件使用目标模型 tokenizer 实现 `measure`。

### 12.2 内置 `model_summary` 算法

通用策略不理解任何 Agent 消息角色。Agent Framework Adapter 先把消息序列映射为有序
原子组：

```text
G = [g0, g1, ..., gn]
R = 必须保留的组集合
cost(gi) = 该组的 token 估算
```

mini-SWE Adapter 将以下内容标成必保留或原子关系：

- 前导 system/developer 指令必须保留；
- 第一个非指令消息视为任务契约，必须保留；
- assistant 及紧随其后的 tool/observation 结果组成一个原子组；
- 单条消息不会被从中间截断。

选择算法固定保留 `R`，再从最新端保留至少 `min_recent_groups` 个完整交互组。其余更早
的组组成摘要输入 `H`：

```text
K = R union newest_complete_groups(G, min_recent_groups)
H = G - K
T_summary = max(1, T_target - cost(K))
summary = SummaryProvider(H, max_output_tokens=T_summary)
compressed_context = insert(summary, K, at=first_removed_position)
```

Summary Provider 的指令要求保留任务目标、显式约束、已验证事实、决策、文件和命令、
测试结果、未解决错误与后续工作，并禁止补造事实。mini-SWE Adapter 默认复用当前模型，
把 `H` 序列化为独立摘要请求；Runtime Core 只看到 Provider 契约，不依赖 LiteLLM、vLLM
或具体消息模板。摘要请求具有独立 request ID，因此其排队、Prefill、Decode、Token 和
成本可以与主请求分开分析。

算法不会为追求 20% 数字而拆开 assistant-tool 协议组，也不会截断模型返回的摘要。
如果必保留组、最近组与摘要合计超过目标，仍保留协议正确性并返回 `PARTIAL`；达到预算
返回 `COMPLETE`。因此“压缩到 20% 左右”表示以窗口 20% 为预算目标，不保证输出恰好
等于 20%。模型摘要不能证明语义等价，后续任务成功率、重试、工具路径和错误集合仍是
必要验证证据。

旧 `recent_groups` 策略仍可显式选择，用于不调用摘要模型的确定性裁剪对照。它保留 `R`
和最新连续组，但不再是默认算法，也不能称为语义摘要。

### 12.3 请求副本与失败语义

压缩只作用于当次发给模型 Provider 的消息副本。Agent 内部完整消息列表和最终
trajectory 不删除，因此后续审计仍能看到原执行历史。AgentMon 的 `events.jsonl` 只写
策略名、状态、压缩起止与耗时、估算质量、压缩前后数量、摘要请求身份与 usage、比例、
限制和禁止结论，不写 prompt、摘要内容或被移除工具输出。

状态语义如下：

| 状态 | 含义 |
|---|---|
| `DISABLED` | 子开关关闭或 Runtime 优化总开关关闭。 |
| `NOT_TRIGGERED` | 已评估，但当前 Trigger Plugin 决定不压缩。 |
| `COMPLETE` | 已压缩且达到目标预算。 |
| `PARTIAL` | 已压缩，但协议/必保留约束使目标无法达到，或压缩后计数仍不完整。 |
| `UNRESOLVED` | 模型窗口或上下文测量缺失，未执行压缩。 |
| `ERROR_BYPASS` | 插件加载、测量或压缩失败，原上下文放行。 |

失败放行避免观测插件故障直接终止 coding task。若业务要求压缩失败必须拒绝请求，应由
另一个显式的 enforce 策略或 Policy Gate 实现，不能悄悄改变默认失败语义。

### 12.4 插件契约

压缩链路包含两类插件契约：

```text
trigger.evaluate(ContextCompressionRequest) -> ContextCompressionTriggerResult
measure(context, adapter_metadata) -> ContextMeasurement
compress(ContextCompressionRequest) -> ContextCompressionResult
```

触发插件通过 `register_context_compression_trigger` 注册，由
`trigger_strategy` 和 `trigger_options_json` 选择；可实现 token 比例、回合数、低进展、工具输出
增长或显式标记触发。压缩插件通过 `register_context_compression_strategy` 注册，
由 `strategy` 和 `strategy_options_json` 选择；可实现 tokenizer 计数、抽取式摘要、模型
摘要、工具结果去重或检索式记忆。`plugin_modules` 在构建两类策略前显式导入。

### 12.5 为什么可能提速

上下文从接近 `0.8W` 降到不高于 `0.2W` 后，理论上可减少单请求 prefill token、KV
分配和传输字节，进而降低请求 E2E、排队放大和长上下文并发时的 HBM 压力。它不能保证
单 token decode 内核更快，也可能因删除重要历史导致额外回合、错误工具调用或任务失败。
因此必须同时比较：任务成功率、任务 E2E、模型请求数、实际 prompt tokens、TTFT、
recompute、KV/HBM、压缩次数和 `PARTIAL/ERROR_BYPASS` 比例。

## 13. P2/遥测在速度优化中的作用

P2、P1 和 P0 事件本身不是控制动作，但它们为调度和优化提供证据：

- P1 给出 Agent step、model request、tool call 的客户端视角耗时；
- P2 给出 vLLM request queue、TTFT、prefill、decode、KV、scheduler iteration 等服务侧视角；
- P0 给出主机、进程、NPU、HBM 等资源趋势。
- MoE 路由摘要可给出逻辑专家负载集中度和熵，用于识别路由偏斜是否与 queue、
  TTFT 或长尾同时出现；它本身不改变路由，也不能证明物理专家或 rank 的执行耗时。

这些信号用于判断瓶颈位置：

```text
queue high, decode normal        -> 降低 request inflight 或任务并发
prefill high, long context high  -> 限制长上下文任务混部
tool latency high, CPU high      -> 收紧 CPU-heavy tool pool
IO wait high                     -> 收紧 IO-heavy tool pool
LOW_PROGRESS tasks present       -> 建议 replan/compact/degrade
```

策略必须保留数据质量和限制条件。缺失 P2 时可以做黑盒限流，但不能宣称具体是 vLLM queue、prefill 或 KV 导致。

## 14. 策略之间的相互作用

### 14.1 Scheduler 与 Model Gate

Scheduler 控制 run 级别并发，Model Gate 控制请求级别 inflight。

- Scheduler 粒度粗，适合避免任务级过载；
- Model Gate 粒度细，适合处理任务内部突发模型请求；
- 两者同时存在时，Scheduler 应控制整体 workload mix，Model Gate 应处理短周期压力。

### 14.2 Scheduler 与 Tool Pool

Scheduler 不知道每一步具体会运行什么工具。Tool Pool 在工具调用发生时做更细粒度控制。

- 如果 CPU-heavy 工具过多，Tool Pool 可以局部限流；
- 如果某类任务长期制造工具压力，后续可由 workload-aware scheduler 减少这类任务同时运行。

### 14.3 Optimizer 与 Control Plane

Optimizer 只判断压力和建议目标，Control Plane 决定是否执行。这样可以支持：

- advise 模式下先观察；
- replay 模式下用历史事件验证；
- enforce 模式下小流量灰度；
- 失败时回退到安全基线。

## 15. 当前不能做到的优化

在不修改推理引擎或底层系统软件的前提下，当前 Runtime 策略不能直接做到：

- 改变 vLLM continuous batching 内部排序；
- 改变 vLLM preemption、scheduler iteration 或 KV block 分配算法；
- 提高单请求 decode kernel tokens/s；
- 改变 CANN/HCCL/NPU kernel 行为；
- 控制 LMCache-Ascend 的真实 pin/offload/prefetch/evict，除非后续发现公开可控接口；
- 控制绕过 Runtime 的外部工具进程；
- 对未接入 adapter/provider 的 Agent 框架透明限流。

因此，所有收益都应表述为 Runtime 层调度和资源控制带来的 workload-level 改善，而不是底层推理引擎性能提升。

## 16. 验证方法

建议通过 A/B 实验验证每个策略的速度收益。

### 16.1 对照组

对照组应保持：

- 相同任务集；
- 相同模型、端口和硬件；
- 相同采样配置；
- 相同 mini-SWE/Agent 参数；
- 相同 P2/P1/P0 可观测性；
- 只关闭或保持默认 Runtime 控制策略。

### 16.2 实验组

逐步开启：

1. 固定并发上限；
2. workload-aware scheduler；
3. optimizer advise；
4. Model Gate advise；
5. Tool Pool advise；
6. 小流量 enforce。

不要一次打开所有策略，否则无法判断收益来源。

### 16.3 关键指标

任务层：

- attempted runs；
- execution success；
- publishable runs；
- workload makespan；
- successful workload throughput；
- p50/p95/p99 task duration。

模型层：

- model request count；
- client-observed model latency；
- server queue latency；
- TTFT；
- prefill；
- decode；
- request E2E；
- timeout/error rate。

资源层：

- CPU utilization；
- IO pressure；
- HBM 使用；
- KV usage；
- KV eviction；
- NPU utilization。

策略层：

- admit/defer count；
- model gate admitted/rejected/deferred count；
- tool pool admitted/rejected/deferred count；
- LOW_PROGRESS/STALLED count；
- data quality coverage。

### 16.4 判定标准

一个策略可认为有效，至少应满足：

- successful workload throughput 不下降，或端到端 p95 明显下降；
- TTFT/queue latency 有可解释改善；
- success/publishable 比例不显著下降；
- 监控数据质量足以支撑结论；
- 没有引入新的大规模 timeout、误拒绝或饥饿。

## 17. 推荐调优顺序

1. 先只用观测数据找系统拐点：并发、queue、TTFT、KV/HBM、tool latency。
2. 设置保守的固定并发上限，避免明显过载。
3. 让 Runtime 生成 TaskState，观察 LOW_PROGRESS/STALLED 是否符合人工判断。
4. 给任务输入 workload prediction，启用 workload-aware scheduler。
5. 开启 Optimizer advise，确认 pressure 和 risk flags 与 P2/P0/P1 证据一致。
6. 开启 Model Gate advise，评估如果 enforce 会影响多少请求。
7. 小流量启用 Model Gate enforce，观察 TTFT、queue 和成功率。
8. 开启 Tool Pool advise，再对 CPU-heavy/IO-heavy 工具小流量 enforce。
9. 根据 A/B 结果调整预算和阈值，避免把某次实验的最优值写成通用默认值。

## 18. 通用性原则

当前 Runtime 速度优化遵循以下原则：

- Runtime core 不感知 mini-SWE、vLLM 或具体任务 ID；
- 阈值、预算、字段映射和策略选择来自配置、Profile 或运行时发现；
- 缺失数据保持 UNKNOWN/PARTIAL，不猜测补齐；
- Optimizer 输出建议，Control Plane 负责校验和执行；
- advise/replay 先于 enforce；
- 速度收益必须通过证据链说明，不把相关性直接写成因果结论；
- 不把某次服务器观测到的并发档位、硬件数值、事件数或经验门限写进通用逻辑。

## 19. 一句话总结

AGENTRUN 当前的速度优化路线是：

```text
不改推理引擎，而是在 Runtime 层调节任务进入系统的方式、
模型请求进入推理服务的节奏、本地工具占用资源的强度，
并识别低进展任务以减少无效 token 和无效工具消耗。
```

它主要优化的是多 Agent 并发 workload 的排队、尾延迟、成功吞吐和端到端完成时间，而不是单个推理请求的底层算子速度。
