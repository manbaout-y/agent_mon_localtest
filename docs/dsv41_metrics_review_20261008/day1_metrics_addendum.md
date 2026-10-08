# Day 1 指标补充报告

## 原始证据及来源

输入：[用户任务文档原字节副本](evidence/user_task_original.md)，来源为用户提供的服务器 node0 终端输出整理文档，记录日期 2026-10-08 Asia/Singapore。**Prometheus 的实际采样时刻 UNKNOWN**；文档日期、smoke 目录时间和 `*_created` 均不能代替采样时刻。

原始指标已从文档完整提取为 [user_metrics.prom](evidence/user_metrics.prom)，保留原字段和数值，没有添加 TYPE/HELP、标签或时间。该文件是历史人工提供文本，不是本轮重新访问 endpoint 得到的实时 scrape。文档中的独立 INFO 日志保留在原字节副本中：`loggers.py:310`，`10-08 08:13:01`；时区及其与指标采样的关系未知。

离线结构化结果：[metrics_review.json](evidence/offline_review/metrics_review.json)。解析复用现有 `agentmon.analysis.prometheus.parse_prometheus_text`；没有增加另一个 Prometheus parser。

## 已验证事实（USER_PROVIDED_OUTPUT）

| 观测 | 原值 | 限定范围 |
|---|---|---|
| running / waiting | 0 / 0 | `model_name=deepseek-v41, engine=0` |
| KV usage | 0.0 | 同一服务级指标；物理 HBM 未采集 |
| Prefix queries / hits | 781 / 256 | 同一服务级计数器；单位、累积与 reset 实现待查 |
| External Prefix queries / hits | 0 / 0 | 本计数器观测范围，不排除未覆盖的其他路径 |
| preemptions | 0 | 本计数器观测范围 |
| cache_config_info | 1.0 | **只有 engine 标签，没有 model_name 标签** |
| block_size / sliding_window | 32 / 128 | 两个不同的配置标签 |
| num_gpu_blocks / kv_cache_size_tokens | 13448 / 1301333 | 标签定义和计算实现待核验 |
| enable_prefix_caching / dtype / utilization | True / bfloat16 / 0.9 | 配置报告值，非逐 Tensor 的验证 |
| KV offload backend / size | native / None | 没有迁移事件，不证明 offload |

正文原始输出见所链接 `.prom` 与原文档，均保留所有 `*_created` 数值。该类创建时间信息不表示 Block 创建或请求时间；这里不据此重建生命周期。

## 推导（DERIVED）及不能推出的结论

- `256/781 = 0.32778489116517284`，即约 32.7785%；四舍五入与独立 INFO 日志的 32.8% 一致。**尚未证明** Logger 和 Exporter 使用相同计数窗口、单位与 reset。
- `13448×32 = 430336`，不等于标签容量 1301333。这里只能确认算术不一致，不能宣布缓存容量错误，也不能据此猜测三个 KV Group。
- `1301333/262144 = 4.964191436767578`，与配置标签 4.9641934293097085 接近但并不完全相同；不能宣布找到了其源码公式。
- usage=0 不能推出 HBM=0、权重占用=0、预留 KV pool=0 或可复用缓存=0。
- Prefix 比值不能解释为某个请求节省了 32.8% 的计算量；服务级统计不能转成请求级 allocate/free。
- Engram PLE CPU Offload 与 KV Offload 是不同观测对象，当前 KV 迁移为 NOT_PROVEN。
- `kv_cache_max_concurrency` 不证明实际 `max_num_seqs`；当前调度并发上限 UNRESOLVED。

## 客户端执行证据

用户给出的普通 API smoke：HTTP 200，content=V41_SMOKE_OK，reasoning=null，finish_reason=stop，prompt/completion=18/8，HTTP 总时长 0.600292s。该时长不是 streaming TTFT。响应 ID `chatcmpl-ad5dd3c06ca95045` 尚未映射到 engine/scheduler。

用户给出的 AgentMon smoke：FIRST_ACTION_VALID、TOOL_EXECUTION、SECOND_ACTION_VALID、WIRE_THINKING_AND_REQUEST_IDS、CLIENT_SMOKE 均 PASS，exit=0；输出目录 `/home/yangbolun/agent_mon-ver_hetero_state/outputs/live-acceptance/v41-day1-20261008-161229`。这是 USER_PROVIDED_OUTPUT 的 live PASS。完整 `request_evidence.jsonl`、`client_smoke.json`、当前环境冻结/源码快照未随本次输入提供，逐项文件审计 NOT_CAPTURED。

指纹 `vllm-0.27.1-tp8-ep-63a3c9a2` 支持服务声称的版本/TP/EP 信息，不是实际包路径和 Git SHA 的完整证明。vLLM-Ascend 版本仍来自历史交接资料。原开发报告的 NOT_RUN 表示当时本地开发没有执行服务器实验，不应覆盖。
