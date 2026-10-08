# AgentMon 实验编排与正式矩阵门禁

本文说明 AgentMon 实验编排层。推荐优先使用统一入口
`scripts/agentmon_experiment.py`，它把“简单实验请求 → 完整 contract →
smoke/pilot/formal/analyze 门禁”连起来。底层兼容入口
`scripts/agentmon_experiment_orchestrator.py` 仍然可直接使用。

它不会重写 `agentmon_batch_runtime.py`、`agentmon_server_preflight.py`、`analysis run-all` 或 Runtime Core。它也不会证明优化收益；它只回答：

```text
这次 A/B 实验配置差异是否受控？
B 组机制是否真的经过了预期执行路径？
事件里是否看到了机制激活证据？
pilot 是否达到进入 formal matrix 的最低门槛？
后续分析应走 batch-native analyze-existing 还是通用 Analysis Runtime？
READY_FOR_FORMAL 是 YES 还是 NO？
```

## 10 分钟冒烟推荐流程

先写一份很小的 request，例如 `experiment.toml`：

```toml
mechanism = ["context_compression"]

[experiment]
name = "context-compression-test"
mode = "advise"

[source]
repo = "/path/to/code"

[workload]
tasks = "/path/to/tasks.tsv"

[pilot]
tasks = 3

[formal]
tasks = 50
concurrency = [1, 5, 10, 20, 30]

[commands]
preflight = ["python", "scripts/agentmon_server_preflight.py", "--help"]
ab_single_task = ["bash", "/path/to/run-ab-single-task.sh"]
pilot = ["bash", "/path/to/run-3-task-pilot.sh"]
formal_matrix = ["bash", "/path/to/run-formal-matrix.sh"]
```

模式使用 `advise` 或 `enforce`；旧请求中的 `advice` 会转换成 `advise`。
对要求覆盖率的机制（例如 context_compression），完成采集后在请求的
`[evidence]` 中填写 `activation_paths` 和实际测得的 `coverage_denominator`，
或在 contract 的 B variant 中填写 `activation_coverage_denominator`。
分母应来自目标请求数量，不能用已匹配事件数量代替；缺少分母时检查不会放行。

生成 contract：

```bash
PYTHONPATH=src python scripts/agentmon_experiment.py prepare \
  --request /path/to/experiment.toml \
  --output-dir /path/to/experiment-plan
```

输出：

```text
PLAN_READY
/path/to/experiment-plan/experiment-contract.json
```

随后按阶段推进：

```bash
PYTHONPATH=src python scripts/agentmon_experiment.py smoke \
  --contract /path/to/experiment-plan/experiment-contract.json \
  --output-dir /path/to/experiment-smoke \
  --execute

PYTHONPATH=src python scripts/agentmon_experiment.py pilot \
  --contract /path/to/experiment-plan/experiment-contract.json \
  --output-dir /path/to/experiment-pilot \
  --execute

PYTHONPATH=src python scripts/agentmon_experiment.py formal \
  --contract /path/to/experiment-plan/experiment-contract.json \
  --output-dir /path/to/experiment-formal \
  --execute \
  --run-formal

PYTHONPATH=src python scripts/agentmon_experiment.py analyze \
  --contract /path/to/experiment-plan/experiment-contract.json \
  --output-dir /path/to/experiment-analysis \
  --execute
```

`formal` 阶段有硬保护：没有 `READY_FOR_FORMAL=YES`，或者没有显式
`--run-formal`，正式矩阵不会启动。

## 推荐位置

如果已经有完整 contract，也可以直接调底层 orchestrator：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" \
  "$AGENTMON_REPO/scripts/agentmon_experiment_orchestrator.py" \
  --contract /path/to/experiment-orchestration-contract.json \
  --output-dir /path/to/orchestration-report
```

默认只写计划和门禁报告，不执行配置里的命令。要执行 preflight、service check、单任务 A/B、pilot 或 analysis 命令，显式加：

```bash
--execute
```

正式矩阵有额外保护：即使加了 `--execute`，`formal_matrix` 也只有在 `READY_FOR_FORMAL=YES` 且显式加 `--run-formal` 时才会执行。

## Request 到 Contract

`scripts/agentmon_experiment_init.py` 是独立的 request 生成器入口：

```bash
PYTHONPATH=src python scripts/agentmon_experiment_init.py \
  --request /path/to/experiment.toml \
  --output-dir /path/to/experiment-plan
