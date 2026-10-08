# DeepSeek V4.1：从当前服务采集证据，到开发新版 Hook 的完整操作指南

记录日期：2026-10-08，UTC+8。适用服务器：Linux / aarch64，8×Ascend 910B1，独立 Docker daemon，DeepSeek V4.1 W4A16 + Engram PLE Offload。

**现在先完成一次只读采集。** 你已跑通并固化客户端 smoke，这一轮不用重新跑模型。我们要拿到当前服务的源码、启动配置和指标，用来定位新版 Hook 应该放在哪里、如何绑定请求、如何记录运行状态。

本文所有操作命令都在服务器 Bash 终端执行。本轮不启动/停止/重启服务，不修改容器源码，不开启旧 P2 Hook，不运行新的模型 POST 请求。

## 1. 先弄清楚这轮在做什么

你已经证明：AgentMon 能调用模型，mini-SWE 能执行工具并记录客户端事件。

下一步需要把外部请求与内部执行联系起来：

```text
AgentMon model call / HTTP attempt
                  ↓
             API request
                  ↓ 需要显式身份关系
          EngineCore request
                  ↓
          Scheduler request
                  ↓
     KV group / Block / 引用关系
                  ↓ 后续阶段
       Worker / Rank / 物理状态
```

现有旧 Hook 面向 vLLM 0.18.0；当前目标是 0.27.1+empty 和指定 Ascend patch。**不能只改一个版本常量就启用旧 Hook。** 要先确认实际函数签名、对象字段、ID 改写、IPC 和缓存操作位置，再实现独立 `vllm_0271` 适配。

本次采集将回答四类问题：

1. 当前究竟运行哪个容器、什么参数、哪份源码？
2. 为什么历史 block_size=128，而当前 metrics 报告32？
3. Logger 展示的命中率和 KV usage，实际上由谁产生、用什么单位统计？
4. API → Engine → Scheduler → KV 的哪些位置可以低开销地观测真实身份和状态？

## 2. 准备代码：先同步，再执行

如果不能直接传文件，先按照 [Git 仓库同步指南](GIT_SYNC_SERVER_GUIDE_ZH.md) 建立自己的私有仓库，并在服务器克隆到新目录。使用新目录时，本文的 `cd ~/agent_mon-ver_hetero_state` 相应替换为 `cd ~/agentmon-dsv41-dev`；既有环境和成功 smoke 的绝对路径保持原值。

本地更新目录为 `E:\agentproject\agent_mon-ver_hetero_state`；服务器预计为 `~/agent_mon-ver_hetero_state`。沿用你已有的文件传输方式，将新增文件放到服务器对应相对路径。**本地文件修改不会自动出现在服务器。** 不覆盖服务器既有 outputs、环境、模型目录和启动脚本。

本轮主流程必须具备：

```text
agentmon/scripts/dsv41_metrics_collect_readonly.sh
agentmon/scripts/dsv41_kv_source_snapshot.py
agentmon/scripts/dsv41_metrics_review.py
agentmon/scripts/dsv41_request_discovery.py
agentmon/src/agentmon/analysis/kv_metrics_review.py
agentmon/src/agentmon/analysis/prometheus.py
```

最后两个 discovery/parser 中已有实现可以复用；用最新版确认它们存在。若还要跑本文末尾测试，同时同步测试文件和 `docs/dsv41_metrics_review_20261008/`，因为新测试使用其中保存的历史指标作为输入。

在服务器检查：

```bash
cd ~/agent_mon-ver_hetero_state
pwd
for FILE in \
  agentmon/scripts/dsv41_metrics_collect_readonly.sh \
  agentmon/scripts/dsv41_kv_source_snapshot.py \
  agentmon/scripts/dsv41_metrics_review.py \
  agentmon/scripts/dsv41_request_discovery.py \
  agentmon/src/agentmon/analysis/kv_metrics_review.py \
  agentmon/src/agentmon/analysis/prometheus.py
do
  if [ -f "$FILE" ]; then
    printf 'FOUND   %s\n' "$FILE"
  else
    printf 'MISSING %s\n' "$FILE"
  fi
done
bash -n agentmon/scripts/dsv41_metrics_collect_readonly.sh
```

