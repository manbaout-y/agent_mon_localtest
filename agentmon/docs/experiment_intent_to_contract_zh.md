# 实验意图到快速冒烟 Contract

本文说明如何把自然语言实验意图转成 AgentMon 服务器快速冒烟输入。目标不是让用户填更多
底层参数，而是把流程分成：

```text
自然语言意图
→ experiment_request.json
→ server quick-smoke discovery/synthesis
→ quick-smoke-contract.json
→ Resource/Service/Runtime gates
→ 1-task smoke / pilot
```

核心边界：

```text
LLM 生成意图，不生成服务器事实。
Discovery 生成事实，不猜测实验意图。
Contract 合并二者，并标注来源。
Gate 只信 evidence-backed 字段。
```

## 用户只需要表达什么

用户可以用自然语言写：

```text
我想用 Qwen3-Coder-30B，在 2 张 Ascend 910B 上跑 bench120 v2 的 AgentMon
mini-SWE 快速冒烟。任务包在 /data/node0_disk3/chenyang/ver_0730/dist.zip。
先跑 py-001，pilot 跑 py-007、py-036、py-060。要求 P1/P2 证据完整，
服务必须支持 tool calling，启动失败 60 秒内要发现。
```

网页版 LLM 可以把它整理成
`examples/experiment_requests/qwen3_2card_agent_smoke.json` 这种 request。request 里允许写：

- 实验名称和 intent；
- benchmark zip；
- smoke/pilot task id；
- 希望使用几张卡、优先设备、优先端口；
- 模型路径和 served model；
- 是否要求 tool calling、P2 runtime gate、mini-SWE 非交互、startup watchdog。

request 不应凭空填写：

- `--tool-call-parser`；
- prefix cache 的具体 CLI 负向参数；
- vLLM 实际版本、真实 parser registry、真实端口占用；
- mini-SWE 隔离 `HOME/XDG_CONFIG_HOME` 后需要的初始化文件；
- AgentMon resolved config 的最终值。

这些必须由服务器上的 discovery、配置合成和 gate 生成证据。

模型名到 tool-call parser 的候选映射位于 server profile 的
`preflight.tool_call_parser_candidates`，不在 quick-smoke 通用代码中。匹配结果仍只是 `candidate`，必须由
Tool-call Gate 验证真实服务响应后才能成为机制已激活证据。

上面的任务 ID 只是 request 示例，不是 AgentMon 默认值。用户没有指定 smoke task 时，quick-smoke 会从
`benchmark_zip` 及其中嵌套的 runtime tar.gz 查找 `tasks/<task-id>/`，按字典序选择一个确定性候选，并在
`discovered.smoke_tasks` 记录来源和可发现任务数。zip 不可读或没有任务目录时返回
`smoke_task_unresolved`，必须修正 benchmark 或显式提供 `--smoke-task`，系统不会猜测固定任务 ID。

## 生成快速冒烟实例

先完成 Package/Source Gate 和 Static Preflight。然后用 static preflight JSON 作为
prefix-cache CLI 发现证据：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$AGENTMON_PY" \
  "$AGENTMON_REPO/scripts/agentmon_server_quick_smoke.py" \
  --request "$AGENTMON_REPO/examples/experiment_requests/qwen3_2card_agent_smoke.json" \
  --server-profile "$AGENTMON_REPO/examples/server_profiles/chenyang_ascend910b.json" \
  --source-root "$AGENTMON_REPO" \
  --static-preflight-json "$PROVENANCE_ROOT/static-server-preflight.json" \
  --output-root "$AGENTMON_HOME/quick-smoke"
