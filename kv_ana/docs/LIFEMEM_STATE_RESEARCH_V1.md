# LifeMem 异构状态研究数据底座 v1：使用与数据契约

完整实施计划见 `HETEROGENEOUS_STATE_BATCH1_PLAN.md`。本扩展在 `state_research/`
中独立实现，框架版本仍为 2.0.1。无需 torch，不导入 AgentMon，也不修改其源码。

## 1. 现在能够完成什么

- 读取 LifeMem 原始 JSON/JSONL；保存精确正文，不自动规范化换行或空白。
- 建立请求身份、session、context version、memory identity/version。
- 定位 chunk 字符区间，识别重复正文与唯一 subject wrapper；歧义保留为歧义。
- 使用显式 token IDs/offsets，或调用本地 fast tokenizer（可选）建立 token 边界映射。
- 生成同一 memory 相邻出现的变化对、逐 session 相邻请求的文本变化图、代表案例。
- 输出有序 replay manifest；构造顺序和观测时间严格区分。
- 对已有 AgentMon JSONL 做精确 ID 关联；统计单一时刻的状态存储，去重物理共享。
- 生成 DeepSeek-V4 源码支持的采集清单，明确 V4.1 的 runtime schema 仍待核对。

这里生成的是输入准备和研究证据，不运行调度器、模型或 KV 修复，也不测量性能。

## 2. 最快运行

在项目根目录使用现有 Python（项目原有依赖包括 numpy）：

```bash
python3 scripts/state_research_smoke.py --output outputs/state_research_smoke_new
python3 scripts/prepare_state_research.py \
  --input /path/to/lifemem_results.json \
  --dataset-id lifemem-study-v1 \
  --output outputs/lifemem_prepared_v1
```

output 必须为新目录或空目录，避免覆盖旧实验。原始数据只有 `formatted_context`
也能运行，但 `FULL_PROMPT_MISSING` / `TOKENIZATION_MISSING` 会保留在报告中。
`validation=PASS` 仅表示输入检查和准备完成，不代表 token、runtime 或修复实验已通过。

支持单条 JSON、JSON 数组、JSONL，以及只有一个明确列表的 `records/data/items` 容器。
不自动修补不完整 JSON，不下载数据或 tokenizer。

## 3. 原始数据与可选 sidecar

原有字段直接沿用：

```json
{
  "question_id": "q1",
  "conversation_id": "session1",
  "query": "问题",
  "results": [{"content": "X", "metadata": {"subject": "memory", "memory_id": "m1"}}],
  "retrieval_metadata": {"formatted_context": "memory: X"}
}
```

新增信息可以直接置于记录的 `research` 字段，也可放独立 sidecar：

```json
[
  {
    "record_index": 0,
    "research": {
      "request_id": "request1",
      "full_prompt": "SYSTEM\nmemory: X\nQUESTION",
      "context_span_in_prompt": [7, 16],
      "model_family": "explicit-model-version",
      "request_time_ns": 1000000,
      "clock_domain": "host-and-boot-identity",
      "agentmon": {
        "agent_run_id": "run1",
        "service_run_id": "service-boot1",
        "model_request_id": "model1",
        "engine_request_id": "engine1"
      }
    }
  }
]
```

此处文本只是说明字段，不是模型模板。真实 `full_prompt` 必须来自实际模板渲染结果，
不能由准备器将 system/query/memory 任意拼接。sidecar 用原始输入 record_index 对齐，
发生字段冲突时拒绝覆盖。

```bash
python3 scripts/prepare_state_research.py \
  --input /path/to/lifemem_results.json \
  --sidecar /path/to/research_sidecar.jsonl \
  --agentmon-events /path/to/events.jsonl \
  --dataset-id lifemem-study-v1 --output outputs/lifemem_prepared_with_links
```

身份语义：

- question_id 可重复，不作为运行实例唯一键；显式 request_id 优先，否则使用 dataset_id
  和原始 record_index。该默认身份用于此输入文件，重新排序文件后需使用新 dataset_id
  或显式 request_id。
- session_id 使用 `[dataset_id, conversation或unlinked_request, 原ID]` 的 JSON 身份，
  原 conversation_id 另保留；不同 dataset 不会因同名 session 串数据。
  缺失或空白 conversation_id 的记录不跨请求关联；显式 request_id=0 不会丢失。
