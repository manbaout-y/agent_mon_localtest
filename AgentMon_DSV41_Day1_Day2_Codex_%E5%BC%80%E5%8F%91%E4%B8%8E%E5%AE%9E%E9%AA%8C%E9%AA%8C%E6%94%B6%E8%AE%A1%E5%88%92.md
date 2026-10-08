# AgentMon × DeepSeek V4.1 — Day 1 / Day 2 开发与实验验收任务书

> **文档性质**：交给 Codex 的可执行工作说明（开发任务 + 人工运行手册 + 验收标准）。  
> **编制日期**：2026-10-08  
> **阶段目标**：在真实 DeepSeek V4.1 / vLLM-Ascend 环境中，复现 AgentMon 的客户端 Live 基线，并打通 **Agent 请求 → API 请求 → Engine 请求 → Scheduler 请求** 的显式关联。  
> **优先顺序**：保留运行基线 → 只读源码 Discovery → 最小 Request Binding → 真实 C=1 验证。  
> **当前状态**：本文件是待执行计划，**不表示 Day 1 或 Day 2 已经验收通过**。

---

## 0. 给 Codex 的最高优先级指令

你是此工作区的开发助手。请**先盘点现有实现，再提出最小变更并运行离线测试**。不要把计划、文档里的功能描述、或旧版本已通过的测试直接写成“当前 V4.1 线上已验证”。

### 0.1 这次必须交付

1. **Day 1 基线验证脚本/说明**：能够安全检查 Docker/NPU/API/版本、运行已有 AgentMon Live Smoke，并按 run 目录保存退出码、原始日志与请求证据。
2. **Day 2 源码调用链报告**：由真实 `vLLM 0.27.1+empty` 容器中的类、函数、签名和源码位置形成；不可照抄 vLLM 0.18 的路径当作已验证事实。
3. **最小 Request Binding 实现或精确缺口分析**：优先复用现有 AgentMon P1 关联与 P2 数据契约，新增版本适配必须与 `vllm_018` 隔离，明确 opt-in、版本门槛、质量标签和失败隔离。
4. **离线测试 + Live 人工验收操作手册**：测试能够识别 direct / heuristic / unavailable 以及 API ↔ Engine 不同 ID 的情况；Live 只有真实服务端证据出现后才能写 `PASS`。
5. **一份验收报告**：PASS / FAIL / BLOCKED / NOT_RUN 逐项说明，并指出对应文件和证据，不凭推测补齐。

### 0.2 严格禁止

- **不要在 vLLM `0.27.1+empty` 上加载旧 `vllm_018` Hook**，也不要绕过它的 `0.18.0` 兼容性门槛。
- 不自动重启/删除/重建任何共享容器；不自动运行 `npu_stop.sh`、`npu_keep_alive.sh`、`kill`，不改 Docker Socket、驱动、固件、CANN 或系统依赖。
- 不覆盖当前成功运行的 serving 配置；不自动创建另一套 TP8 竞争 8 张 NPU。
- 不自动开启高频 KV 热路径 Hook：`get_computed_blocks` / Block allocation/free/eviction 等留待 Day 3、显式授权后测试。
- 不记录 Prompt、Completion 全文、完整 HTTP 请求体、密钥、敏感环境变量、访问 Token。生成日志时优先最小字段、文件权限 0600、目录 0700。
- 不把 Engram PLE Offload 视为 KV Offload；不把 `npu-smi` 总 HBM 当作真实 KV Bytes。
- 不把时间相近、调用顺序相近的请求映射写成 `direct`；不能因为返回 `chatcmpl-*` 就宣称它是 Scheduler 的 ID。
- 不用旧 Qwen / `verl-cann90` Python 运行 DeepSeek 容器内的实际 Discovery，避免混淆不同环境。
- 未经操作者明确确认，不执行可能启动重量级模型、停掉占卡进程、注入服务端 Hook 或重启服务的命令；先输出建议命令供人工批准。

### 0.3 工作方法

1. `git status`、阅读项目 README/已有 scripts 和 P2 规范；列出现有能力与缺口。
2. 优先复用既有程序，不平行发明另一套 JSONL schema。
3. 将**主机侧安全只读检查**与**服务端插桩实验**解耦。
4. 实现最小可复现版本；每一步有输出、断言、失败解释和证据文件。
5. 在离线测试通过后提供人工 Live 操作步骤，**不要伪造 Live 测试结果**。

---

## 1. 系统事实与版本约束

### 1.1 DeepSeek V4.1 运行环境（用户提供的历史快照）

