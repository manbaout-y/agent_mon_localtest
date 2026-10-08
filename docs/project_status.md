# 项目状态

## 已实现

- KVReuse frozen2.0.1、real adapters、B1–B4 regression/B5 pipeline。
- 20-paper specs/recipe declarations、代表性provider orchestration，不是20篇完整复现。
- Observability1.0.1与forensic/cost accounting。
- 状态inventory、allocation/view、lifecycle、dependency/recovery、离线比较。
- AgentMon P0/P1、batch、分析报告与其明确版本支持的P2。
- V4.1 profile/registry、request sidecar、wire validator、受限C=1 live smoke脚本。

## 本地验收

上次记录：KVReuse333tests；AgentMon466passed/1skipped/17subtests；
framework448records/224groups；state10checks；wire15checks。
本次迁移重跑以acceptance.md和outputs日志为准，不直接套用旧数字。

## 用户历史服务器报告（本次未重跑）

- Q8 pair0 POSITION：36层K/V，114×1024。
- Q35 pair146 POSITION：16实际attention层，68×1024，K/V SSE=0。
- DS V4 PREFIX C4 frozen strict mapping smoke通过。
- V4.1普通请求显式thinking=false可返回content。

## 待真实验收/未实现

- V4.1全状态schema、producer/consumer、allocation、capture coverage。
- API attempt→engine绑定、server input IDs/state-row映射。
- V4.1 P2、在线partial forward/query/attention providers。
- 在线状态调度、functional logits/generation安全、真实性能收益。

旧V4 precheck不是V4.1插件；离线恢复方案NOT_EXECUTED不是线上修复成功。
