# Request Binding 与 KV 生命周期开发方案

适配目标已补充到 [DSV41 环境基线](DSV41_ENVIRONMENT_BASELINE.md)：8×910B1、TP8/EP8、vLLM0.27.1+empty、指定 Ascend patch 和 Python3.12.13。该清单同时包含不同历史 run，当前服务状态 NOT_VERIFIED；实际安装 profile 仍必须由目标源码和加载路径确认。开发目的明确为请求负载与运行状态观测。

## 现有实现与未证明环节

客户端代码 `mini_swe/model.py:316` 的 inject_request_headers 已有 run/model-call headers。`deepseek_v41_client_smoke.py:67` 的 HTTP observer 保存实际请求证据；`:83` 从真实 response JSON 取 api_request_id。request_attempt_id 在 observer 产生，当前没有发送到服务端。只有返回的客户端响应 ID，没有目标服务器的 ID 改写和 IPC 材料，不能确认 API/Engine/Scheduler 对应关系。

本次只有 smoke PASS 摘录，没有完整 client sidecar。因此不能逐行复核该 run 的四个客户端 ID，更不能假设普通 curl 的 `chatcmpl-ad5dd3c06ca95045` 就是 mini-SWE 两次调用之一。现有 `analysis/request_binding.py` / `scripts/analyze_request_binding.py` 可直接审计真实 sidecar 与服务端 Event 1.0，不需要另写一个靠时间猜配的 join。

| 链路 | 当前状态 | 缺口 |
|---|---|---|
| Agent run → model call → HTTP attempt → API response | USER_PROVIDED_OUTPUT smoke PASS；文件审计 NOT_CAPTURED | 原始 client_smoke.json、request_evidence.jsonl 及异常尝试 |
| API headers/response ID → Async/input 内部 ID | NOT_CAPTURED | 实际 header 读取、ID 生成/改写对象以及 fan-out |
| Async → CoreClient IPC → EngineCore | NOT_CAPTURED | 序列化字段、跨进程 ID 传递，不能假设 trace headers 全部保留 |
| EngineCore → Scheduler | NOT_CAPTURED | 真实 native ID/对象，add_request 与终态 |
| Scheduler request → KV group/block/ref | NOT_CAPTURED | 成功分配/挂接/释放的对象和引用来源 |

## vLLM 0.18 P2 兼容性核查

已核查本仓源码，保留 [原兼容报告](../dsv41_day1_day2/P2_018_TO_V41_DIFF.md)；没有修改旧安装入口、版本门槛和 sitecustomize。

| 可核查事实（LOCAL_SOURCE） | 0.27.1 的风险/待验证内容 |
|---|---|
| `vllm_018/version.py:10` 只支持 0.18.0；旧 discovery.py:25 同样固定旧版本 | 0.27.1+empty 被拒绝是预期；不能改常量跳过门槛 |
| hook.py:954 假设 get_computed_blocks 返回 tuple，可从 result[0/1] 取 blocks/token count | 新版返回结构、group 列表、cached-token 语义需实际签名和源码 |
| hook.py:979 从 allocate_slots 返回值计数 | 返回 None/部分分配、已有块挂接与新块分配可能不同；不能从返回数量补 Block 身份 |
| hook.py:1039 free 前读取 request blocks；:1071 包装 manager.evict_blocks | 真实释放、refcount 到零、缓存保留/淘汰可能发生在不同层；目标 evict 方法是否存在未验证 |
| events.py:423 的 kv_block_event 只有数量和 usage，不存 Block 身份/refcount | 数量事件可复用为汇总，不足以满足真实生命周期要求 |
| hook.py:1717/1781 读取旧 Stats.prefix_cache_stats、kv_cache_usage | Stats 类型、字段、累计/增量/reset 与新版兼容性待核验 |
| 旧 wrapper 通常调用原方法并捕获观测异常 | 不能推出新 profile 的 once-call、异常透明、多进程安装已经验证 |

可复用：Event envelope、私有输出、P1 headers、sidecar、alias/index helpers、严格 binding 分析器。需新建独立 `vllm_0271` profile/adapter，不能让新版本回退到 0.18 wrapper。当前新 adapter、Hook、Block schema 实现均未提交；本轮提交的是调查工具与设计。

## 阶段 A：当前服务只读证据闭环

执行 SERVER_RUNBOOK 的采集工具，拿到 metadata/源文件/行号、argv 和配置快照，解释 128/32。AST 成功不等于 loaded runtime 一致；之后仍需要原 discovery 的显式 runtime-introspection 校验版本/路径/签名（独立进程，有导入副作用风险，操作者决定；本次默认命令不导入）。冻结源码 SHA、dirty patch/源码内容与 Ascend 实际来源，不能只用 fingerprint 当 commit。

完成标准：actual source chain + 当前配置证据，缺失项仍列出，不用候选路径填 PASS。

## 阶段 B：最小 Request Binding（后续开发，当前不注入）

