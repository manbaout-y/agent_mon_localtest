# 状态研究方案与实现复核 v1.0.1

2026-09-22。审计范围为第一批 LifeMem/关联模块及新增第二、三阶段离线扩展；对冻结
framework 全量运行回归，不重新实现论文算法。对照用户立项讨论稿的三个研究目标核对
实现边界。没有修改该 Word 文档、AgentMon 或模型 runtime。冻结核心有一处已证明
correctness bug 的最小例外：仅规范 artifact provenance 的集合排序，详见下文。

## 结论

原 238 项测试通过，但漏掉了会影响研究结论的组合场景。已修复下面问题并新增对应反例。
另纠正“离线阶段已完成”的解释：已交付的是实验工具与对照基线，不能表述为三个研究
技术点已经实现并证明有效。真实采集、V4.1 接入、在线执行及功能/性能验证仍未完成。

## 可复现问题与修复

| 问题 | 影响 | 修复 |
|---|---|---|
| context_version 兼任 state 内容版本 | 同 prompt 索引刷新被误记成上下文变更，跨请求共享被错误限制 | 独立 content_version；共享内容同步、各 view 上下文分开；旧混用输入明确降级 |
| 迁移更换物理地址就算新内容 | 实际 runtime 的迁移决策污染替代策略 miss 统计 | 稳定 replay_object_id；迁移保留内容 generation；保留真实地址证据 |
| producer 改变但依赖消费无提示 | 可能把未刷新的依赖工作集当完全可验证 | 记录 dependency revalidation 要求，suite 显示 PARTIAL，不能当安全结论 |
| runtime event 能覆盖 synthetic state 来源 | synthetic 证据可能被标为 runtime | action/state 来源一致；递归工作集保留所有 state_sources |
| 成本 key 丢弃 layer/ratio/variant/parameters | 不同方法或粒度混成无意义的中位数 | 完整 scope + operation variant/parameters 隔离，runtime 契约缺字段阻塞 |
| corrected state 自身坐标未验证 | 错行修复也可报告高 explained | 三方独立显式 canonical positions；真实输入缺字段拒绝 |
| AgentMon alias 跨 service run 共享 | 重启后的 engine ID 被关联到旧请求 | 按 service run 隔离 bridge；保留未知/歧义/冲突 |
| session 没有 dataset namespace，空白 ID 串联 | 不同数据集/未标会话混在一起 | 命名空间身份，空白会话隔离，保留原 conversation_id |
| 状态与请求只按 ID 对齐 | 错模型/错上下文状态可以进入“通过”报告 | 第一批和 suite 都校验明确 model/context；重复请求不静默覆盖 |
| 无 consume 或空 policy 也能通过 | 没有做实验却获得 PASS | 分配不等于访问；无消费标缺输入，空/重复策略拒绝 |
| 单个坏输入丢弃同阶段其他结果 | 故障隔离与审计不足 | per-policy/per-case 记录失败，保留成功结果，整体仍 FAIL |
| capsule 配置可突破硬上限 | 无法按服务器文本边界回传 | 写输出前限制 512–45000 UTF-8 bytes；报告显式标证据来源 |
| 核心 frozen artifact hash 包含无序 frozenset repr | 同输入跨进程有不同 artifact_hash，破坏可复现的 provenance | 仅在已有 hash 调用前排序 fit_pair_ids，SVD/算法/方法配置哈希不变 |

其它修复包含：同 allocation 的新 view 必须显式 share；释放/重建不继承旧访问距离；
同一次 demand 不接受一个对象的多版本；snapshot 写 NPZ 前校验 metadata，避免坏 binding
留下不可解释的半成品；不同类型 view 不重复改变物理预算归属。

## 数值影响边界

本轮会改变**新状态研究回放 baseline** 中被错误计费的案例：原小 fixture 的 3 hits / 4 misses
修正为 4 hits / 3 misses，消除了等内容迁移导致的伪 miss。这是新增工具的 correctness 修复。
不修改既有 B1–B5、AgentKVShift、CacheSlide 数学实现；不把本次回放结果写入论文 tournament。

### 冻结核心最小例外

在输出对照中发现：448 条 framework 记录有 98 条仅 artifact_hash 不同，summary 和成本
完全相同。源码原因是 `execution/artifacts.py` 将 frozenset 的 repr 纳入 hash，受
PYTHONHASHSEED 影响。修复仅把这一个参数改为 `tuple(sorted(fit_ids, key=repr))`，没有增加
新哈希子系统，也没有更改训练集合、basis、rank 或投影。新增三个不同 Python hash seed
进程的一致性测试及 basis 与直接 SVD 完全相同的测试。

framework 版本仍为 2.0.1，扩展补丁为 state-research-offline-v1.0.1。旧 checkpoint 不被
覆盖或迁移；若需要规范化后的 artifact provenance，使用新输出目录重跑对应 CPU 实验。
不应把旧文件的旧 hash 和新规范 hash 相等当作验收条件；数值、method_config_hash 和 split
仍按原口径检查。本次将此改动作为唯一核心文件例外单独审计。

## 与立项稿三个技术点的真实关系

| 技术点 | 当前有的 | 仍然没有的 | 下一道证据门 |
|---|---|---|---|
| State ↔ Resource | 容量/来源核算、固定 trace LRU/类型分区 baseline | 边际收益估计、动态预算优化和实际调控 | 真实访问/成本/压力；对比调优静态基线，计入迁移和控制成本 |
| State ↔ State | 显式依赖、共享 view、联合逐出 baseline、依赖变更提示 | 运行时跨步继承、分支/恢复执行和联合复用收益证明 | 核对真实 producer-consumer graph；验证工作集减少重复生成或搬运 |
| Old ↔ New State | 文本/token 变化、明确状态映射、外部证据下的成本比较、supplied-state SSE | 模型专用修复器、执行器、质量有效性验证 | 对比最低替代恢复成本，确认 logits/next-token/输出质量 |

这些环节互相提供信息，不是必须依次完成的三个独立优化算法。本工具只把离线分析流程
串起来，没有创建“万能 KV Manager”。尤其固定需求回放保留观测 device 放置，不是任意
跨设备缓存策略的反事实仿真；work/bytes/hit 不等于 latency。

## 仍需真实环境的事项

1. 全类型状态采集 hook 与对象覆盖证明：现有 compressed_kv dump 不等于完整 live cache。
2. DeepSeek-V4.1 runtime 身份、实际布局、共享/索引/窗口依赖，当前不从 V4 推导。
3. 同条件恢复成本试验；不能拿 synthetic 成本或声明 VALIDATED 替代外部证据。
4. 真实输入上的离线校准/回放与状态评估；目前本轮实际运行的是 synthetic。
5. 单请求功能检查通过后再验证在线计划执行、并发、TTFT/TPOT/P99；本轮不启动模型。

## 验证记录

精确测试数量、smoke、冻结文件检查和便携包检查见 `STATE_RESEARCH_OFFLINE_VALIDATION.md`
中的 v1.0.1 审计记录。原输出不覆盖，审计修复输出使用独立目录。
没有声称发现的输入契约能自动证明上游模型语义；缺少真实证据仍是明确边界。
