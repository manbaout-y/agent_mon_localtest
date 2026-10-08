# 本轮变更清单

全部代码为新增文件，未修改旧 P2、生产推理逻辑、KVReuse 冻结算法或在线服务。

| 文件（工作区相对路径） | 功能 |
|---|---|
| `agentmon/src/agentmon/analysis/kv_metrics_review.py` | 复用原 Prometheus parser，按模型/engine 筛选服务指标；config 无模型标签保留 ENGINE_ONLY；算术推导与 UNRESOLVED 边界 |
| `agentmon/scripts/dsv41_metrics_review.py` | 无网络离线 CLI；保存输入原字节/来源/UNKNOWN 历史采样时刻；拒绝覆盖目录 |
| `agentmon/scripts/dsv41_kv_source_snapshot.py` | stdlib 只读 AST，候选 Logger/Stats/Scheduler/KV/Pool/config 源码、定义/词法调用/引用、metadata/Git；生成 inventory；不导入 vLLM |
| `agentmon/scripts/dsv41_metrics_collect_readonly.sh` | 人工在服务器执行的一次性 GET/config/ps/源码采集，私有新目录、UTC 区间、stderr/exit code；没有重启或注入 |
| `agentmon/tests/unit/test_dsv41_metrics_review.py` | 13 项新离线测试，包括真实输入文本、缺失/重复/非有限/作用域、算术与 layout 区分、覆盖保护、AST 不执行及旧版本门槛 |

补充报告在本目录 README、day1_metrics_addendum、block_size_investigation、metrics_to_kv_call_chain、request_binding_gap、acceptance_delta、SERVER_RUNBOOK、TEST_RESULTS 和本清单。没有覆盖原 Day1/Day2 文档。

证据新增：原用户 MD 字节副本、原 Prometheus 提取文本、旧文档/测试输出 SHA256 清单、离线分析 JSON。新增测试与 workspace 结果另存 `outputs/dsv41-metrics-review-20261008/`。

本工作区不是 Git checkout，不能提交真实 commit 或输出 Git diff；本清单是新增文件清单，不伪造提交号。后续新 0.27.1 Hook 适配与 Block 生命周期代码是开发方案，尚未实现或部署。