1. 基于实际调用链建立 0.27.1 白名单 profile：包 release/实际加载路径、方法签名、ID 对象字段、API→IPC→Scheduler 边界。API 与 EngineCore 进程分别安装并记录 capability/version/path/signature/drop。
2. 复用 Event 1.0，记录 service run、agent run、model call、实际收到的 attempt（若有）、api/engine/scheduler native ID、stage、pid/host/process role、clock domain、单调时间、来源位置和 correlation quality。缺失字段用 null + NOT_CAPTURED，不从请求序号或相同字符串补造 alias。
3. API 边界读取真实 run/model headers 与 API ID；内部 ID 改写完成后读取实际 request object；IPC 发送/接收必须核对同一序列化身份，Scheduler 接收边界读取 native ID。API fan-out 多条保留；response 丢失且未实际观察 attempt 时保持不能唯一绑定。
4. terminal finished/cancelled/error/timeout 对齐；同 ID 的跨 service epoch/进程 scope 不混合。异步引用需要真实对象传递/序列化证据，不在不兼容对象上添加自定义字段。
5. 实现只观察、不改原函数参数/返回值/调度对象。观测错误隔离、有界 queue、drop counter 明确。丢事件导致验收不完整，不能静默降级为完整 direct。

离线断言：真实签名不匹配拒绝安装；原函数只执行一次；返回对象及异常保持；观测/写盘失败不影响请求；API/EngineCore 加载覆盖；retry/fan-out/取消/响应丢失；跨 scope、孤儿和冲突拒绝 direct。复用已实现 binding tests，再增加真正 adapter wrapper 的模拟测试。

仅在独立环境获准后执行 C=1 的两次短调用；已有 analyzer 联合真实 client report/server stream 验收。不能用 synthetic runtime 标签伪装 live。

## 阶段 C：请求关联的 KV 生命周期（后续开发）

先确定 BlockPool/各 KV group 管理器真实对象与操作位置，再实现低频边界观察。lookup 命中候选不一定被使用；reuse 应在成功挂接到请求/引用增加处证明。allocate_slots 的新增块、已有块和命中块应分别记录。

拟议 payload（扩展 existing Event 的新命名空间，**不是当前实际采集值**）：

| 字段 | 来源/要求 |
|---|---|
| operation | allocate / reuse / release / evict；request free 只代表 release，不能自动等于物理释放 |
| native request IDs、binding quality、binding event refs | 阶段 B 的实测关联；全局 eviction 可无 request，不能硬归给最近请求 |
| service/pool epoch、group ID、实际 block ID | 真实管理器/pool 对象；未证明则 null/NOT_CAPTURED |
| slot/content generation | 解决 block ID 复用；若运行时无 generation，可用 observer 分配周期编号，但必须标注 OBSERVER_ASSIGNED，不宣称模型原生字段；缺事件即无完整性 |
| request↔block attach/detach、refcount before/after | 实际读到的引用关系，不用 pool usage 差值反推 |
| producer/consumer、logical token range | 有实际对象依据才记录；prefix lookup 不能证明 Tensor 内容消费 |
| pid/rank/host/clock/time/event sequence、source path/profile | API 通常无 worker rank，不能给它填 rank=0；调度 group 不等于 worker rank/layer |
| physical allocation、logical view、artifact bytes、device/layer/shape/dtype | 分开记录；Scheduler 观测不到的字段 NOT_CAPTURED，后续 Worker/schema 阶段补充 |
| coverage/drop/overflow、collection phase | 有界 batch；截断/采样输出不通过完整生命周期验收 |

release 后 refcount 到零不必意味着内容失效；缓存可保留至 evict，evict 又可能只是逻辑内容失效而非 HBM pool 归还。需基于目标源码定义，不能用统一 free 事件替代。

离线验收覆盖共享块、多请求引用、取消释放、抢占/重算、缓存淘汰、槽位复用、reset、分组不同粒度、异常和缺事件。禁止 per-token 全 Tensor dump；先批量记录请求边界的实际 Block 列表，评估 hook 开销/队列溢出。哈希/token/prompt/物理地址默认不输出，敏感字段按需要单独审查。

## 后续独立实验与回滚（方案，当前不执行）

维护者批准后在隔离环境建立基线，启用新 profile 的 opt-in、私有输出目录和负载预算；签名/版本不匹配即不安装。比较 off/on 的生成正确性、请求终态、TTFT/TPOT/JCT 和 drop/开销。原函数异常透明、输出一致只是适配安全验证，不等于 StatePair 恢复/迁移 functional safety。

回滚为关闭新 opt-in、恢复原镜像/启动配置，由维护者在同一隔离窗口重启；不会在本轮在线容器中热卸载包装器。正式 C=2/4/8/16 或改变 serving 参数需要重新冻结配置与受影响 baseline，不能从容量指标推测可支持 10–16 个 256K 请求。
