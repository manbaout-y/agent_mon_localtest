# DSV41 C=1：服务器上先演练，再采集一次请求

日期：2026-10-09。入口：`agentmon/scripts/dsv41_c1_observe.py`。

本手册依据用户预检、两份V2材料和项目内V3计划编写。当前对话没有服务器连接；本地已完成开发及离线测试，**真实请求与真实资源验收仍待服务器执行**。以下命令均在NPU服务器的主机终端运行，不在容器里运行。

## 1. 这一轮要看懂什么

```text
读取当前容器、版本、Host PID、模型挂载设备
             ↓
默认dry-run：只读采样10秒，零次推理请求
             ↓
人工确认约60–90秒独占窗口
             ↓
再采样约10秒 → 一次流式POST → 再采样约10秒
             ↓
停止本脚本采样，保留原始记录，生成三份报告
```

这是一次独立HTTP资源观测，不会再次启动mini-SWE，也不会执行工具任务。复用AgentMon的Host读取和Prometheus解析；保持kv_ana、AgentMon生产core独立。kv_ana的离线状态实验不能替代这次真实资源采样。

脚本不重启、停止、修改服务，不注入Hook，不启用旧P2，不清缓存，不下载模型，不重建镜像，不自动重试POST。会在容器内另起一个隔离Python进程读取包metadata、磁盘Git HEAD和白名单启动参数；这不证明已有EngineCore实际加载的模块来源。

## 2. 更新代码与选择已有Python

先进入你**已有的、origin指向自己GitHub仓库的服务器checkout**。如果目录仍是历史压缩包解压结果，请先确认其Git身份；不要在有未提交修改的目录上强行覆盖。

```bash
cd ~/agent_mon-ver_hetero_state
git remote -v
git status --short
git pull --ff-only

PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
"$PY" --version
"$PY" agentmon/scripts/dsv41_c1_observe.py --help
```

如果项目已经放在其他目录，仅替换`cd`。预期origin为`https://github.com/manbaout-y/agent_mon_localtest.git`。`git pull`失败、存在冲突或入口不存在时，先解决代码同步，不执行正式请求。无需安装新包或换容器Python。

**第一次通过Git部署、还没有服务器checkout时**，可以新建另一个目录，保留旧项目：

```bash
cd ~
git clone https://github.com/manbaout-y/agent_mon_localtest.git agentmon-dsv41-dev
cd ~/agentmon-dsv41-dev
PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
"$PY" agentmon/scripts/dsv41_c1_observe.py --help
```

已有checkout就不执行这一段。不要将GitHub令牌写进命令或发给聊天。

## 3. 先执行零请求演练

以下命令一次执行完。脚本自行新建最终run目录，**不要提前mkdir这个run目录**。

```bash
RUN_TAG=$(date +%Y%m%d-%H%M%S)-$$
DRY="$PWD/outputs/live-acceptance/v41-c1-dry-$RUN_TAG"
env AGENTMON_VLLM_P2_ENABLE=0 "$PY" agentmon/scripts/dsv41_c1_observe.py --run-dir "$DRY"
DRY_RC=$?
printf 'DRY_RC=%s\nDRY=%s\n' "$DRY_RC" "$DRY"
cat "$DRY/capsule.txt"
cat "$DRY/preflight.json"
```

继续正式采集需要同时满足：

- `DRY_RC=0`、`preflight.json`为`DRY_RUN_VERIFIED`、`POST_ATTEMPTS=0`。
- NPU解析到全部8卡；Host、Worker RSS/身份、目标磁盘、必要vLLM指标均有有效采样。
- 演练期间running/waiting读数均为0。
- 你已与其他服务使用者确认约60–90秒内不向8800提交其他请求；暂停自己其他客户端任务即可，不停服务。
- 演练后容器、启动实例、进程列表、代码commit和模型挂载映射没有变化。

**零请求演练的`OBSERVATION=DRY_RUN_OR_BLOCKED`是正常的**：它没有推理，不能有请求前/中/后的验收结论；以`PREFLIGHT=DRY_RUN_VERIFIED`判断工具是否准备好。

已知Worker `/proc/PID/io`可能Permission denied。只要RSS/进程身份和磁盘等必需采样可用，演练允许通过；进程I/O继续标NOT_OBSERVED，不提权、不伪造零值。PSS可能也被拒绝，保留原因。`max_num_seqs=1`不等于没有其他用户排队。

如果演练BLOCKED，请回传`preflight.json`及失败命令指向的`raw/command_*.txt`，**此时不发POST**。脚本会检查镜像digest、版本/commit、模型名、TP8/EP、max_num_seqs、262144上下文、端口及模型挂载设备，任何不符都先停止。不要为了通过检查更改在线服务。

## 4. 条件满足后，正式模式只执行一次

保留同一终端里的`PY`和`DRY`变量。下面的确认参数表示你确实已经获得独占窗口；它不是采集器能自动证明的事实。

```bash
LIVE="$PWD/outputs/live-acceptance/v41-c1-live-$(date +%Y%m%d-%H%M%S)-$$"
env AGENTMON_VLLM_P2_ENABLE=0 "$PY" agentmon/scripts/dsv41_c1_observe.py \
  --mode single-request \
  --validated-dry-run "$DRY" \
  --confirm-exclusive-window \
  --run-dir "$LIVE"
LIVE_RC=$?
printf 'LIVE_RC=%s\nLIVE=%s\n' "$LIVE_RC" "$LIVE"
cat "$LIVE/capsule.txt"
cat "$LIVE/01_BEGINNER_SUMMARY.md"
```