- memory_id 作用域是 session；缺失时按 subject 匹配并标 `HEURISTIC_SUBJECT`。
  subject 也缺失时只能按相同正文匹配，不能识别内容更新版本。
- context version 按所分析文本（full_prompt 或 formatted_context）的首次出现编号；
  同一文本再次出现复用版本号。不同 scope 的版本不可解释为完整 prompt 演进。
- 本批不改原有 48-pair manifest/split；未来训练需以 session/memory 为隔离单位，
  避免同一原始内容通过重叠 pair 泄漏。

AgentMon engine/API alias 按 event.run_id / sidecar.service_run_id 区分不同服务运行。
缺 run 且 ID 重用则保持歧义；显式冲突字段不能被另一个匹配 ID 掩盖。
提供 state snapshot 时同时校验 request identity、明确 model_family 和 context_version。

## 4. token 对齐

`research.tokenization` 接受：

```text
input_ids: 实际 token IDs
offsets: [[char_start, char_end], ...]，与 IDs 等长
tokenizer_id: 模型/tokenizer 的固定身份，含 revision 和相关模板/编码配置
source: runtime | provided | local_tokenizer | synthetic
text_scope: full_prompt | formatted_context
```

offset 是 Python Unicode 字符索引、半开区间，不能传 UTF-8 字节索引。
`[0,0]` 表示 tokenizer 的无文本 special token；支持多个 byte-fallback token
对应同一个 Unicode 字符。offset 必须保持顺序。

若 chunk 的字符边界落在一个 token 中间，标 `BOUNDARY_CROSSING`，不会截取半个 token
并宣称精确。完全没有 token 覆盖也会记录问题。token edit map 只有两侧边界精确、
tokenizer identity 和 source 一致时才生成。相同正文不保证相同 token IDs。

`context_span_in_prompt` 可显式消除重复 context 歧义；`research.chunk_spans`
使用字符串 result_rank 对应 formatted_context 内的精确字符区间。每个显式区间
都检查其切片是否等于原始 body。未显式给出时，正文唯一出现或唯一 `subject: body`
wrapper 才定位；没有唯一依据就保留 MISSING/AMBIGUOUS。

可以只加载本地 tokenizer 文件，不加载模型：

```bash
python3 scripts/prepare_state_research.py \
  --input /path/to/lifemem_results.json --sidecar /path/to/full_prompts.jsonl \
  --tokenizer-dir /path/to/local/tokenizer \
  --tokenizer-id model-and-tokenizer-revision \
  --dataset-id lifemem-study-v1 --output outputs/lifemem_retokenized
```

该可选路径需要现有环境安装 transformers 和对应 fast tokenizer 依赖；使用
`local_files_only=True`、`trust_remote_code=False`，不会联网。
默认 `add_special_tokens=False`，需要时显式加 `--add-special-tokens`；此配置纳入
tokenizer identity。已存在的 sidecar tokenization 优先，不被重新编码覆盖。
本地重新编码标 `local_tokenizer`，不能冒充 runtime 捕获的 token IDs，后续应与
真实请求核对。没有 fast tokenizer/offsets 时明确报错，不退化成按字符分词。

API 可以注入实现 `TokenizerProvider.tokenize(text, text_scope=...)` 的 provider。
`fixtures.SyntheticCharacterTokenizer` 只用于测试，source 固定 synthetic，CLI 不将
它作为缺失 tokenizer 的 fallback。它不会使真实状态对齐就绪计数增加。

## 5. 变化映射与请求顺序

默认 `--order constructed`：严格按输入顺序生成清单，没有 release 时间、等待时间或
并发度。原记录即使带时间也不会被偷偷解释为真实序列。

`--order observed` 要求每条记录都有非负 request_time_ns 和相同 clock_domain，
按时间排序。相同时间保持输入顺序，变化对中标 timestamp_tied；不能声称这是唯一
真实先后顺序。跨设备时间必须先由上游对齐时钟。

- memory 变化只比较相邻出现的两个请求，不生成 n(n-1)/2 个“命中”。
- intervening_session_requests 是中间隔了多少同 session 请求，不是 LRU stack distance。
- elapsed_ns 仅在 observed 模式输出，含义是相邻出现的请求时间差，不是等待或服务耗时。
- 同一 memory 在一个请求内出现多次时，保持歧义，不任意选择一份也不跨过该记录配对。
- memory_prefix 仅由已定位 memory 顺序组成；同时提供 full_text_prefix_same。
  `ALL_SAME_MEMORY_CONTEXT` 不能说明 system/query/完整 prompt 相同。