```

输出：

```text
STEP=QUICK_SMOKE_PREPARE
PURPOSE=synthesize one isolated server smoke instance without starting vLLM
STATUS=PASS|PARTIAL|FAIL
REASON=pass|blocking_reasons_present|...
NEXT=RESOURCE_GATE|STOP
PASS_CONDITION=contract status PASS and blocking_reasons empty
JSON=.../quick-smoke-prepare.json
SUGGESTION=...
QUICK_SMOKE_PREPARE=PASS|PARTIAL
OUTPUT_DIR=...
CONTRACT=.../quick-smoke-contract.json
```

主要产物：

```text
runtime-instance.env
resource-gate.sh
resource-gate.json
start-vllm-p2.sh
service-start-gate.sh
service-start-gate.json
agentmon_config_runtime.toml
mini_model_runtime.yaml
tool-call-probe.json
tool-call-gate.sh
tool-call-gate.json
mini-swe-noninteractive-probe.sh
startup-watchdog.sh
quick-smoke-contract.json
quick-smoke-prepare.json
```

`PARTIAL` 不代表源码坏了，表示仍有不能猜测的协议事实缺证据。例如：

```text
prefix_cache_disable_args_unresolved
tool_call_parser_unresolved
```

此时不要启动正式实验。补充 static preflight JSON、显式 discovery 结果或人工确认后重跑
quick-smoke prepare。

## 阶段输出协议

后续 gate 都必须按同一组行输出，避免把冒烟做成黑盒：

```text
STEP=<当前阶段>
PURPOSE=<这一步验证什么>
STATUS=PASS|PARTIAL|FAIL
REASON=<机器可读原因>
NEXT=<下一阶段或 STOP>
PASS_CONDITION=<通过条件>
JSON=<本阶段机器可读报告，若有>
LOG=<日志路径，若有>
SUGGESTION=<失败/部分通过时的修复建议，若有>
```

`STATUS=PASS` 才能进入 `NEXT`。`STATUS=PARTIAL` 不是源码失败，表示还有必须由
服务器事实或人工确认补足的内容；此时 `NEXT=STOP`，不要启动正式实验。每个脚本仍保留
兼容标记，例如 `RESOURCE_GATE=PASS`、`SERVICE_START_GATE=PASS`、
`TOOL_CALL_GATE=PASS` 和 `STARTUP_WATCHDOG=PASS`。

## Contract 里如何区分事实和候选

`quick-smoke-contract.json` 中：

- `experiment_request`：用户/LLM 生成的意图；
- `discovered`：来自 profile、static preflight 或启发式候选的证据；
- `synthesized`：生成后的本轮实例配置；
- `config_consistency`：AgentMon runtime TOML、mini-SWE runtime YAML 和目标 Base URL
  是否一致；
- `blocking_reasons`：缺少哪些不能猜测的事实；
- `limitations`：哪些结论还必须等 gate 验证。

例如 Qwen3-Coder 的 `qwen3_coder` parser 可以由模型名启发式生成候选，但只有
tool-call gate 返回 `finish_reason=tool_calls` 且 arguments 可解析，才能证明服务协议可用。

## 后续 Gate

quick-smoke prepare 通过后，继续使用同一个 `runtime-instance.env`：

```bash
set -a
. /path/to/quick-smoke/runtime-instance.env
set +a
```

然后按生成脚本继续。每一步都可以单独执行，失败时先看 `REASON`、`SUGGESTION` 和
`LOG`，不要跳关：

```text
Resource Gate
→ Service Start Gate
→ Tool-call Gate（要求工具调用时）
→ Runtime P2 Gate
→ 1-task startup watchdog smoke
→ pilot
```

推荐命令：

```bash
RESOURCE_GATE_MANUAL_DEVICE_CONFIRM=YES /path/to/quick-smoke/resource-gate.sh
/path/to/quick-smoke/service-start-gate.sh
/path/to/quick-smoke/tool-call-gate.sh
/path/to/quick-smoke/mini-swe-noninteractive-probe.sh
/path/to/quick-smoke/startup-watchdog.sh
```

常见失败和处理：

```text
prefix_cache_disable_args_unresolved
  含义：要求关闭 prefix cache，但没有来自 static preflight 的真实 vLLM 参数。
  处理：传入 static preflight JSON，或显式传 --prefix-cache-args；不要猜参数名。

device_isolation_unconfirmed
  含义：端口和路径可用，但工具不能证明 TARGET_DEVICES 没有被旧服务占用。
  处理：查看 resource-gate 的 LOG 中 npu-smi 证据，确认后用
  RESOURCE_GATE_MANUAL_DEVICE_CONFIRM=YES 重跑。

model_not_listed
  含义：服务进程还活着，但超时前 /v1/models 没列出 SERVED_MODEL。
  处理：看 vllm-server.log，确认模型路径、模型名、端口和启动耗时；不要误连旧服务。

tool_call_not_returned
  含义：服务可响应 chat completion，但没有返回 tool_calls。
  处理：确认 start-vllm-p2.sh 包含 --enable-auto-tool-choice 和正确
  --tool-call-parser；重启服务后重跑 gate。

events_missing / no_p2_events_yet
  含义：Runtime probe 或 smoke 请求没有在本轮 events.jsonl 产生 P2 事件。
  处理：确认请求发往当前 VLLM_BASE_URL，AGENTMON_EVENTS_PATH 与启动脚本一致，
  并查看 service log 中 hook 安装状态。

smoke_task_unresolved
  含义：request 没有指定 smoke task，benchmark zip 中也没有发现可用任务目录。
  处理：确认 zip 路径和内容，或显式传 `--smoke-task <task-id>`；不要在通用脚本中写死任务 ID。
```

如果启动脚本、端口、设备、模型名、prefix-cache 参数或 tool-call 参数发生变化，旧的
Runtime P2 Gate 自动失效，必须重新跑 gate 并保留新的 JSON/console log。
