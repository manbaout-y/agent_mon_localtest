# AgentMon/AGENTRUN 项目介绍、服务器使用与模型交接手册

本文面向三类读者：第一次接触项目的工程师、需要在服务器上执行实验或离线分析的操作人员，以及负责逐步指导服务器操作的语言模型。

本文对应源码包：

```text
agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz
```

源码包旁边应同时存在：

```text
agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.sha256
agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.BUILD_MANIFEST.json
agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz.summary.json
agentmon-server-test-ready-20260806-project-audit-hardening-test-summary.txt
```

使用时以同目录 `.sha256` 文件为校验依据。quick/full `TEST_MANIFEST` 和 gate JSON 由
服务器上的 release gate 生成真实结果，不应在本机伪造 PASS。

## 1. 一句话定位

AgentMon/AGENTRUN 是一套面向 coding agent 并发负载的运行、观测、资源编排和确定性分析系统。它把上游 Agent 框架、推理服务、工具进程与分析算法隔离在适配器、契约和 Profile 后面，使通用 runtime 与分析核心不依赖某一个 Agent 框架，也不要求与某一种推理引擎强绑定。

当前仓库同时包含两条可以独立使用的主线：

1. AgentMon 观测与 Agent Runtime：启动任务、采集 P0/P1/P2 事件、管理本地运行队列、做准入和资源控制。
2. Analysis Runtime：递归发现已有实验数据，构建规范表，执行一致性校验、性能分析、证据生成和模型输入包构建。

如果服务器上已经有原始轨迹，优先使用第二条主线。离线分析不要求重新启动 Agent 或 vLLM，也不会修改原始数据。

## 2. 当前能力边界

### 2.1 已实现并经过本地自动测试的能力

- Python 3.10 及以上版本的源码运行和可编辑安装。
- P0 主机、进程、vLLM 指标和 Ascend NPU 采样。
- mini-SWE P1 适配器、轨迹导入与事件关联。
- vLLM 0.18.0 的版本门控 P2 观测 Hook。
- vLLM/vLLM-Ascend 0.18.x 的可选 MoE 逻辑路由摘要与压缩 trace、请求关联和报告汇总。
- JSONL 权威事件存储和可选 SQLite 查询缓存。
- run finalization barrier：child 退出后先停止 collector、drain/fsync/close event writer、等待稳定 trajectory、按配置导入 P2 窗口，再生成 report/summary。
- 本地 Process Runtime Backend、持久化队列、租约和过期任务恢复。
- 固定并发 Scheduler、workload-aware Scheduler、负载感知 Optimizer、workload-aware Optimizer、决策回放和策略注册表。
- Optimization Experiment Harness：从既有 batch 构建 M1 证据底座，对多个 optimizer 策略执行 M2 离线 advice replay，并生成 M3 advice-proxy 机制比较、消融、冲突审计和报告。
  M3 在报告和 provenance 之前完成；`--resume` 同时校验输入、源码、策略、配置、Schema、预算和产物 hash；
  任一阶段 `FAILED` 都会传播到顶层和 CLI 非零退出码。
- Experiment Request / Contract / Orchestration：由简洁 `experiment.toml/json/yaml`
  生成完整 contract，并通过 `prepare`、`smoke`、`pilot`、`formal`、`analyze`
  阶段输出 `PLAN_READY`、`READY_FOR_PILOT`、`READY_FOR_FORMAL` 和分析路由。
- Batch workspace/runtime source 安全门禁：执行前解析并限制 workspace/cwd，拒绝
  AgentMon 源码重叠、符号链接逃逸和任务间共享目录；worker 从只读源码快照导入，
  workload 前后校验源码哈希，并把 run-id 缺失升级为实验无效。
- Model Request Gate、Tool Pool、CPU affinity、nice 和内存限制执行入口。
- Runtime TaskState 派生快照、进展/上下文建议和只读控制建议。
- Coding Agent 请求前上下文压缩：当前可配置为 80% 窗口触发、以 20% 窗口为
  目标；压缩算法通过独立策略插件注册和选择，并保留完整 trajectory。
- 通用、递归、Profile 驱动的实验数据发现和规范化。
- 可恢复 Stage DAG、断点、状态心跳、超时、资源预算和并行 run 规范化。
- 请求解析守恒、身份关联、P1/P2 覆盖、生命周期和数据质量审计。
- 吞吐、并发、生命周期、长尾、资源、调度、流水线等分析模块。
- 标准分析数据底座：实验合同、研究问题、Metric/实体/时钟语义、数据质量门禁、
  守恒检查汇总和基础 handoff 注册表。
- Evidence Bundle、Claim Registry、`model_input_pack` 和模型报告校验。

本地发布校验结果记录在源码包旁的 test summary 中。该结果证明本地 fixture 和单元测试通过，不等价于真实服务器环境已经完成验证。

### 2.2 需要服务器真实环境验证的能力

- 真实大规模压缩 P2 流的运行时间、磁盘增量和峰值内存。
- 服务器 Python 环境中的 `numpy`、`pandas`、`pyarrow`、`zstandard` 可用性。
- vLLM-Ascend、CANN、`npu-smi` 与真实设备拓扑。
- P2 Hook 对服务器实际 vLLM 0.18.0 构建的符号发现与事件完整性。
- Agent 进程、vLLM 进程与 NPU device/rank 的可证明映射。
- 调度和控制策略对真实吞吐、延迟及公平性的收益。
- 目标 MoE 模型在实际量化、EP/TP、ACLGraph、MTP 组合下的 routed-expert
  返回兼容性、额外开销，以及逻辑专家到动态物理放置的映射。
- 上下文估算与目标模型 tokenizer 实际计数的偏差，80% 触发线和 20% 目标线在
  服务器 chat template 下的准确性。
- 压缩启用前后的任务成功率、任务端到端时间、TTFT、实际 prompt tokens、KV/HBM 和
  额外模型请求数；必须一起检查压缩是否导致重试或任务质量退化。
- 自定义 tokenizer、语义摘要、检索式记忆等压缩策略插件在目标 mini-SWE 和模型环境中的
  正确性、延迟、失败放行和隐私边界。

### 2.3 不应误解为已经完成的能力

- 通用核心不会直接修改 vLLM 内部 scheduler 参数。真实动态 batch 控制必须通过版本化 inference adapter、公开服务能力或请求准入门实现。
- Model Gate 已提供可执行的 HTTP 代理入口，但是否接管生产流量由部署决定。
- Tool Pool 能控制经它启动的工具进程，不能自动接管绕过 runtime 启动的任意进程。
- 默认 Optimizer 主要用于可解释的阈值决策和联调基线，不代表算法已经对所有 workload 自动最优。
- `PARTIAL` 不一定代表程序错误，它可能表示某类数据没有采集，或者证据不足以支持强结论。

## 3. 分层架构

```text
Agent Frameworks
  mini-SWE / other agents / custom scripts
             |
             v
Agent Adapter + Workload Contract
             |
             v
Runtime Core ---- Scheduler ---- Optimizer ---- Policy
    |                |              |              |
    |                +------ decision/event -------+
    |
    +---- Runtime Backend: local process / future container / cluster
    +---- Model Provider + Model Gate
    +---- Tool Executor + Tool Pool + Resource Actuator
    +---- Canonical Telemetry + Audit Event Store
             |
             v
Analysis Runtime
  discovery -> profile/schema -> normalization -> validation
            -> analyzers -> evidence -> model_input_pack -> report validator
```

当前源码组织已经把几个历史大入口拆成包内模块：

- `scripts/agentmon_batch_runtime.py`：只保留兼容 CLI；实现位于 `src/agentmon/batch/`。
- `scripts/agentmon_step_sequence.py`：只保留兼容 CLI；实现位于 `src/agentmon/step_sequence/`。
- `scripts/agentmon_server_preflight.py`：只保留兼容 CLI；实现位于 `src/agentmon/server/preflight.py`。
- `scripts/agentmon_existing_data_analysis.py`：只保留兼容 CLI；实现位于 `src/agentmon/analysis/existing_data.py`。
- `scripts/agentmon_analyze_steps.py`：只保留兼容 CLI；实现位于 `src/agentmon/analysis/step_csv.py`。
- `src/agentmon/reporting/report.py`：保留旧 import 门面；新增 `event_log`、`p1`、`p2`、`quality`、`writers`、`builder` 等领域入口。
- `src/agentmon/analysis/stages.py`：Analysis Runtime 的 stage catalog、stage spec 和未来插件协议。
- `src/agentmon/adapters/inference/vllm_018/hook_config.py`：vLLM P2 hook 环境开关和 prefix-cache/deep 配置解析。
- `src/agentmon/experiments/` 与 `src/agentmon/optimizer/harness/`：optimizer experiment harness 的 evidence、snapshot、advice replay、risk audit、mechanism evaluation、Analysis SOP packaging、experiment request/contract 生成和实验阶段门禁；兼容入口是 `scripts/agentmon_optimizer_experiment.py`，新实验编排入口是 `scripts/agentmon_experiment.py` 与 `scripts/agentmon_experiment_init.py`。

