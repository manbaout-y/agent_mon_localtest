# AgentMon × KVReuse 工作区整体审查

审查日期：2026-09-28。对象：`E:/agentproject/agent_mon-ver_hetero_state` 当前展开目录。

## 1. 审查范围与证据等级

本报告完成全目录盘点、Python AST 符号/导入索引、入口与关键调用链审查、交付清单核对，以及可在本机执行的针对性验证。不是对 430 个 Python 文件逐行完成的形式化验证，也不是 V4.1 服务器端到端验收。对主线相关代码进行了函数级核查；外围模块完成结构、入口、职责与边界核查。没有连接或修改 NPU 服务器，没有启动模型，没有安装依赖，没有修改业务代码。

盘点排除 `.git`、环境、缓存、outputs、dist 和本报告目录：581 个文件，430 个 Python 文件，全部 AST 解析成功。完整文件、行数、SHA256、函数及类的位置见 `inventory.json` 和 `source_index.md`。AST 成功仅说明可解析，不代表算法或运行正确。

证据分四类：

- 代码可见：当前文件中存在的实现与检查。
- 本机实测：本次 Windows / Python 3.10.9 / CPU 环境运行的测试。
- 用户服务器证据：用户已提供的 C=1 smoke、容器和 vLLM 源码搜索输出。
- 待验收：还需实际 V4.1 运行验证的能力。

## 2. 核心结论

1. 这是两个独立 Python 项目的联合研究工作区，不是一套已完成的 V4.1 在线状态管理服务。
2. AgentMon 已有真实任务执行、P0/P1 观测、批次运行、报告、请求关联和 vLLM 0.18 P2 实现。之前将服务端关联描述为完全从零开始是不准确的。
3. V4.1 当前接入是客户端 profile、真实 serializer 验证、受限 live smoke 和 request sidecar。没有找到可直接运行的 vLLM 0.27.1/V4.1 server binding、state capture 或在线 State Fabric adapter。
4. KVReuse 已有离线算法、真实历史状态读取器、状态生命周期/策略回放/恢复计划工具。它们不自动获得服务器状态，也不自动执行恢复。
5. 正确推进方式是复用既有数据契约、事件写入、关联与测试，在独立的目标版本适配层补真实证据生产者；不绕过旧 P2 版本门，不直接把 V4 patch 用于 V4.1。
6. 当前展开目录存在交付文件缺失和一处历史 patch 与归档不一致；framework synthetic CLI 还有可复现的错误状态漏报。应先将这些问题列为已知项，不能笼统沿用原环境的全绿验收结论。

## 3. 目录地图

| 目录 | 职责 | 是否是当前 V4.1 在线实现 |
|---|---|---|
| agentmon/src/agentmon | Agent 执行、观测、分析和控制 | 客户端可用；P2 仅指定版本 |
| agentmon/scripts | CLI/兼容入口、实验编排、验收 | 按脚本区分，不都适用于 V4.1 |
| agentmon/examples | TOML、模型 profile、registry、实验配置 | 有独立 V4.1 客户端配置 |
| agentmon/tests | 模块与集成契约测试 | 不替代真实 runtime 验收 |
| agentmon/offline_ana | LifeMem 文本重复与变化统计 | 离线字符层分析 |
| agentmon/artifacts/patches | 历史修复补丁 | 历史材料，不自动应用 |
| agentmon/agentmon_implementation_spec | 历史设计说明 | 不能单独据此断言已实现 |
| kv_ana/src/kvreuse_b5 | 算法评估、真实读取器、状态研究 | 主要为 CPU 离线工具 |
| kv_ana/runtime_intervention | 旧 V4 定点干预实验 | 非 V4.1 插件 |
| kv_ana/runtime_references | 固定版本 runtime 源码参考 | 证据参考，不是当前服务器源码 |
| kv_ana/manifests、references | 固定数据划分与历史数值 | 不应随意改动 |
| scripts | 联合验收、打包 | 编排已有实现 |
| integration | 联合边界说明 | 没有独立运行的集成服务 |
| outputs | 运行结果 | 不是源码能力证明 |
| dist | 历史源码归档 | 本次找到可恢复的缺失材料 |

