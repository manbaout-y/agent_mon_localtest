# DSV41 Hook 目标环境基线与观测目标

来源：用户在本次对话提供的环境清单。记录于 2026-10-08；没有对应完整环境快照附件，也没有本轮服务器复核。**这是适配目标和历史证据，CURRENT_RUNTIME_STATUS=NOT_VERIFIED。** 不能自动更新原 live/source 验收状态。

## 环境清单（USER_PROVIDED，当前运行待复核）

| 项目 | 用户提供值 |
|---|---|
| 项目 | DeepSeek V4.1 Single-Node Ascend Runtime |
| 硬件 | 8×Ascend 910B1；每卡 HBM 65536 MB；192 logical CPUs；1.5 TiB host RAM；aarch64 / Kunpeng 920 |
| Host Driver / Firmware | 26.0.rc1 / 9.0.0.0.205 |
| Host CANN evidence | 9.0.0；V100R001C10SPC001B250；不能自动认定为容器进程实际加载 CANN |
| Docker isolation / image | 独立 daemon `unix:///run/docker-v41.sock`；`dsv41-single:w4a16-engram-offload` |
| 历史容器 | `dsv41-smoke`；另有先前用户 live 材料中的 `dsv41-smoke-256k`，不能视为同一运行实例 |
| 容器 Python | `/usr/local/python3.12.13/bin/python3` |
| vLLM / Ascend | `0.27.1+empty` / `0.1.dev5097+ge43cf1e9f`；Ascend revision prefix `e43cf1e9`，完整 SHA/dirty patch/实际加载路径待核验 |
| Model | `DeepSeek-V4.1-Flash-W4A16-Engram-AutoRound`；served name `deepseek-v41` |
| 模型根目录 | Host `/data/node0_disk2/chenyang/models/`；Container `/models/`，具体挂载/当前路径需核对 |
| API | bind `0.0.0.0:8800`；port publishing NOT_VERIFIED；bind 地址不等于宿主机 endpoint 可达证明 |
| 并行 | TP=8；EP enabled / 8 ranks；PP rank0 observed；DP rank0 observed；不能从单个 rank0 观测推断完整 PP/DP 拓扑 |
| MoE | 384 global experts；每 EP rank 48 local experts；linear placement；实际 global/local expert 映射未采集 |
| 量化 | `--quantization ascend`；W4A16；Ascend ModelSlim；不代表 KV dtype=int4 |
| Engram | enable_engram=true；enable_engram_ple_offload=true；engram_storage=w4a16；不是 KV Offload 证据 |
| 内存配置 | gpu_memory_utilization=0.90；历史 block_size=128；最新用户提供 metrics label=32，差异 UNRESOLVED |
| 初始 serve | max_model_len=131072；max_num_batched_tokens=4096；max_num_seqs=1 |
| 后续长上下文 | max_model_len=262144；约245K validation recorded；此次清单未附精确验证 artifact，不能混成初始 run |
| 模型加载 | multithread=true；num_threads=128；safetensors strategy=lazy；48/48 shard progress complete；weight loading 615.33s |
| 编译 | cudagraph_mode=FULL_DECODE_ONLY；enable_npugraph_ex=false；enable_static_kernel=false；OOT custom compilation backend observed |
| Tokenizer/parsers | tokenizer_mode=deepseek_v41；reasoning_parser=deepseek_v41；tool_call_parser=deepseek_v41；enable_auto_tool_choice=true；trust_remote_code=true |
| Warning | CANN/HDK 32-padding allocation warning；Transformers rope_parameters unrecognized keys；没有证据表明前者导致 block_size=32 |
| September23 snapshot | 容器 running、8 workers、weights loaded，但检查时8800未监听；权重完成不等于 API ready |
| 当前状态 | NOT_VERIFIED；先前另份 live 输出 PASS 保留，不用本清单替代其运行身份/时刻 |

不混入 Qwen2.5 / verl-cann90 环境。硬件、Python、vLLM 和 Ascend 版本约束加入新适配 profile 的审核输入；版本字符串一致仍不能代替目标函数签名/字段/补丁内容一致。

## 明确的开发目标

目标是在这套 DSV41 环境中适配观测 Hook，获取 **Agent 请求负载如何进入服务、如何被调度、产生和使用哪些运行状态**。第一版只观测，不改变调度、缓存保留策略或模型计算。旧 `vllm_018` 保留；新建独立 `vllm_0271` 实现并复用已有事件、身份分析和输出设施。

| 观测层 | 需要获取 | 实现边界 |
|---|---|---|
| Client/API | run/model call/attempt/API ID，实际模型、token usage、请求终态 | 保留已有 client observer；attempt 当前不发服务端，丢响应不能强行绑定 |
| API→Engine→Scheduler | 显式 ID alias，接收/排队/首次调度/完成/取消；pid/role/clock/version/path | 优先完成 request binding；跨进程 IPC 的真实字段先确认 |
| 负载与执行阶段 | running/waiting，队列等待，实际 scheduled token counts，prefill/decode/抢占 | 服务 gauges 已有采样器；请求阶段/数量依据真实 scheduler 对象，不能由日志吞吐反推 |
| KV 逻辑生命周期 | group/block 身份、request attach/detach、复用、引用、淘汰和 reset | 真实 manager/pool 操作；数量事件不证明身份和 ownership |
| Worker/物理状态 | rank/layer/spec/dtype/shape、实际分配/位置、状态生产消费 | 后续阶段；pool Block ID 不等于每张卡的 Tensor 地址；Engram、KV、expert 权重分别分类 |
| 资源与性能 | NPU HBM/Host memory，TTFT/TPOT/JCT、queue/migration wait | 优先复用已有 sampler；指标口径与时钟必须核验，物理字节不能由 KV usage 直接换算 |

张量原文、token IDs、attention/query 等采集属于后续可选证据范围，必须先确定捕获点、完整性和输出开销。第一版不靠抓取全部 Tensor 证明生命周期，也不从线性 expert 配置推断实际路由/访问。

## 下一步落地顺序

1. **当前在线服务只读采集**：核对当前容器究竟是哪个、实际 argv/包路径/源码、128/32 和 KV group 公式；采集工具及命令见 SERVER_RUNBOOK。
2. **0.27.1 请求 Hook 的代码与离线验证**：拿到目标源码后固定方法 profile，实现跨 API/Engine/Scheduler 身份与终态；原函数执行一次、异常透明、观测故障隔离、多进程覆盖与 drop 检测。
3. **操作者批准的隔离 C=1 验收**：比对真实 sidecar/server events 和原生成结果，确认请求绑定，再增加低频 KV 生命周期事件。
4. **物理状态与并发负载实验**：补 worker/rank/state schema 和正确性；实际 max_num_seqs 与参数先冻结，再决定 C=2/4/8/16。当前不重配在线服务。

本清单不会自动将旧 block_size128 写进新 Hook，也不会把 `max_num_seqs=1`、PP/DP、CANN 等历史/局部观察当作当前已验证值。新适配在版本/路径/签名不匹配时拒绝安装，未知能力标注 NOT_CAPTURED。
