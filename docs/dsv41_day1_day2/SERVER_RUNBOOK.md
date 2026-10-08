# Day 1 / Day 2 服务器人工操作手册

本轮开发没有访问目标服务器、启动模型、注入 Hook 或重启服务。
所有命令在 Linux 服务器执行。先同步本次变更，不覆盖服务器现有实验输出或启动脚本。

## 1. 只读预检

```bash
cd ~/agent_mon-ver_hetero_state
export PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
export V41_SOCKET=unix:///run/docker-v41.sock
export V41_CONTAINER=dsv41-smoke-256k
umask 077
export EXP_DIR="$PWD/outputs/live-acceptance/dsv41-$(date +%Y%m%d-%H%M%S)"

"$PY" agentmon/scripts/dsv41_day1_acceptance.py \
  --client-python "$PY" --socket "$V41_SOCKET" --container "$V41_CONTAINER" \
  --run-dir "$EXP_DIR/precheck"

cat "$EXP_DIR/precheck/DAY1_ACCEPTANCE.md"
cat "$EXP_DIR/precheck/env/container-state.json"
cat "$EXP_DIR/precheck/env/npu-info.txt"
```

默认只有 Docker/NPU/版本/配置只读检查和 GET /models。没有模型 POST。
宿主机与容器内 API 分开判断：仅容器内可达时 D1-G2 BLOCKED，因为既有 smoke 在宿主机访问固定 8800。
容器使用 host network 时 docker port 可以为空，实际 HTTP 探测才决定 host 可达性。
Exited/缺容器时不 exec Python，也不会自动 start。NPU 当前占用必须由操作者核查；脚本不自动归因或清理。

只有你确认资源、原容器和维护窗口后，才考虑手动执行下面的恢复命令：

```bash
docker -H "$V41_SOCKET" start "$V41_CONTAINER"
```

这一步不是预检的一部分。等待真实 API Ready，权重 48/48 不等于就绪。不要另外启动 TP8。
重新预检时换一个新的 run-dir，例如 `"$EXP_DIR/precheck-after-start"`。

## 2. Day 1 Live（由操作者明确执行）

确认没有其他模型请求、NPU 竞争任务，且上述 host API/model/precheck 正常后：

```bash
"$PY" agentmon/scripts/dsv41_day1_acceptance.py \
  --client-python "$PY" --socket "$V41_SOCKET" --container "$V41_CONTAINER" \
  --run-dir "$EXP_DIR/live" --live --confirm-exclusive-window

cat "$EXP_DIR/live/DAY1_ACCEPTANCE.md"
cat "$EXP_DIR/live/logs/live-smoke-exit-code.txt"
cat "$EXP_DIR/live/smoke/client_smoke.json"
```

完全复用 `deepseek_v41_client_smoke.py`：两个顺序模型请求、一次固定 `printf` 工具执行。
它不是完整 Agent task。--confirm-exclusive-window 是人工声明，不能替代 Day 2 Scheduler evidence。
超时/非零返回码与失败 artifact 会保留。只读脚本 exit 0 不代表 DAY1_REAL_PASS，须读各 Gate。

目录 0700，文件 0600（POSIX）。服务/库日志采用内容过滤，原始 prompt/completion、完整 inspect/env/body 不落盘。
保留原 smoke 的 content-free sidecar/P1 events、退出码、固定检查输出；过滤后的日志不能用来做未记录的时间测量。

## 3. Day 2 目标容器只读 Discovery

仅在已确认容器 Up 后执行。下面显式导入模块以取得真实签名，不实例化 engine/model。
依赖导入可能触发 Ascend 注册或失败；失败记为证据缺口，不升级为可用 Hook。
exec 的旧 P2 enable 被设为 0，仅影响新开的检查进程，不改正在运行的服务。

```bash
docker -H "$V41_SOCKET" exec -i -e AGENTMON_VLLM_P2_ENABLE=0 "$V41_CONTAINER" \
  /usr/local/python3.12.13/bin/python3 -I - --runtime-introspection \
  --source-root /vllm-workspace/vllm/vllm \
  < agentmon/scripts/dsv41_request_discovery.py \
  > "$EXP_DIR/live/source/v41-request-discovery.json"
DISCOVERY_RC=$?
printf '%s\n' "$DISCOVERY_RC" > "$EXP_DIR/live/source/discovery-exit-code.txt"

"$PY" agentmon/scripts/dsv41_request_discovery.py \
  --render-snapshot "$EXP_DIR/live/source/v41-request-discovery.json" \
  --output-dir "$EXP_DIR/live/source/rendered"

cat "$EXP_DIR/live/source/rendered/REQUEST_CALL_CHAIN.md"
```

需要纯静态检查时移除 `--runtime-introspection`。静态报告不能通过运行时签名门槛。
容器停止时可人工 `docker cp` 指定源目录到新的本地目录，再用 `--source-root` AST 扫描；这不会变成 runtime PASS。
如果 Discovery 非零或报告 PARTIAL，先看 import_error_type/MISSING/INSPECTION_ERROR，不执行任何 Hook。

## 4. 完成环境证据审核，更新 Day 1 报告

`env/config-review.example.json` 只填入真实 /proc argv 观测值，未知字段保留 UNKNOWN。
复制为 `env/config-reviewed.json`，逐字段核对本次运行配置，附实际 evidence_file 与 source；不能照抄历史配置当观测。
字段所引用文件相对该 JSON 目录解析，必须存在；source 只允许 observed_runtime / observed_process_argv。