期望所有文件 FOUND，`bash -n` 不报语法错误。若缺文件，先补同步；无需在服务器重新手写这些脚本。脚本通过 `bash 文件名` 运行，不需要 chmod。

## 3. 设置宿主机 Python 与 Docker socket

```bash
export PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
export V41_SOCKET=unix:///run/docker-v41.sock
export CONTAINER_PY=/usr/local/python3.12.13/bin/python3
umask 077

"$PY" --version
docker -H "$V41_SOCKET" ps -a \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

PY 用于宿主机离线解析；CONTAINER_PY 用于容器内读源码。两个 Python 环境不需要相同版本。

如果 PY 不存在，换成服务器上实际可用的 Python3.10+。本轮主采集/解析只使用标准库及已有项目 parser，不要求安装 torch、vLLM 或客户端推理依赖。

## 4. 确认当前容器，不要照抄历史容器名

你提供的历史环境写 `dsv41-smoke`，此前 live 输出写 `dsv41-smoke-256k`。以当前 `docker ps` 和实际8800服务归属为准。

以下示例假设当前目标是 `dsv41-smoke-256k`；如果列表显示实际目标是另一名字，只改这一行：

```bash
export V41_CONTAINER=dsv41-smoke-256k

docker -H "$V41_SOCKET" inspect "$V41_CONTAINER" \
  --format 'Status={{.State.Status}} StartedAt={{.State.StartedAt}} Image={{.Image}} Network={{.HostConfig.NetworkMode}}'
```

期望目标为 running。多个候选容器都存在时，核对其启动参数和服务归属，不自动选择最新容器。如果是 exited 或不存在，此轮先保存结果，不执行 start/restart，也不另起 TP8。

检查 API 是否可访问：

```bash
curl -fsS --max-time 15 http://127.0.0.1:8800/v1/models \
  | "$PY" -m json.tool
```

期望当前响应中包含 `deepseek-v41`。按实际输出记录 max_model_len；131072与262144可能来自不同 run。容器 bind `0.0.0.0:8800` 不自动证明宿主机127.0.0.1可达；连接失败要保留错误，不能默认 port publishing 已配置。

## 5. 确认源码位置与容器 Python

下面只读取路径和包 metadata，不导入 vLLM，不创建 engine，也不加载模型：

```bash
docker -H "$V41_SOCKET" exec -i \
  -e AGENTMON_VLLM_P2_ENABLE=0 \
  "$V41_CONTAINER" "$CONTAINER_PY" -I - <<'PY'
from pathlib import Path
import importlib.metadata as md
import sys

print("PYTHON:", sys.executable, sys.version.split()[0])
for name in ("vllm", "vllm-ascend"):
    try:
        print("METADATA:", name, md.version(name))
    except md.PackageNotFoundError:
        print("METADATA:", name, "UNKNOWN")

root = Path("/vllm-workspace/vllm/vllm")
print("VLLM_ROOT:", root, "EXISTS:", root.is_dir())
for relative in (
    "v1/metrics/loggers.py", "v1/metrics/stats.py",
    "v1/core/sched/scheduler.py", "v1/core/kv_cache_manager.py",
    "v1/core/block_pool.py", "config/cache.py",
):
    print("SOURCE:", relative, "EXISTS:", (root / relative).is_file())

candidates = [Path("/vllm-workspace/vllm-ascend/vllm_ascend")]
try:
    candidates.append(Path(md.distribution("vllm-ascend").locate_file("vllm_ascend")))
except md.PackageNotFoundError:
    pass
for path in dict.fromkeys(candidates):
    print("ASCEND_CANDIDATE:", path, "EXISTS:", path.is_dir())