```

它支持 JSON、TOML，以及安装 PyYAML 时的 YAML。用户只需要写：

- `experiment.name` / `experiment.mode`；
- `mechanism` 或 `mechanisms`；
- `source.repo`；
- `workload.tasks`；
- `pilot.tasks`；
- `formal.tasks` 和 `formal.concurrency`；
- 可选 `commands.*`。

生成器会根据 mechanism registry 自动填充：

- B 组 `required_config`；
- A/B 允许差异 `allowed_variant_differences`；
- B 组候选执行路径 `execution_path`；
- `execution_path_requirements`；
- activation probe 默认事件和覆盖率规则。

它不会猜测未产生的数据。比如真实 `events.jsonl`、pilot batch root 或 P2
service events 还不存在时，后续 smoke/pilot/analyze 阶段会继续输出
`UNKNOWN`、`PARTIAL` 或 `READY_FOR_FORMAL=NO`，而不是用配置值冒充证据。

## 输出

输出目录会包含：

- `experiment_orchestration_report.json`：总报告；
- `activation_probe_report.json`：机制激活证据扫描；
- `analysis_route.json`：自动分析路由建议和命令；
- `ready_for_formal.env`：可由 shell 读取的门禁结果。

`ready_for_formal.env` 示例：

```text
READY_FOR_FORMAL=NO
EXPERIMENT_ORCHESTRATION_STATUS=FAIL
BLOCKING_REASONS=mechanism_not_active:B:model_gate
```

## Contract 示例

```json
{
  "schema_version": "1.0",
  "experiment_id": "model-gate-ab-001",
  "baseline_variant": "A",
  "allowed_variant_differences": [
    "runtime_optimization.enabled",
    "control.mode",
    "model_gate.enabled",
    "model_gate.*"
  ],
  "variants": {
    "A": {
      "role": "baseline",
      "config": {
        "runtime_optimization": {"enabled": false},
        "control": {"mode": "advise"},
        "model_gate": {"enabled": false},
        "model": {"name": "same-model"}
      }
    },
    "B": {
      "role": "candidate",
      "mechanisms": ["model_gate"],
      "execution_path": "runtime_controlled_model",
      "activation_evidence_paths": ["/path/to/run-or-runtime/events.jsonl"],
      "config": {
        "runtime_optimization": {"enabled": true},
        "control": {"mode": "advise"},
        "model_gate": {"enabled": true},
        "model": {"name": "same-model"}
      }
    }
  },
  "pilot": {
    "batch_root": "/path/to/3-task-pilot-batch",
    "min_task_count": 3,
    "min_success_count": 3
  },
  "analysis": {
    "batch_root": "/path/to/formal-or-pilot-batch",
    "task_file": "/path/to/tasks.tsv",
    "analysis_preset": "standard",
    "analysis_workers": 1,
    "p2_events": "/path/to/p2-service-global.events.jsonl",
    "p2_index_cache_root": "/path/to/p2-index-cache"
  },
  "commands": {
    "preflight": ["python", "scripts/agentmon_server_preflight.py", "--help"],
    "ab_single_task": ["bash", "/path/to/run-ab-single-task.sh"],
    "pilot": ["bash", "/path/to/run-3-task-pilot.sh"],
    "formal_matrix": ["bash", "/path/to/run-formal-matrix.sh"]
  }
}
```

## 门禁语义

`READY_FOR_FORMAL=YES` 至少要求：

1. A/B variant 的配置差异都在 `allowed_variant_differences` 里；
2. 每个候选机制的 `execution_path` 符合机制要求；
3. 每个要求激活的机制都在 evidence JSONL 中出现了匹配事件；
4. 如果配置了 `pilot`，pilot batch 的任务数和成功数达到最低要求。

否则输出：

```text
READY_FOR_FORMAL=NO
```

正式矩阵必须拒绝启动。

## 默认机制激活证据

默认机制规则来自：

```text
src/agentmon/experiments/mechanisms/*.yaml
src/agentmon/experiments/mechanisms/*.toml
```

建议新增机制优先写 YAML profile；TOML 读取保留用于无额外依赖的兼容场景。

当前内置 registry：

| 机制 | 默认激活事件 | 明确不算激活的 reason/status |
|---|---|---|
| `scheduler` | `runtime.queue.running`、`runtime.queue.deferred` | `runtime_optimization_disabled`、`scheduler_disabled` |
| `model_gate` | `control.model_gate.acquire`、`control.model_provider.*` | `gate_disabled`、`control or model gate disabled`、`runtime optimization disabled` |
| `tool_pool` | `control.tool_pool.acquire`、`control.tool_execution.*` | `pool_disabled`、`tool_pool_disabled`、`runtime optimization disabled` |
| `context_compression` | `context_compression.decision` | `DISABLED`、`ERROR_BYPASS` |
| `prefix_cache` | `vllm.request.prompt_cache_accounted`、`vllm.kv.snapshot` | `disabled`、`UNAVAILABLE`、`UNKNOWN` |

这些规则只证明“流量经过了相应控制点或 adapter 决策点”。它们不证明性能收益，也不证明任务质量不下降。

registry 也可以声明 `coverage.min_ratio`。如果 contract 或 variant 提供了
`activation_coverage_denominator` / `expected_activation_count`，门禁会计算覆盖率；
如果没有分母，覆盖率保持 `UNKNOWN`，不会猜测补齐。

如果某次实验有自定义事件名或更严格条件，可以显式写 `activation_probes` 覆盖默认规则：

```json
{
  "activation_probes": [
    {
      "variant": "B",
      "mechanism": "model_gate",
      "evidence_paths": ["/path/to/_runtime/events.jsonl"],
      "min_count": 1,
      "profile": {
        "event_types": ["custom.model_gate.admit"],
        "negative_reason_paths": ["payload.reason"],
        "negative_reasons": ["disabled"]
      },
      "required_payload": {
        "payload.mode": "advise"
      }
    }
  ]
}
```

`required_payload` 的 key 是点分路径，value 必须精确相等。不要把 prompt、摘要正文、原始工具输出或完整 diff 放进 contract 或激活证据输出。

## 执行路径门禁

默认机制到执行路径的要求同样来自 registry：

| 机制 | 允许的执行路径 |
|---|---|
| `scheduler` | `runtime_service` |
| `model_gate` | `runtime_controlled_model`、`model_gate_proxy`、`runtime_service` |
| `tool_pool` | `tool_pool_executor`、`runtime_service` |
| `context_compression` | `agent_adapter_context_compression`、`runtime_controlled_model` |
| `prefix_cache` | `runtime_controlled_model`、`direct_model_endpoint`、`runtime_service` |

例如，测 Model Gate 时，B 组必须让真实模型请求经过 runtime-controlled model 或 model gate proxy；直接打模型 endpoint 的 runner 不满足门禁。测 Scheduler 时，必须走 RuntimeService，而不是绕过 runtime 直接启动任务。

可用 `execution_path_requirements` 覆盖或扩展这些规则，但覆盖内容必须写在 contract 中。

## 自动分析路由

`analysis.batch_root` 指向已知 batch，并且目录中存在 `batch-manifest.json`、`batch-summary.json` 或 `batch-results.tsv` 时，脚本选择：

```text
batch_native_analyze_existing
```

并生成 `scripts/agentmon_batch_runtime.py --analyze-existing --analysis-preset standard ...` 命令。

只有未知历史目录或非 batch 输入才选择：

```text
analysis_runtime_run_all
```

这避免已知 batch 实验绕开 batch-native analysis，也避免未知历史目录被误当成标准 batch。

## 推荐实验顺序

1. `prepare`：由 request 生成 contract；
2. `smoke`：确认环境、服务和 A/B 单任务机制激活；
3. `pilot`：小规模任务，写出 `READY_FOR_FORMAL`；
4. `formal`：只有 `READY_FOR_FORMAL=YES` 且带 `--run-formal` 才启动正式矩阵；
5. `analyze`：自动选择 batch-native `--analyze-existing` 或通用 Analysis Runtime；
6. 再做 A/B compare 和 optimizer advice replay。

## 边界

- 这是实验编排和验收层，不是新的 Runtime。
- 它不启动、不停止、不 patch vLLM；服务管理仍由外部脚本或现有 preflight 流程负责。
- 它不根据单次 advice replay 宣称收益。
- 它不把固定并发档位、任务 ID、平台阈值或经验门限写进通用逻辑。
- 机制存在或配置启用不等于机制生效；必须以事件证据为准。