```bash
cp "$EXP_DIR/live/env/config-review.example.json" "$EXP_DIR/live/env/config-reviewed.json"
# 用服务器编辑器核对未知项并引用实际证据；未确认的项继续 UNKNOWN。

"$PY" agentmon/scripts/dsv41_day1_acceptance.py \
  --run-dir "$EXP_DIR/live" --finalize \
  --runtime-discovery "$EXP_DIR/live/source/v41-request-discovery.json" \
  --config-evidence "$EXP_DIR/live/env/config-reviewed.json"
```

finalize 只读已有证据，更新自己的验收报告，不运行 Docker/HTTP/model。
实际 loaded module/version/path/signature、同一容器版本、全部必需配置和 D1-G1..5 都满足时才写 DAY1_REAL_PASS。
配置证据是操作者提供的审查材料，必须核对其原始文件；程序不会自动证明材料真实性。

## 5. 先观察客户端缺口，再分析真实服务器事件

即使没有服务端 Hook，也可以看到哪些字段缺失：

```bash
"$PY" agentmon/scripts/analyze_request_binding.py \
  --client-evidence "$EXP_DIR/live/smoke/request_evidence.jsonl" \
  --output "$EXP_DIR/live/binding/client-only"
cat "$EXP_DIR/live/binding/client-only/request_identity_audit.json"
```

只有 client 文件时预期 exit 2 / BLOCKED / unmatched attempts，这是正确结果。
当后续批准的独立 0.27 adapter 真正产生事件后，再执行以下分析命令（每进程文件可重复 --server-events）：

```bash
"$PY" agentmon/scripts/analyze_request_binding.py \
  --client-evidence "$EXP_DIR/live/smoke/request_evidence.jsonl" \
  --client-report "$EXP_DIR/live/smoke/client_smoke.json" \
  --server-events "$EXP_DIR/live/logs/binding-server-events.jsonl" \
  --live-evidence --output "$EXP_DIR/live/binding/real"
```

本轮尚没有 0.27 Hook，所以最后这条是后续输入就绪后的命令，不是当前可宣称 PASS 的实验。

## 6. 服务端 producer 最小契约（复用 Event 1.0；尚未安装）

所有服务事件使用 `source=vllm_p2`、唯一 event_id、service run_id、wall/monotonic 时间。
payload 必须含 source_pid/source_host/clock_domain/process_role/evidence_source。
同机多个进程只共享经确认的 clock domain，分析器不计算跨进程时延。

| stage | role | 必需的显式证据 |
|---|---|---|
| api | api | mapping_basis=observed_header；agentmon_run_id/model_request_id、api_request_id；observed_header_fields 列出实际收到的两个字段 |
| async_llm | api | mapping_basis=observed_request_object；API→Engine 两个原生 ID，同一个真实对象转换/构造的证据 |
| engine_core | engine_core | 实际 EngineCore.add_request 的 engine_request_id，mapping_basis=observed_request_object |
| scheduler | engine_core | 显式 engine_request_id→scheduler_request_id，mapping_basis=observed_request_object；active_request_count 为当前全部未终结请求数 |

每个真实进程还须产生 `vllm.adapter.capability`：status=installed、adapter_namespace=vllm_0271、
loaded_vllm_version、signature_validation=PASS、dropped_events=0，以及相同 PID/role/scope。
finished/cancelled/timeout 继续使用原 P2 event names。one-to-many 用多条对象 alias，不覆盖旧条目。
没有 API response 时必须实际捕获 attempt ID 才能映射重试。当前 smoke 未发送 attempt header，本轮未改此行为。

只有完整 direct + HTTP 客户端真实成功报告 + runtime 来源 + 两侧进程加载 + Scheduler C1 + finished + 无孤儿/冲突/丢失证据时，分析器才允许 LIVE_BINDING_PASS。
合成测试最多是 OFFLINE_BINDING_PASS；输入 source 声明不能代替操作者核查原始证据。

## 7. Hook 部署方案与回滚（只提出方案）

1. 先审核本次 Discovery 的真实签名、ID 构造/IPC/对象字段，写独立 vllm_0271 profile。
2. 离线用目标签名对象验证仅调用原函数一次、原异常传播、观测异常隔离、writer 失败/drop 计数。
3. 决定独立验证服务或维护窗口；同一组 8 NPU 没有资源时不运行第二套 TP8。
4. 使用独立 enable/env/path，不使用旧 AGENTMON_VLLM_P2_ENABLE=1。确认 API 和 EngineCore 子进程均加载。
5. 操作者批准后才改变启动环境、重启服务、做真实 C=1，再验收 direct。
6. 回滚：恢复已备份启动环境，移除新 opt-in/PYTHONPATH，操作者在维护窗口重启；检查能力事件和新请求。

由于 profile/目标证据尚未就绪，本轮没有提供会实际注入或重启的可复制命令。
Day 3 只能在真实 engine/scheduler binding 后开始，不提前启用 KV 热路径或 Tensor 捕获。

## 8. 服务器离线回归

在装有 pytest 的现有 CPU Python 环境执行，不安装依赖：

```bash
cd ~/agent_mon-ver_hetero_state/agentmon
PYTHONPATH=src python -m pytest tests/unit/test_dsv41_day1_day2.py \
  tests/unit/test_p2_discovery.py tests/unit/test_p2_vllm_adapter.py \
  tests/unit/test_v41_profile.py tests/unit/test_server_preflight.py -q

cd ~/agent_mon-ver_hetero_state
PYTHONUTF8=1 python scripts/validate_workspace.py --suite core \
  --output "outputs/day1-day2-server-core-$(date +%Y%m%d-%H%M%S)"
```

workspace core 含 loopback fixture，不连接目标模型。现有 paper source 缺失可能继续阻塞完整 KV suite，应读报告。
wire 另需要 mini-SWE/LiteLLM/OpenAI 固定版本，当前开发机未安装它们；可在已有服务器环境按 README 的 wire 命令验收。