## 4. AgentMon：从任务到报告

### 4.1 正式执行路径与 smoke 路径必须分开

正式路径大致为：`cli/runtime/batch -> RunController/ProcessBackend -> mini-SWE -> 模型与工具 adapter -> events/trajectory -> P2 import（可选） -> reporting/analysis`。

`run_controller.py` 负责创建 run 目录、启动子进程、建立环境、调用 host/proc/NPU/vLLM sampler、轨迹导入和最终报告。`runtime/` 提供 RunSpec、后端接口、持久队列、lease/heartbeat、恢复和任务状态。`batch/` 管理多任务执行、独立工作区、源码快照、批次清单、分析和有效性判定。

V4.1 客户端 smoke 绕过完整 RunController：直接构造 profile/model/sink/environment，调用两次 model.query，中间执行一次限定的 printf。它不是完整 mini-SWE benchmark，也不生成完整 task trajectory、P0 资源采样或标准整套 report。

### 4.2 P0、P1、P2

- P0：主机、进程、设备、服务聚合指标。不能自动把共享服务资源当成某个请求独占。
- P1：Agent step、model request、tool call spans，模型响应 ID、usage、工具进程与结果。
- P2：引擎请求生命周期、调度和 KV accounting 等。当前实现位于 `adapters/inference/vllm_018/`，仅支持 vLLM 0.18.0。

P2 的 token 计数/时间观测不等于完整输入 token 序列存档，KV block accounting 也不等于张量 capture。

### 4.3 兼容包装层

`adapters/mini_swe/*` 使用 import_module 映射到 `adapters/agent_frameworks/mini_swe/*`；`adapters/vllm_018/*` 映射到 `adapters/inference/vllm_018/*`。不要在旧命名空间和新命名空间各写一套实现。

多个 scripts 文件也是兼容门面，主要逻辑已迁入 batch、server、analysis、experiments。审查和修改应追到实际 package 模块。

### 4.4 已有分析链

`analysis/stages.py` 定义阶段 DAG：输入盘点、发现、schema、P2 index、标准化、P1/P2 审计、validation、吞吐/并发/生命周期/工作负载/尾延迟/资源分析、证据和交付输出。`step_sequence/` 处理步骤和模型/工具 spans、P2 投影、资源窗口。`reporting/` 输出 run、request、tool 等表及质量信息。

这些是已有且应复用的下游分析器，不需要为了 V4.1 重建一个平行报表体系。新适配器必须明确字段与质量等级；缺数据不能补成 0 或伪造精确值。

### 4.5 runtime 优化到底优化什么

`control/model_gate.py` 控制客户端模型请求准入；`tool_pool.py` 管理工具并发；`resource_actuator.py` 可对工具子进程设置 nice、内存限制和 CPU affinity；`scheduler/optimizer` 根据任务和压力给出调度/准入策略；`context_compression` 可改变后续请求上下文；`optimizer/harness` 对历史证据回放策略建议并做机制分析。

这些并不等于 vLLM 内部 KV placement、HBM 迁移或模型状态修复器。AgentMon 的 Runtime 是 Agent 运行控制；vLLM runtime 是模型执行；State Research 的 replay 是离线缓存模拟，三者不可混用。

## 5. 请求身份：现成实现及真正缺口

### 5.1 客户端调用链

`ProfiledLitellmModel.query` 把配置中的 extra_headers 提升到调用 kwargs，再进入 `InstrumentedModelMixin.query`。`inject_request_headers` 注入：

- X-AgentMon-Run-Id
- X-AgentMon-Agent-Id（有值时）
- X-AgentMon-Request-Id
- X-AgentMon-Model-Call-Id
- X-AgentMon-Client-Request-Id

当前 model call 和 client request 值通常相同。它们是关联标签，不是 server engine ID。

V4.1 model profile 指向 `hosted_vllm/deepseek-v41`，API 为 localhost:8800/v1，extra_body 提供 thinking=false；客户端正常输出预算为 4096，live smoke 下调为 256。TOML 禁用 context compression 和 runtime optimization，P2 events path 为空。配置中的 max_model_len/max_num_seqs 是描述，不能替服务器修改容量。

