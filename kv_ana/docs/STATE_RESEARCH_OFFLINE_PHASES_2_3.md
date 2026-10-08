# 第二、第三阶段：离线实现与逐步验收

扩展版本 `state-research-offline-v1.0.1`；冻结 framework 保持 2.0.1。
本交付把第一批 LifeMem 数据准备器向后接到**状态生命周期分析、策略回放、恢复计划比较**。
所有新增实现位于 `state_research/`、独立脚本和测试中。没有修改 AgentMon，也没有改变
论文方法、B1–B5、真实 adapter 的数值/映射语义。
审计补丁另包含一处核心 metadata 修复：artifact hash 前排序训练身份集合，消除进程间
随机顺序；不改变训练或评估数学。旧 checkpoint 不自动迁移，见审计文档。

## 完成范围与验收边界

| 阶段 | 已能在 CPU 离线执行 | 后续真实验收输入 |
|---|---|---|
| 第一阶段 | LifeMem 正文/完整 prompt/显式 token 对齐、上下文变化、代表案例、AgentMon 精确 ID 关联 | 全量 LifeMem、目标 tokenizer/真实输入 IDs |
| 第二阶段 | 状态事件校验、共享物理容量核算、依赖工作集、访问间隔、固定轨迹策略对比 | 各类型 runtime 状态事件与实际消费记录、设备容量 |
| 第三阶段 | 显式依赖失效传播、恢复成本 profile、受证据约束的恢复路径比较、给定状态结果评估 | 同条件成本测量、路径有效性证据、真实 A/B/修复态及 row mapping |

本地 synthetic 通过是**工具/协议验收**，不是论文结论、完整采集覆盖或真实性能验收。
`real_acceptance=PENDING` 始终保留，模型执行状态为 `NOT_RUN`。
没有在线资源调控、自动修复执行、模型 forward、NPU/GPU 启动或 AgentMon adapter。

**完成度修正**：第二、三阶段这里指离线实验工具链，不是立项稿三个技术点的算法实现完成。
固定 LRU/分区/联合逐出仅为 baseline；没有基于边际收益的动态资源优化器；依赖工作集分析
不是运行时跨步继承/分支执行器；外部证据约束的恢复路径选择不是状态修复算法。
三个研究技术点也是互相反馈的控制关系，不应把本工具的 CLI 顺序误解为研究架构的单向流水线。
本轮审计的具体反例、修复和剩余缺口见 `STATE_RESEARCH_AUDIT_V1_0_1.md`。

## 代码与接口

| 模块 | 作用 |
|---|---|
| `lifecycle.py` | `StateEvent`、默认关闭的 `EventRecorder`、`analyze_lifecycle` |
| `snapshot_archive.py` | `write_snapshot`：保存调用方显式提供的 CPU NumPy 状态视图 |
| `policy_replay.py` | `replay_demands`：三个固定访问轨迹缓存管理 baseline |
| `recovery.py` | `CostProfiles`、`plan_recovery`：成本查询和计划，不执行恢复 |
| `state_compare.py` | 显式 row mapping 的 NPZ 状态比较、依赖图影响分析 |
| `scenarios.py` | 构造请求到达/间隔场景；不伪造服务时间 |
| `offline_suite.py` | 分阶段离线执行、报告、<=45KB UTF-8 capsule |
| `offline_fixtures.py` | 已标明 synthetic 的完整可检查输入例子 |

## 先本地验收，再替换真实输入

从项目或便携包根目录运行，输出目录必须新建或为空：

```bash
# 第一批数据准备器（可单独验收）
python3 scripts/state_research_smoke.py --output outputs/accept_batch1

# 第二/三阶段：会生成 inputs/config.json 和所有 synthetic 输入文件
python3 scripts/state_research_offline_smoke.py --output outputs/accept_phases23

# 同一批输入重新运行，验证不是 smoke 内置结果
python3 scripts/run_state_research_offline.py \
  --config outputs/accept_phases23/inputs/config.json --output outputs/accept_replay

# 真实资料尚未填入时，应产生 PARTIAL 而非通过
python3 scripts/run_state_research_offline.py \
  --config configs/state_research_offline.example.json --output outputs/accept_missing
```

服务器可将 `python3` 替换为已安装 NumPy 的 CPU Python。没有网络安装、特殊 Mac 包依赖或
torch 必选依赖。当前不连接服务器，不执行上述命令中的任何模型。
真实配置中的文件路径相对配置文件目录解析；从生成的 `inputs/config.json` 理解实际格式，
再使用空白 example 配置逐项填写，**不要保留 synthetic 的 validity/source/cost 冒充真实证据**。

## 事件契约

`schema_version=heterogeneous-state-events-v1`。每个事件包含唯一 event_id、严格递增 sequence、
非递减 timestamp_ns、单一 clock_domain、request_id、action、source，以及可空的
agent_run_id / model_request_id / engine_request_id。时间相同时由 sequence 排序。
跨机器时钟必须上游显式对齐，本工具不猜偏移。