后续新增功能应进入对应包内模块，旧脚本和 `report.py` 只用于兼容，不再承载新业务逻辑。

### 3.1 通用核心

通用核心位于 `src/agentmon/runtime`、`contracts`、`scheduler`、`optimizer`、`control` 和 `analysis` 的通用部分。它只依赖规范化的任务、资源、遥测、控制动作和事件契约。

通用核心不应出现某次实验的并发档位、任务 ID、固定观测计数、目录名、平台值或经验系数。阈值来自配置，字段和事件映射来自 Schema/Profile，数量来自运行时发现或显式 baseline。

### 3.2 Agent 框架适配层

Agent 框架通过 adapter 接入。目前提供 mini-SWE 适配器，包括 model、agent、environment、workload、event sink 和 runtime control。新框架应实现相同契约，而不是把框架对象传入 runtime 核心。

接入级别可以逐步提升：

1. Level 0：把 Agent 当作普通子进程，只有 P0 观测。
2. Level 1：导入框架轨迹，获得 Agent step、model call 和 tool call。
3. Level 2：框架主动发规范事件，并传播 run/request/response ID。
4. Level 3：框架接受 pause、resume、cancel、budget 等协作式控制。

### 3.3 推理框架适配层

推理服务通过 `ModelProvider`、`InferenceTelemetryProvider` 和版本化 adapter 接入。通用 runtime 只看请求准入、服务能力、队列、KV、token 和延迟等规范字段。

当前深度适配目标是 vLLM 0.18.0。其他 vLLM 版本、SGLang 或远端 API 应新增独立 adapter/Profile，不应修改通用调度器来识别框架私有字段。

### 3.4 分析三层约束

分析实现严格区分：

1. 通用 SOP 核心：发现、规范化、校验、Stage DAG、证据规则和产物协议。
2. AgentMon/vLLM 领域 Analyzer 与版本 Profile：事件语义、字段别名、版本能力和领域指标。
3. 实验 Fixture：某次实验的目录、计数、任务、并发档位和失败现场。

新的并发档位、任务数、trial、AgentMon 版本或 vLLM 版本应通过运行时发现、配置或新增 Profile 支持，不得写入通用代码分支。

## 4. 核心数据契约

### 4.1 原始数据权威性

```text
events.jsonl     = 权威原始事件
metrics.sqlite3  = 可重建的 P0/事件查询缓存
```

SQLite 不能替代 JSONL 作为原始事实来源。分析过程可以构建自己的 P2 SQLite 索引，但该索引同样是可重建缓存。

每个 run 的 `metrics.sqlite3` P0 缓存当前默认关闭。需要临时恢复时，在 AgentMon TOML 中设置 `storage.sqlite=true`，或设置环境变量 `AGENTMON_STORAGE_SQLITE=true`。环境变量 `AGENTMON_STORAGE_SQLITE=false` 可以覆盖仍启用 SQLite 的旧配置。该开关不影响独立 Analysis Runtime 自己的 P2 索引。

`agentmon run` 的自动报告只应基于 finalization 完成后的 `events.jsonl` 快照。`report.json` 包含：

```json
{
  "event_snapshot": {
    "source": "events.jsonl",
    "valid_event_count": 0,
    "invalid_line_count": 0,
    "file_size_bytes": 0,
    "snapshot_after_writer_close": true
  },
  "finalization": {
    "child_done": true,
    "collectors_joined": true,
    "event_writer_closed": true,
    "trajectory_status": "IMPORTED",
    "p2_status": "IMPORTED",
    "completed": true
  }
}
```

`summary.json` 同步暴露 `event_count`、`event_file_size_bytes` 和 `finalization_completed`。
`finalization.trajectory_status` 是 runtime 结束时的快照；后处理补导入后的最终可分析状态
写入 `analysis_state.trajectory.status`，并在 summary 中暴露为
`analysis_trajectory_status`、`runtime_finalization_trajectory_status` 和
`post_run_trajectory_import_status`。如果缺少 trajectory 或 P2，状态必须保持
`NOT_CONFIGURED`、`MISSING_AFTER_TIMEOUT`、`NO_MATCHING_EVENTS` 等可审计状态，不得猜测补齐。

### 4.2 时间与身份

可发布的跨层分析应尽可能建立以下链路：

```text
experiment -> trial -> batch -> run -> agent step
           -> request attempt -> API request -> engine request
           -> scheduler iteration -> token/KV/resource sample
```

时间优先使用单调时钟做时长计算，并保留 wall clock 供跨进程关联。请求关联优先使用传播的稳定 ID，不应只凭时间邻近强行匹配。

### 4.3 输入目录

`analysis run-all --input` 可以指向一个实验根目录、一个导出包根目录、多个嵌套 batch/trial 的共同根目录，或者包含部分数据的目录。发现过程递归扫描，不要求固定目录结构。

可识别的典型输入包括：

- 含 `manifest.json` 和 `events.jsonl` 的 run 目录。
- `batch-manifest.json`、`batch-summary.json`、`batch-results.tsv`。
- `report.json`、`summary.json`、trajectory 和 step CSV。
- 共享 P2 JSONL、`.jsonl.gz` 和 `.jsonl.zst`。
- `batch-phase-events.jsonl`、`p2-index-metrics.json` 和 NPU 映射证据。

扫描前不要假定上述文件全部存在，也不要把某个导出包的 `core/`、`raw-p2/` 或 `run-events/` 写成通用前提。

## 5. 源码包部署

以下命令以 Linux Bash 为例。命令块只包含可执行命令。先修改路径变量，再逐块执行，不要把解释文字或终端输出粘贴回 shell。

### 5.1 设置包和安装目录

这一节只需要你提前填写三个路径：源码包所在目录、安装父目录和包名。
`AGENTMON_REPO` 是解压后的目录，不能在解包前要求它已经存在。

```bash
set -euo pipefail
umask 022

PACKAGE_DIR=/absolute/path/to/package-directory
INSTALL_PARENT=/absolute/path/to/install-parent
PACKAGE_NAME=agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz
AGENTMON_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/agentmon/bin/python
VLLM_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python
INSTALL_ROOT="$INSTALL_PARENT/agentmon-src-$(date +%Y%m%d-%H%M%S)"
PROVENANCE_ROOT="$INSTALL_ROOT/provenance"
mkdir -p "$PROVENANCE_ROOT"
```

检查文件：

```bash
ls -lh "$PACKAGE_DIR/$PACKAGE_NAME" "$PACKAGE_DIR/$PACKAGE_NAME.sha256"
```

### 5.2 校验 SHA256

GNU/Linux：

```bash
cd "$PACKAGE_DIR"
sha256sum -c "$PACKAGE_NAME.sha256"
tar -tzf "$PACKAGE_DIR/$PACKAGE_NAME" > "$PROVENANCE_ROOT/archive-members.txt"
```

系统没有 `sha256sum` 时：

```bash
cd "$PACKAGE_DIR"
shasum -a 256 -c "$PACKAGE_NAME.sha256"
```

只有输出 `OK` 才继续。校验失败时重新传包，不要继续分析。

解包前再检查归档成员。这里故意只用 Python 标准库，避免依赖本次源码已经解出：

```bash
test -x "$AGENTMON_PY"
"$AGENTMON_PY" - "$PACKAGE_DIR/$PACKAGE_NAME" <<'PY'
from pathlib import PurePosixPath
import sys
import tarfile

bad = []
with tarfile.open(sys.argv[1], "r:gz") as tar:
    for member in tar.getmembers():
        path = PurePosixPath(member.name)
        parts = path.parts
        unsafe = path.is_absolute() or ".." in parts or member.issym() or member.islnk()
        unsafe = unsafe or not (member.isdir() or member.isfile())
        forbidden = (
            any(part in {"__MACOSX", ".AppleDouble", "__pycache__"} for part in parts)
            or path.name == ".DS_Store"
            or path.name.startswith("._")
            or path.name.endswith((".pyc", ".pyo"))
        )
        if unsafe or forbidden:
            bad.append(member.name)
if bad:
    print("ARCHIVE_CONTENT=FAIL")
    print("\n".join(bad[:50]))
    raise SystemExit(1)
print("ARCHIVE_CONTENT=PASS")
PY
```

### 5.3 解压