### 5.2 三种 attempt 不能按名称直接拼接

1. mini-SWE adapter 的 `_query` 发出 `model_request.attempt.start/end/error`，以 parent span + attempt_index 描述其调用层尝试。这不必然覆盖 SDK 内部每次 HTTP 重试。
2. V4.1 smoke 拦截 `httpx.Client.send`，为每次拦截调用生成 UUID，写入 request_evidence.request_id 和 agentmon.request_attempt_id。这一 UUID 并未由该函数写入 HTTP header。
3. `analysis/normalize.py:append_request_tables` 的 request_attempt_id 是由 run_id 和模型调用/别名字段构造的标准化分析键。它不是 smoke 的 UUID。

因此需要明确转换规则；不能把同名字段认为语义相同，也不能把 model-call 级记录当作每次 HTTP 尝试。

### 5.3 现有服务端关联并非空白

`adapters/inference/vllm_018/hook.py` 已有：

- `record_request_id_alias`：维护外部别名到 engine 身份。
- `record_request_context` / enrich：保存、传播 AgentMon 上下文。
- `wrap_async_add_request`：观察请求接收并建立别名关系。
- `agentmon_context_from_headers`：从 trace_headers 读取关联字段。
- `emit_received`：输出 `vllm.request.received`。
- scheduler、first token、engine finish、API finish 和 KV 分配观测。

`events.py` 的 sink 复用 JSONL、进程、host/boot、clock_domain 和 sequence 字段。服务 run_id 来自配置/环境，默认 unattached 不能自动充当可靠服务启动身份。

限制：`version.py` 严格要求 0.18.0；`sitecustomize.py` 也只引导这个 adapter。现有 wrapper 对返回对象、参数和方法路径有版本假设。用户提供的 0.27.1 源码搜索显示 external_req_id 与内部 request_id 存在转换，这需要匹配当前调用语义，不能仅修改支持版本字符串。

### 5.4 KVReuse 的两种关联接口

`state_research/agentmon.py:correlate_events`：根据显式 ID 关联已有请求清单与 AgentMon/P2 events；只允许明确的 received 事件学习别名；区分服务实例，保留 UNMATCHED/CONFLICT/AMBIGUOUS。

`state_research/request_bridge.py:bind_state_captures`：消费 requests + bindings + captures，以 `(service_run_id, engine_request_id)` 为服务执行键，检查 attempt、模型、context、来源和 runtime_identity。它不生成 binding，不获取服务器 token，不自动推导内存所有权。

全仓调用点核查：request_from_wire 在 wire validator 和 live smoke 中使用；bind_state_captures 在 validator 和单元测试中使用。未发现连接真实 V4.1 runtime 的生产绑定生成器或独立 CLI。

### 5.5 最小新增边界

建议在 AgentMon 推理适配命名空间内增加独立目标版本 adapter/插件，不修改旧 P2 的版本门。复用 Event、sink、客户端标签及下游关联约定。首先只实现 API/external/internal 请求桥与真实 server input IDs；后续再增加状态采集。

需要显式维护：service_run_id（服务重启变化）、HTTP attempt、API response、external request、internal/child request，以及 capture 的执行/rank 归属。一个外部请求可能派生多个内部请求，不能强制所有阶段一对一。成功响应可通过已记录的 API ID 回溯；失败、超时和重试路径若没有响应 ID，则需额外的前向关联机制。

这不是本报告宣称已经完成的实现；仍需用户当前容器函数上下文和真实运行验收。后续应一次收集完整的版本/API 调用证据，而不是让用户反复执行零散 grep。

## 6. KVReuse 算法主线

`adapters -> StatePairDataset -> ExperimentRunner -> methods -> metrics/observability -> results/summary/checkpoint`。

StatePair 统一 A=旧/复用态、B=目标态，形状 `[T,F]`，positions 按行对齐；保留 model/condition/state/pair/layer 身份以及 train/test 划分。StatePair 的转换辅助函数能调用 cpu()，但不代表存在有效的服务器采集与布局适配。张量的物理布局解释应由 adapter 完成。