PY
```

如果容器 Python 路径错误，先设置实际路径，再重做本步，不切换到 Qwen/verl 环境。metadata 版本仍不是实际服务进程已加载模块路径的完整证明。

若第一个 Ascend candidate 为 EXISTS=True：

```bash
export ASCEND_ROOT=/vllm-workspace/vllm-ascend/vllm_ascend
```

如果实际存在的是其他 candidate，将 ASCEND_ROOT 改成该实际目录。若都不存在，先保留这个缺口；不能把仅有 vLLM 源码的结果宣布为完整 Ascend 调用链。vLLM 根路径若与候选不同，当前包装器需要按证据调整路径后再用，不默认寻找另一个版本替代。

## 6. 正式运行：一条脚本收齐证据

完成前面检查后执行：

```bash
cd ~/agent_mon-ver_hetero_state
mkdir -p outputs/live-acceptance
RUN_LOG=$(mktemp "$PWD/outputs/live-acceptance/v41-collect-terminal-XXXXXX.log")

bash agentmon/scripts/dsv41_metrics_collect_readonly.sh 2>&1 | tee "$RUN_LOG"
COLLECT_RC=${PIPESTATUS[0]}
printf 'COLLECT_EXIT_CODE=%s\n' "$COLLECT_RC" | tee -a "$RUN_LOG"
printf 'TERMINAL_LOG=%s\n' "$RUN_LOG"
```

`PIPESTATUS[0]` 记录采集脚本的退出码，避免只看到 tee 成功而漏掉采集失败。

脚本会：

1. 创建新的私有输出目录，不覆盖历史实验。
2. GET当前 `/metrics` 和 `/v1/models`，记录 scrape 开始/结束 UTC。
3. 保存容器身份、启动命令和当前进程 argv。
4. 静态读取 Logger/Stats/Scheduler/KVCacheManager/BlockPool/config/request 等候选源码和引用。
5. 复用现有 parser 进行指标分析，生成源码和 Request inventory。
6. 为每步保存退出码和 stderr。

它不会做模型 POST、Hook 注入、服务启动/停止/重启、量化变更或 KV 策略调整。检查进程的旧 P2 开关被设为0，不修改在线服务环境。

## 7. 定位这一次的输出目录

成功走到最后时，终端会显示：

```text
Evidence saved: /.../outputs/live-acceptance/v41-metrics-review-日期时间-随机串
```

可直接从本次终端日志读取，避免混入其他历史 run：

```bash
OUT=$(sed -n 's/^Evidence saved: //p' "$RUN_LOG" | tail -n 1)
printf 'OUT=%s\n' "$OUT"
if [ -n "$OUT" ] && [ -d "$OUT" ]; then
  cat "$OUT/exit_codes.tsv"
else
  printf '未取得输出目录：先查看 %s，不继续下面命令。\n' "$RUN_LOG"
