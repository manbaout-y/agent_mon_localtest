# 离线第二、第三阶段交付检查

2026-09-22；扩展版本 `state-research-offline-v1`。

## v1 历史验证结果

| 检查 | 结果 |
|---|---|
| 完整 pytest | 238 passed；本轮新增 55 项（此前 183 项） |
| 原 framework synthetic smoke | PASS；448 records / 224 summary groups |
| 第一批数据准备 smoke | PASS |
| 第二/三阶段离线集成 smoke | 10/10 检查 PASS；全部来源 synthetic |
| 从生成配置独立 rerun | PASS；策略、恢复计划、状态指标与 smoke 输出一致 |
| 缺真实输入配置 | PARTIAL；没有伪造通过 |
| 显式禁止导入 torch/transformers/vllm 的子进程测试 | PASS |
| compileall src/scripts/tests | PASS |
| frozen core + methods/recipes/providers/adapters/observability | 71 个文件前后内容完全相同 |
| AgentMon | 未修改、未安装在线 adapter |
| 真实 runtime / 模型 / NPU/GPU | 未执行 |

第一批输入/输出结构和 freeze framework 版本不变。
本次新增生命周期和 snapshot 契约独立版本化，不改已有 observability trace schema。

## 验证产物

`outputs/state_research_offline_v1_validation/`

```text
synthetic/
  inputs/                 # 可检查/复跑的完整 synthetic 配置、JSONL、NPZ
  analysis/               # 状态、策略、恢复、关联、capsule 等结果
  smoke_result.json
batch1/                   # 第一阶段兼容性 smoke
framework/                # 原 framework smoke
replay/                   # 不经过 smoke 入口，独立执行生成的配置
missing_inputs/           # PARTIAL 正确性检查
```

独立 rerun capsule 当前 942 bytes，上限检查 <=45000 UTF-8 bytes。
输出保留在本机，不要求将服务器完整数据复制回来；服务器可只回传 capsule。

## 专项覆盖

共享 alias 容量去重、跨设备副本、迁移全部 aliases、内容版本更新、生产依赖、暂停请求禁止
消费、release 依赖安全、事件去重/时间/时钟、预算分区、工作集超预算 bypass、历史依赖
bundle、策略前缀不受未来访问影响、旧版本禁止命中、校准/评估隔离、无成本不当免费、
synthetic 证据不能授权真实路径、缺 provider 不替代、B 不能授权 planner、不同 source row
索引、zero-baseline degradation、量化/浮点表示隔离、schema 缺失独立隔离、跨 condition
split leakage、ID 关联未完成不报通过、无模型依赖、CPU snapshot 不触发 device transfer。

## 分阶段完成状态

- **已完成本地实现**：研究数据准备 → 生命周期/工作集核算 → 固定需求策略 baseline →
  依赖失效分析 → 基于外部证据和成本的恢复计划 → supplied-state 数值评估 → 报告。
- **未完成真实研究验收**：全量真实 LifeMem、runtime 全类型状态覆盖、V4.1 实际结构、
  真实恢复成本、修复有效性、在线调控与功能/性能效果。
- **刻意不实现**：猜测模型状态布局、自动推导压缩 row mapping、把 probe/attention 造出来、
  把缓存命中率或 SSE 转换成 logits 安全性或时延收益。

下一步按 `STATE_RESEARCH_OFFLINE_PHASES_2_3.md` 的验收清单逐项替换真实输入，
无需修改冻结算法或一次性启动完整实验。便携包可用 `scripts/build_state_research_bundle.py`
重新生成，包内带代码、文档、配置、小测试及一份用于 source-evidence 测试的只读
`dsa_v1.py` 官方源码引用；不会 import、安装或修改该 runtime。没有私人上下文或原始 tensor。

## v1.0.1 整体审计修复

2026-09-22。这是在上表 v1 之上的后续审计，未覆盖旧输出。

| 检查 | 结果 |
|---|---|
| 修复后完整 pytest | **322 passed**；比审计前 238 项新增 84 项 |
| 第一批 smoke | PASS |
| 第二/三阶段离线 smoke | 10/10 PASS；明确使用 synthetic 数据 |
| framework smoke | PASS；448 records / 224 summary groups |
| 不同 Python hash seed 完整运行 | results、summary、sample costs、artifact manifest 完全相同 |
| 对照原 v1 framework 结果 | 数值、成本、method_config_hash 均相同；artifact_hash 规范排序后变化 |
| 从配置独立 rerun | 策略、恢复计划、状态指标与 synthetic smoke 相同 |
| 缺输入配置 | PARTIAL，不生成伪通过 |
| compileall src/scripts/tests | PASS |
| 冻结核心检查 | 71 文件中仅 execution/artifacts.py 一处 metadata 排序例外；其余 70 文件完全相同 |
| AgentMon / 模型 runtime / 服务器 | 未修改 / 未执行 / 未访问 |

核心例外的证据、代码范围及旧 checkpoint 处理见 `STATE_RESEARCH_AUDIT_V1_0_1.md`。
本轮研究回放 fixture 的命中数修正为 4 hits / 3 misses：是修复迁移伪 miss，
不是改变旧论文算法或历史 B1–B5 数值。

新验证输出：`outputs/state_research_audit_v1_0_1/`，包含 synthetic、batch1、framework、
framework_seed1、framework_seed2、replay、missing_inputs。独立 rerun capsule 为 1161 bytes。
便携包为 `dist/kvreuse_state_research_offline_v1.0.1.tar.gz`，旧 v1 包保留。

仍未完成实际研究验收：动态资源收益优化器、在线联合复用/继承执行器、模型专用修复器，
以及真实 runtime 状态采集、V4.1 结构适配、功能/性能实验。不能把本表解释为上述研究
方法已经完成，或把声明的有效性 evidence 当作工具自动证明的模型正确性。