主要算法类别：no_correction、shared residual oracle、pair/frozen low-rank oracle、probe mean/frozen basis、anchor residual、random/boundary/static/joint selective、RoPE、detector 和 composite。部分方法是 oracle 或读取保存的目标信息；不能因 deployable_like 标签或离线运行成功，声称已经有线上信号提供者。

`methods/placeholders.py` 明确存在 InterfaceOnlyMethod，缺能力会返回 CAPABILITY_MISSING；这是边界声明而非完整论文实现。

`providers/base.py` 定义 partial forward、query、attention、fresh probe、detector、anchor、embedding、position、cache runtime 的抽象接口。`providers/synthetic.py` 为合成实现；`pipeline/providers.py` 的 full-target providers 从保存的 B 提供离线数据，显式 oracle/uses_full_target。它们不是在 V4.1 上真正执行 partial forward。

`recipes/registry.py` 加载 20 个声明，注册 4 个论文家族的 7 个 executable variant。Executable 表示编排代码可调用，并不表示相应 runtime/provider 已具备，更不代表 20 篇完整复现。

指标：baseline SSE=||B-A||²，corrected SSE=||B-A_hat||²，explained=1-corrected/baseline；零基线有专门退化状态，聚合需按 SSE 汇总，不能随意平均 explained。work units、artifact bytes 和时间分开记录。

### 真实数据路径

`QwenRawKVAdapter` 读取既有 Qwen 保存文件；`DeepSeekPrefixC4Adapter` 读取旧 V4 packed 文件和 strict mapping，固定 layer/schema 约束。它们是 reader，不会调用在线服务采集。

`run_real_offline_pipeline.py` 为 Qwen3-8B、Qwen3.5-27B、旧 V4 数据构造 PRECHECK/REGRESSION/PAPER_AUDIT/PAPER_OFFLINE/B5/AGGREGATE/PROVIDER_PLAN/CAPSULE 阶段，包含固定样本形状、层和历史 SSE 锚点。它不是任意 V4.1 数据的通用入口；新 V4.1 adapter 不能靠换 model_family 字符串进入历史锚点流程。

## 7. State Research 主线

### 输入准备

LifeMem JSON/JSONL + 可选 research sidecar + 明确 tokenizer/offsets -> prepare/alignment -> requests/chunks/transitions/payloads/replay manifest/quality report。字符相同不是 token 相同，更不是状态相同。local tokenizer 来源不能冒充 runtime token。prepared replay manifest 不执行模型。

### 状态与生命周期

StateInstance 区分 state_id（逻辑对象）、allocation_id + device（物理分配）、context_version（请求上下文）、content_version（可变内容）。共享视图不重复算物理容量；跨设备副本分开计费。

StateEvent 包括 create/share/consume/update/move/release/restore 及 request begin/pause/resume/end。EventRecorder 默认关闭，只记录显式传入事件，不装 hook。

`analyze_lifecycle` 需要有序 sequence、非递减时间和单 clock_domain，并要求依赖关系可解释。因此 TP8 多进程原始事件不能简单 cat 后直接喂给分析器；接入层需要定义合并顺序、时钟语义和 allocation 身份。move 描述迁移完成的稳态；瞬时双份副本应通过明确 create/restore + release 表达。

### 快照

`write_snapshot` 接收调用方提供的 CPU NumPy arrays 和几何 binding，保存 state_views.npz/manifest.json。不会自动执行 NPU 同步、拷贝、bf16/量化转换。声明覆盖仅为 PROVIDED_STATES_ONLY。

此 manifest 不是 StatePair 数据集，也不是 state_comparison case 配置；需要另行定义 A/B/修正态对应关系和 row mapping。保存快照成功不能自动进入所有算法。

### 策略与恢复

policy_replay 在固定消费轨迹上比较 shared_lru/type_partitioned_lru/dependency_bundle_lru，保留观测 device，按稳定内容身份回放，缺容量或 consume 不通过。miss_bytes 是传输代理，不是实测 I/O；模拟淘汰不是服务端真正释放。

