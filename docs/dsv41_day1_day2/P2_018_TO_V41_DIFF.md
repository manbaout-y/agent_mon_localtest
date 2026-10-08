# P2 0.18 → DeepSeek V4.1：当前源码差异与复用边界

检查日期：2026-10-08。当前目录是源码快照，无 `.git`；Commit/Branch 为 UNKNOWN。
本报告来自本地仓库审查，不是 vLLM 0.27.1 目标容器的在线验收。

| 分类 | 已有实现及证据 | 本轮处理 |
|---|---|---|
| 可复用 | `agentmon/adapters/agent_frameworks/mini_swe/model.py:316` 的 `inject_request_headers` | 保留 P1 run/model-call/client headers，未改模型请求行为 |
| 可复用 | `agentmon/scripts/deepseek_v41_client_smoke.py:67` 的 HTTP observer，及 `kv_ana/.../request_bridge.py:14` 的 sidecar | Day 1 包装器调用原脚本，核验真实 HTTP 200、thinking=false、两次顺序调用和一次固定工具执行 |
| 可复用 | `agentmon/events.py` 的 Event 1.0 envelope；旧 P2 `events.py` 的 `source=vllm_p2` | 分析器读取原格式，不另造服务器 JSONL stream |
| 可复用 | 旧 P2 `p2_index.py` 的 `is_p2_event`、`p2_alias_ids`；`agentmon/paths.py` 私有输出工具 | 分析器复用识别和权限实现，生成派生绑定表 |
| 可复用 | `agentmon/server/preflight.py:199/218/234` 的环境清理、关闭旧 P2 和命令超时 | Day 1 薄包装复用这些函数；不调用旧的全套 active probes |
| 可复用但须区分口径 | `runtime/p2_import.py:86` 按 run/trajectory/window 选择事件；`reporting/p2.py` 的 identity audit facade | importer 的选择质量不能代替四个真实阶段的 direct 证明，新报告更严格 |
| 需要适配 | 旧 `hook.py:600` 的 AsyncLLM wrapper，`:127` alias map，`:1125` received；`:741/809` EngineCore/Scheduler wrapper | 原 wrapper 的返回值 fallback 不能直接证明 0.27 内部 ID；新 Hook 暂不部署 |
| 需要适配 | 旧 `agentmon_p2_discovery.py` 顶部 `TARGET_VLLM_VERSION=0.18.0`，旧 module/object 候选 | 新独立 Discovery 使用目标实际 AST 和可选运行时签名；不修改旧版本门槛 |
| 需要解耦 | `inference/vllm_018/__init__.py` 原先急切导入 events/adapter/hook | 改为保持相同公开名称的按需导入，让离线 reader/version 不加载 Linux procfs/Hook |
| 尚不存在 | 经过当前 0.27.1 容器源码/签名确认的 Hook profile | 本轮不凭历史 grep 结果写可部署 monkeypatch；交付精确缺口和 producer 输入契约 |
| 尚不存在 | 当前 server 真实接收 P1 headers、attempt、API/engine/scheduler alias 证据 | 真实 Day 2 direct gate 保持 PENDING |
| 尚不存在 | 独立 API 与 EngineCore 的 0.27 Hook 安装证据、drop counter、终态与 C=1 scheduler evidence | 新分析器检测缺口，不用 API capability 冒充 EngineCore capability |
| 暂缓 | get_computed_blocks、allocation/free/eviction 热路径，rank/layer Tensor、迁移/专家映射 | Day 3+，本轮未实现 |

旧 `version.py:10` 仍只接受 `0.18.0`（允许 local suffix），`sitecustomize.py` 仍只有旧 opt-in 路径。
新程序不会设置 `AGENTMON_VLLM_P2_ENABLE=1`，不会在 0.27 上加载旧 Hook。按需导入没有改变版本检查函数或安装函数。

## 三个必须保留的身份区别

1. P1 `model_request_id` 对应模型调用。mini-SWE retry 与实际 HTTP attempt 不是同一层。
2. 现有 smoke 在 HTTP observer 中生成 `request_attempt_id`，当前不把这个 UUID 发到服务端。
   现有可行关联是客户端实际 response.id + 服务端实际观测 run/model headers + 显式内部 alias。
   若响应丢失/超时，需要真实观察到 attempt header 才能绑定该 attempt；不能仅凭 model-call 或顺序补齐。
3. 服务 stream 的 Event.run_id 是 service run；payload.agentmon_run_id 是 Agent run。分析器不混用。

## 服务端 Hook 的精确缺口及最小方案（未执行）

- API：找到实际 raw_request 与 response ID 创建点，白名单读取两个 P1 headers，记录 stage=api。
- Async/InputProcessor：在真正构造/改写请求对象后读取真实 internal ID，记录 API→Engine alias。
  不把传入 ID 的 fallback 当作改写后 ID。fan-out 每条显式保存。
- IPC：确认 CoreClient 的序列化字段、真实发送对象与 EngineCore 接收对象的 ID 含义；不猜 transport。
- EngineCore：实际 add_request 调用处记录 stage=engine_core/native engine ID。
- Scheduler：实际 add_request 处记录 Engine→Scheduler alias，补充本次观测全部未终结请求数。
- API 与 EngineCore 各自安装，输出现有 Event envelope 中的 capability（PID/host/clock/version/path/signature/drop）。
- 仅在真实签名 profile 校验通过且单独 opt-in 时安装；包装器必须调用原函数一次，原异常原样传播；观测异常隔离。
- 不改变原请求对象、输入 token、调度或返回值；不安装任何 KV 热路径 Hook。

上述方案依赖真实 Discovery。这里没有可执行注入命令，当前 `sitecustomize.py` 也未新增 0.27 分支。