```bash
mkdir -p "$INSTALL_ROOT"
tar --no-same-owner --no-same-permissions -xzf "$PACKAGE_DIR/$PACKAGE_NAME" -C "$INSTALL_ROOT"
mapfile -t REPOS < <(
  find "$INSTALL_ROOT" -type f -path '*/src/agentmon/__init__.py' -print |
    sed 's#/src/agentmon/__init__.py$##' |
    sort -u
)
test "${#REPOS[@]}" -eq 1
AGENTMON_REPO="${REPOS[0]}"
ls -la "$AGENTMON_REPO"
```

如果打开了新终端，之前设置的变量会消失，需要重新执行变量赋值。

服务器首次冒烟的路径边界：

- 需要人工确认：`PACKAGE_DIR`、`PACKAGE_NAME`、`INSTALL_PARENT`、`AGENTMON_PY`、`VLLM_PY`、
  以及离线分析输入 `INPUT` 和输出父目录 `OUTPUT_PARENT`。
- 解包后生成：`AGENTMON_REPO`。
- 运行时生成：`PROVENANCE_ROOT`、`REPORT_DIR`、`SMOKE_OUTPUT`、`FULL_OUTPUT`、`TEMP_ROOT`、
  `BATCH_ROOT`、`P2_ROOT`、`_p2_index/`、`analysis_bundle/`。
- `events_path` 的父目录可以提前创建；使用 server profile 且
  `create_events_parent=true` 时，wrapper 会为 AgentMon 自己的 P2 events 路径创建父目录。
  `events.jsonl` 文件可以由 P2 服务写出。

当前常用 Ascend 910B 服务器的 mini-SWE、vLLM、CANN 和模型服务路径已沉淀到
`examples/server_profiles/chenyang_ascend910b.json`。使用
`examples/server_test_config_chenyang_ascend910b.json` 时，`${AGENTMON_REPO}` 会自动指向
本次解包目录；缺少后续阶段输入时 wrapper 会返回 `NEEDS_INPUT/PARTIAL`，不应视为
源码自检失败。

### 5.4 选择独立 Python

建议使用独立 AgentMon 环境，不要把 AgentMon 安装进正在运行 vLLM 的环境。

```bash
AGENTMON_PY=/absolute/path/to/agentmon-python
VLLM_PY=/absolute/path/to/vllm-python
test -x "$AGENTMON_PY"
"$AGENTMON_PY" -c 'import sys; print(sys.executable); print(sys.version)'
"$AGENTMON_PY" -c 'import pytest, agentmon' 2>/dev/null || true
test -x "$VLLM_PY"
"$VLLM_PY" -c 'import sys; print(sys.executable); print(sys.version)'
```

`AGENTMON_PY` 要求 Python 3.10 及以上，并需要能运行本项目测试依赖；`VLLM_PY`
用于真实 vLLM/CANN 探测，两者可以不同。

### 5.5 无安装运行

在服务器依赖已经存在时，最稳妥的是通过 `PYTHONPATH` 直接运行源码：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$AGENTMON_PY" -m agentmon --version
PYTHONPATH="$AGENTMON_REPO/src" "$AGENTMON_PY" -m agentmon --help
```

这种方式不会修改 Python 环境，也不会依赖 shell 中残留的旧 AgentMon 安装。

### 5.6 可编辑安装

只有确认服务器允许安装依赖时才执行：

```bash
cd "$AGENTMON_REPO"
"$AGENTMON_PY" -m pip install --no-build-isolation -e '.[analysis]'
```

生产服务器不应自动回退到公共 PyPI。优先使用已有环境、内部镜像或内部 wheelhouse。

分析依赖规则：

- `numpy`、`pandas`：所有完整分析必需。
- `pyarrow`：Parquet 输出必需，CSV 输出时可选。
- `zstandard`：输入中存在 `.jsonl.zst` 时必需。
- `.jsonl.gz` 使用 Python 标准库 gzip，不要求 `zstandard`。

### 5.7 本地重建干净源码包

发布给服务器时不要手工 `tar` 整个工作目录。使用内置脚本构建源码包，它会固定收录
`src/`、`scripts/`、`docs/`、`examples/`、`tests/` 等源码材料，并过滤
`.DS_Store`、`._*`、`__MACOSX`、`__pycache__` 和 `.pyc`：

```bash
cd /path/to/agentmon-source
python3 scripts/package_agentmon_source.py \
  --output artifacts/packages/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz
```

脚本会同时写出 `.sha256`、`.summary.json` 和 `.BUILD_MANIFEST.json`。
`summary.status` 必须是 `PASS`，`summary.forbidden_members` 必须为空。包上传前或解包后可用
`$AGENTMON_REPO/scripts/agentmon_release_gate.py` 生成 `.TEST_MANIFEST.json`，记录实际执行的门禁命令和结果。
`--level quick` 或旧的 `--skip-full-tests` 只表示快速源码冒烟；full gate 通过后才允许进入
服务器静态 preflight。服务器上执行 gate 时不要依赖当前工作目录，脚本路径使用解包后的
绝对路径，控制台输出同时落盘：

```bash
PYTHONPATH="$AGENTMON_REPO/src${ACL_PY:+:$ACL_PY}" "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_release_gate.py" \
  --package "$PACKAGE_DIR/$PACKAGE_NAME" \
  --agentmon-python "$AGENTMON_PY" \
  --level quick \
  --deadline-seconds 600 \
  --provenance-dir "$PROVENANCE_ROOT" \
  --output "$PROVENANCE_ROOT/quick-gate.json" \
  --test-manifest "$PACKAGE_DIR/${PACKAGE_NAME}.TEST_MANIFEST.quick.json" \
  2>&1 | tee "$PROVENANCE_ROOT/quick-gate-console.log"

PYTHONPATH="$AGENTMON_REPO/src${ACL_PY:+:$ACL_PY}" "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_release_gate.py" \
  --package "$PACKAGE_DIR/$PACKAGE_NAME" \
  --agentmon-python "$AGENTMON_PY" \
  --vllm-python "$VLLM_PY" \
  --level full \
  --require-previous-gate "$PROVENANCE_ROOT/quick-gate.json" \
  --provenance-dir "$PROVENANCE_ROOT" \
  --output "$PROVENANCE_ROOT/full-source-gate.json" \
  --test-manifest "$PACKAGE_DIR/${PACKAGE_NAME}.TEST_MANIFEST.json" \
  2>&1 | tee "$PROVENANCE_ROOT/full-source-gate-console.log"
```

full gate 通过时应输出 `SOURCE_RELEASE_GATE=PASS`、`ELIGIBLE_FOR_SERVER_PREFLIGHT=YES`
和 `ELIGIBLE_FOR_NEXT_STAGE=YES`。quick gate 只输出 `QUICK_SMOKE=PASS`、
`ELIGIBLE_FOR_FULL_SOURCE_GATE=YES` 和 `ELIGIBLE_FOR_NEXT_STAGE=YES`。机器可读报告位于
`$PROVENANCE_ROOT/quick-gate.json`、`$PROVENANCE_ROOT/quick-gate.json.sha256`、
`$PROVENANCE_ROOT/full-source-gate.json` 和
`$PROVENANCE_ROOT/full-source-gate.json.sha256`。交付 `SHA256SUMS` 只绑定源码包与
交付 manifest，不包含服务器运行结果。
如果目标服务器通过独立 ACL Python 目录导入 `acl`，例如
`/usr/local/Ascend/cann-9.0.0/python/site-packages`，静态 preflight 和 Runtime P2 Gate
都要显式传入 `--acl-python-path "$ACL_PY"`，并记录 `readlink -f "$AGENTMON_PY"`、
`readlink -f "$MINI_SWE_PY"`、`readlink -f "$VLLM_PY"`。
完整服务器冒烟状态机固定为
`Package/Source Gate -> Static Preflight -> Resource Gate -> Service Start Gate -> Runtime P2 Gate -> Experiment`。
Runtime 阶段每轮必须使用独立设备、端口、`P2_ROOT`、`events.jsonl`、server log 和 PID
file，并把完整启动环境写入自包含启动脚本；Runtime Gate 的 Base URL、模型名、启动脚本和
事件文件必须来自同一个 `runtime-instance.env`。

## 6. 已有原始轨迹的推荐流程

这是服务器首次测试时应优先执行的流程。顺序是：环境识别、轻量清点、preflight、dry-run、fast smoke、结果核对、全量分析。

### 6.0 10 分钟冒烟快线

如果现场窗口只有约 10 分钟，先跑这一条快线。它的目标是证明源码包可导入、输入目录可发现、核心规范化链路能启动并产出可审计状态；它不是完整发布验收，不验证真实 vLLM/NPU 在线 runtime，不生成完整 Evidence Bundle / optimizer harness / model pack，也不支持性能或因果结论。

准备四个路径变量。`AGENTMON_PY` 是源码和离线分析 Python；若只跑这一条 fast smoke，
可以把 `PYTHON` 设成它的兼容别名：

```bash
export AGENTMON_PY=/absolute/path/to/agentmon-python
export PYTHON="$AGENTMON_PY"
export AGENTMON_REPO=/absolute/path/to/agentmon-source
export INPUT=/absolute/path/to/raw-experiment-or-batch-root
export OUTPUT_PARENT=/absolute/path/to/analysis-results
export RUN_TAG=$(date +%Y%m%d-%H%M%S)
export SMOKE_OUTPUT="$OUTPUT_PARENT/agentmon-10min-smoke-$RUN_TAG"
export TEMP_ROOT="${TMPDIR:-/tmp}/agentmon-10min-smoke-$RUN_TAG"
mkdir -p "$SMOKE_OUTPUT" "$TEMP_ROOT"
```

先确认 Python 和 AgentMon 入口：

```bash
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), sys.version; print(sys.executable, sys.version)'
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon --version
```

然后执行限时 fast smoke：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all \
  --input "$INPUT" \
  --output "$SMOKE_OUTPUT" \
  --temp-dir "$TEMP_ROOT" \
  --profile auto \
  --output-format csv \
  --preflight \
  --jobs 8 \
  --fast-smoke \
  --skip-evidence \
  --skip-handoff \
  --skip-model-pack \
  --no-figures \
  --status-file "$SMOKE_OUTPUT/analysis_status.json" \
  --progress-interval-seconds 10 \
  --timeout-seconds 600 \
  2>&1 | tee "$SMOKE_OUTPUT/run-all.log"
```