| 项 | 记录值 | 使用注意 |
|---|---|---|
| 项目 | DeepSeek V4.1 Single-Node Ascend Runtime | 与其他 Qwen 运行时隔离 |
| 硬件 | 8 × Ascend 910B1 | 每卡 65536 MB HBM |
| CPU / RAM | aarch64 Kunpeng 920、192 logical CPUs、1.5 TiB RAM | 主机配置 |
| 驱动 / 固件 | `26.0.rc1` / `9.0.0.0.205` | 历史证据，非当前探测 |
| CANN | `9.0.0`、`V100R001C10SPC001B250` | 历史证据 |
| Docker socket | `unix:///run/docker-v41.sock` | 独立 daemon，不能省略 `-H` |
| Image | `dsv41-single:w4a16-engram-offload` | 必须保存实际 image ID |
| 容器 | 历史 `dsv41-smoke`；后续使用 `dsv41-smoke-256k` | 运行容器名需当场确认 |
| 容器 Python | `/usr/local/python3.12.13/bin/python3` | 不使用 Qwen 的 Python |
| vLLM | `0.27.1+empty` | P2 0.18 Hook 不兼容 |
| vLLM-Ascend | `0.1.dev5097+ge43cf1e9f` | Revision 前缀 `e43cf1e9` |
| 模型 | `DeepSeek-V4.1-Flash-W4A16-Engram-AutoRound` | Served name `deepseek-v41` |
| API | `0.0.0.0:8800` | **宿主机 8800 映射尚未验证** |
| 并行 | TP8 / EP8 | 384 global experts，48 local experts / EP rank，linear placement |
| 量化 | Ascend W4A16 / ModelSlim | 与普通 fp16 KV 公式区分 |
| Engram | `enable_engram=true`、`enable_engram_ple_offload=true` | **不等于 KV Offload** |
| 缓存配置 | `gpu_memory_utilization=0.90`、`block_size=128` | 基线，不直接推算真实 KV 占用 |
| 早期上下文 | `max_model_len=131072`、`max_num_batched_tokens=4096`、`max_num_seqs=1` | **早期的一次配置** |
| 后期上下文 | `max_model_len=262144`，有约 245K token 验证记录 | **不同的历史运行；原始验证文件不在此处** |
| 编译 | `cudagraph_mode=FULL_DECODE_ONLY`、`enable_npugraph_ex=false` | 注意扩展编译后端 |
| Parser | `deepseek_v41` tokenizer/reasoning/tool parser | 当前环境特性 |
| 当前状态 | `NOT_VERIFIED` | 必须重新检查 |

**已知近期实际现象**：使用 `docker exec` 进入 `dsv41-smoke-256k` 曾得到 `container ... is not running`。用户之后计划手动处理 NPU 保活占用并启动模型；**此消息链尚未给出重启成功证据**。因此任何后续流程都要先检查容器是否 Up。

### 1.2 原 P2 文档的适用边界

参考资料：《P2 vLLM Request and KV Observability Requirements》（本对话提供）。

- 原目标：Qwen3-Coder-30B-A3B + vLLM `0.18.0(+empty)` + vLLM-Ascend `0.18.0`。
- P2.0 Discovery、P2.1 Request Event/Mapping、P2.2 latency/token、P2.3 KV/NPU、P2.4 prefix-cache planner 各有既有契约/实现基础。
- `agentmon.adapters.inference.vllm_018.hook` 通过 opt-in `sitecustomize.py` 加载；默认禁用。
- 文档验收清单最后的 **target-server P2 Live Hook API + EngineCore 加载**仍是未通过项；不能将文档的 `[x]` 直接解释为在我们 V4.1 环境通过。
- `AGENTMON_VLLM_P2_ENABLE=1` 是旧版 0.18 适配器的启用配置，不在当前 V4.1 上直接执行。
- 文档要求同时覆盖 API 与 EngineCore 子进程的执行环境，仅 Hook API 进程不够。
- P2 JSONL service stream 不等于完整的 AgentMon run；导入/关联/finalization 需单独处理。

---

## 2. Day 1 — 恢复环境与基线验证

### 2.0 Day 1 的成功定义

**运行服务证据** + **真实模型请求证据** + **AgentMon Client Evidence** + **可复现环境快照**。单独 `/v1/models` HTTP 200 不算完成。客户端 Live Smoke 成功也不代表 P2 服务端 Hook 成功。

### 2.1 预检查：只读检查容器和 NPU

在**宿主机**、当前项目根目录执行：

```bash
cd ~/agent_mon-ver_hetero_state
export LAB_ROOT="$(pwd)"
export V41_SOCKET='unix:///run/docker-v41.sock'
export V41_CONTAINER='dsv41-smoke-256k'  # 历史名称：务必以 ps -a 输出复核
export PY='/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python'

umask 077
export RUN_ID="dsv41-day1-$(date +%Y%m%d-%H%M%S)"
export RUN_DIR="$LAB_ROOT/outputs/live-acceptance/$RUN_ID"
mkdir -p "$RUN_DIR"/{env,logs,api,smoke,source,binding}
chmod 700 "$RUN_DIR" "$RUN_DIR"/*

# 只读，不会启动任何服务
docker -H "$V41_SOCKET" ps -a \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}' \
  | tee "$RUN_DIR/env/docker-containers.txt"
npu-smi info | tee "$RUN_DIR/env/npu-info.txt"
```

检查结果：

- 容器 **不存在** → STOP；重新查实际名称，不创建替代容器。
- 容器 **Exited** → 先采集 State/Logs，再由操作者确认是否启动。
- 容器 **Up** → 继续 API 检查。
- 8 卡存在其他非本项目任务 → STOP；避免自动清理占卡进程。

