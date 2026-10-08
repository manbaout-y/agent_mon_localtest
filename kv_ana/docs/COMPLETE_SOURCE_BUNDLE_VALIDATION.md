# 完整联合源码包验收记录 — 2026-09-23

## 本次改动

只新增完整说明、完整打包脚本和本验收记录。未修改 AgentMon 或 KVReuse
算法、adapter、provider、instrumentation、配置、数值口径。两个最新工作树的
源码与未提交的新文件均按正向清单打包；不是旧发布包的重新命名。

## 解压目录验收

候选包解压到项目目录之外的临时目录，再从解压后的项目执行：

| 验收 | 结果 |
|---|---|
| kv_ana 全量 pytest，Python 3.11 | 333 passed |
| AgentMon 全量 pytest，Python 3.11 | 466 passed, 1 skipped, 17 subtests passed |
| frozen framework synthetic smoke | PASS；448 records，224 groups，version 2.0.1 |
| state_research offline smoke | 10/10 checks PASS |
| 使用生成 inputs/config.json 再运行 offline suite | OFFLINE_CHECKS_PASS |
| 真实 mini-SWE/LiteLLM/OpenAI serializer 验证 | 15/15 checks PASS |
| recipe registry 加载 | 20/20 paper entries，保留 blocked/component 分类 |
| 两项目 source/scripts + V4 intervention compileall | PASS |
| AgentMon 主 CLI/batch/analysis 帮助入口 | PASS |

kv_ana 原工作树的全量测试也通过。最终包相对候选验收包只增加本记录；
最终归档再检查与候选包的已有源码文件逐字节一致及文件清单完整性。

Client 环境：mini-swe-agent 2.4.3、LiteLLM 1.90.1、OpenAI 2.44.0。
HTTP transport 被拦截，socket 被阻断；synthetic 503 用于测试真实 retry 路径。
没有访问模型服务。AgentMon 既有 integration tests 使用本地 loopback fixture，
不连接服务器。1 个既有 skipped test 不算服务器验收通过。

## 归档边界

保留全部当前一方源码、测试、配置、文档、paper-spec/evidence metadata、
历史 patch 和 V4 pinned 三文件 reference。排除 PDF、下载的第三方完整 repo、
实验输出、旧 bundle、环境、模型、原始 tensor、cache 和 symlink。

paper-spec metadata 可以包含原始绝对来源路径；这些不是 tar member 路径，
且不代表目标机器拥有该材料。需要重新审查论文/第三方实现时须另备原材料。
历史 patch 仅供审计，不自动应用；V4 reference/intervention 不适用于 V4.1。

这次没有运行真实数据、模型、GPU/NPU，也没有进行在线状态 capture 或服务器部署。
