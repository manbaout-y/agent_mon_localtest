# 第一批：LifeMem 上下文与异构状态研究数据底座 v1

## 目标和验收边界

本批把已有 LifeMem 检索结果变成可审计的研究输入，接通未来 AgentMon / runtime
事件所需的关联契约。交付可运行的离线准备器、样例、测试和质量报告。
不把字符重复率解释为 KV 命中，不合成缺失的模型 token，不把静态数据变成真实时间线。
framework 版本保持 2.0.1；新增模块独立版本 `state-research-batch1-v1`。

## 对研究目标的贡献

| 研究点 | 本批交付 | 后续实测依赖 |
|---|---|---|
| 资源优化 | 按请求顺序的 memory 再出现距离、显式构造/观测的回放清单 | 各类型占用、访问、恢复成本、并发实测 |
| 联合复用 | 状态类型目录、共享实例和生产/消费契约、AgentMon 关联检查 | 模型实际依赖、共享绑定及缓存执行 |
| 选择性修复 | 上下文版本、严格定位、token 对齐、前后映射、代表案例 | A/B 状态、partial forward、功能与成本验证 |

## 工作包

1. **输入契约与导入**：接受原始 LifeMem JSON / JSONL（`results[]` 和
   `retrieval_metadata.formatted_context`），以及可选 `research` sidecar。
   保留原始文本；完整 prompt 仅在显式提供时使用。重复定位不任意选一个。
2. **对齐与变化**：显式记录字符和 token 两种坐标。真实 token 仅接受调用方提供的
   token IDs / offsets / tokenizer identity。支持无需模型的 tokenizer provider 注入；
   synthetic provider 必须标 synthetic。按相邻 memory 出现生成变化对而非全部两两配对。
   输出 equal/replace/insert/delete 区间以及 chunk 移动关系，均不声称状态相等。
3. **时间与回放**：默认输入顺序只标 constructed；显式 observed 顺序必须有同一时钟域
   的完整时间戳。回放仅生成清单，不发送模型请求。缺失等待/并发/服务时间不补造。
4. **状态与关联**：提供模型无关状态实例契约；DeepSeek-V4 清单来自本地固定 runtime
   引用。V4.1 只列需求，标 runtime schema 未确认。提供与 AgentMon JSONL 的只读精确
   ID 关联，不按时间最近猜测，也不修改 AgentMon。
5. **报告与样例**：输出 requests/chunks/transitions/replay/joins、代表案例、质量报告、
   状态采集清单、下一次 runtime 所缺字段；正文和 token 只保存在单独的 payload 文件中。
6. **验证**：真实 LifeMem 结构的 synthetic fixtures、错位和缺失数据测试、现有全套
   pytest、synthetic smoke、compileall；比较冻结代码前后摘要，确认无算法修改。

## 关键定义

- request identity：优先 sidecar 显式 request_id，否则使用 dataset_id + record_index；
  question_id 为业务关联，不假设唯一；缺失 conversation_id 的记录不自动跨请求合并。
- memory identity：优先显式 memory_id，其次 conversation + subject（标 heuristic），
  最后只按相同正文识别（无法发现其版本变更）。
- version：正文的不同版本按该 memory 的首次出现顺序编号；context 版本独立编号。
- reuse distance：同 session 相邻出现之间的请求间隔；不等于真实时间或 cache hit。
- 坐标：半开区间；chunk 边界切到 token 内部时标 BOUNDARY_CROSSING，不宣称 exact。
- pair grouping：本批只作数据准备，不重分已有 48-pair train32/test16。未来建训练集时
  应按 session / memory 身份隔离，不能把重叠 pair 随机划分。
- 状态字节：物理分配以 allocation_id + device 去重；共享消费者不重复计容量。
  不同设备上的真实副本各计一次。逻辑状态数量与实际内存分别输出。
- 缺失 provider 为 MISSING / UNRESOLVED；没有 query/attention/state/cost 时不自动估算。

## 本批不包含的工作

真实 runtime hook、NPU/GPU 执行、DeepSeek-V4.1 runtime 接入、资源调控、恢复/修复算法、
在线 LifeMem/AgentMon adapter、真实性能收益。下一批先在单请求上采齐主状态/窗口/索引
和状态关联，再开展等待恢复与并发；性能实验与张量 dump 分开运行。

## 运行接口

见 `docs/LIFEMEM_STATE_RESEARCH_V1.md`。主入口为
`scripts/prepare_state_research.py`，synthetic 入口为 `scripts/state_research_smoke.py`。