对容器状态、退出原因和镜像 ID 留证：

```bash
docker -H "$V41_SOCKET" inspect "$V41_CONTAINER" \
  --format 'Status={{.State.Status}} ExitCode={{.State.ExitCode}} OOMKilled={{.State.OOMKilled}} Started={{.State.StartedAt}} Finished={{.State.FinishedAt}} ImageID={{.Image}}' \
  | tee "$RUN_DIR/env/container-state.txt"

docker -H "$V41_SOCKET" logs --tail 300 "$V41_CONTAINER" \
  > "$RUN_DIR/logs/container-before-test.log" 2>&1
```

> **人工操作（需授权）**：如已确认没有与之冲突的 NPU 任务，且操作者决定恢复停止的原容器，才执行：  
> `docker -H unix:///run/docker-v41.sock start dsv41-smoke-256k`  
> 不由 Codex 默认执行。`docker start` 只说明容器主进程开始运行，不等于权重和 API 已就绪。

### 2.2 API 与进程就绪检查

先看 Docker 端口发布情况：

```bash
docker -H "$V41_SOCKET" port "$V41_CONTAINER" \
  > "$RUN_DIR/env/port-publishing.txt" 2>&1 || true
```

如果宿主机 `127.0.0.1:8800` 已经确认映射：

```bash
curl -sS --max-time 15 \
  -o "$RUN_DIR/api/models.json" \
  -w '%{http_code}\n' \
  http://127.0.0.1:8800/v1/models \
  | tee "$RUN_DIR/api/models-http-code.txt"
```

**宿主机没有映射时不要误判失败**；在运行中的容器内部测试：

```bash
docker -H "$V41_SOCKET" exec "$V41_CONTAINER" \
  /usr/local/python3.12.13/bin/python3 -c \
  'import urllib.request; u="http://127.0.0.1:8800/v1/models"; r=urllib.request.urlopen(u, timeout=15); print(r.status); print(r.read(4096).decode())' \
  > "$RUN_DIR/api/models-from-container.txt" 2>&1
```

判断标准：至少有一种从**实际调用端**到模型 API 的可达路径；确认 `/v1/models` 返回 HTTP 200 且实际 served model ID 合理。**调用端**可能是宿主机 AgentMon，而不只是容器内。

如果 API 没准备好，查看最新日志中的根因：

```bash
docker -H "$V41_SOCKET" logs --tail 500 "$V41_CONTAINER" \
  > "$RUN_DIR/logs/container-readiness.log" 2>&1
grep -Ein 'traceback|error|EngineCore|OOM|killed|ready|listening' \
  "$RUN_DIR/logs/container-readiness.log" | tail -100 || true
```

### 2.3 版本和环境指纹（只读）

仅在容器 **Up** 时运行：

```bash
docker -H "$V41_SOCKET" exec -i "$V41_CONTAINER" \
  /usr/local/python3.12.13/bin/python3 - <<'PY' \
  > "$RUN_DIR/env/runtime-python-packages.txt" 2>&1
import sys, importlib.metadata as md
import vllm, vllm_ascend
print('python=', sys.executable)
print('vllm_version=', vllm.__version__)
print('vllm_file=', vllm.__file__)
print('vllm_ascend_file=', vllm_ascend.__file__)
for package in ('vllm', 'vllm-ascend'):
    try: print('distribution', package, md.version(package))
    except md.PackageNotFoundError: print('distribution', package, 'NOT_FOUND')
PY

# 不导出整个 docker inspect，避免记录 Secret/Env
docker -H "$V41_SOCKET" inspect "$V41_CONTAINER" \
  --format 'Image={{.Config.Image}} ImageID={{.Image}} Entrypoint={{json .Config.Entrypoint}} Cmd={{json .Config.Cmd}}' \
  > "$RUN_DIR/env/container-launch-fingerprint.txt"

# 保存 AgentMon 工作区源码状态
git rev-parse HEAD > "$RUN_DIR/env/agentmon-commit.txt" 2>&1 || true
git branch --show-current > "$RUN_DIR/env/agentmon-branch.txt" 2>&1 || true
git status --short > "$RUN_DIR/env/agentmon-status.txt" 2>&1 || true
git diff --stat > "$RUN_DIR/env/agentmon-diff-stat.txt" 2>&1 || true
```

可选：在容器内读取 vLLM Git Revision；失败时记录不可用，**不可推断 Commit**。

```bash
docker -H "$V41_SOCKET" exec "$V41_CONTAINER" sh -lc \
  'cd /vllm-workspace/vllm && git rev-parse HEAD && git status --short' \
  > "$RUN_DIR/source/vllm-git-state.txt" 2>&1 || true
```

**单独记录**启动命令实际使用的是 131072 还是 262144 上下文，以及 `max_num_seqs`、batch tokens、TP/EP、模型名、cache/block 参数。若启动命令未能确认，字段填 `UNKNOWN`，不要把不同运行合并。

### 2.4 先确认模型真实推理（不仅是 models endpoint）