fi
```

目录为空时先排查 RUN_LOG，不对不确定路径执行后续操作。

## 8. 怎样看结果

### 8.1 先看每一步是否执行成功

```bash
cat "$OUT/exit_codes.tsv"
```

第一列是输出文件/步骤名，第二列是退出码。0表示该命令完成；非0表示有缺口或失败。采集器总 exit2 表示至少一步非0。

**采集命令全部0仍不等于 Request Binding 或 KV Capture 已通过。** 它只能证明这批材料采集/解析完成。

查看非空错误文件：

```bash
find "$OUT" -maxdepth 1 -type f -name '*.stderr' -size +0c -print
```

按显示的具体路径读取相应 stderr，不删除失败文件。

### 8.2 当前配置和指标

```bash
cat "$OUT/container_command.txt"
cat "$OUT/processes.txt"
"$PY" -m json.tool "$OUT/metrics_review/metrics_review.json"
```

重点核对：

- 当前 argv 的 --block-size、--max-num-seqs、--max-model-len 是否明确出现。
- 指标 cache_config_labels 的 block_size、num_gpu_blocks、kv_cache_size_tokens、sliding_window。
- `cache_config_scope=ENGINE_ONLY`：当前 config_info 没有 model_name 标签，不把它伪装成模型级配置。
- `block_size_investigation.resolution=UNRESOLVED` 是正常边界。脚本尚未读懂源码语义，不会自动替你解释差异。

历史256/781约等于32.78%，只是计数器比值；不是单请求节省的计算比例。usage=0不是HBM=0。native/None不是已发生KV Offload。

### 8.3 源码与身份路径

```bash
less "$OUT/source_inventory/source_inventory.md"
less "$OUT/request_inventory/REQUEST_CALL_CHAIN.md"
```

按 q 退出 less。没有 less 时用 `cat` 或 `sed -n '1,160p' 文件路径`。

inventory 包含实际读到的文件、类/方法、定义行号和相关词法调用。SOURCE_READ证明读到了该源码；MISSING/SOURCE_ERROR说明缺口；UNRESOLVED dispatch表示尚未确认对象的真实类型。不是所有 `self.manager.foo()` 都已证明是目标 KVCacheManager。

最重要的完整材料是 `kv_source_snapshot.json`，其中保存候选文件函数体；不要只交回 grep 行号或 inventory 摘录。我们要沿源码确认 Stats 单位/窗口、BlockPool 操作和 Request ID 传递。

## 9. 常见失败如何处理

| 现象 | 本轮处理 |
|---|---|
| 文件 MISSING | 同步相应源码文件；不要现场复制手写一个替代实现 |
| Docker socket权限/daemon不可达 | 保存错误，核对原daemon与访问权限；不自行启动新daemon |
| 容器 exited | 保留状态；当前任务不负责恢复或重启服务 |
| 8800 connection refused | 记录当前未就绪/不可达；48/48 weights完成不等于API监听，不能立即归因死锁 |
| CONTAINER_PY不存在 | 核对真实容器Python路径；只改检查变量 |
| ps不存在 | processes步骤保留非0；不要为了采集修改镜像或安装软件 |
| source snapshot exit2 | 查看哪些核心候选文件未读取；它不是请求绑定失败的证明 |
| Ascend源码路径不存在 | 核对实际源码/安装路径；报告缺口，不混用其他运行环境 |
| metrics review exit2 | 查对应stderr及JSON中的issues：可能指标缺失、重复或作用域不符；不填零冒充已观测 |
| rawmetrics为空、后续JSON解析失败 | 先解决该次HTTP采集错误；新建一次采集，不覆盖失败run |
| 容器argv128、指标32 | 保存冲突，追踪配置解析/平台赋值/分组布局；不在线改成其中一个 |
| 容器argv32、历史128 | 可能不是同一次启动；仍需源码证明字段口径一致，不能仅凭argv结束调查 |

容器Cmd可能只指向入口脚本，所以要与进程argv对照。源码文件若已修改但服务未重启，静态文件也不一定等于服务已加载内容，这一点仍需后续 runtime profile 核验。

## 10. 保留之前的客户端证据

本轮不重复 smoke。先查已经通过的目录：

```bash
LIVE=/home/yangbolun/agent_mon-ver_hetero_state/outputs/live-acceptance/v41-day1-20261008-161229
find "$LIVE" -type f \( -name request_evidence.jsonl -o -name client_smoke.json \)
```

将找到的真实 `request_evidence.jsonl`、`client_smoke.json` 一并交回；保留原路径和原文件。若该历史目录不在当前服务器，使用实际固化目录，不创建假同名证据。

可选：在没有服务端事件时运行已有绑定分析器，查看缺口。先把 CLIENT_EVIDENCE 设置为 find 打印出的真实文件：

```bash
CLIENT_EVIDENCE=/替换成真实路径/request_evidence.jsonl
"$PY" agentmon/scripts/analyze_request_binding.py \
  --client-evidence "$CLIENT_EVIDENCE" \
  --output "$OUT/client_binding_review"
