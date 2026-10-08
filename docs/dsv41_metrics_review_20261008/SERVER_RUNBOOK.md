# 服务器只读实验命令

当前服务不修改、不重启、不注入。无需重复 client smoke、长上下文能力或 serving baseline。先同步本轮新增源码/文档，保留服务器原 outputs。

## 一次收齐现有指标与目标源码

新增环境清单写的是历史容器 `dsv41-smoke`，先前 live 材料使用 `dsv41-smoke-256k`。先只读列出容器，再把下方 V41_CONTAINER 设置为当前实验的实际运行容器；不启动另一个历史容器：

```bash
docker -H unix:///run/docker-v41.sock ps -a \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

```bash
cd ~/agent_mon-ver_hetero_state
export PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
export V41_SOCKET=unix:///run/docker-v41.sock
export V41_CONTAINER=dsv41-smoke-256k
# 沿用原 Day1 环境的容器 Python 路径；若实际路径不同，先人工核对再改变量。
export CONTAINER_PY=/usr/local/python3.12.13/bin/python3
export ASCEND_ROOT=/vllm-workspace/vllm-ascend/vllm_ascend
bash agentmon/scripts/dsv41_metrics_collect_readonly.sh
```

工具自动创建全新私有 `outputs/live-acceptance/v41-metrics-review-时间-随机串/`，输出目录在最后显示。只执行 GET /metrics、GET /models、inspect、读取 ps、静态 AST 和 git rev-parse；没有 POST、docker start/stop/restart、模型实例、vLLM import 或 Hook install。`-e AGENTMON_VLLM_P2_ENABLE=0` 只作用于新检查进程，不修改现有服务进程。

新增 scrape 保存开始/结束 UTC，不能与此前人工指标当作同一时刻。保留 stderr、各步骤 exit code；总 exit2 表示有证据步骤失败。source/metadata 不一致、MISSING、路径错误或 ps 不存在应原样保留，不自动找另一个环境替代、不自动启动容器。

容器 argv 可能含内部参数/路径，输出目录 umask077，提交前人工脱敏。采集不读取全量容器 Env。指标 raw snapshot 若服务自定义了敏感标签也应内部保存。

采集后（把 OUT 替换成工具实际输出目录，不用通配符混入历史运行）：

```bash
OUT=/完整路径/工具刚输出的新目录
cat "$OUT/exit_codes.tsv"
cat "$OUT/container_command.txt"
cat "$OUT/processes.txt"
cat "$OUT/metrics_review/metrics_review.json"
cat "$OUT/source_inventory/source_inventory.md"
cat "$OUT/request_inventory/REQUEST_CALL_CHAIN.md"
```

需要交回：`metrics.prom`、两个 UTC 时间文件、container_command/processes、kv_source_snapshot.json、request_source_snapshot.json、两份生成的 inventory、exit_codes.tsv 与失败 stderr。源码 snapshot 内含真实函数体/定义行号/词法引用；目前尚需它来完成指标生产链。metadata 不等于实际已加载路径，工具不会假报 runtime PASS。

## 保留已经成功的客户端证据，只做离线审计

```bash
LIVE=/home/yangbolun/agent_mon-ver_hetero_state/outputs/live-acceptance/v41-day1-20261008-161229
find "$LIVE" -type f \( -name request_evidence.jsonl -o -name client_smoke.json \)
"$PY" agentmon/scripts/analyze_request_binding.py --help
```

按 find 的真实文件路径使用已有分析器，不新造 run，也不重复请求。没有 server stream 时分析器预期 BLOCKED，而不是运行成功就 direct。后续真实 server events 到位后再按 --help 的参数联合审计；本轮不提供注入命令。

## 服务器 CPU 离线测试

```bash
cd ~/agent_mon-ver_hetero_state/agentmon
PYTHONPATH=src PYTHONUTF8=1 "$PY" -m pytest \
  tests/unit/test_dsv41_metrics_review.py tests/unit/test_dsv41_day1_day2.py \
  tests/unit/test_prometheus.py tests/unit/test_p2_discovery.py \
  tests/unit/test_events.py tests/unit/test_clock.py -q -p no:cacheprovider
cd ..
umask 077
CHECK_OUT=$(mktemp -d "$PWD/outputs/v41-core-review-XXXXXX")
"$PY" scripts/validate_workspace.py --suite core --output "$CHECK_OUT"
```

这些是 CPU/离线检查，可能因该 Python 环境缺少 pytest/numpy/yaml 等测试依赖失败；记录真实错误，不自动安装或下载，也不以 Windows 的已知限制推断 Linux 一定通过。