- 优先复用已有 **最短请求 smoke**，不要另写一个绕过既有 Client Instrumentation 的测试工具。
- 单请求/小 token 输出；必要时先经容器内已确认可达的 endpoint 测试。
- 记录 HTTP 状态、模型实际响应是否成功、生成 token 使用字段（如果有）、服务日志时间范围。
- 不记录用户敏感 Prompt 或 Completion；可只保存测试文本的可复现短固定输入摘要/哈希。

**注意**：API 的 `response.id` 只是 API 响应 ID；暂不能据此确认 Engine/Scheduler ID。

### 2.5 AgentMon Live Smoke（Day 1 核心）

先检查脚本存在和帮助说明，确认参数保持兼容：

```bash
ls -l agentmon/scripts/deepseek_v41_client_smoke.py
"$PY" agentmon/scripts/deepseek_v41_client_smoke.py --help \
  > "$RUN_DIR/env/client-smoke-help.txt" 2>&1
```

确保模型服务从脚本运行位置可达后，才执行：

```bash
set -o pipefail
"$PY" agentmon/scripts/deepseek_v41_client_smoke.py \
  --live \
  --kvreuse-root ./kv_ana \
  --output "$RUN_DIR/smoke" \
  2>&1 | tee "$RUN_DIR/logs/live-smoke.log"
SMOKE_RC=${PIPESTATUS[0]}
printf '%s\n' "$SMOKE_RC" > "$RUN_DIR/logs/live-smoke-exit-code.txt"
```

验证：

- `SMOKE_RC=0` 并核对脚本内部 pass 判定；不能仅凭 HTTP 200。
- 出现真实服务端模型响应，而非 dry-run / mocked 结果。
- 查找真实产生的 `request_evidence.jsonl` 或其等价证据文件：

```bash
find "$RUN_DIR/smoke" -type f | sort \
  > "$RUN_DIR/logs/smoke-file-list.txt"
find "$RUN_DIR/smoke" -type f \
  \( -name '*evidence*.jsonl' -o -name '*result*.json' -o -name '*capsule*.txt' \)
```

预期 Client 侧 ID 链路（以实际 evidence 为准）：

```text
agent_run_id
    -> model_request_id
    -> request_attempt_id
    -> api_request_id
```

如果链路缺字段，请输出缺失字段、关联失败原因、证据来源；不填虚构 ID，不用相近时间补缺。

### 2.6 Day 1 报告与门槛

生成 `DAY1_ACCEPTANCE.md`，至少包含：

| 关卡 | 通过条件 | 失败/阻塞状态 |
|---|---|---|
| D1-G1 | 实际运行容器身份、状态明确 | 容器缺失/退出 |
| D1-G2 | 实际请求路径可以访问 API | 仅容器内可达、宿主机无法访问应注明 |
| D1-G3 | 一次真实 DeepSeek V4.1 模型请求成功 | `models` 200 不算 |
| D1-G4 | AgentMon Live Smoke 真实 PASS | dry-run 不算 |
| D1-G5 | Client-side Request Evidence 可核验 | 记录缺失字段 |
| D1-G6 | 镜像、Python、vLLM、配置、源码 Git 状态已有快照 | 缺项标 UNKNOWN |

当 D1-G3/G4 未通过，Day 2 仍可做只读源码研究，但不得宣称 `LIVE_BINDING_PASS`。

---

## 3. Day 2 — 源码 Discovery 与 Request Binding

### 3.0 目标与不做的事

**目标**：对同一个真实请求，证明 `AgentMon model_request_id/HTTP attempt` 如何绑定到 **vLLM API request / Engine Request / Scheduler Request**。允许 API 与 Engine 是不同字符串，但必须有明确映射证据。

**本次不做**：KV Tensor 导出、Worker/Rank 物理分配追踪、HBM↔DDR 迁移、缓存替换策略、MoE 逻辑/物理专家映射。这些属于后续 Day 3+。

### 3.1 先看旧代码，列差异；不要直接加载

Codex 在当前 checkout 中自行确认路径：

```bash
cd ~/agent_mon-ver_hetero_state
find agentmon -type f \
  \( -name 'hook.py' -o -name 'events.py' -o -name 'kv.py' \
     -o -name 'version.py' -o -name 'agentmon_p2_discovery.py' \) \
  | grep -E 'vllm_018|p2_discovery' || true

grep -RInE \
  'X-AgentMon-Request-Id|AGENTMON_VLLM_P2_ENABLE|record_request_id_alias|emit_received' \
  agentmon/src/agentmon agentmon/scripts 2>/dev/null | head -150 || true
```

应阅读（若路径确实存在）：

- `agentmon/src/agentmon/adapters/inference/vllm_018/hook.py`
- `.../events.py`、`.../version.py`、`.../kv.py`
- `agentmon/scripts/agentmon_p2_discovery.py`
- `sitecustomize.py`（实际文件优先）
- P1 的 LiteLLM HTTP Headers / `request_evidence` 实现
- `agentmon import-p2`、P2 report 的 identity audit 机制