10 分钟冒烟验收只看可审计的核心信号：

```bash
"$PYTHON" -m json.tool "$SMOKE_OUTPUT/run_summary.json" | sed -n '1,120p'
"$PYTHON" -m json.tool "$SMOKE_OUTPUT/analysis_status.json" | sed -n '1,120p'
test -f "$SMOKE_OUTPUT/stage_dag.json"
test -f "$SMOKE_OUTPUT/01_normalized/normalization_execution.json"
test -f "$SMOKE_OUTPUT/04_evidence/skip_reason.json"
test -f "$SMOKE_OUTPUT/04_evidence/optimizer_experiment/skip_reason.json"
grep -E 'PROCESS_STATUS|PREFLIGHT_STATUS|ANALYSIS_SOP_STATUS|RUNS_PARSED|REQUEST_ATTEMPTS_PARSED|P2_REQUESTS_PARSED|RESUME_STATUS|ARTIFACT_STATUS|UNRESOLVED_QUESTIONS|FORBIDDEN_CONCLUSIONS' "$SMOKE_OUTPUT/run-all.log"
```

通过标准：

1. 命令完成或在 600 秒限时处留下明确 `analysis_status.json`，不能是无状态崩溃。
2. `RUNS_PARSED` 与输入目录实际规模相符；如果为 0，`00_inventory/` 或 `02_validation/analysis_ready.json` 必须能解释。
3. 输入包含 Agent 请求事件时，`REQUEST_ATTEMPTS_PARSED` 不应无解释为 0；输入包含可匹配 P2 数据时，`P2_REQUESTS_PARSED` 不应无解释为 0。
4. `04_evidence/skip_reason.json` 和 `04_evidence/optimizer_experiment/skip_reason.json` 在这条快线里是预期产物，表示完整 Evidence 与 optimizer harness 被主动跳过，不是失败。
5. 若状态为 `PARTIAL`，必须保留 `analysis_ready.json`、`data_quality_gate.json`、日志最终状态行和 skip reason，用这些材料说明限制条件。

快线通过后，再使用新的输出目录执行 6.8 的全量分析；完整 optimizer experiment harness 只在 evidence 输出启用且发现 batch artifact 时生成。

### 6.1 设置本次路径

输入必须是只读原始数据根目录，输出必须是一个新的独立目录。不要把输出放进输入目录内部，否则递归发现可能扫描自己的产物。

```bash
INPUT=/absolute/path/to/raw-experiment-root
OUTPUT_PARENT=/absolute/path/to/analysis-results
TEMP_ROOT=/absolute/path/to/analysis-temp
RUN_TAG=$(date +%Y%m%d-%H%M%S)
SMOKE_OUTPUT="$OUTPUT_PARENT/agentmon-smoke-$RUN_TAG"
FULL_OUTPUT="$OUTPUT_PARENT/agentmon-full-$RUN_TAG"
mkdir -p "$OUTPUT_PARENT" "$TEMP_ROOT"
```

### 6.2 轻量环境和目录清点

```bash
uname -a
df -h "$INPUT" "$OUTPUT_PARENT" "$TEMP_ROOT"
du -sh "$INPUT"
find "$INPUT" -maxdepth 3 -type f | sed -n '1,120p'
```

检查压缩输入类型和大文件：

```bash
find "$INPUT" -type f \( -name '*.jsonl' -o -name '*.jsonl.gz' -o -name '*.jsonl.zst' -o -name '*.sqlite3' \) | sed -n '1,120p'
find "$INPUT" -type f -printf '%s\t%p\n' | sort -nr | sed -n '1,50p'
```

这些命令只做清点，不解析整个事件流。

### 6.3 Preflight

首次运行建议使用 CSV，降低环境依赖：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis preflight \
  --input "$INPUT" \
  --output "$SMOKE_OUTPUT" \
  --temp-dir "$TEMP_ROOT" \
  --profile auto \
  --output-format csv \
  --json
```

继续执行的必要条件：

```text
PYTHON_PREFLIGHT_STATUS=PASS
REQUIRED_DEPENDENCIES_STATUS=PASS
HEAVY_ANALYSIS_ALLOWED=1
```

`PYARROW_STATUS=UNAVAILABLE` 在 CSV 模式下可以接受。输入包含 `.jsonl.zst` 时，`ZSTANDARD_STATUS` 必须是 `PASS`。

Preflight 失败时不会开始重扫描大文件。应先修复 Python、依赖、输入可读性、输出可写性或磁盘空间。

### 6.4 Dry-run

Dry-run 写出 Stage 执行计划，不做重型输入扫描：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all \
  --input "$INPUT" \
  --output "$SMOKE_OUTPUT" \
  --temp-dir "$TEMP_ROOT" \
  --profile auto \
  --output-format csv \
  --jobs 4 \
  --dry-run
```

检查：

```bash
"$PYTHON" -m json.tool "$SMOKE_OUTPUT/stage_execution_plan.json"
```

如果文件名因后续版本变化而不同，先执行：

```bash
find "$SMOKE_OUTPUT" -maxdepth 2 -type f | sort
```

### 6.5 Fast smoke

Fast smoke 仍会解析和规范化核心 run/request/P2 数据，但跳过体积较大的可选 event inventory、完整 evidence 和 model pack。对于数 GB 的共享 P2 流，它不是几秒钟的采样命令，仍可能需要较长时间。

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all \
  --input "$INPUT" \
  --output "$SMOKE_OUTPUT" \
  --temp-dir "$TEMP_ROOT" \
  --profile auto \
  --output-format csv \
  --preflight \
  --jobs 4 \
  --fast-smoke \
  --no-figures \
  --status-file "$SMOKE_OUTPUT/analysis_status.json" \
  --progress-interval-seconds 30 \
  2>&1 | tee "$SMOKE_OUTPUT/run-all.log"
