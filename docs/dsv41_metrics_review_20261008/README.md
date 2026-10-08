# Day 1 KV 指标与 Day 2 源码追踪补充审查

日期：2026-10-08。**客户端 live 已有用户提供的 PASS；服务级指标已获得；请求级绑定、Block 生命周期和 128/32 差异仍待补证。** 本轮没有访问服务器、修改在线配置、执行模型请求、注入 Hook 或重启服务。

本目录追加材料，不修改 `../dsv41_day1_day2/` 的原报告及原测试日志。

初次操作请从工作区根目录 [完整服务器操作指南](../../DSV41_HOOK_START_HERE_ZH.md) 开始。它按步骤给出同步检查、只读采集、结果解释、失败处理与证据交回命令。

| 材料 | 用途 |
|---|---|
| [DSV41_ENVIRONMENT_BASELINE.md](DSV41_ENVIRONMENT_BASELINE.md) | 用户补充环境基线、运行身份边界及目标 Hook 的负载/状态观测范围 |
| [day1_metrics_addendum.md](day1_metrics_addendum.md) | 原始指标、来源、作用域与分析边界 |
| [block_size_investigation.md](block_size_investigation.md) | 128/32、容量公式及逐项缺口 |
| [metrics_to_kv_call_chain.md](metrics_to_kv_call_chain.md) | 已确认源码与目标源码待审路径 |
| [request_binding_gap.md](request_binding_gap.md) | 0.18 兼容差异、绑定和 KV 事件开发方案 |
| [acceptance_delta.md](acceptance_delta.md) | 原验收与补充证据的状态差异 |
| [TEST_RESULTS.md](TEST_RESULTS.md) | 本轮实际测试、失败与限制 |
| [CHANGE_LIST.md](CHANGE_LIST.md) | 新增代码和材料清单 |
| [SERVER_RUNBOOK.md](SERVER_RUNBOOK.md) | 当前服务的只读人工核查命令 |

证据等级：VERIFIED 表示可核查的观测/源码事实，且同时标注 LOCAL_SOURCE、LOCAL_TEST 或 USER_PROVIDED_OUTPUT；DERIVED 是算术/设计推导；UNRESOLVED 是解释尚缺证据；NOT_CAPTURED 是没有对应运行时材料。用户提供的 live 输出不是本轮 Codex 执行的 live 实验。

**下一步只做一件事：同步新增脚本后，在服务器运行本目录手册中的只读采集命令，交回整个新输出目录。** 它将当前启动实参、指标、Logger/Stats/调度/缓存源码一起保存，避免继续逐条手工 grep。拿到实际源码后才能完成带行号的 0.27.1 指标生产链和适配设计。