recovery 按精确 scope、operation、variant 和来源匹配校准成本，仅在外部有效性证据和能力满足时选择候选。结果明确 NOT_EXECUTED，不含模型专用修复器。

state_compare 读取显式 A/B/corrected NPZ 和三方位置映射，用浮点同表示数据评估误差；整数索引/量化 bytes 不能直接套 SSE。dependency_impact 仅基于声明 DAG 做可达性分析，不自动证明真实模型安全。

### 两条研究分支不是强制串行

元数据/消费事件 -> 生命周期 -> 策略回放，可以在没有 StatePair 时开展。

选定浮点状态和对照映射 -> StatePair/状态比较，可以独立于完整缓存策略进行。

恢复计划再需要有效性和成本。不是每个状态都必须转成 `[T,F]`，也不是每个研究阶段都要先完整 tensor dump。

## 8. 入口矩阵：什么时候用哪个脚本

| 入口 | 输入 | 执行模型/访问服务 | 正确用途 |
|---|---|---|---|
| scripts/validate_workspace.py --suite core | 环境和仓库 | 无模型；测试可能本机 loopback | 联合本地回归 |
| 同上 --suite wire | 固定客户端依赖 | 阻断真实 socket | HTTP 序列化验收 |
| deepseek_v41_client_smoke.py --live | 已运行 8800 服务 | 两次真实请求、一次受限工具 | V4.1 客户端闭环 |
| agentmon run / runtime / batch | 正式任务、配置、工作区 | 会运行任务/请求 | 正式 workload 与 P0/P1 |
| agentmon_p2_discovery.py | 目标 runtime 环境 | 主要静态发现，需看参数 | 核对已有 0.18 hook，不是自动移植器 |
| agentmon_server_preflight.py | 服务器配置 | 某些参数启用主动请求 | 版本/服务验证，执行前检查参数 |
| agentmon_existing_data_analysis.py | 已完成 batch | 离线 | 已有数据对比 |
| agentmon_optimizer_experiment.py | 已有证据 | 离线 harness | 策略建议和机制评估 |
| kvreuse_b5.cli.smoke | synthetic 配置 | 无模型 | 算法样例；PASS 有已发现缺陷 |
| kvreuse_b5.cli.run --synthetic | synthetic 配置 | 无模型 | 当前通用 CLI 仅此分支 |
| prepare_state_research.py | LifeMem/sidecar | 无模型，可本地 tokenizer | 请求、变化和对齐准备 |
| state_research_smoke.py | 内置样例 | 无模型 | 第一批数据契约示例 |
| state_research_offline_smoke.py | 内置样例 | 无模型 | 生成全套示例输入并做 10 项检查 |
| run_state_research_offline.py | 指定 config 和输入文件 | 无模型 | 生命周期、回放、恢复、状态比较 |
| real_adapter_smoke.py | 历史 PT/mapping | CPU 读取 | 验证一个真实历史状态 pair |
| real_observability_smoke.py | 历史状态 | CPU 离线 | 验证算法观测输出 |
| run_real_offline_smoke.py / run_real_offline_pipeline.py | 三条历史真实数据来源 | CPU 离线 | 固定历史回归及有限论文组件 |
| runtime_intervention/dsv4_precheck_v1 | 指定旧 runtime + artifact | 改旧 runtime/请求/需重启 | 仅旧 V4 的受限干预，不用于 V4.1 |

## 9. 历史实现应怎样复用

旧 P2：复用事件语义、sink、关联/质量逻辑和测试模式；重新验证目标版本 hook 方法和返回类型。

旧 V4 precheck：支持 C4、transformer layer 2、显式行、same-input identity/zero/external replay。patch_runtime 校验固定 SHA 和已有 DSV4_KV_DUMP 锚点；它依赖先前存在的 capture instrumentation，并不是给任意 runtime 自动安装完整采集。可借鉴开关、范围约束、回滚与验收设计，但不能直接安装到 V4.1。

runtime_references 固定的是旧 V4 源码证据。compressed_kv 是 scatter 前中间输出，不等于完整 live cache。state_inventory 中 V4.1 明确 REQUIRES_RUNTIME_SCHEMA。

