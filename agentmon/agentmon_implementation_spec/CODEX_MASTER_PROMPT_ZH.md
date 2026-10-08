# 交给 Codex 的主执行指令

你要从零实现 `agentmon-ascend`。首先完整阅读同目录的 `AGENTMON_TECHNICAL_SPEC_ZH.md`。

## 工作方式

1. 只实现当前 iteration，不得提前实现后续阶段。
2. 每个 iteration 开始时，先输出：
   - 本阶段范围；
   - 不做的内容；
   - 预计新增/修改文件；
   - 测试计划。
3. 先建立测试 fixture 和失败测试，再实现 parser/公式。
4. 完成后必须运行测试，并输出真实结果。
5. 不可用“TODO”代替本阶段强制功能。
6. 不修改系统、CANN、vLLM 或 mini-SWE 的现有安装。
7. P0 只用 Python 标准库；确需依赖必须停止并解释，不得自行安装。
8. 所有外部命令必须使用参数列表、超时和错误捕获；禁止 `shell=True`。
9. 任何采集失败都要降级并保留 raw evidence。
10. 不保存 Prompt、源码、完整命令和工具输出作为默认行为。

## 当前 iteration

从 **Iteration 0：仓库与数据契约** 开始：

- 创建 `pyproject.toml` 和 `src/agentmon` 包；
- 最低 Python 3.10；
- 实现配置 dataclass、时钟锚点、统一事件 dataclass；
- 实现 append-only JSONL writer；
- 实现最小 CLI：`agentmon --version` 和占位的 `doctor`；
- 建立 unittest 测试；
- 不实现任何真实采样；
- 不引入第三方依赖。

## Iteration 0 验收

- `python3 -m agentmon --version` 成功；
- 事件可以序列化为合法 JSON；
- NaN/Inf 被安全处理；
- JSONL 并发写入通过测试；
- 时钟锚点同时包含 wall/monotonic；
- 配置覆盖顺序有测试；
- 所有测试通过。

完成后停止，不进入 Iteration 1，等待人工审查。
