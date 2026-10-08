# 第一批实施验收

日期：2026-09-22。新增扩展版本：state-research-batch1-v1。

## 结果

| 检查 | 结果 |
|---|---|
| 实施前原测试 | 127 passed |
| 实施后完整测试 | 183 passed（新增 56 项） |
| 原 framework synthetic smoke | PASS，448 records / 224 summary groups |
| 新数据准备 synthetic smoke | PASS，5 requests / 15 chunks / 12 adjacent memory transitions |
| 单条历史 LifeMem 本地样例 | 1 request / 10 chunks / 10 located；0 跨请求变化对 |
| compileall src/scripts/tests | PASS |
| 冻结核心、recipes、providers、adapters、observability | 前后 Python 源码摘要一致 |
| AgentMon src | 前后 Python 源码摘要一致 |
| 真实模型 / runtime / NPU/GPU | 未执行 |

测试使用本机 Python 3.9.6。第一次原测试有 1 项因 macOS 默认 py_compile 缓存目录
无写权限失败；将 `PYTHONPYCACHEPREFIX` 指向 `/private/tmp/kvreuse_batch1_pycache`
后原 127 项全部通过。没有为适应环境而修改 runtime patch 或原测试。

```bash
PYTHONPYCACHEPREFIX=/private/tmp/kvreuse_batch1_pycache python3 -m pytest
PYTHONPYCACHEPREFIX=/private/tmp/kvreuse_batch1_pycache python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m kvreuse_b5.cli.smoke \
  --config configs/synthetic_smoke.yaml --output /path/to/new/framework_smoke
python3 scripts/state_research_smoke.py --output /path/to/new/state_research_smoke
```

## 可查看的本次产物

- `outputs/state_research_batch1_validation/synthetic/smoke_result.json`
- `outputs/state_research_batch1_validation/synthetic/prepared/report.md`
- `outputs/state_research_batch1_validation/synthetic/legacy/report.md`
- `outputs/state_research_batch1_validation/lifemem_qa177/report.md`
- `outputs/state_research_batch1_framework_smoke/`

真实样例来自此前对话的本地 pasted-text，原片段缺最外层起始 `{` 并带一个尾逗号。
此次验证在独立导入调用中明确补起始括号、移除一个尾逗号后解析，正文和 context 未改。
转换说明保存在 source_paths 中；通用 loader 没有增加自动修复行为。
样例缺完整 prompt/tokenization，因此报告明确保留 FULL_PROMPT_MISSING 和
TOKENIZATION_MISSING。单条 QA 无法产生跨请求对，也不能推断全量数据复用比例。

## 专项覆盖

原始输入不变、Unicode/byte-fallback offsets、special tokens、跨 token 边界、重复正文
歧义、明确 wrapper、显式 span 错误、上下文缺失/重复、不同 tokenizer 隔离、相同正文
不同 token IDs、相邻而非全组合、重复 memory 歧义、session 隔离、原始顺序与 observed
时间、跨时钟拒绝、时间并列、变化图覆盖、sidecar 冲突、AgentMon 显式 bridge/乱序/冲突/
歧义、共享 allocation 去重和跨设备副本、状态依赖、未知请求、输出保护、无 torch import。

本地 tokenizer provider 的 local-only 参数、special-token 策略及来源标签由 mock 测试
验证。本次没有加载真实模型 tokenizer；目标 tokenizer 与 runtime 输入 IDs 的一致性
仍需下一批在有文件的环境核对。

## 已知边界

- 本批准备输入和关联证据，不采 runtime 状态，不恢复/修复状态，不衡量质量或时延。
- 状态清单只对显式提供的一个 snapshot 计容量；不把缺失 snapshot 当真实零占用。
- subject 身份是 heuristic；文本 edit map 不是 compressor strict mapping。
- V4.1 runtime schema 和真实 producer/consumer 关系仍待采集确认。
- 没有改变旧 48-pair train/test split、DeepSeek strict mapping 或 method_config_hash。