```

首次 smoke 不建议加 `--strict`。缺少资源或深层事件时，分析可能合法地返回 `PARTIAL`；先检查解析计数和审计，再决定是否把它作为严格发布门禁。

不要把以下内容粘贴进终端：

```text
===== QUICK CHECK =====
[INFO] ...
Process finished with exit code ...
```

它们是输出或说明，不是命令。不要为了执行分析使用 `exit`、`exec` 或全局 `set -e`，它们可能关闭终端或让会话在一个非零状态处提前结束。

### 6.6 观察长任务

新开一个终端后，重新设置 `SMOKE_OUTPUT`，再执行：

```bash
watch -n 10 "cat '$SMOKE_OUTPUT/analysis_status.json'"
```

没有 `watch` 时：

```bash
"$PYTHON" -m json.tool "$SMOKE_OUTPUT/analysis_status.json"
```

查看日志：

```bash
tail -n 100 "$SMOKE_OUTPUT/run-all.log"
```

查看进程资源：

```bash
ps -eo pid,ppid,etime,%cpu,%mem,rss,cmd | grep '[p]ython'
```

关键状态字段包括：

- `CURRENT_PHASE`：当前 Stage。
- `RAW_EVENTS_READ`、`EVENT_BYTES_READ`：原始流进度。
- `P2_INDEX_EVENTS_INDEXED`：共享 P2 索引进度。
- `RUNS_SCANNED`、`RUNS_PARSED`：run 规范化进度。
- `REQUEST_ATTEMPTS_PARSED`、`P2_REQUESTS_PARSED`：请求解析结果。
- 速率、ETA、RSS 和临时文件系统剩余空间。

`build_p2_index` 对单个共享压缩流采用确定性的顺序读取和索引，单个 Python 进程单核占用可能是正常现象。`normalize_runs` 才会依据 `--jobs` 并行。应根据 `CURRENT_PHASE` 判断，而不是仅看总 CPU。

### 6.7 Smoke 验收

```bash
"$PYTHON" -m json.tool "$SMOKE_OUTPUT/run_summary.json"
"$PYTHON" -m json.tool "$SMOKE_OUTPUT/02_validation/analysis_ready.json"
"$PYTHON" -m json.tool "$SMOKE_OUTPUT/01_normalized/request_parse_audit_summary.json"
"$PYTHON" -m json.tool "$SMOKE_OUTPUT/01_normalized/normalization_execution.json"
```

提取最终状态行：

```bash
grep -E 'PROCESS_STATUS|PREFLIGHT_STATUS|ANALYSIS_SOP_STATUS|RUNS_PARSED|REQUEST_ATTEMPTS_PARSED|P2_REQUESTS_PARSED|RESUME_STATUS|ARTIFACT_STATUS|UNRESOLVED_QUESTIONS|FORBIDDEN_CONCLUSIONS' "$SMOKE_OUTPUT/run-all.log"
```

基本验收逻辑：

1. `PROCESS_STATUS` 应为完成态，不能是执行失败。
2. `RUNS_PARSED` 应与实际发现的 run 规模相符。
3. 有 Agent 请求事件时，`REQUEST_ATTEMPTS_PARSED` 不应无解释地为 0。
4. 有 P2 数据且 Profile 匹配时，`P2_REQUESTS_PARSED` 不应无解释地为 0。
5. `request_parse_audit_summary.json` 应解释每个 source 的候选、解析、拒绝和去重数量。
6. `normalization_execution.json` 应说明请求的 jobs、实际 jobs 和任何降级原因。
7. `PARTIAL` 时必须从 `analysis_ready.json` 找到受限模块和原因，不能直接当成脚本失败。

### 6.8 全量分析

Smoke 计数和审计合理后，使用新的输出目录执行全量分析。全量运行默认生成 Evidence Bundle 和 `model_input_pack`，仍默认省略重复原始事件的 `event_inventory`。

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all \
  --input "$INPUT" \
  --output "$FULL_OUTPUT" \
  --temp-dir "$TEMP_ROOT" \
  --profile auto \
  --output-format csv \
  --preflight \
  --jobs 4 \
  --no-figures \
  --status-file "$FULL_OUTPUT/analysis_status.json" \
  --progress-interval-seconds 30 \
  2>&1 | tee "$FULL_OUTPUT/run-all.log"
```

确认数据能力完整、并希望把 `PARTIAL` 或验证失败转换为非零退出码时，再加 `--strict`。

服务器具备 `pyarrow` 且 Parquet smoke 通过时，可以把 `--output-format csv` 改成：

```bash
--output-format parquet
```

不要在同一个 bundle 内混用 CSV 和 Parquet 重跑。格式或 Profile 变化时使用新的输出目录，或者显式 `--fresh`。

## 7. 恢复、阶段控制和资源保护

### 7.1 正常恢复

进程中断后，用完全相同的输入、输出、配置、Profile 和格式恢复：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all \
  --input "$INPUT" \
  --output "$FULL_OUTPUT" \
  --temp-dir "$TEMP_ROOT" \
  --profile auto \
  --output-format csv \
  --jobs 4 \
  --resume \
  --no-figures \
  --status-file "$FULL_OUTPUT/analysis_status.json" \
  --progress-interval-seconds 30 \
  2>&1 | tee -a "$FULL_OUTPUT/run-all.log"
```

恢复会核对输入、代码、Profile、依赖、配置、Schema 和缓存产物。`resume_status.json` 为 `REJECTED` 时，不要立刻使用 `--force-resume`。先检查不兼容项；通常应换新输出目录或使用 `--fresh`。

### 7.2 Stage 控制

Stage 名称以 bundle 的 `stage_dag.json` 为准。

```bash
"$PYTHON" -m json.tool "$FULL_OUTPUT/stage_dag.json"
```

常用选项：

- `--from-stage NAME`：从指定阶段开始规划。
- `--to-stage NAME`：运行到指定阶段后正常停止。
- `--force-stage NAME`：强制重算该阶段及受影响的下游。
- `--invalidate-stage NAME`：将阶段标记为失效，不直接删除旧产物。
- `--fresh`：忽略已有 manifest/cache，完整新跑。

阶段名称不要凭文档猜测，先读取当前 bundle 的 DAG。

### 7.3 超时、内存和磁盘

可选保护参数：

```text
--timeout-seconds N
--memory-limit-bytes N
--min-free-bytes N
--temp-dir PATH
--keep-temp
```

示例只展示参数组合，不代表推荐固定数值：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all \
  --input "$INPUT" \
  --output "$FULL_OUTPUT" \
  --profile auto \
  --output-format csv \
  --jobs 4 \
  --memory-limit-bytes 17179869184 \
  --min-free-bytes 53687091200 \
  --timeout-seconds 21600 \
  --temp-dir "$TEMP_ROOT" \
  --resume
```

这些值必须根据服务器资源重新计算，不属于通用默认值。

### 7.4 可选的重型校验

以下选项可能显著增加 I/O 和时间，只在发布审计或输入损坏排查时使用：

```text
--full-jsonl-integrity
--full-file-checksums
--include-event-inventory
```

## 8. Baseline 和严格退出码

预期数量必须来自实验清单、导出索引或运行方确认，不能写死在分析代码中：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all \
  --input "$INPUT" \
  --output "$FULL_OUTPUT" \
  --profile auto \
  --output-format csv \
  --expected-runs "$EXPECTED_RUNS" \
  --expected-request-attempts "$EXPECTED_REQUEST_ATTEMPTS" \
  --expected-p2-requests "$EXPECTED_P2_REQUESTS" \
  --strict
```

尚不知道预期数量时，不要传这些参数。探索性分析需要在不阻断执行的情况下记录差异时，可用 `--allow-baseline-mismatch`。

严格模式退出码：

| 退出码 | 含义 |
|---:|---|
| 0 | 成功，或请求的终止 Stage 成功完成 |
| 2 | Baseline 数量不匹配 |
| 3 | Preflight 失败 |
| 4 | 执行失败 |
| 5 | 验证或最终状态未通过 |
| 6 | Resume 不兼容 |
| 7 | 必需产物不可用 |
| 64 | CLI 用法错误 |

非严格模式的 `run-all` 可能在分析状态为 `PARTIAL` 时仍返回 0，这是为了保留可用证据。判断结果必须同时看进程退出码、`run_summary.json` 和 `analysis_ready.json`。

## 9. Analysis Bundle 说明

主要目录：

```text
00_inventory/       输入、文件、run、batch、实验合同、研究问题、完整性和 P2 索引信息
01_normalized/      experiment/trial/batch/run/request/resource 等规范事实表
02_validation/      确定性校验、守恒检查和 analysis_ready
02_semantics/       Metric 字典、实体集合定义和时钟域审计
03_analysis/        吞吐、并发、生命周期、长尾、资源、调度等分析结果
04_evidence/        已验证事实、关联、推断、待解决问题和结论约束
05_handoff/         基础 observation/anomaly/contradiction/unresolved/backlog 注册表
model_input_pack/   供语言模型写报告的紧凑输入
stages/             每个 Stage 的 checkpoint、耗时、资源和错误
```

根目录关键文件：

```text
analysis_status.json
run_summary.json
stage_dag.json
resume_status.json
artifact_manifest.json
resolved_config.json
```

最重要的审计文件：

```text
01_normalized/request_parse_audit_summary.json
01_normalized/request_parse_audit.csv
01_normalized/request_parse_conservation.csv
01_normalized/request_identity_audit.csv
01_normalized/normalization_execution.json
00_inventory/experiment_contract.json
00_inventory/contract_validation.csv.gz 或 Parquet
00_inventory/p2_index_metrics.json
02_validation/analysis_ready.json
02_validation/data_quality_gate.json
02_semantics/metric_dictionary.md
03_analysis/p2_match_audit.csv.gz 或 Parquet
03_analysis/p1_capture_summary.csv.gz 或 Parquet
04_evidence/claim_registry.json
04_evidence/forbidden_conclusions.json
05_handoff/handoff_manifest.json
```

CSV 模式下，规范事实表通常以 `.csv.gz` 保存，部分便于人工审计的小表同时提供普通 `.csv`。

实验合同是标准分析的数据底座。服务器实验有明确设计时，建议先生成模板，补全实验特定字段，通过校验后再作为 `run-all` 输入：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis build-contract-template \
  --output "$FULL_OUTPUT/experiment-contract.json"

PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis validate-contract \
  --contract "$FULL_OUTPUT/experiment-contract.json"

PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all \
  --input "$EXPERIMENT_ROOT" \
  --output "$FULL_OUTPUT" \
  --experiment-contract "$FULL_OUTPUT/experiment-contract.json"
```