必须输出 `P2_018_TO_V41_DIFF.md`，包含：**可复用代码 / 需要适配 / 尚不存在 / 暂缓**；注明每项的源码证据位置。

### 3.2 容器内只读 Discovery（vLLM 0.27.1）

注意：原 P2 Discovery CLI 的 `--strict-version` 针对 0.18，直接运行可能按设计拒绝 v0.27；这不是服务故障。优先使用下列**仅扫描现有真实模块**的标准库脚本；如果已有 Discovery 可配置新版本再复用。

**仅在容器 Up 时**执行：

```bash
docker -H "$V41_SOCKET" exec -i "$V41_CONTAINER" \
  /usr/local/python3.12.13/bin/python3 - <<'PY' \
  > "$RUN_DIR/source/v41-request-discovery.txt" 2>&1
import importlib, inspect, sys
import vllm
print('python:', sys.executable)
print('vllm:', vllm.__version__)
print('vllm_file:', vllm.__file__)
targets = [
  ('vllm.v1.engine.async_llm', 'AsyncLLM', ['generate', 'add_request', 'abort']),
  ('vllm.v1.engine.input_processor', None, []),
  ('vllm.v1.engine.core_client', None, []),
  ('vllm.v1.engine.core', 'EngineCore', ['add_request', 'step', 'step_with_batch_queue']),
  ('vllm.v1.core.sched.scheduler', 'Scheduler', ['add_request', 'schedule', 'update_from_output']),
  ('vllm.v1.core.kv_cache_manager', 'KVCacheManager', ['get_computed_blocks', 'allocate_slots', 'free']),
]
for module_name, cls_name, methods in targets:
    print('\nMODULE', module_name)
    try:
        m = importlib.import_module(module_name)
        print('MODULE_FILE', inspect.getfile(m))
        if cls_name is None:
            continue
        cls = getattr(m, cls_name)
        print('CLASS', cls_name, inspect.getsourcefile(cls))
        for name in methods:
            obj = getattr(cls, name, None)
            if obj is None:
                print('MISSING', name)
            else:
                try: sig = str(inspect.signature(obj))
                except Exception as exc: sig = 'SIGNATURE_ERROR:' + repr(exc)
                try: line = inspect.getsourcelines(obj)[1]
                except Exception: line = 'UNKNOWN'
                print('METHOD', name, 'LINE', line, 'SIG', sig)
    except Exception as exc:
        print('IMPORT_ERROR', type(exc).__name__, repr(exc))
PY
```

提示：在 Ascend 特定导入环境中，模块 import 可能有副作用或缺少 ACL 路径；脚本**不会启动推理服务**，但失败时需对照容器真实环境，不得将失败的静态候选当作有效 Hook。若已运行服务无法安全 `exec`，可用 `docker cp` 复制指定源码到本地做静态分析，但那只能证明静态源码，不算运行时签名验收。

### 3.3 沿调用链查源码并输出准确行号

要研究的**概念路径**：

```text
P1 model_request_id / HTTP attempt
     -> HTTP headers / Chat Serving
     -> AsyncLLM / Input Processor
     -> EngineCoreClient / IPC
     -> EngineCore.add_request
     -> Scheduler.add_request
     -> Scheduler 内部 request_id
```

查找入口（路径可能变化，先 `find` 再 grep）：

```bash
docker -H "$V41_SOCKET" exec "$V41_CONTAINER" sh -lc '
ROOT=/vllm-workspace/vllm/vllm
for f in \
  entrypoints/openai/chat_completion/serving.py \
  v1/engine/async_llm.py \
  v1/engine/input_processor.py \
  v1/engine/core_client.py \
  v1/engine/core.py \
  v1/core/sched/scheduler.py \
  v1/request.py
 do
   [ -f "$ROOT/$f" ] && echo "FOUND $f" || echo "MISSING $f"
 done
'
```

需要逐层回答并将**真实路径/函数/行号**写入 `REQUEST_CALL_CHAIN.md`：

1. API 侧 `chatcmpl-*` 是在哪里创建？是否有 HTTP Response ID ↔ Engine ID 的 alias？
2. Request Header `X-AgentMon-Request-Id` 是否真正进入 API Server？P1 是否确实发送成功？
3. AsyncLLM 的哪个入口接收 `request_id`？Input Processor 是否修改 ID 或扩展为 subrequests？
4. EngineCoreClient 将请求通过什么接口/IPC 发到 EngineCore？是否保持 ID？
5. EngineCore 把哪一个对象交给 Scheduler？
6. Scheduler 是否沿用相同 request_id，或创建其他标识？
7. 哪些 Hook 必须分别在 API Process 和 EngineCore Process 内安装？
8. 如果有请求拆分、重试、取消，关联关系如何表达为 one-to-many？

### 3.4 请求身份设计与正确性要求

**规范字段**（保留旧 Schema 兼容；真实键名以项目现有字段为准）：

```json
{
  "agent_run_id": "<observed>",
  "model_request_id": "<observed>",
  "request_attempt_id": "<observed>",
  "api_request_id": "<observed or null>",
  "engine_request_id": "<observed or null>",
  "scheduler_request_id": "<observed or null>",
  "correlation_quality": "direct | server_generated | heuristic | unavailable",
  "evidence_sources": ["client_evidence", "api_hook", "engine_hook", "scheduler_hook"],
  "status": "..."
}
```

