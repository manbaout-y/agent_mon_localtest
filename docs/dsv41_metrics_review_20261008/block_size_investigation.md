# Block Size 差异调查 — UNRESOLVED

## 两份证据同时保留

历史交接启动配置为 block_size=128；当前用户提供的服务指标为 block_size=32、user_specified_block_size=True、_block_size_resolved=True。它们来自不同材料，当前实际启动 argv、启动脚本内容、解析后配置及 backend 修改链尚未取得。**不覆盖任何一份证据，不把 sliding_window=128 解释成 block_size=128。**

若两份数字最终都确认为相同对象的 token block 粒度，则粒度相差 4 倍。它对分配、hash 匹配和碎片的影响仍依赖真实 KV 类型和分组布局，不能先按普通 Transformer 公式换算 V4.1 所有状态。

## 逐项调查状态

| 问题 | 已有事实 | 仍需的实际证据 |
|---|---|---|
| 当前 --block-size、--max-num-seqs、--max-model-len | API max_model_len=262144；metrics label=32 | 当前容器 Cmd、进程 argv、实际入口脚本，三者相互核对 |
| CacheConfig.block_size 赋值、验证、resolved | 两个布尔标签 True | `config/cache.py`、`engine/arg_utils.py` 的字段与初始化/校验，以及全源码赋值引用；文件路径为候选 |
| user_specified=True 是否代表 CLI | 只有该标签 | 解析赋值来源；不能用名称推断来自 --block-size |
| cache_config_info 产生/更新时间 | 原始样本为 info=1 | `v1/metrics/loggers.py` 中 info 注册和更新的调用位置，配置引用是否在后续修改前/后取值 |
| blocks 和容量定义 | 13448、1301333，简单乘积不符 | `kv_cache_utils.py`、CacheConfig、group/spec、scheduler 初始化及 Ascend 平台覆盖点 |
| dtype 各状态覆盖 | 标签 bfloat16 | KV spec、group 对应 layer、worker/backend allocation；逐 Tensor 尚未采集 |
| KV offload size=None | 无迁移事件 | 配置解析/connector/offload 源码与未来运行事件；当前 NOT_PROVEN |

本地仓库没有目标 0.27.1 上述源码。已有 `kv_ana/runtime_references/dsv4_runtime_official_reference_v0221/` 只含旧 V4/0.22.1 的模型、压缩与 attention 参考，不能拿它补目标缓存管理源码的行号。

## 优先检查顺序

1. 保存当前进程实参。若 argv 明确为 32，则优先检查是否是不同启动或脚本；仍要核对最终配置/分组布局。若 argv 为 128 而 info 为 32，则追踪中间赋值或平台解析，不立刻归因。
2. 找 `CacheConfig` 和实际 backend 对 block_size 的全部赋值、验证/resolve 调用；同时看方法调用时序，静态出现一个赋值不足以证明本次执行。
3. 找 `cache_config_info` 的 exporter 定义与 `.info(...)` 调用，追踪传入对象及其是否为 engine/group/平台配置。
4. 找 `num_gpu_blocks`、`kv_cache_size_tokens`、`kv_cache_max_concurrency` 的具体公式及 group/spec 输入，确认 sliding/hybrid/专用缓存是否真的存在并参与本次计算。
5. 将 source path/line + 当前配置/argv + 本次时刻写入闭环结论。配置与源码若仍不足以证明物理布局，物理层保留 NOT_CAPTURED。

只读脚本会同时提取 vLLM 和指定 Ascend 源目录中的相关引用，避免仅追主仓库漏掉插件转换。未取得源码前，以上都是调查计划，不是已验证调用链。

当前不改 block size、不做 32/128 A/B、不重启模型。后续若确需比较，先在独立维护窗口固定其他参数、分别冻结配置并重做受影响 baseline；不是本轮操作。