- character/token edit map 的坐标分别相对于对应文本或 chunk 的 token 片段。
  token_map 内坐标加 old_token_span/new_token_span 的起点即可映射到输入 token 序列。
  equal/replace/insert/delete 是确定性的文本匹配，不证明 KV 对应或数值相等。
  重复文本可存在多种匹配，本实现采用 difflib 的启发式对齐，没有最优编辑或语义匹配保证。
- 默认超过两侧合计 100000 个单位的 edit map 标 SIZE_LIMIT，可配置 `--max-edit-units`；
  仍保留身份、分类与 chunk 坐标。
- representative_cases 只按分类取前若干相邻变化对，可用 `--cases-per-class` 调整；
  不读取 B、残差或模型结果。缺少某类案例时不伪造真实样本。

## 6. AgentMon 关联与状态清单

与现有 RequestIdentity/P2 字段对应：

| 上游字段 | 关联键 |
|---|---|
| model_call_id / model_request_id / agentmon_model_request_id | model_request_id |
| engine_request_id / vllm_request_id | engine_request_id |
| api_request_id、request_attempt_id、client_request_id、trajectory_response_id | 同名键 |

`vllm.request.received` 中同时出现的显式 ID 可建立 bridge，因此乱序文件中的 engine
事件也能关联。仅 run_id 或时间接近不能关联；相互冲突为 CONFLICT，多候选为 AMBIGUOUS。
payload.agentmon_run_id 可缩小候选范围。service run_id 不能代替 agent run_id。
此处只证明请求身份关联，不把共享服务资源算作单请求独占。

`--state-snapshot` 输入 `{snapshot_id, states}`。states 对应 `StateInstance`：
state_id/request_id/model_family/state_kind/context_version、producer_layer、consumer_layers、
allocation_id/device/tier/allocation_bytes/logical_bytes、dtype/shape/source。
可附 artifact_path、valid_range、positions_semantics、mapping_ref 和 dependency_state_ids。
不读取 artifact，不加载张量。request_id 必须存在于准备好的请求清单中。

输入表示一个时刻的 snapshot。allocation_id 必须来自实际分配实例，不能跨已释放后重用的
物理 slot 共用同一个身份。以 `(device, allocation_id)` 去重容量；相同对象多个消费者
不增加物理字节，不同设备的副本分别计费。跨快照的生命周期不能相加后当作同时占用。
未知依赖单独输出 unresolved_dependency_ids。未提供 snapshot 时标 NOT_PROVIDED；
其中的零计数不代表真实运行时没有状态占用。

capture_requirements.json 只记录本地 fixed runtime 的字段证据和下一批需求：
压缩主状态、SWA、compressor working state、indexer state/keys/scales/full cache。
某个字段是否实际启用、形状和字节数必须由 runtime 填写。V4.1 状态目录保持
REQUIRES_RUNTIME_SCHEMA，未借用 V4 布局冒充其支持。

## 7. 输出与下一批

| 文件 | 内容 |
|---|---|
| requests.jsonl / chunks.jsonl | 身份、版本、坐标、输入完整度 |
| payloads.jsonl | 原始文本、query、retrieved contents、token IDs/offsets；独立保存 |
| transitions.jsonl / context_changes.jsonl | 相邻 memory/请求的映射与证据 |
| transition_summary.csv / representative_cases.json | 精简对比和机制案例 |
| replay_manifest.json | 只读执行清单，未执行请求 |
| agentmon_joins.jsonl | 关联、歧义与冲突结果 |
| state_inventory.jsonl / capture_requirements.json | 已提供的单时刻状态和未来采集要求 |
| quality_report.json / report.md / manifest.json | 质量、限制、文件清单 |

下一批才做目标版本 runtime 接入、真实状态消费与恢复成本采集、单请求安全干预和
等待/恢复负载。AgentMon 原 P2 的 vLLM 0.18.0 gate 与传统 KV 容量公式不能直接用于
DeepSeek-V4 0.22.1；需要独立版本适配。V4.1 需先确认目标 runtime 和状态布局。
当前新增文件不改变 AgentMon、StatePair、B1–B5 或正式 recipe。