上面的值是**Schema 示例**，不是实测数据。

- `direct`：P1 ID 被服务端真实接收，并被显式绑定到内部 ID，或具有可审计的等价映射证据；不能仅凭 `request_id` 文本刚好相同。
- `server_generated`：只有 vLLM 服务端 ID 和真实时间，未证明 Agent → Engine 身份映射。
- `heuristic`：按时间/序列推断，只作为弱证据，绝不能标 direct。
- `unavailable`：无安全映射。
- 对任何 one-to-many 和 retry，保留 alias / attempt，不静默覆盖。
- 事件必须有来源 `pid`、process role、host、request ID、stage、可核验的时钟域；跨进程时间戳不能盲目相减。结构遵循原 P2 JSONL envelope（`source="vllm_p2"` 或经明确版本化的扩展）。
- P2 的 `canonical_request_id` 是分析用标识，**不等于**保证 API / Engine / Scheduler 原生 ID 相同。

### 3.5 实现方案（先离线、后服务端；最小范围）

**阶段 A：零注入**

- 从现有日志、源码调用链和 Client Evidence 尝试还原 ID 映射。
- 写一个 `analyze_request_binding.py` 或复用已有 P2 importer/report：输入客户端证据 + 服务端事件，输出 `request_binding.jsonl`、`request_identity_audit.json`。
- 明确区分 `direct` / `heuristic` / `unavailable`，并产生 unmatched P1 requests、orphan P2 events。
- 日志不足时，不自动升级质量。

**阶段 B：需要服务端 Hook 才能取得 direct 时**

- 实现**独立、显式 opt-in**的 vLLM 0.27.1 适配（建议版本分离 namespace；名称以项目已有架构为准）。
- 在安装前校验**真实加载**的 vLLM/Ascend 版本、模块路径、类、方法和签名，不单靠 package metadata。加载失败时 fail-closed，不影响 serving。
- 借鉴原 0.18 P2 的 `sitecustomize` / 事件写入实现，但**不能默认 Monkeypatch 到已有生产进程**。
- 仅观测 API → AsyncLLM → EngineCore → Scheduler 身份和最少量生命周期事件；**不修改调度、输入 token、返回内容或模型行为**。
- 对异常 fail-open（观测错误只上报 `vllm.adapter.error`），多进程事件文件采用安全 append 与唯一 event ID；记录丢事件数量。
- 通过同版本独立验证服务启动参数注入，检查子进程实际继承；只有操作者批准时执行 Live。
- 如资源不足以额外运行第二套 TP8，先停在**代码+离线测试/静态签名检查**，安排服务维护窗口，不得偷偷并发启服务。

### 3.6 测试计划：离线单测、集成模拟、服务器 Live

#### 离线（Codex 应自动执行）

至少覆盖：

1. 版本兼容：0.18 Hook 拒绝 0.27，0.27 Adapter 仅接受显式兼容且签名匹配的目标版本。
2. 直接映射：通过显式服务端链路形成 `direct`，并保留 `api_request_id` 与 `engine_request_id` 不同的情况。
3. 仅相近时间：应为 `heuristic` 或 `unavailable`，绝不 `direct`。
4. 并发两请求 + 重试 + 一个孤儿服务端 Request：不发生错归属。
5. API/Engine 多进程：事件可以可靠汇总，并检测未加载 Hook 的进程。
6. 少字段/服务端异常/事件 writer 写入失败：标记降级、无 Model 请求副作用。
7. 取消 / 超时 / 一对多请求：保持终态一致，不冒充 `finished`。
8. 不产生 Prompt/Completion/secret 字段；事件体大小受控。

#### 服务器 Live（人工确认后执行）

实验固定在**一次短真实请求、并发 C=1**：

- `max_num_seqs=1` 是服务端容量设置，**不单独证明只有一次请求在运行**；需要通过隔离的测试窗口和 Scheduler evidence 确认。
- 模型响应正常（记录结果和状态；不必存文本内容）。
- Client `model_request_id` 能与服务端 API 事件直接对应。
- 有服务端 EngineCore `add_request` 观测与同一请求 alias。
- Scheduler `add_request` 确认一个内部 Request ID。
- 输出一个完整 `request_binding.jsonl`，质量 `direct`，附证据文件和对应 event IDs。
- 如果只看到了 `chatcmpl-*` 与 Scheduler 日志时间匹配，仍然**不能通过 direct gate**。

### 3.7 Day 2 验收分级

