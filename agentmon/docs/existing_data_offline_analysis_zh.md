# AgentMon 已有数据离线分析

`scripts/agentmon_existing_data_analysis.py` 对已经完成的一个或多个 batch 做只读分析。
它不重跑任务、不重启 vLLM、不修改 run 目录，也不安装新的 hook。适合用 C1 作为
基线，对 C5、C10、C20 等并发实验做请求、时间轴、任务路径和共享资源分析。

当前该脚本只是兼容命令入口；实际实现位于 `src/agentmon/analysis/existing_data.py`。
服务器和已有流程继续使用脚本路径即可。

## 1. 输入要求

每个 batch 目录优先读取：

```text
batch-manifest.json
batch-summary.json
batch-results.tsv
step-sequence/all-steps.csv
```

如果没有聚合 Step CSV，脚本会读取 `batch-results.tsv` 中每个 run 的
`step_sequence_csv`。它还会读取 `run_dir/events.jsonl`，用于独立审计 P2 候选、
lifecycle 和 host counter。输入缺失不会被填成 0，而会成为 `null`、coverage 缺口
或阻塞校验。

## 2. 运行命令

```bash
export AGENTMON_REPO=/absolute/path/to/agentmon
export AGENTMON_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/agentmon/bin/python

cd "$AGENTMON_REPO"

PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" scripts/agentmon_existing_data_analysis.py \
  --batch C1=/path/to/dist50-c1 \
  --batch C5=/path/to/dist50-c5 \
  --batch C10=/path/to/dist50-c10 \
  --baseline C1 \
  --min-p2-match-rate 0.95 \
  --min-p2-lifecycle-rate 0.95 \
  --strict \
  --output-dir /path/to/offline-analysis
```

`--batch` 可重复任意次数，标签不要求必须是 `C<number>`。`--strict` 在任一阻塞
校验失败时返回 1，但仍保留全部诊断产物；未加 `--strict` 时分析命令本身不会因为
数据质量失败而失败。

## 3. 先看哪些文件

```text
offline_root_cause_report.md       人工入口和各批次摘要
validation_checks.json            PUBLISHABLE / UNPUBLISHABLE 及硬校验
existing_data_gaps.md              缺失字段和不可离线恢复的能力
p2_correlation_audit.csv/json      每个 P1/P2 请求的双向关联审计
request_metrics.csv                请求级时延、token 和 cache 派生指标
paired_task_decomposition.csv      同 task 跨并发的 workload/system 分解
paired_model_calls.csv             同 task 内 model call 序列对齐
tail_classification.csv            慢任务分类及证据
timeline_validation.json           时间轴与并发硬校验
timeline_violations.csv            具体冲突区间
device_imbalance.csv               每个 step 窗口的卡间不均衡
device_metrics_long.csv            每卡长表；device ID 不等于 TP rank
host_metrics.csv                   CPU、load、queue、fault、swap、PSI 派生值
```

所有 JSON 都有 `schema_version`。输出目录和文件会尽量设置为 `0700/0600`。

## 4. 请求指标

主要公式：

```text
uncached_prompt_tokens = prompt_tokens - cached_prompt_tokens
effective_decode_tokens = generation_tokens - 1
tpot_s = decode_s / effective_decode_tokens
decode_tokens_per_second = effective_decode_tokens / decode_s
ttft_per_1k_prompt_tokens_s = ttft_s * 1000 / prompt_tokens
prefill_proxy_tokens_per_second = computed_or_uncached_tokens / prefill_to_first_token_s
```

生成 token 小于等于 1 时，TPOT 没有有效分母，因此输出 `null`。真正的 prefill
完成事件不存在时，只输出明确标记为 `proxy_not_exact_prefill` 的 proxy，不把它写成
精确 prefill。原始事件回算时延前会校验 clock domain 和 host boot ID；无法证明
同域时保持 `null`。

P2 审计不是只相信 Step CSV。脚本会重新扫描 run-local P2 事件：

```text
exact_match          一个 P1 model 只对应一个 engine request
multiple_candidates  一个标识命中多个 engine request，不擅自选择
ordinal_fallback     只有缺少 response ID 时才接受的顺序降级
unmatched_p1         P1 model 没有 P2 候选
unmatched_p2         P2 request 没有 Step CSV model
```

匹配率和 lifecycle 完整率按 batch 分别执行门槛，不能由其他并发组的高覆盖率抵消。

## 5. 时间轴校验

`request_timeline.csv` 使用绝对 monotonic ns，不使用容易混淆的 step 相对 offset。
硬校验包括：

```text
result 数与 manifest task 数一致
run ID 唯一
started/completed/valid interval 数一致
t50 <= t90 <= t95 <= t100
active model <= active run
观测 active run 不超过配置并发
同一 run 不出现重叠 model interval
model/tool interval 完整包含在 run interval
Model/Tool sweep 积分误差 < 1%
clock domain 和 host boot ID 完整且一致
```

任一阻塞项失败都会标记 `UNPUBLISHABLE`。诊断文件仍可使用，但不应从中发布确定性
瓶颈结论。

## 6. Workload 与 System 分解

脚本按 `task_id` 配对 baseline 和 candidate。对 queue、prompt proxy、decode、model
residual、tool 使用同一个可加和公式：

```text
workload contribution = (candidate units - baseline units) * baseline unit cost
system contribution   = candidate units * (candidate unit cost - baseline unit cost)
```

前者表示任务路径、token 或调用次数变多；后者表示相同单位工作在更高并发下变慢。
`unexplained_residual_s` 用于检查分解守恒。缺任一必要字段时，整项标记
`partial_missing_metrics`，不会把缺失项按 0 参与计算。

`paired_model_calls.csv` 对相同 task 的 model 序列做对齐。路径长度一致时可使用 ordinal；
路径变化时根据 prompt/generation token 相似度做序列对齐，并显式输出 quality，不能解释
为 request ID 的精确跨实验身份。

## 7. Tail、NPU 和 Host

Tail 同时参考 baseline p95 和同 task 相对 baseline 的 1.5 倍，可能分类为 queue、
prefill proxy、decode、长上下文、长输出、Agent/Tool 路径扩张、prompt recompute、KV
eviction、tool timeout/retry、execution failure 或 pipeline overhead。`unknown` 表示现有
证据不足，不代表没有问题。

卡间 HBM/AI Core 只表示同一时间窗内的共享服务观测：

```text
observation_scope = shared_service_observation
request_attribution = not_request_attributed
rank_mapping_quality = unavailable
```

脚本不会把 device ID 猜成 TP rank，也不会把共享服务资源归为某个请求独占。Host
指标同样是共享观测，包含 per-core CPU、load/CPU、run queue、context switch、page
fault、swap、paging、memory 和 PSI。counter 回退时该 interval 输出 null 并记录
`counter_reset_fields`；未采集 `/proc/diskstats` 时磁盘吞吐保持 null。

## 8. 无法从旧数据补出的内容

以下数据只有原始运行时事件存在时才能精确分析：

```text
真实 prefill-completed 边界
NPU collective/通信时间
device 到 TP rank 的可靠映射
请求独占的 NPU/HBM 利用率
未写入 run-local P2 流的 service extra request
未被 P1 原生采集的 tool 进程 CPU/PSS/I/O
```

这些字段不会通过 ordinal、字符串裁剪或 0 值伪造。

## 9. 本地验证

```bash
PYTHONPATH=src "$AGENTMON_PY" -m unittest -v \
  tests.unit.test_existing_data_analysis

PYTHONPATH=src "$AGENTMON_PY" -m unittest discover -s tests -v
```