**这一段不要执行第二次。** 失败、超时、断流或报告为PARTIAL，也不要新建目录再试。

固定请求为本机`127.0.0.1:8800/v1/chat/completions`、`deepseek-v41`、`thinking=false`、流式输出、`max_tokens=128`。提示让模型逐行列出1到20和“观测测试”。不保证实际生成128 Tokens。

客户端总超时默认45秒；正式采样窗口名义上不超过10+45+10=65秒，另有预检/收尾时间。NPU或其他命令慢时实际采样频率会下降，真实起止与duration写入记录。不承诺固定墙钟总时长。

发POST之前先保存`request_attempt_started.json`。HTTP失败也消耗本轮唯一尝试；脚本无重定向、无重试。客户端超时不能证明服务器已停止计算；报告标server_terminal未知，不自动发abort。

## 5. 看懂结果，避免把PARTIAL当失败重跑

```bash
cat "$LIVE/preflight.json"
cat "$LIVE/response_summary.json"
cat "$LIVE/metric_deltas.json"
cat "$LIVE/02_TECHNICAL_REPORT.md"
```

如果没有`response_summary.json`，先检查是否有`request_attempt_started.json`和`single_request_report.json`，不要凭文件缺失认定请求未发送。任何已消耗尝试都先审查证据，不自动补请求。

| 结果 | 意义 |
|---|---|
| request.status=VERIFIED，POST_ATTEMPTS=1 | 一次流式请求完成，有正文、API ID和结束证据 |
| observation=PARTIAL | 请求可成功，但缺少某些采样/权限或存在旁路风险；例如Worker I/O被拒绝 |
| RESOURCE_TIMELINE_VERIFIED | 定义的前/中/后来源和进程I/O覆盖完整，仍只是资源窗口证据 |
| SINGLE_REQUEST_FAILED | 未取得完整请求完成证据，不能补发 |
| null / NOT_OBSERVED | 没观测到或无权读，不是0 |
| UNRESOLVED | 如128/32语义、运行模块来源、计数重置等还不能闭环 |

正式模式退出码0只表示请求完成、容器epoch稳定、采样线程已收尾；**不等于所有指标齐全或证明offload**。退出码2也不等于请求没发送，以POST尝试证据为准。目录已存在时拒绝覆盖。

默认名义采样间隔：Host、Worker RSS/I/O、磁盘、metrics为1秒，NPU为2秒。PSS仅低频边界采集。严格during样本的采集起止必须全部落在HTTP窗口中，跨边界样本单列overlap；没有during就写未知，不拿前后值补峰值。

vLLM统计刷新周期和外部1秒采样不是一回事。差分采用最后一条pre和最后一条post读数，包含post观察期等待统计刷新；具体窗口时刻在raw记录。短请求期间gauge一直为0也可以是真实结果，不能推断模型没使用KV。Prefix counter增量是Engine窗口，`hits/queries`只在分母有效时计算，不能当某请求的物理KV复用量。

## 6. 输出目录与三个必交文件

```text
<LIVE>/
  01_BEGINNER_SUMMARY.md       中文概念、请求成败、HBM/DDR/SSD/KV和缺口
  02_TECHNICAL_REPORT.md      身份、时间线、公式、差分、权限、Hook建议
  03_RAW_OBSERVATIONS.txt     重要原始数值、采样时刻、命令及raw索引
  response_summary.json      请求完成证据（请求失败也通常生成）
  request_attempt_started.json
  request_evidence.jsonl
  runtime_identity.json / preflight.json
  single_request_report.json / metric_deltas.json / residency_evidence.json
  capsule.txt / commands.json / commands.jsonl
  samples/{npu,host,process,disk,metrics}.jsonl
  raw/command_*.txt / raw/metrics_*.prom
```

三份报告在演练、预检阻塞或请求失败时也生成，并明确该轮是否发了请求。真实报告只从该run数据生成，不复制历史预检或离线测试数字。raw文件和samples需保留在服务器，不上传模型/tensor或打大型压缩包。

先把下面的小输出发回供审查：

```bash
printf 'LIVE=%s\n' "$LIVE"
cat "$LIVE/capsule.txt"
cat "$LIVE/01_BEGINNER_SUMMARY.md"
cat "$LIVE/metric_deltas.json"
```

随后可直接提供三个报告文件；技术报告较长就按章节粘贴。**本次真实验收要等这些服务器输出，离线78项通过不能替代。**

## 7. 这轮之后才考虑Hook

本轮即使看到SSD读取增加、RSS或HBM变化，也不证明权重/KV迁移。下一阶段先关联API/外部请求、内部request_id、Engine执行实例，再记录Scheduler→KVCacheManager→Coordinator→BlockPool生命周期，最后验证Layer/Rank上的真实backing allocation。

128/32差异必须用当前定制构建的KV group/layout解释，不能直接使用`num_gpu_blocks×32`或×128换算HBM字节。Hook方案提交后再单独决定如何部署；本入口不会注入或重启。