## 10. 审查发现与影响

### F1：当前展开目录缺少 67 个交付文件（已确认）

对照 BUILD_MANIFEST 的 633 条文件记录，67 条缺失，全部位于 `kv_ana/literature_fetch_pack_v3/literature/paper_specs/`。20 个 recipe 声明仍在 src 中，因此 list 能列出 20 篇；但 source evidence 全部找不到，capability_audit 会返回 BLOCKED_SOURCE。它影响论文能力审计/相关 pipeline，不表示状态生命周期样例失败。

原始 dist 源码包有 646 个 member，包含上述 67 文件及当前缺少的根 .gitignore；SHA256 与旁边校验文件一致。归档中已存在且可比较的当前文件有 576 个字节一致，1 个历史 patch 不一致。没有自动解压覆盖，也没有杜撰缺失文献。

建议从这份已校验归档恢复缺失资产到原相对路径，再跑相关回归。服务器目录是否同样缺失尚未检查，不能从本机推断服务器状态。

### F2：framework smoke 会对 ERROR 记录报告 PASS（已复现）

`kvreuse_b5/cli/smoke.py` 统计 accepted，但未用它控制 status 和 exit code；ExperimentRunner 把方法异常记为 ERROR，invariants 会跳过非 OK 行。用独立进程故障注入 NoCorrection.correct 后，14 条结果全部 ERROR，但 CLI 返回 0，输出 PASS、accepted=0。

这会影响顶层 framework_smoke 的通过判定；不等同于 State Research 10-check smoke 或用户 live smoke 有同样问题。本次未修改代码。建议新增 ERROR 行失败门与回归测试，保持 CAPABILITY_MISSING 的明确允许语义。

### F3：当前重新打包有确定的缺失输入阻碍（代码与文件证据）

package_workspace 会要求 paper_specs 和根 .gitignore，collect 对不存在路径抛 FileNotFoundError。当前两者缺失，不能声称可直接按 README 成功重新打包。未实际创建新归档。

### F4：attempt 名称跨层不统一（已确认的接入风险）

P1 的调用尝试序号、wire sidecar UUID、分析表的复合键不同。对无重试的当前 smoke 容易看似一致，但在 SDK 重试、summary、并发时不能混同。需通过显式父子身份/来源转换解决，而非全局重命名或重写分析器。

### F5：事件、快照、StatePair 不是即插即用的同一文件格式（已确认）

现有 P2 JSONL 不是 StateEvent JSONL；request sidecar 不能替代 state_events；snapshot manifest 不能替代 comparison case；原始 NPU 布局不能直接当 StatePair。缺少 V4.1 生产者与转换器是主要接入工作。

### F6：早期文档和 CLI 提示落后于代码（已确认）

server_adapter_contract.md 仍说真实 adapters 未实现；当前已有 QwenRawKVAdapter 和 DeepSeekPrefixC4Adapter。cli/run.py 仍说 no real adapter is bundled，但实际含义是这个通用 CLI 没接真实数据分支。使用真实数据应走专用脚本。顶层状态文档、完整指南与实际实现优先于早期说明。

### F7：本地平台限制和历史材料漂移（已确认）

AgentMon procfs.py 在导入时调用 os.sysconf，本机 Windows 无此 API，5 个选定测试模块 collection 被阻断；不能写成 AgentMon 测试已通过，也不推断 Linux 同样失败。Python 默认编码造成的两项 KVReuse 失败在 UTF-8 模式消失。

历史 patch `agentmon-target-device-token-attribution-fix-20260703-v3.patch` 当前为 85838 字节，清单为 86031，与归档内容不同；未判断差异成因，不应自动覆盖或应用。

## 11. 本次验证

