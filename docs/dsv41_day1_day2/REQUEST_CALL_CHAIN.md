# REQUEST_CALL_CHAIN — 本地确认部分与目标容器待确认部分

本报告状态：`TARGET_SOURCE_NOT_RUN / REAL_ACCEPTANCE_PENDING`。
本地已有历史 V4/0.22 引用，不能作为本次 0.27.1 的真实源码。当前没有服务器连接或导出源码。

## 本地已经确认的路径

```text
InstrumentedModelMixin.query
  → inject_request_headers (mini_swe/model.py:316)
  → LiteLLM / httpx.Client.send
  → deepseek_v41_client_smoke.observed_send (:67)
  → kvreuse_b5.state_research.request_bridge.request_from_wire (:14)
  → response.json().id 写入 request_evidence.jsonl
```

HTTP 实际目的地由原 smoke 限定为 `http://127.0.0.1:8800/v1/chat/completions`。
上述路径证明客户端 wire evidence 的生成方式，不证明服务器已收到 header 或生成了哪个内部 ID。

## 必须由目标源码回答的八项问题

| 问题 | 当前证据状态 | 新 Discovery 应提供的入口候选 |
|---|---|---|
| API response ID 在哪里创建、是否有 alias | UNKNOWN | Chat Serving、base request ID helper |
| P1 header 是否被实际读取 | UNKNOWN | raw_request/header 到 engine.generate 的源码 |
| AsyncLLM/InputProcessor 如何改写或拆分 ID | UNKNOWN | generate/add_request/process_inputs/assign_request_id |
| CoreClient 的 IPC 接口、序列化及 ID 保留 | UNKNOWN | core_client 实际 class/method |
| EngineCore 传给 Scheduler 的真实对象 | UNKNOWN | EngineCore.add_request |
| Scheduler 原生 ID 是否变化 | UNKNOWN | Scheduler.add_request、Request |
| API/EngineCore 的启动与加载覆盖 | UNKNOWN | 导入环境、进程 capability 和实际 PID |
| retry/fan-out/cancel/timeout 如何关联 | UNKNOWN | 上述源码中的拆分/取消分支及真实事件 |

入口只是定位候选；缺文件、缺方法或 import 错误会原样记录，不能把候选写成确认路径。

## 生成真实源码报告

`agentmon/scripts/dsv41_request_discovery.py` 能经容器 Python stdin 运行，不需要复制或安装 AgentMon。
默认只用 AST 读源码；显式 `--runtime-introspection` 才导入目标 vLLM/Ascend、检查实际包路径与方法签名。
CoreClient、API、AsyncLLM、InputProcessor、EngineCore、Scheduler 的必要签名不完整时报告 PARTIAL_OR_BLOCKED。
即使签名观测成功，状态仍为 `SIGNATURES_OBSERVED_REVIEW_REQUIRED`，需要读分支确认 ID 流转。

从保存的 JSON 执行 `--render-snapshot`，会在新的目录生成：

- `v41-request-discovery.txt`：实际 Python、版本来源、模块路径、AST/运行时签名及缺项；
- `REQUEST_CALL_CHAIN.md`：实际文件/行号、签名、ID 相关源码行和待审核问题。

它不会自动补写“IPC 保持 ID”“Scheduler ID 等于 API ID”等语义结论，也不会安装 Hook。