- `state.create/restore`：`data.state` 是第一阶段的完整 StateInstance；依赖必须已存在。
- `state.share`：另外提供 parent_state_id；必须是相同物理 allocation 和 content_version。
  多个 request 各建一个逻辑 view；不把跨 request 访问自动当成共享。
  不同 request 的 context_version 可以不同，不因此推断内容可以安全共享；这是对上游实际
  共享行为的记录，不是本工具授权共享。
- `state.consume`：consumer_layer 必须显式声明；递归展开 dependency_state_ids，按
  device + allocation_id 去重工作集。selected_rows_count 是逻辑需求，measured_read_bytes
  是可选实测；前者绝不自动换算后者。
- `state.update`：显式更大的 content_version，表示原位修改同一分配；同步所有 alias 的
  内容版本，但保留各逻辑 view 的 request-local context_version。同 prompt 的索引刷新
  不再伪造上下文变化。确实改变上下文时，额外传 context_version，只更新目标 owner view。
  producer 内容改变后，消费依赖对象会输出 revalidation 提示，直到依赖被显式刷新；
  该提示不擅自断定模型数学失效或安全。旧 synthetic/provided context-only update 保留
  兼容但整体为 PARTIAL；runtime 必须显式给 content_version。
- `state.move`：device/tier/allocation_id/allocation_bytes 是完整物理分配的迁移，更新所有
  aliases；该事件表示迁移结束的稳态，不能反映 copy 期间瞬时双份占用。
  如需记录重叠，分别 create/restore 目标副本、release 源视图。
- `state.release`：释放一个 view；最后一个 view 释放才减少物理占用。仍被 live view
  依赖的 state 不可先释放。跨设备副本分别计费。
- `request.begin/pause/resume/end`：可选的显式请求阶段；pause/end 后不允许 consume。
  request.end 不自动释放缓存，避免把跨请求持久缓存错误计为释放。

EventRecorder 默认关闭时不建目录、不写盘；只接受调用方构造的事件，没有安装真实 hook。
事件不放 tensor。快照打包器保存独立 CPU 数组；量化值/scale/index 可以分别保存并用 binding
关联，不能自动反量化。bf16/NPU tensor 不隐式转换或同步，需要后续显式 serializer。
`coverage_status` 只声明所提供的状态，不声明“模型所有 KV 已保存”。

## 三个策略 baseline 的可比性

全部从相同 consume 轨迹的空缓存开始，按稳定 replay_object_id 和内容 generation 缓存。
物理 allocation_id 作为观测地址单独保留；等内容迁移不应变成新缓存对象或额外 miss。
真实内容更新会失效；释放后地址再分配是新对象，容量变更要重新 admission。
不会查询未来访问，也不读取 target B。每次 demand 的依赖工作集在当前步保护不被逐出。
物理分配的预算类型以第一次 create/restore 的 state_kind 为准，后加的不同类型逻辑视图
不改变该分类；否则 share/release 会无故改变分区。逻辑类型仍逐 view 保留在快照中。

1. `shared_lru`：同设备不同类型共享容量，逐 allocation LRU。
2. `type_partitioned_lru`：显式固定各类型容量，各分区 LRU；分区总和不得超设备容量。
   未配置类型的预算为 0（bypass），不是默认为无限。
3. `dependency_bundle_lru`：将已观察到的同设备共同工作集连接为 eviction bundle。
   LRU victim 连带逐出已驻留的关联对象；当前需求对象仍受保护。连接只来自历史访问，
   并在 eviction 时删除。它是研究 baseline，不是某篇论文实现，也不保证优于 LRU。

超过预算的工作集作 transient bypass；不能伪造常驻成功。原位更新的旧 generation 不可命中。
模拟 eviction 不执行真正 I/O，真实 runtime 的 release 决策不强制施加给替代策略。
回放仍使用观测到的 device 放置；可选 consumer_device 仅保留证据，不实现跨设备放置优化。
因此它不是任意 offload/placement 策略的无偏反事实实验。
比较固定轨迹隐含“策略不改变需求序列”的假设，不能证明在线并发行为不变。
输出 hits/misses、miss_bytes、bypass_bytes、evictions 和驻留峰值：
**miss_bytes 是整个 allocation 的传输代理，不是实测带宽；所有 work 均不是 latency。**
纯 profile 拟合没有插入回放 critical path，禁止汇总成端到端时延或速度提升。

## 恢复与状态比较