| 检查 | 结果 | 限制 |
|---|---|---|
| 全仓 Python AST | 430 文件可解析 | 不证明运行正确 |
| 交付清单 | 633 条中缺 67；1 项大小差异 | 当前展开目录 |
| 原归档校验 | SHA256 匹配；包含缺失文件 | 未恢复文件 |
| KVReuse 默认编码 pytest | 330 passed / 3 failed | 2 项编码差异 |
| KVReuse UTF-8 pytest 最终 | 332 passed / 1 failed | 剩余是 paper spec 缺失 |
| AgentMon 主线相关 5 模块 | collection 5 errors | Windows os.sysconf，未执行测试 |
| framework smoke 错误门故障注入 | 14 ERROR，仍 PASS、exit 0 | 已复现，未改源码 |
| V4.1 live / NPU | 本次未执行 | 用户此前报告已通过客户端 smoke |
| wire pinned-client validator | 本次未重跑 | 本机不是交接规定 Python/客户端环境 |

曾有一次 UTF-8 重跑因本次审查命令的 basetemp 父目录未创建而产生 84 个 fixture errors；这是审查命令问题，修正目录后最终结果为上表 332/1，不算项目缺陷。

## 12. 后续主线：只补必要缺口

当前已完成：用户服务器 C=1 client closure、离线 State Research 学习运行。无需反复要求用户重跑这些。

M1：真实 request/engine binding。确定当前 0.27.1 API response -> external -> internal/child 请求路径，复用 P2 event 契约和 KVReuse join。增加独立目标版本观测，服务实例身份明确，成功/错误/重试分别保留证据。最初不要求 tensor capture。

M2：真实 server input IDs 与执行上下文。明确 tokenizer/template、实际 preprocessed input、执行阶段和 rank 语义。输入 token 内容应作为研究 artifact 显式保存，不能把普通日志隐私默认策略悄悄改变。

M3：V4.1 state schema。把具体对象映射到 StateInstance：producer/consumer、allocation/view、dtype/layout、有效范围、content/context、依赖和采集点。无法表达的状态契约应明确扩展，不能编造 producer layer 等字段。

M4：一个短请求、一种状态、有限范围的事件或快照接入。实测同步/拷贝开销单独记录；事件分析和张量分析可以分别验收。TP8 只限制采集的 rank/层，不改变服务的 TP8 架构。

M5：真实数据进入已有 offline suite/StatePair evaluator，明确 row mapping 和表示一致性。需要对照 B 的指标只能用于评估，不能偷用于部署决策。

M6：恢复/复用干预与功能正确性，再做在线性能和并发。max_num_seqs=1 当前可保留；正式并发实验前单独固定参数与容量 envelope。256K 单请求能力不证明 10–16 个 256K 请求同时驻留。

建议代码归属：runtime 事件生产在 AgentMon 新推理 adapter；模型状态解释、V4.1 reader、StateEvent/StatePair 转换在 KVReuse；联合编排只做边界连接。具体新目录名称是后续实现设计，不是仓库已有文件。

## 13. 学习顺序与停止条件

1. 阅读 live smoke，理解两次 model.query 与一次 tool execute。
2. 阅读 P1 model/profiled_model，再读 RequestIdentity，区分 call/attempt/API。
3. 阅读旧 P2 wrap_async_add_request、alias/sink、版本门，理解可复用设计。
4. 阅读 request_bridge 与 correlate_events，看输入契约和阻塞测试。
5. 阅读 StateInstance、StateEvent、lifecycle，再复盘用户已生成的 timeline。
6. 分支 A 读 policy/recovery；分支 B 读 StatePair/adapters/metrics/methods。
7. 最后看 batch/analysis/harness，理解正式实验和结果质量门。

不必学完所有论文、全部分析器或完成所有 synthetic 练习后才推进 M1。也不应在不了解现有边界时先写独立 capture 工具。

## 14. 本报告交付文件

- `REPORT.md`：本报告。
- `source_index.md`：全目录源码符号与文档章节索引。
- `inventory.json`：文件盘点、哈希、符号与导入信息。
- `integrity.json`：交付清单缺失、大小差异和 paper spec 路径。
- `archive-inspection.json`：原归档 member 与 SHA256 核验。
- `encoding_archive_comparison.json`：当前文件与归档字节比较。
- 测试日志和复现摘要：见本目录对应文件；临时测试数据位于 outputs 审查目录。

本轮只新增审查资料；修复 smoke gate、恢复缺失资产、V4.1 adapter 开发均未混入审查。

