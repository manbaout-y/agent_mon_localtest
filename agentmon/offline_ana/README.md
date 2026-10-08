# LifeMem exact-content KV reuse offline analysis

`lifemem_kv_reuse_analysis.py` analyzes existing LifeMem JSON/JSONL
results without importing AgentMon, LifeMem, vLLM, a tokenizer, or a model.
It measures exact retrieved-memory body reuse in characters; it does not claim
token-level cache hits or performance speedup.

## 直接修改路径并运行

打开 `lifemem_kv_reuse_analysis.py`，修改文件开头的配置区：

```python
INPUT_PATH = "/path/to/your/lifemem_results.json"
OUTPUT_PATH = "/path/to/analysis_out"
LIMIT = 1
VERBOSE = True
MAX_PAIR_DETAIL_ROWS = 20_000
```

- `INPUT_PATH` 是合法 JSON/JSONL 文件。
- `OUTPUT_PATH` 是输出目录，不存在时自动创建。
- `LIMIT = 1` 只分析第一条 QA；smoke 正确后改成 `None` 分析全部。
- `MAX_PAIR_DETAIL_ROWS` 只限制 pair 明细 CSV；聚合计数始终覆盖全部 pair。

在 IDE 中直接运行文件，或者在 `offline_ana` 目录执行：

```bash
python lifemem_kv_reuse_analysis.py
```

## 可选命令行方式

只要提供命令行参数，脚本会自动使用参数并忽略代码内路径：

```bash
python lifemem_kv_reuse_analysis.py \
  --input /path/to/results.json \
  --output /path/to/analysis_out \
  --limit 1 \
  --verbose
```

`--input` accepts a valid JSON record, JSON array, JSONL with one object per
line, or a JSON object containing one clear record-list field such as
`records`, `data`, or `items`. Use `--input -` to read the same valid formats
from stdin. JSON fragments with a missing outer brace or a trailing comma are
intentionally rejected with a clear error.

The output directory contains:

- `records.csv`: one row per analyzed QA record.
- `chunks.csv`: one row per valid `results[]` item, without full chunk text.
- `reuse_groups.csv`: one row per `content_hash` repeated in `results[]`.
- `intra_context_duplicates.csv`: repeated body spans within one context.
- `context_variation_groups.csv`: 每个 logical memory 的三类上下文对聚合统计。
- `context_pair_comparisons.csv`: 每个可比较上下文对的 hash、prefix、位置和分类明细。
- `context_variation_report.md`: 可直接阅读的中文总览和逐 subject 表格。
- `summary.json`: compact totals, ratios, warnings, and metric definitions.

CSV positions are zero-based Python character offsets into the original
`formatted_context`; the end is exclusive. JSON arrays stored in CSV cells are
serialized as compact JSON. Missing CSV values use the literal `null`.

## Exact-match and context rules

The normalized body is created only by converting CRLF/CR newlines to LF and
stripping outer whitespace. Its identifier is
`SHA256(normalized_content)`. The analysis does not use `metadata.summary` as
prompt content and does not perform fuzzy or semantic matching.

All occurrences of a retrieved body are located in `formatted_context`.
When the same body appears behind both `[周期摘要 period_YYYY-mMM]` and the
retrieved chunk's `subject: ` wrapper, the retrieved-wrapper occurrence is the
canonical position used to build `preceding_chunk_hashes`. The period body is
retained as a separate actual context occurrence for redundancy analysis.

`CROSS_CONTEXT_NON_PREFIX_REUSE_CANDIDATE` requires the same hash in at least
two QA records and different ordered preceding-memory hash sequences. A repeat
inside one formatted context is reported separately as
`REDUNDANT_CONTEXT_CANDIDATE`; the two categories may overlap in larger data.
Top-level `non_prefix_candidate_*` metrics use the cross-context category only;
an intra-context row can still carry the broader `NON_PREFIX_REUSE_CANDIDATE`
classification when its preceding-memory sequences differ.
`preceding_chunk_hashes` describes only retrieved memory chunks, never the
complete model prompt prefix.

## 上下文变化三分类

为了判断“对应的 memory”，脚本优先使用：

```text
conversation_id + normalized metadata.subject
```

subject 缺失时退化为 `content_hash`，因此这类 fallback group 无法发现正文版本变化。
比较单位是同一 logical memory 在两个不同 QA record 中形成的“上下文对”，不是单条 occurrence。
每个 pair 严格落入一个互斥分类：

- `CONTENT_CHANGED`：对应 subject 的 `content_hash` 不同。
- `SAME_CONTENT_PREFIX_CHANGED`：内容相同，只改变 preceding memory chunks。
- `SAME_CONTENT_POSITION_CHANGED`：内容和 preceding memory chunks 相同，只改变字符位置。
- `SAME_CONTENT_PREFIX_AND_POSITION_CHANGED`：内容相同，prefix 和位置都改变。
- `ALL_SAME`：内容、preceding memory chunks 和字符位置都相同。

报告中的“内容相同但 prefix 或位置变化”是中间三个分类之和。三大类：
内容变化、内容相同但 prefix/位置变化、三者都相同，其计数之和必然等于
`comparable_context_pairs`。

## Character metrics

- `retrieved_content_chars` is occurrence-weighted over valid `results[]`
  rows. A 1,000-character result retrieved five times contributes 5,000.
- `repeated_content_chars` includes every result occurrence belonging to a
  repeated hash group.
- `potential_reusable_chars` keeps one located occurrence per repeated hash as
  the baseline and counts the remaining located body characters. In the
  1,000-by-5 example it is 4,000.
- `non_prefix_potential_reusable_chars` applies the same calculation only to
  cross-context non-prefix groups.
- `redundant_context_chars` counts additional body spans within the same
  formatted context after one baseline span. It is reported separately and is
  not added to cross-context reuse metrics.

Opportunity ratios use `located_retrieved_content_chars` as the denominator.
These are character-level opportunity estimates, not token counts, KV tensor
measurements, cache hit rates, or causal performance estimates.

## Known limitations

- Only explicit `[周期摘要 period_YYYY-mMM]` wrappers ending at a standalone
  `---` separator are specially interpreted.
- Context assembly wrappers other than `subject: content` may be located by
  body but tagged `unclassified_match`; a warning-worthy fallback canonical
  position is then used.
- Exact body reuse says nothing about tokenizer block alignment, model layer
  state, selective recomputation correctness, or realizable latency savings.