未提供合同时，`experiment_contract.json` 会记录 `contract_status=PARTIAL`，可从 discovery 得到的字段标记为 `DISCOVERY`，其余保持 `UNKNOWN`。`05_handoff` 只整理基础分析事实、异常、禁止结论和未解决问题，不执行定制化深入分析闭环。

## 10. 状态语义和结论纪律

### 10.1 状态

- `PASS`：当前数据和规则足以通过该检查。
- `PARTIAL`：部分数据可用，但缺失、覆盖不足或证据强度有限。
- `FAIL`：确定性规则失败，或必需能力不可用。
- `SKIPPED`：按配置、fast smoke 或阶段范围主动跳过。
- `CACHED`：兼容检查通过后复用缓存。
- `UNAVAILABLE`：环境或输入不提供该可选能力。
- `UNRESOLVED`：证据无法确定映射或机制。

### 10.2 不可越过的结论门禁

在以下情况中，不应写强机制结论：

- P1 capture 不是 `PASS`，但结论依赖 Agent step 或 model/tool 边界。
- P2 audit 不是 `PASS`，但结论依赖 engine 内部生命周期。
- token timeseries 不是 `PASS`，但结论依赖稳态 generated-token throughput。
- NPU mapping 未通过，但结论把某个进程负载归因到具体 NPU。
- 生命周期守恒、流水线对账或 Little Law 校验未通过。
- 跨 bundle 比较存在不可比维度。

`model_input_pack` 中的 `allowed_conclusions`、`forbidden_conclusions`、`unresolved_questions` 和 `claim_registry` 是报告约束，不是可忽略的提示。

## 11. 模型报告工作流

全量 `run-all` 默认已经生成 `model_input_pack`。需要单独重建时：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis build-model-pack \
  --bundle "$FULL_OUTPUT" \
  --output "$FULL_OUTPUT/model_input_pack"
```

交给模型时，优先提供 `model_input_pack/`，不要要求模型重新遍历数 GB 原始 JSONL 并自行计算指标。模型应使用 pack 内证据索引并保留结论分类。

模型生成 Markdown 报告后执行：

```bash
REPORT_MD=/absolute/path/to/generated-report.md
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis verify-report \
  --model-pack "$FULL_OUTPUT/model_input_pack" \
  --report "$REPORT_MD" \
  --output "$FULL_OUTPUT/report-validation"
```

比较多个实验 bundle：

```bash
COMPARE_OUTPUT=/absolute/path/to/comparison-output
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis compare \
  --bundle /absolute/path/to/bundle-a \
  --bundle /absolute/path/to/bundle-b \
  --output "$COMPARE_OUTPUT"
```

比较前会检查任务集、模型、推理框架、AgentMon 版本、硬件可见性和 Schema 等可比维度。

## 12. 常见故障定位

### 12.1 找不到 `smoke.log`

常见原因是新终端里的 `SMOKE_OUTPUT` 为空、路径拼错，或者分析命令没有通过 `tee` 写日志。

```bash
printf 'SMOKE_OUTPUT=%s\n' "$SMOKE_OUTPUT"
test -n "$SMOKE_OUTPUT" && echo VARIABLE_SET || echo VARIABLE_EMPTY
find "$OUTPUT_PARENT" -maxdepth 3 -name 'run-all.log' -o -name 'run_summary.json'
```

不要依赖一个旧终端里临时设置的 `REPORT_DIR`。排障时直接使用绝对路径更可靠。

### 12.2 `RUNS_PARSED=0`

优先检查：

1. `INPUT` 是否指到了真正包含数据的共同根目录。
2. `00_inventory/input_inventory_summary.json` 是否发现 run/event/report/trajectory。
3. `00_inventory/inventory_summary.json` 和 manifest 中的 rejected/unknown 文件原因。
4. Profile 自动检测结果是否与实际 AgentMon 版本匹配。
5. 输入是否只是导出的索引，而没有权威事件或 per-run 数据。

不要因为文件夹名字看起来像某次实验，就假定 run discovery 成功。

### 12.3 `RUNS_PARSED>0` 但请求为 0

检查：

```bash
"$PYTHON" -m json.tool "$FULL_OUTPUT/01_normalized/request_parse_audit_summary.json"
find "$FULL_OUTPUT/01_normalized" -maxdepth 1 -name 'request_parse*' -o -name 'request_identity*' | sort
```

需要确认候选事件类型、字段映射、拒绝原因、去重数量和身份链。不要直接修改通用解析器来匹配一份数据；先判断是否需要新增或修正 AgentMon/vLLM Profile。

### 12.4 运行很慢且只有一个核

先读取 `CURRENT_PHASE`：

- `build_p2_index`：单个压缩流顺序解压和建立索引，单核可能正常。
- `normalize_runs`：应检查 `normalization_execution.json` 的 effective jobs 和 fallback reason。
- `full_file_checksums` 或 `full_jsonl_integrity`：属于显式重型审计。
- 输出目录位于慢盘或临时盘空间不足：检查 `df -h`、I/O wait 和状态文件。

同一输入和兼容配置的第二次运行应优先复用 P2 索引或已完成 Stage。不要删除 cache 后再抱怨 resume 没有变快。

### 12.5 Bundle 很大

常见来源包括 materialized event inventory、规范化大表、全量校验和 Evidence。默认不要加 `--include-event-inventory`。初次验证使用 `--fast-smoke --no-figures`，最终分析再生成 Evidence 和 model pack。

不要把 output 放到 input 内，也不要把之前生成的 bundle 再次作为原始输入根目录的一部分。

### 12.6 `NPU_MAPPING_STATUS=UNRESOLVED`

这表示当前数据不能证明 PID/rank/device 对应关系。它不影响所有指标，但禁止把进程级指标强行归因到某个 NPU。需要补充 runtime 捕获的 PID、rank、device visibility、启动参数和 `npu-smi` 证据。

### 12.7 Resume 被拒绝

```bash
"$PYTHON" -m json.tool "$FULL_OUTPUT/resume_status.json"
```

输入、代码、Profile、Schema、配置、依赖或产物 hash 变化都可能导致拒绝。推荐新建输出目录。只有人工确认旧缓存仍满足语义兼容时才考虑 `--force-resume`。

## 13. 采集新的 Agent/vLLM 实验

这一节用于需要重新跑实验时。只分析旧数据时可以跳过。

### 13.1 环境 Doctor

复制并修改 `examples/config.target.toml`，至少检查输出目录、Agent 路径、Python、vLLM URL、model、NPU 和采样参数。

```bash
AGENTMON_CONFIG=/absolute/path/to/agentmon-target.toml
DOCTOR_OUTPUT=/absolute/path/to/doctor.json
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon doctor \
  --config "$AGENTMON_CONFIG" \
  --output "$DOCTOR_OUTPUT"
```

Doctor 会报告 host、vLLM `/metrics`、`/v1/models`、`npu-smi`、mini-SWE 适配器和路径能力。单个探针失败通常记录为 partial/unsupported/timeout，而不是隐藏失败。

### 13.2 P0 包裹任意 Agent 命令

```bash
TARGET_REPO=/absolute/path/to/target-git-repository
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon run \
  --config "$AGENTMON_CONFIG" \
  --cwd "$TARGET_REPO" \
  --label agent-smoke \
  -- /absolute/path/to/agent-command argument1 argument2
```

`--` 后面是被监控的真实命令。通用 runtime 不需要知道它属于哪个 Agent 框架。

### 13.3 查看单次运行

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon live RUN_ID --once --config "$AGENTMON_CONFIG"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon report RUN_ID --config "$AGENTMON_CONFIG"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon summary RUN_ID --format json --config "$AGENTMON_CONFIG"
```

把 `RUN_ID` 替换为实际 run ID。不要保留大写示例字符串原样执行。

### 13.4 P1 和 P2