成本记录按**全部** scope 字段和 operation/variant/parameters 精确匹配，包括
model/runtime/device/state_kind/size/dtype/layout/context_tokens/batch_size/measurement_protocol；
runtime 额外必须明确 condition/layer_id/compress_ratio/operation_unit/unit_count，layer/ratio
不适用时可显式 null，不能省略当作已知。扩展字段不会从 key 丢失；不同算法参数不能混成
同一个中位数。分别保存 runtime 与 synthetic 来源。只在 calibration/train
拟合中位数；evaluation sample 不得重复。没有长度外推、跨设备套用或测试样本调参。
operation_duration_ns 是调用方给出的同步测量协议下的单操作成本，不是请求延迟。

允许路径 direct_reuse/load/replay/index_rebuild/repair/full_recompute。每条候选必须有
VALIDATED、evidence_refs、匹配的 evidence_source，以及 required_capabilities。
runtime 候选还须提供 variant 和显式 uses_target_b=false；缺契约被阻塞。
这是一份由模型语义/实测出具的前置证据契约，工具不能自动证明外部证据正确。
文本相同、cosine 高、residual 小都不会自动授权 direct_reuse。
`uses_target_b=true` 不允许进入 planner；B 只进入独立状态评估阶段。

仅在证据有效且能力满足的候选中比较实测中位数；有候选缺成本则
PARTIAL_COST_COVERAGE，没有可比项则 BLOCKED。runtime 候选仍有未解决有效性证据时为
PARTIAL_CANDIDATE_COVERAGE。不会用成本完备掩盖候选证据缺失。
不宣称全局最优或 safe reuse，不执行计划。
依赖失效传播只做显式 DAG 可达性，无边不代表真实无依赖。

NPZ 比较要求显式 row_a/row_b/row_corrected、三者各自的 canonical positions
（positions_a/positions_b/positions_corrected）和 mapping evidence。三组坐标必须一致且唯一。
旧 synthetic 缺 corrected 坐标可保留数值以兼容，但标 UNVERIFIED，suite 不算通过。
不从 token edit 推导 C4 support，不插值、不补齐、不把 C4/C128 当 MLA cKV。
只比较同表示的浮点 `[T,F]`；整数 index/量化 raw bytes 只打包，不能套残差 SSE。
复用冻结的 StatePair 和 metrics；zero baseline 退化保留真实 SSE，explained 写 null，
状态 ZERO_BASELINE_DEGRADED。外部 supplied corrected state 不代表本工具实现了修复算法。

## 输出、故障与 AgentMon

`state_timeline.json` / `state_demands.jsonl` / `policy_replay.json` /
`policy_comparison.csv` / `recovery_plans.json` / `recovery_comparison.csv` /
`state_comparison.json` / `dependency_impact.json` / `agentmon_correlation.json` /
`workload_schedule.json` / `runtime_acceptance_requirements.json` / `run_config.json` /
`report.json` / `capsule.txt`。run_config 保留配置和输入基目录，不额外建立哈希子系统。
没有对应输入的阶段不制造空结果；报告 INPUT_MISSING 或 BLOCKED_DEPENDENCY。
没有 consume 不能验证策略；空/重复 policy 列表也不能通过。提供 requests manifest 时，
状态事件必须属于其中明确请求，新建状态还必须匹配明确 model/context。
坏 schema 为 FAIL（CLI exit 1），其它独立阶段仍产生可检查结果。
坏单个 policy/case 不丢失同阶段已完成的结果；单独 errors 文件或失败行保留审计。
报告和 capsule 显式列 evidence_sources；capsule 配置强制 512–45000 bytes。
PARTIAL 的 exit 0 只表示工具完成，不表示研究已验收；应看 report 内各阶段状态。

AgentMon 关联复用第一阶段显式 ID join：可关联乱序 engine/model ID，不按时间最近匹配。
service run 通过 event.run_id / sidecar.service_run_id 隔离，不跨服务重启复用 engine alias。
这是身份关联，不是资源归属证明；共享 allocation 可有多个消费者。
不加载 AgentMon 的 vLLM0.18 hook，不关闭其 version gate；DS runtime0.22.1 和 V4.1
仍需各自确认 hook。本工具没有创建在线 AgentMon adapter。

## 后续逐步验收，不必一次启动全部系统

1. 验收本包的 synthetic 与缺输入报告，检查格式/算法边界。
2. 真实 LifeMem 完整 prompt + token sidecar，先跑第一批准备器。
3. 单请求 DeepSeek-V4 状态目录/消费事件/快照，核对 shared/window/compressor/index/scale 等覆盖。
4. 两请求顺序与 pause/resume 事件，核对内存和 ownership；再给容量做同轨迹策略比较。
5. 离线校准样本测恢复成本、独立评估样本提供映射/状态/有效性证据。
6. 接入真实 V4.1 前先固定 runtime/source 并确认结构；目前没有假定 V4.1=V4。
7. 另行启动模型功能验收：logits/next-token/generation；最后才验 TTFT/TPOT/P99 与在线调控。

第 3–7 步的真实证据目前未获取；运行工具本身不会启动它们。