| Gate | 要求 | 成功产物 |
|---|---|---|
| D2-G1 Source discovery | 真实包路径、函数和签名已确认 | `v41-request-discovery.txt` |
| D2-G2 Call chain | API → AsyncLLM → IPC → Engine → Scheduler 源码行号明确 | `REQUEST_CALL_CHAIN.md` |
| D2-G3 Compatibility diff | 0.18 Hook 的可复用/不可复用边界明确 | `P2_018_TO_V41_DIFF.md` |
| D2-G4 Offline tests | 所有映射、失败隔离、并发单测通过 | 测试日志 |
| D2-G5 Server event evidence | API 与 EngineCore/Scheduler 真实进程产生事件 | `binding-server-events.jsonl` |
| D2-G6 Live direct binding | 同一真实 Agent Request 实现可审计 direct 关联 | `request_binding.jsonl` + identity audit |

只有 D2-G5 + D2-G6 真实通过，才报告 **`LIVE_BINDING_PASS`**。只有前四项时应写 `SOURCE_AND_OFFLINE_PASS / REAL_ACCEPTANCE_PENDING`。

---

## 4. 固定输出目录结构（建议）

```text
outputs/live-acceptance/<RUN_ID>/
├── env/
│   ├── docker-containers.txt
│   ├── container-state.txt
│   ├── npu-info.txt
│   ├── port-publishing.txt
│   ├── runtime-python-packages.txt
│   ├── container-launch-fingerprint.txt
│   ├── agentmon-commit.txt
│   ├── agentmon-status.txt
│   └── run-config-summary.json
├── api/
│   ├── models-http-code.txt
│   ├── models.json
│   └── models-from-container.txt
├── logs/
│   ├── container-before-test.log
│   ├── container-readiness.log
│   ├── live-smoke.log
│   ├── live-smoke-exit-code.txt
│   └── binding-server-events.jsonl
├── smoke/
│   └── <原有真实脚本生成的 evidence / reports>
├── source/
│   ├── vllm-git-state.txt
│   ├── v41-request-discovery.txt
│   ├── REQUEST_CALL_CHAIN.md
│   └── P2_018_TO_V41_DIFF.md
├── binding/
│   ├── request_binding.jsonl
│   ├── request_identity_audit.json
│   └── binding_acceptance.json
├── DAY1_ACCEPTANCE.md
└── DAY2_ACCEPTANCE.md
```

没有产生的文件不需要伪造空 PASS；报告写 `NOT_RUN` 或 `BLOCKED` 并注明原因。`run-config-summary.json` 应记录来源是 `historical` / `observed_runtime` / `inferred` / `unknown`，避免混淆。

---

## 5. Codex 应拆解成的工作清单

### Phase A — Repo audit（无需真实模型）

- [ ] 检查当前仓库状态、工作树未提交改动；不覆盖现有修改。
- [ ] 确认现有 P1 Live Smoke、P2 `vllm_018` Hook、P2 Discovery、P2 import/report 的代码路径。
- [ ] 生成 `P2_018_TO_V41_DIFF.md`，注明文档承诺与当前代码现实的差别。
- [ ] 确认使用的 Python、依赖安装方式和实际现有测试命令。

### Phase B — Day 1 scripts / docs

- [ ] 写或修正安全只读的预检脚本（支持 `V41_SOCKET`、容器名、RUN_DIR）。
- [ ] 针对 host-port 未发布/容器内 API 可达，输出明确的两种状态。
- [ ] 复用既有 Live Smoke，避免混用 Qwen `verl-cann90`。
- [ ] 输出退出码和运行时证据；生成 `DAY1_ACCEPTANCE.md`。

### Phase C — Day 2 discovery / mapping

- [ ] 实现 v0.27.1 的只读 introspection，检查真实源码行号与函数签名。
- [ ] 写 `REQUEST_CALL_CHAIN.md`（用真实发现而不是猜测）。
- [ ] 优先复用旧 P2 事件 Schema，形成独立 request-binding 报告。
- [ ] 若需新 Adapter，先完成兼容门槛/离线单测/自动禁用；不自动部署。
- [ ] 准备 API、EngineCore、Scheduler 三处最小观测点与进程覆盖诊断。

### Phase D — Tests and report

- [ ] 运行现有相关回归测试和新增单测。
- [ ] 提供测试命令和真实 PASS/FAIL 汇总。
- [ ] 对未执行的 Live 项标记 `REAL_ACCEPTANCE_PENDING`。
- [ ] 提供操作者可执行、可回滚、尽量只读的 Live 验证说明。
- [ ] 写清下一个 Day 3 的接口：`scheduler_request_id -> KVCacheManager events -> block lifecycle`，**不提前实现物理 Tensor 捕获**。

---

## 6. 请求 ID 绑定的验收示例（仅 Schema，不是事实记录）

```json
{
  "experiment_id": "<run-id>",
  "agent_run_id": "<observed-run-id>",
  "model_request_id": "<observed-model-id>",
  "request_attempt_id": "<observed-attempt-id>",
  "api_request_id": "<observed-api-response-id>",
  "engine_request_id": "<observed-engine-id>",
  "scheduler_request_id": "<observed-scheduler-id>",
  "request_id_aliases": [
    {"from": "api_request_id", "to": "engine_request_id", "evidence_event_id": "<id>"},
    {"from": "engine_request_id", "to": "scheduler_request_id", "evidence_event_id": "<id>"}
  ],
  "correlation_quality": "direct",
  "quality_reason": "explicit client/header and server-internal binding events observed",
  "evidence_files": ["<client-evidence>", "<server-events>"],
  "validation_status": "PASS"
}
```