P1 通过 Agent framework adapter 捕获 step/model/tool 语义。mini-SWE 的详细配置见 `README.md` 和 `docs/p2_deep_agent_workload_zh.md`。

P2 Hook 安装默认关闭，必须在 vLLM 服务启动前设置 `ENABLE`。Hook 安装后 Deep P2
默认开启；下列 `DEEP=1` 仅用于配置留痕：

```bash
export AGENTMON_VLLM_P2_ENABLE=1
export AGENTMON_VLLM_P2_DEEP=1
```

P2 发现和启动必须继承 vLLM 自己的 CANN/ACL 环境，包括服务器实际使用的 `LD_LIBRARY_PATH`、`ASCEND_HOME_PATH` 等。不要从 AgentMon 的普通分析环境直接推断 vLLM import 是否可用。

当前 Hook 对 vLLM 0.18.0 做版本门控。版本不同应先执行 discovery 和 compatibility 检查，再增加版本 adapter，不能关闭门控强行套用。

MoE 路由监测默认关闭。启用摘要或 trace 时，分别设置
`AGENTMON_VLLM_P2_MOE=summary|trace`，并在 vLLM 启动命令中增加
`--enable-return-routed-experts`。它观测的是逻辑 top-k expert ID，不是物理
NPU/rank 或专家执行时间；完整契约见 `docs/p2_vllm_adapter.md`。

共享服务上的 per-run P2 关联应优先使用 trajectory 中传播的 response/request ID，不应只依赖 byte offset 时间片。

单次 `agentmon run` 可以在 finalization 阶段自动导入本次 run 的 P2 候选窗口。推荐在 TOML 中设置：

```toml
[finalization]
p2_events_path = "/absolute/path/to/service-p2/events.jsonl"
p2_import_quality = "service_exclusive"
p2_global_event_policy = "auto"
```

也可通过环境变量传入：`AGENTMON_FINALIZATION_P2_EVENTS_PATH`、`AGENTMON_P2_EVENTS_PATH`、`AGENTMON_VLLM_P2_EVENTS_PATH`、`AGENTMON_SERVICE_P2_EVENTS_PATH`，或父 shell 中的 `AGENTMON_EVENTS_PATH`。AgentMon 会在 run 启动前记录 `p2_start_offset`，child 与 collectors 完成后记录 `p2_end_offset`；如果 trajectory 已导入，优先用 `assistant.extra.response.id` 过滤 P2 request lifecycle。没有可用 ID 时只保留 service-window 低置信观测，不把它伪装成排他归属。

### 13.5 批量并发运行

批量运行器入口：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" "$AGENTMON_REPO/scripts/agentmon_batch_runtime.py" --help
```

先以当前源码的 `--help` 为准，再按 `docs/agent_runtime_full_usage_zh.md` 准备 task TSV、并发参数、Agent 命令模板、P2 事件路径和输出目录。不要从旧聊天记录复制可能已经变化的长命令。

## 14. Agent Runtime、调度和控制

### 14.1 提交普通任务

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon runtime submit \
  --backend process \
  --priority 10 \
  --label runtime-smoke \
  --cwd /absolute/path/to/workdir \
  --config "$AGENTMON_CONFIG" \
  -- /absolute/path/to/command argument1
```

查看队列和 backend 能力：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon runtime queue --config "$AGENTMON_CONFIG"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon runtime capabilities --config "$AGENTMON_CONFIG"
```

查看单个 run 的派生 TaskState：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon runtime task-state \
  --config "$AGENTMON_CONFIG" \
  <run-id-or-run-dir>
```

TaskState 是从 `events.jsonl` 重建的派生产物，包含 `UNKNOWN/PARTIAL` 数据质量、限制和证据引用，不替代原始事件。

执行一个有界队列 drain：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon runtime daemon \
  --once \
  --max-runs 1 \
  --config "$AGENTMON_CONFIG"
```

恢复过期 running lease：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon runtime recover \
  --stale-seconds 3600 \
  --action defer \
  --config "$AGENTMON_CONFIG"
```

### 14.2 调度与 Optimizer

查看已注册策略：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon strategy list --domain all
```

查看当前决策：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon optimizer decisions --config "$AGENTMON_CONFIG"
```

提供模型服务遥测快照：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon optimizer decisions \
  --model-service-snapshot /absolute/path/to/model-service-snapshot.json \
  --config "$AGENTMON_CONFIG"
```

Optimizer 的输入应是规范化快照，例如 queue depth、running/waiting、queue latency、KV usage 和资源压力。算法输出 `OptimizerDecision` 或 `ControlAction`，由 control plane 校验权限、冷却和边界后执行。

### 14.3 控制 vLLM batch 的推荐方式

按侵入性从低到高：

1. Model Gate 动态控制进入 vLLM 的最大 inflight 请求数。
2. Model Provider 在多个 endpoint 之间路由，或按 workload class 设置准入。
3. 使用推理服务公开的管理能力修改支持的参数。
4. 通过版本化 vLLM adapter 修改内部 batch/scheduler 参数。

通用 runtime 不应直接 import vLLM scheduler。优先用请求准入实现稳定控制；只有公开接口不足时才使用版本化 adapter，并要求 capability discovery、读回验证、失败回滚和版本门控。

Model Gate 状态：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon control model-gate status --config "$AGENTMON_CONFIG"
```

代理一次 chat completions 请求前，先查看帮助并准备 JSON payload：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon control model-gate chat-completions --help
```

### 14.4 调度 CPU 给 Agent Tool

Tool Pool 只控制经它执行的命令。它按 executable/workload class 分类，并可配置并发上限、CPU affinity、nice 和内存限制。

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon control tool-pool status --config "$AGENTMON_CONFIG"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon control tool-pool classify --config "$AGENTMON_CONFIG" -- /absolute/path/to/tool argument1
```

执行工具：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon control tool-pool exec \
  --config "$AGENTMON_CONFIG" \
  --cwd /absolute/path/to/workdir \
  --timeout-seconds 600 \
  -- /absolute/path/to/tool argument1
```

如果需要 cgroup v2 CPU quota、cpuset 或 memory.max，应新增或配置 `ResourceActuator` backend，并由服务器权限和容器策略决定能否执行。不要在通用调度算法里直接写 `/sys/fs/cgroup`。

### 14.5 配置原则

主动速度优化的总开关为：

```toml
[runtime_optimization]
enabled = true
```

也可用 `AGENTMON_RUNTIME_OPTIMIZATION_ENABLED=false` 临时关闭。关闭只旁路
Scheduler/Optimizer/Model Gate/Tool Pool/Resource Actuator 和上下文压缩的主动动作，
P0/P1/P2 和 TaskState 证据仍保留。总开关与子开关同时为 true 时，对应策略才实际生效。

Coding Agent 上下文压缩是独立子开关：

```toml
[context_compression]
enabled = true
trigger_strategy = "token_ratio"
strategy = "model_summary"
plugin_modules = []
trigger_ratio = 0.80
target_ratio = 0.20
window_tokens = 0
estimator_chars_per_token = 4.0
trigger_options_json = '{}'
strategy_options_json = '{"min_recent_groups":1}'
```

`window_tokens=0` 时使用 `[model].max_model_len`。`trigger_strategy` 定义何时压缩，
`strategy` 定义如何压缩。默认 `model_summary` 通过 mini-SWE Adapter 复用当前模型生成
独立、可关联和可计量的 continuation summary；失败时原上下文旁路，不回退到静默截断。
外部模块可分别注册 `ContextCompressionTrigger` 和
`ContextCompressionStrategy`，再用 `plugin_modules` 加载；新的触发方式或压缩算法均无需改
Runtime 或 mini-SWE Adapter。

主 runtime 使用 TOML 配置，分析 runtime 使用 JSON/YAML 分析配置。以下参数属于策略配置，不是不可变常数：

- scheduler strategy 和最大 running runs。
- workload-aware scheduler 的 P90 token 预算、long-context 限制和 abnormal-path 风险阈值。
- optimizer mode、strategy 和负载阈值。
- finalization 的 collector join timeout、writer close timeout、trajectory wait/poll/stable checks、P2 source path 与 P2 import quality。
- TaskState 进展阈值、重复 tool/error 窗口和上下文增长阈值。
- model gate 的 inflight 上下限。
- tool pool 分类、并发、CPU、nice 和内存限制。
- context compression 的开关、策略名、插件模块、窗口来源、触发/目标比例、估算器和
  策略私有选项。
- analysis 完整性阈值、守恒误差、稳态窗口和统计参数。
- Profile 检测规则、事件别名和版本 Eligibility。

生产启用控制动作时，建议先 `advise`，通过 replay 和小流量灰度后再进入 `enforce`。

## 15. 即插即用扩展

新增能力应优先落在以下扩展点：

- `RuntimeBackend`：进程、容器、Kubernetes 或其他执行后端。
- `AgentFrameworkAdapter`：新的 Agent 框架和 workload 语义。
- `ModelProvider`：OpenAI-compatible、vLLM、SGLang 或路由服务。
- `InferenceTelemetryProvider`：推理服务遥测和 capability discovery。
- `ToolExecutor`/`ResourceActuator`：新的隔离与资源控制机制。
- `SchedulerStrategy`：公平、优先级、DRF、deadline 等队列策略。
- `OptimizerStrategy`：AIMD、PID、预测式或多目标优化。
- `ContextCompressionTrigger`：token 比例、回合数、低进展、工具输出增长或显式标记触发。
- `ContextCompressionStrategy`：tokenizer 精确计数、抽取式压缩、语义摘要、工具结果去重或
  检索式记忆。
- `Analysis Profile`：新的事件 Schema、版本和字段别名。
- `Analyzer`：只依赖规范表的新分析模块。
- `Reporter`/Claim policy：新证据呈现和结论约束。

扩展的最低要求：

1. 声明名称、版本、输入契约、输出契约和 capability。
2. 缺少输入字段时可降级，不能伪造 0 或默认事实。
3. 配置和阈值可序列化，并进入 hash/resume 兼容检查。
4. 决策和控制动作写审计事件，支持 replay。
5. 有 contract test、fixture 和失败路径测试。
6. 框架或版本特有代码不进入通用核心。

## 16. 本地或服务器源码自检

快速语法和 CLI 检查：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m compileall -q "$AGENTMON_REPO/src" "$AGENTMON_REPO/scripts"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon --help
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m agentmon analysis run-all --help
```

