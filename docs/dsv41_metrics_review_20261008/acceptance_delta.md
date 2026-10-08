# 补充验收状态差异

原 [DEVELOPMENT_ACCEPTANCE.md](../dsv41_day1_day2/DEVELOPMENT_ACCEPTANCE.md) 描述当时 Windows 本地开发，没有服务器访问；本报告描述新增用户服务器输出。两者来源不同，原 NOT_RUN 保留，不回写旧结果。

| 项目 | 原报告/缺口 | 新材料支持的状态 | 来源/边界 |
|---|---|---|---|
| API_MODELS_PASS | 本地 NOT_RUN | PASS | USER_PROVIDED_OUTPUT；deepseek-v41 / 262144 |
| MODEL_CONTENT_SMOKE_PASS | 本地 NOT_RUN | PASS | USER_PROVIDED_OUTPUT；thinking=false、200、stop、有 content |
| AGENTMON_CLIENT_SMOKE_PASS | 本地 NOT_RUN | PASS | USER_PROVIDED_OUTPUT；五项 PASS/exit0 |
| METRICS_ENDPOINT_PASS | 未有本次指标 | PASS | USER_PROVIDED_OUTPUT；原样指标文本 |
| PREFIX_COUNTERS_OBSERVED | 未有本次指标 | PASS | USER_PROVIDED_OUTPUT；781 / 256 |
| CLIENT_EVIDENCE_FILE_AUDIT | 尚待 sidecar | NOT_CAPTURED | 本次未收到完整 request_evidence/client report |
| CURRENT_ENV_SOURCE_FREEZE | 尚待 runtime source | PENDING | 当前 package/path/argv/source SHA 尚未提供 |
| BLOCK_SIZE_DISCREPANCY_RESOLVED | 历史 128 | UNRESOLVED | 当前 label32，原因未知 |
| METRICS_PRODUCTION_CHAIN | 候选源码 | UNRESOLVED | 仅 logger 字符串/输出行号；尚无目标源码函数体 |
| REQUEST_TO_ENGINE_BINDING | 未注入/未采集 | PENDING | 没有显式 API/IPC/Engine/Scheduler runtime 事件 |
| REQUEST_KEYED_KV_EVENTS | 未采集 | PENDING | 指标/旧数量事件不替代真实 Block 生命周期 |
| TENSOR_CAPTURE | 未采集 | PENDING | 没有 Tensor、rank/layer/physical mapping |
| KV_OFFLOAD_OBSERVED | 无证据 | NOT_PROVEN | native/None、Engram 不足以证明 |

本轮本地新增指标/源码工具回归 57 项 PASS；完整 workspace core 仍 FAIL，详情 TEST_RESULTS.md。新增材料不等于 DAY2_LIVE_BINDING_PASS，不意味着真实 KV Reuse 或异构状态调度收益已验收。

旧报告与旧 outputs 的哈希列表在 `evidence/prior_artifact_hashes.json`；本轮结束时逐文件验证原字节未改，验证日志在 `outputs/dsv41-metrics-review-20261008/prior-preservation.json`。