**严格要求**：上面的 `direct` 只能在服务端实际采到了关联事件后出现；离线构造测试不得伪装成真实实验。

---

## 7. 异常处理 / 决策树

```text
Docker container Exited
  ├─ 仅源码研究 → docker cp 指定文件/静态 inspect，不自动启动模型
  └─ 需要 Live → 人工确认 NPU 资源、镜像、端口后 docker start
          ↓
API 无法调用
  ├─ 宿主机端口未发布 → 确认网络路径，不认定模型故障
  └─ 容器内也不可达 → 查看 EngineCore / serving 日志，Day 1 BLOCKED
          ↓
API 已可用，但 Live Smoke 失败
  ├─ 排查 P1 环境、LiteLLM、脚本配置、HTTP 可达性
  └─ 证据链缺失 → CLIENT_EVIDENCE_PARTIAL，不写 PASS
          ↓
V4.1 源码发现成功，但 P2 0.18 Hook 版本不匹配
  ├─ 属于预期设计 → 必须保持 vllm_018 拒绝加载
  └─ 单独适配 v0.27.1 / 离线验收
          ↓
无法部署 P2 服务端 Hook
  ├─ 没有空闲 NPU 或没有维护窗口 → 停留在离线验证
  └─ 获得明确授权 → 单独验证服务 / 受控维护窗口
          ↓
有 API 和 Scheduler 日志，但没显式映射
  ├─ 只能标 heuristic/server_generated
  └─ 不宣称 LIVE_BINDING_PASS
```

---

## 8. 交接给 Day 3 的前置条件

Day 3 将追踪 `Scheduler -> KVCacheManager -> BlockPool -> Worker/Rank`，但它依赖 Day 2 先提供：

1. **一个真实、已被证明身份的 Engine/Scheduler 请求**；
2. Request ID alias 映射、事件时间与进程来源；
3. 确切的 vLLM 0.27.1 Scheduler/KVCacheManager 类和方法签名；
4. 不影响 serving 的适配加载与故障隔离机制；
5. 可重放/审计的客户端和服务器原始证据文件。

特别提醒：P2 已有 `allocated_block_count`、`freed_block_count`、`evicted_block_count` 等**请求级统计契约**，但不能自动推出每个物理 Block 的真实地址、Tensor、Layer/Rank 归属或 HBM/DDR 迁移。Day 3 之后的重点将是**真实物理状态证据**，不是仅延伸统计表。

---

## 9. Codex 完成时的汇报模板

请 Codex 按以下格式在一次工作结束时汇报：

```text
1. 检查的仓库 Commit / 工作树状态
2. 实际发现的 V4.1 环境（无证据则标 UNKNOWN）
3. 已修改或新增的文件（每个文件的意图）
4. 当前 Request 调用链（真实源码位置、函数、行号）
5. 0.18 P2 与 0.27.1 V4.1 的兼容差异
6. 离线测试命令、通过数、失败数、具体日志路径
7. Day 1 验收各 Gate：PASS/FAIL/BLOCKED/NOT_RUN
8. Day 2 验收各 Gate：PASS/FAIL/BLOCKED/NOT_RUN
9. Live C=1 结果与直接证据（没有实际运行则 REAL_ACCEPTANCE_PENDING）
10. 仍需人工执行的命令与安全风险
11. Day 3 准备就绪程度与遗留问题
```

**总体验收口径**：

- `DAY1_REAL_PASS`：真实模型和 AgentMon Client Live 运行成功，证据链与环境版本可复核。
- `DAY2_SOURCE_PASS`：真实源码/调用链追踪通过。
- `DAY2_OFFLINE_PASS`：请求身份映射实现与离线回归测试通过。
- `DAY2_LIVE_BINDING_PASS`：一个真实的 Agent 请求取得从 P1 到 Engine/Scheduler 的**direct** 关联，并有 API/EngineCore 子进程实际证据。
- `REAL_ACCEPTANCE_PENDING`：已开发但未在真正的目标服务器验证，**不能宣传已经线上通过**。

---

## 10. 原始资料 / 来源说明

- 用户提供的 **DeepSeek V4.1 Single-Node Ascend Runtime 环境信息**（本会话，2026-10-08），属于历史/运行配置记录；当前在线状态仍需验证。
- 用户上传的 **P2 vLLM Request and KV Observability Requirements** 文本。其目标是 vLLM 0.18.0 + Qwen3-Coder；可复用数据契约和研究方法，但不得把 0.18 的 Hook 兼容性自动推及 v0.27.1。
- 既有项目历史：P1 Client Live Smoke 曾成功；kv_ana 离线验证曾 `OFFLINE_CHECKS_PASS`，并明确 `MODEL_EXECUTION=NOT_RUN`、`REAL_ACCEPTANCE=PENDING`。这些历史成功不能替代本次运行时验收。

**本文件的性质是计划与开发任务书；所有执行结果须由真实命令输出和保存的原始证据确认。**