BINDING_RC=$?
printf 'CLIENT_ONLY_BINDING_EXIT=%s\n' "$BINDING_RC"
```

这是离线读取；没有 server events 时通常为 BLOCKED/exit2，符合预期，并不推翻已经成功的客户端 smoke。不要把 --live-evidence 当成开启 Hook 的开关，它只是分析验收声明。

## 11. 交回哪些材料

保留整个本次 OUT 目录，尤其是：

```text
metrics.prom
metrics_started_at_utc.txt
metrics_finished_at_utc.txt
models.json
container_command.txt
processes.txt
kv_source_snapshot.json
request_source_snapshot.json
exit_codes.tsv
*.stderr
metrics_review/metrics_review.json
source_inventory/source_inventory.md
request_inventory/REQUEST_CALL_CHAIN.md
```

另附本次 RUN_LOG，以及之前通过的 client_smoke.json/request_evidence.jsonl。

可将 OUT 打包（只创建新压缩包，不移动或删除实验目录）：

```bash
ARCHIVE="${OUT}.tar.gz"
if [ -e "$ARCHIVE" ]; then
  printf '压缩包已存在，保留原文件：%s\n' "$ARCHIVE"
else
  tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
  printf 'ARCHIVE=%s\n' "$ARCHIVE"
fi
```

原始 argv/指标可能有内部路径或自定义敏感标签，材料按内部实验资料保存，交回前人工查看。不用上传模型权重、全量环境、原始 Tensor 或完整业务prompt。

## 12. 可选：服务器CPU测试

不是运行主采集的前置要求。若测试依赖已具备，可以核验新增工具和已有组件；不要自动安装依赖或下载环境。

```bash
cd ~/agent_mon-ver_hetero_state/agentmon
PYTHONPATH=src PYTHONUTF8=1 "$PY" -m pytest \
  tests/unit/test_dsv41_metrics_review.py tests/unit/test_dsv41_day1_day2.py \
  tests/unit/test_prometheus.py tests/unit/test_p2_discovery.py \
  tests/unit/test_events.py tests/unit/test_clock.py -q -p no:cacheprovider
```

已有Windows本地结果为57项通过。Bash采集脚本尚未在Linux验收，先运行第2节的bash语法检查。服务器测试的结果按实际输出记录，不预先宣布PASS。

## 13. 完成本次采集之后，再开发什么

拿到这些材料后，我们按以下顺序推进：

| 阶段 | 实际工作 | 验收依据 |
|---|---|---|
| 源码闭环 | 解释128/32；确认指标单位/reset与真实生产链 | 当前argv、配置、源码路径/行号和公式 |
| 请求Hook适配 | 独立vllm_0271 profile，API/Engine/Scheduler身份、终态、多进程覆盖 | 签名/版本/路径校验；离线wrapper异常透明与once-call测试 |
| 隔离C=1 live | 操作者批准后注入新版Hook，短请求验证 | 真实client/server事件显式绑定、零未说明drop、生成正确性 |
| KV生命周期 | 真实Block身份、group、attach/detach、引用、复用/淘汰 | 请求关联、生命周期完整性；释放不自动等于物理回收 |
| 物理状态与负载实验 | Worker/rank/state schema，再考虑并发及异构优化 | functional correctness及实际性能/资源指标 |

本轮到“采集证据并交回”即可。Hook注入、重启和并发参数调整会在源码审查及离线实现完成后另给具体方案，由你决定并执行。

## 14. 当前应做的最短清单

1. 同步新增脚本到服务器相同相对路径。
2. 执行第2至5节，确认文件、当前容器、Python和源码路径。
3. 执行第6节的一次性采集。
4. 执行第7至8节，看exit code与错误；保留完整输出。
5. 将第11节材料和既有客户端证据交回。

当前任务成功的含义是拿到足够的目标环境证据，能够开发和验证新版Hook；它不意味着KV Capture、KV Reuse或异构调度已经实现。