完整测试：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" -m unittest discover -s tests
```

服务器没有分析依赖时，部分测试可能不能代表完整分析能力，因此测试之后仍必须执行针对真实 `INPUT` 的 preflight。

## 17. 给另一个模型的排障材料

当需要网页版模型指导时，先提供以下信息。路径可以脱敏，但目录层级、扩展名、大小、状态和错误信息不要省略。

### 17.1 环境

- 操作系统和 shell。
- Python 可执行文件绝对路径和版本。
- AgentMon 源码绝对路径、包名和 SHA 校验结果。
- `numpy`、`pandas`、`pyarrow`、`zstandard` 的 preflight 状态。
- 是否允许联网或安装内部 wheel。
- CPU 核数、内存、输出盘和临时盘可用空间。

### 17.2 数据

- 输入根目录绝对路径。
- `du -sh` 总大小。
- 目录前 2 到 3 层文件列表。
- 最大的 20 到 50 个文件及大小。
- JSONL、gzip、zstd、SQLite、trajectory、report 和 manifest 的数量。
- 数据是原始采集目录、导出包，还是二者混合。

### 17.3 执行现场

- 完整命令，必须包括所有参数。
- 进程退出码。
- `analysis_preflight.json`。
- `analysis_status.json`。
- `run_summary.json`。
- `resume_status.json`。
- 当前失败 Stage 对应的 `stages/*.json`。
- `request_parse_audit_summary.json`。
- `normalization_execution.json`。
- `p2_index_metrics.json`。
- `run-all.log` 最后 100 到 300 行。

不要先传整个数 GB bundle。先传上述小型 JSON、日志和目录清单，模型确认需要后再提供特定表或样本。

### 17.4 一次性生成诊断清单

下面命令只输出信息，不修改输入。先确认各变量不为空：

```bash
printf 'AGENTMON_REPO=%s\nAGENTMON_PY=%s\nINPUT=%s\nFULL_OUTPUT=%s\n' "$AGENTMON_REPO" "$AGENTMON_PY" "$INPUT" "$FULL_OUTPUT"
```

然后执行：

```bash
uname -a
"$AGENTMON_PY" -c 'import sys, platform; print(sys.executable); print(sys.version); print(platform.platform())'
df -h "$INPUT" "$FULL_OUTPUT" "$TEMP_ROOT"
du -sh "$INPUT" "$FULL_OUTPUT"
find "$INPUT" -maxdepth 3 -type f | sed -n '1,200p'
find "$FULL_OUTPUT" -maxdepth 2 -type f | sort | sed -n '1,240p'
```

## 18. 可直接交给指导模型的提示词

将下面文字和第 17 节材料一起交给模型。不要把这个提示词当作 shell 命令执行。

```text
你是 AgentMon/AGENTRUN 的服务器运行指导和故障定位工程师。

项目说明见我提供的《AgentMon/AGENTRUN 项目介绍、服务器使用与模型交接手册》。请严格以当前源码的 CLI --help、preflight JSON、status JSON 和审计产物为事实来源，不要根据旧聊天记录猜测参数。

你的任务是指导我在服务器上完成已有原始轨迹的 AgentMon Analysis Runtime 测试，顺序必须是：
1. 识别服务器 shell、Python、源码、依赖、输入、输出和临时盘；
2. SHA 校验和无安装 CLI 自检；
3. 轻量目录清点；
4. CSV 模式 analysis preflight；
5. dry-run；
6. fast smoke；
7. 检查解析计数、请求守恒、Profile、P2 索引、并行状态和 analysis_ready；
8. smoke 合格后再给全量和 resume 命令。

交互规则：
- 一次只给我一小段可执行命令，等我粘贴完整输出后再给下一步。
- 每个命令必须放在独立代码块中，解释文字不能混入命令块。
- 不要让我把 =====、[INFO]、Process finished 等输出文本作为命令执行。
- 不使用 exit、exec、全局 set -e，也不要给可能关闭当前终端的命令。
- 不假设环境变量在新终端仍存在；每一步先检查关键变量，必要时使用绝对路径。
- 不修改、移动或删除原始输入；输出必须在输入目录之外的新目录。
- 不在 vLLM 环境里安装 AgentMon，优先使用独立 Python 和 PYTHONPATH 源码模式。
- 不知道依赖和路径时先检查，不要直接给 pip、公网下载或 sudo 命令。
- 当前阶段如果是 build_p2_index，不要仅凭单核判断卡死；结合字节、事件、心跳、RSS 和磁盘判断。
- PARTIAL 不自动等于失败；必须从 analysis_ready 和审计文件解释原因。
- 不把某次实验的计数、并发档位、字段、门限或目录写成通用规则。
- 不能从证据证明的因果、NPU 映射或 vLLM 内部机制，必须标为未解决，不能过度结论。

我会先提供：OS/shell、Python 路径和版本、源码与包路径、SHA 结果、input/output/temp 路径、preflight、目录清单和当前错误。请先审阅这些信息，然后只给第一步命令。
```

## 19. 相关文档导航

- `README.md`：AgentMon 观测、P0/P1/P2、服务器路径和基础 CLI。
- `docs/analysis_sop.md`：确定性分析 SOP 的精简规范。
- `docs/agent_runtime_architecture_design_zh.md`：完整 runtime 架构、契约、控制面和插件设计。
- `docs/agent_runtime_full_usage_zh.md`：mini-SWE/vLLM 批量实验完整教程。
- `docs/runtime_speed_optimization_algorithms_zh.md`：不修改推理引擎或底层系统软件时的优化边界、总开关、算法和 A/B 方法。
- `docs/server_release_smoke_zh.md`：真实 vLLM/NPU 目标环境发布门禁。
- `docs/p2_vllm_adapter.md`：vLLM P2 适配要求。
- `docs/p2_deep_agent_workload_zh.md`：深层 P2 与 Agent workload 配置。
- `docs/troubleshooting.md`：通用故障排查。

## 20. 推荐的首次服务器验收顺序

1. 校验源码包 SHA256。
2. 解压到新目录，不覆盖服务器旧版本。
3. 固定独立 Python，使用 `PYTHONPATH` 验证当前源码路径。
4. 对真实 input 做 CSV preflight。
5. 做 dry-run，确认 Stage DAG 和输出路径。
6. 用新 smoke 目录执行 fast smoke，并持续看 `analysis_status.json`。
7. 核对 runs、request attempts、P2 requests、请求解析审计和并行执行元数据。
8. 解释所有 `PARTIAL`、`UNAVAILABLE` 和 `UNRESOLVED`。
9. 用新的 full 目录执行完整 Evidence 和 model pack 构建。
10. 中断时使用兼容性校验后的 `--resume`。
11. 用 `verify-report` 校验模型生成报告。
12. 保存命令、日志、preflight、status、summary、stage 和环境信息，形成可复现实验记录。

完成这 12 步后，才算服务器真实数据路径已经跑通。运行进程退出为 0，只代表命令层面没有被严格模式判为失败，不单独构成分析结果可发布的证明。
