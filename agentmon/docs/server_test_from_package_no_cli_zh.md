# AgentMon 最新源码包服务器测试说明（少 CLI 版）

本文适用于把本地最新 AgentMon/AGENTRUN 源码包拷到另一台有原始轨迹数据或真实
vLLM/NPU 环境的服务器上测试。核心思路是：不记 `agentmon ...` 长命令，只改一个
JSON 配置文件，然后运行一个 Python 脚本。

## 1. 包内关键文件

解包后请确认这些文件存在：

```text
src/agentmon/
src/sitecustomize.py
scripts/run_agentmon_test_from_config.py
scripts/run_agentmon_analysis_from_path.py
scripts/agentmon_server_preflight.py
scripts/agentmon_batch_runtime.py
scripts/agentmon_experiment.py
scripts/agentmon_experiment_init.py
scripts/agentmon_release_gate.py
scripts/package_agentmon_source.py
examples/server_test_config.json
examples/server_test_config_fast_smoke.json
examples/server_test_config_chenyang_ascend910b.json
examples/server_profiles/chenyang_ascend910b.json
docs/server_test_from_package_no_cli_zh.md
docs/experiment_orchestration_zh.md
```

其中：

- `scripts/run_agentmon_test_from_config.py`：推荐入口。通过 JSON 配置执行自检、离线分析、preflight。
- `scripts/run_agentmon_analysis_from_path.py`：更简单的离线分析入口，只需要填原始数据路径。
- `scripts/agentmon_server_preflight.py`、`scripts/agentmon_batch_runtime.py`、
  `scripts/agentmon_step_sequence.py` 等 `scripts/agentmon_*.py` 是稳定兼容入口；
  实际实现位于 `src/agentmon/server/`、`src/agentmon/batch/`、
  `src/agentmon/step_sequence/` 和 `src/agentmon/analysis/`。
- `scripts/agentmon_experiment.py`：推荐的一键实验阶段入口，支持
  `prepare`、`smoke`、`pilot`、`formal`、`analyze`。
- `scripts/agentmon_experiment_init.py`：把简洁实验 request 生成
  `experiment-contract.json`。
- `scripts/agentmon_release_gate.py`：统一发布验收入口，执行编译、导入、CLI、P2
  定向测试、server config runner 测试和可选全量测试，并写出 `TEST_MANIFEST.json`。
- `scripts/package_agentmon_source.py`：本地发布用的干净源码包构建入口，会排除
  `.DS_Store`、`._*`、`__MACOSX`、`__pycache__` 和 `.pyc`，并在包旁写出
  `BUILD_MANIFEST.json`。
- `examples/server_test_config.json`：可复制编辑的测试配置模板。
- `examples/server_test_config_fast_smoke.json`：大目录快速冒烟模板，适合先确认能解析和统计。
- `examples/server_test_config_chenyang_ascend910b.json`：当前 Ascend 910B 服务器的少填项模板。
- `examples/server_profiles/chenyang_ascend910b.json`：稳定服务器环境路径 profile。它只被测试编排脚本读取，不进入通用 runtime 或分析核心。

## 2. 最少操作流程

在服务器上新建一个干净目录，先校验源码包，再解压。不要覆盖正在跑实验的旧目录。

### 2.1 路径填写边界

首次服务器冒烟只需要人工确认这些路径：

- `PACKAGE_DIR`：源码包所在目录。
- `PACKAGE_NAME`：收到的 `.tar.gz` 文件名。
- `INSTALL_PARENT`：源码包要解压到哪个父目录。
- `AGENTMON_PY`：用于运行 AgentMon 源码自检、离线分析和发布 gate 的 Python 3.10+。
- `VLLM_PY`：真实 vLLM/CANN 环境的 Python；只在 P2 discovery 或服务器 preflight 时需要。
- `INPUT`：已有原始轨迹、batch root 或实验根目录；只做源码自检时不需要。
- `OUTPUT_PARENT`：分析结果写到哪个父目录；只做源码自检时不需要。

以下路径不要在一开始手工补齐，它们会在解包或运行时生成：

- `AGENTMON_REPO`：由安全解包后的顶层目录得到。
- `REPORT_DIR`：`run_agentmon_test_from_config.py` 在配置 `report_dir=""` 时自动生成。
- `SMOKE_OUTPUT`、`FULL_OUTPUT`、`TEMP_ROOT`：冒烟命令按时间戳生成。
- `BATCH_ROOT`：batch runtime 或 experiment orchestrator 启动任务时生成。
- `P2_ROOT`：启动带 P2 hook 的 vLLM 验证服务前按时间戳创建。
- `_p2_index/`、`p2-index-cache/`、`analysis_bundle/`：离线分析阶段生成或复用。

如果文档示例里出现 `$AGENTMON_HOME/runs/...`、`$BATCH_ROOT/...` 或
`$P2_ROOT/...`，它们表示运行产物位置，不是解包前必须已经存在的配置路径。

如果只想跑源码、离线分析或 config wrapper，可以设置兼容变量：

```bash
export PYTHON="$AGENTMON_PY"
```

但 release gate 和 preflight 文档会显式区分 `AGENTMON_PY` 与 `VLLM_PY`，避免用
AgentMon 自检环境误判真实 vLLM/CANN 环境。

当前常用服务器可以直接复用 `examples/server_profiles/chenyang_ascend910b.json`。
其中 mini-SWE、vLLM、CANN、模型服务和模型路径来自历史冒烟记录；`${AGENTMON_REPO}`
会在解包后自动替换为本次源码包目录。软件版本可能升级时更新这个 profile 即可，
不要把这些路径写入 `src/agentmon/runtime/`、`src/agentmon/analysis/` 或 optimizer 通用逻辑。

`run_agentmon_test_from_config.py` 的阶段状态含义：

- `PASS`：该阶段完成。
- `PARTIAL`：整体可继续，但有阶段给出警告或等待输入。
- `NEEDS_INPUT`：这个阶段需要稍后补路径或运行产物；这不是源码包自检失败。
- `FAIL`：代码、环境或已明确要求的检查失败。

### 2.2 只有源码包路径时的 quick smoke

如果你只拿到了服务器上的源码包路径，按下面这条快线走。它的目标是快速拒绝坏包，并确认
本次源码能在指定 AgentMon Python 下启动；它不代表可以直接进入正式服务器 preflight。

```bash
set -euo pipefail
umask 022

export PACKAGE_DIR=/absolute/path/to/package-directory
export PACKAGE_NAME=agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz
export INSTALL_PARENT=/absolute/path/to/install-parent
export AGENTMON_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/agentmon/bin/python
export MINI_SWE_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
export VLLM_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python
export ACL_PY=/usr/local/Ascend/cann-9.0.0/python/site-packages
export INSTALL_ROOT="$INSTALL_PARENT/agentmon-src-$(date +%Y%m%d-%H%M%S)"
export PROVENANCE_ROOT="$INSTALL_ROOT/provenance"

mkdir -p "$PROVENANCE_ROOT"

test -x "$AGENTMON_PY"
"$AGENTMON_PY" -c 'import sys; assert sys.version_info >= (3, 10), sys.version; print(sys.executable); print(sys.version)'
test -x "$VLLM_PY"
"$VLLM_PY" -c 'import sys; print(sys.executable); print(sys.version)'
test -d "$ACL_PY"
readlink -f "$AGENTMON_PY" || true
readlink -f "$MINI_SWE_PY" || true
readlink -f "$VLLM_PY" || true

cd "$PACKAGE_DIR"
sha256sum -c "$PACKAGE_NAME.sha256"
tar -tzf "$PACKAGE_DIR/$PACKAGE_NAME" > "$PROVENANCE_ROOT/archive-members.txt"

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

tar --no-same-owner --no-same-permissions -xzf "$PACKAGE_DIR/$PACKAGE_NAME" -C "$INSTALL_ROOT"
mapfile -t REPOS < <(
  find "$INSTALL_ROOT" -type f -path '*/src/agentmon/__init__.py' -print |
    sed 's#/src/agentmon/__init__.py$##' |
    sort -u
)
test "${#REPOS[@]}" -eq 1
export AGENTMON_REPO="${REPOS[0]}"
export PYTHON="$AGENTMON_PY"
export AGENTMON_ACL_PYTHON_PATH="$ACL_PY"
export PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}"

echo "EXTRACTION=PASS"
echo "AGENTMON_REPO_CANDIDATE_COUNT=${#REPOS[@]}"
echo "AGENTMON_REPO=$AGENTMON_REPO"
```

然后跑 package quick gate：

```bash
set -euo pipefail

export QUICK_GATE_JSON="$PROVENANCE_ROOT/quick-gate.json"
export QUICK_GATE_LOG="$PROVENANCE_ROOT/quick-gate-console.log"

set +e
PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}" "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_release_gate.py" \
  --package "$PACKAGE_DIR/$PACKAGE_NAME" \
  --agentmon-python "$AGENTMON_PY" \
  --level quick \
  --deadline-seconds 600 \
  --provenance-dir "$PROVENANCE_ROOT" \
  --output "$QUICK_GATE_JSON" \
  --test-manifest "$PACKAGE_DIR/${PACKAGE_NAME}.TEST_MANIFEST.quick.json" \
  2>&1 | tee "$QUICK_GATE_LOG"
QUICK_GATE_RC=${PIPESTATUS[0]}
set -e
echo "QUICK_GATE_EXIT_CODE=$QUICK_GATE_RC"
echo "QUICK_GATE_JSON=$QUICK_GATE_JSON"
echo "CONSOLE_LOG=$QUICK_GATE_LOG"
test "$QUICK_GATE_RC" -eq 0
```

通过时只应声明：

```text
QUICK_SMOKE=PASS
ELIGIBLE_FOR_FULL_SOURCE_GATE=YES
ELIGIBLE_FOR_NEXT_STAGE=YES
```

它还不能声明 `ELIGIBLE_FOR_SERVER_PREFLIGHT=YES`。进入静态服务器 preflight 前必须再跑 full gate：

```bash
set -euo pipefail

export FULL_GATE_JSON="$PROVENANCE_ROOT/full-source-gate.json"
export FULL_GATE_LOG="$PROVENANCE_ROOT/full-source-gate-console.log"

set +e
PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}" "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_release_gate.py" \
  --package "$PACKAGE_DIR/$PACKAGE_NAME" \
  --agentmon-python "$AGENTMON_PY" \
  --vllm-python "$VLLM_PY" \
  --level full \
  --require-previous-gate "$PROVENANCE_ROOT/quick-gate.json" \
  --provenance-dir "$PROVENANCE_ROOT" \
  --output "$FULL_GATE_JSON" \
  --test-manifest "$PACKAGE_DIR/${PACKAGE_NAME}.TEST_MANIFEST.json" \
  2>&1 | tee "$FULL_GATE_LOG"
FULL_GATE_RC=${PIPESTATUS[0]}
set -e
echo "FULL_GATE_EXIT_CODE=$FULL_GATE_RC"
echo "FULL_GATE_JSON=$FULL_GATE_JSON"
echo "CONSOLE_LOG=$FULL_GATE_LOG"
test "$FULL_GATE_RC" -eq 0
```

full gate 通过时才应看到：

```text
SOURCE_RELEASE_GATE=PASS
ELIGIBLE_FOR_SERVER_PREFLIGHT=YES
ELIGIBLE_FOR_NEXT_STAGE=YES
```

机器可读报告固定在：

```text
$PROVENANCE_ROOT/quick-gate.json
$PROVENANCE_ROOT/quick-gate.json.sha256
$PROVENANCE_ROOT/full-source-gate.json
$PROVENANCE_ROOT/full-source-gate.json.sha256
```

交付包目录里的 `SHA256SUMS` 只覆盖 tarball、BUILD_MANIFEST 和 quick/full
TEST_MANIFEST；本次服务器运行结果不加入该清单。

如果暂时没有真实 vLLM/CANN Python，可以先省略 `--vllm-python` 完成源码 full gate；
但进入 P2 preflight 前仍要用 `VLLM_PY` 执行 `agentmon_server_preflight.py`。

### 2.3 内置源码自检

如果服务器操作不方便，推荐先做内置源码自检：

```bash
cd "$AGENTMON_REPO"
PYTHONPATH="$AGENTMON_REPO/src" "$AGENTMON_PY" scripts/run_agentmon_test_from_config.py
```

没有显式配置文件时，脚本会直接运行内置 `self_check`，不会先生成模板并返回 64。
这条命令的退出码必须为 `0`，并且本次输出的 `SUMMARY=.../test_summary.json` 中
`status` 必须是 `PASS`；不要复用源码包里或旧目录里残留的历史 PASS 文件。

需要长期保存或编辑配置时，显式初始化：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" scripts/run_agentmon_test_from_config.py \
  --init-config agentmon_server_test_config.json
```

然后用文本编辑器填路径、选择 `actions`，再运行：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PYTHON" scripts/run_agentmon_test_from_config.py \
  agentmon_server_test_config.json
```

必须有一次“运行 Python 文件”的动作；但不需要手写 `agentmon analysis run-all ...`
或 batch runtime 的长参数。

## 3. 只做源码自检

适合刚拷到服务器后先确认包没有坏、Python 能导入、provider registry 正常。

`agentmon_server_test_config.json` 保持默认即可：

```json
{
  "actions": ["self_check"],
  "report_dir": "",
  "self_check": {
    "run_unit_tests": false
  }
}
```

运行脚本后看输出中的：

```text
REPORT_DIR=...
SUMMARY=.../test_summary.json
```

通过标准：

- `test_summary.json.status` 为 `PASS`。
- `self_check.json.compile_src_ok` 为 `true`。
- `self_check.json.adapter_capabilities` 中能看到：
  - `mini_swe`
  - `vllm_018`
  - `npu_process_mapping`

如果服务器 Python 版本低于 3.10，请换到 AgentMon/mini-SWE/vLLM 对应的 Python
环境再运行；本项目要求 Python `>=3.10`。

离线分析需要 numpy、pandas；Parquet 还需要 pyarrow，zstd 输入需要 zstandard。允许安装时可在解包目录使用 `python -m pip install -e '.[analysis]'`；隔离服务器应从内部 wheelhouse/镜像提供等价依赖，程序不会自动联网安装。

## 4. 已有原始轨迹/批次目录时的离线分析

如果你现在只知道一个原始文件或目录路径，优先做这一项。把配置改成：

```json
{
  "actions": ["self_check", "offline_analysis"],
  "report_dir": "",
  "self_check": {
    "run_unit_tests": false
  },
  "offline_analysis": {
    "input_path": "/服务器上的原始轨迹或批次目录",
    "output_path": "",
    "profile": "auto",
    "output_format": "csv",
    "analysis_preflight": true,
    "force": false,
    "resume": true,
    "strict": false,
    "experiment_contract": "",
    "skip_handoff": false,
    "timeout_seconds": null
  }
}
```

说明：

- `input_path` 可以填原始轨迹目录、batch 根目录、已有导出目录，脚本会做 discovery。
- `output_path` 留空时，输出会写到本次 `REPORT_DIR/analysis_bundle`。
- `output_format` 建议先用 `csv`，不依赖 `pyarrow`。
- 重复跑同一个输出目录时保留 `resume=true`；只有明确要忽略全部分析缓存时才使用 `fresh=true`。
- `analysis_preflight=true` 会在大量事件扫描前检查 Python、pandas、输出路径和压缩依赖。
- `experiment_contract` 可填显式实验合同路径；留空时会生成
  `contract_status=PARTIAL` 的派生合同，不会猜测补齐缺失实验设计字段。
- `timeout_seconds` 留空表示不设置总时限；设置后会在安全检查点终止并保留可恢复状态。
- 如果 `actions` 已经包含 `offline_analysis` 但 `input_path` 暂时为空，脚本会把该阶段标记为
  `NEEDS_INPUT`，整体 `test_summary.json.status=PARTIAL`，不会把源码自检误判为失败。

通过标准：

- `test_summary.json.status` 为 `PASS` 或 `PARTIAL`。
- `offline_analysis.json.analysis_status` 为 `PASS` 或 `PARTIAL`。
- `offline_analysis.json.run_summary` 指向的文件存在。
- `analysis_bundle/` 下生成 normalized 表、分析摘要、证据包、`02_semantics/`、
  `02_validation/data_quality_gate.json`、`05_handoff/` 和 `model_input_pack/`。

如果只想用最简单入口，也可以直接编辑
`scripts/run_agentmon_analysis_from_path.py` 顶部这两行：

```python
INPUT_PATH = "/服务器上的原始轨迹或批次目录"
OUTPUT_PATH = "/希望输出到哪里，可以留空逻辑等价于默认"
```

然后运行这个 Python 文件。这个方式更少配置，只做离线分析和内置 analysis preflight，不做 vLLM provider/runtime 自检。

## 4.1 大目录快速冒烟分析

如果输入目录很大，先不要直接跑完整证据包。复制
`examples/server_test_config_fast_smoke.json`，只改 `input_path` 和可选的 `output_path`：

```json
{
  "actions": ["self_check", "offline_analysis"],
  "offline_analysis": {
    "input_path": "/path/to/server/experiment-root",
    "output_path": "/path/to/server/fast-smoke-result",
    "output_format": "csv",
    "jobs": 8,
    "fast_smoke": true,
    "skip_evidence": true,
    "skip_handoff": true,
    "skip_model_pack": true,
    "experiment_contract": "",
    "omit_normalized_tables": ["event_inventory"],
    "analysis_preflight": true,
    "resume": true,
    "force": false
  }
}
```

这个模式会：

- 并行解析 run 目录，`jobs` 建议先设为服务器可用 CPU 核数的一半，例如 8 或 16。
- 保留 `runs`、`requests`、`p2_requests`、调度和资源摘要等分析必需表。
- 默认跳过最重的 `event_inventory`，避免把每一行原始事件再复制成一个巨大 CSV。
- 跳过 `04_evidence/`、`05_handoff/` 和 `model_input_pack/`，只写
  `skip_reason.json` 标记原因。
- 如果通过 `analysis run-all` 跑这类 fast smoke，`optimizer_harness_build`
  也会写出 `04_evidence/optimizer_experiment/skip_reason.json`。这表示
  optimizer experiment harness 被 `fast_smoke` 或 `skip_evidence` 主动跳过，
  不是缺失产物。完整 M1/M2/M3 harness 输出应在 fast smoke 通过后，用新的
  输出目录并关闭 `skip_evidence` 再生成。

快速冒烟通过后，再把 `fast_smoke=false`、`skip_evidence=false`、
`skip_handoff=false`、`skip_model_pack=false` 跑一次完整分析，用于最终报告和模型输入包。建议给完整分析使用新的 `output_path`；如果必须复用同一目录，设置 `fresh=true`，因为 fast-smoke 与完整分析的语义配置指纹不同，普通 resume 会按设计拒绝。

如果使用最简单入口 `scripts/run_agentmon_analysis_from_path.py`，也可以在脚本顶部设置：

```python
JOBS = 8
FAST_SMOKE = True
SKIP_EVIDENCE = True
SKIP_HANDOFF = True
SKIP_MODEL_PACK = True
EXPERIMENT_CONTRACT = ""
OMIT_NORMALIZED_TABLES = ["event_inventory"]
```

不想改脚本时，也可设置环境变量：

```bash
export AGENTMON_ANALYSIS_JOBS=8
export AGENTMON_ANALYSIS_FAST_SMOKE=1
export AGENTMON_ANALYSIS_SKIP_EVIDENCE=1
export AGENTMON_ANALYSIS_SKIP_HANDOFF=1
export AGENTMON_ANALYSIS_SKIP_MODEL_PACK=1
export AGENTMON_ANALYSIS_EXPERIMENT_CONTRACT=
export AGENTMON_ANALYSIS_OMIT_TABLES=event_inventory
```

这个简单入口当前默认就是 fast smoke、analysis preflight、resume 和省略 `event_inventory`。首轮通常只需填写 `INPUT_PATH`；运行过程中直接查看输出目录里的 `analysis_status.json`，无需依赖终端环境变量。

## 5. 真实 vLLM/NPU 环境 preflight

当你要确认这份包能和服务器上的 vLLM、CANN/ACL、mini-SWE 环境协同时，使用
`preflight` action。当前常用服务器优先从 profile 模板开始：

```json
{
  "server_profile": "examples/server_profiles/chenyang_ascend910b.json",
  "actions": ["self_check", "preflight"],
  "report_dir": "",
  "self_check": {
    "run_unit_tests": false
  },
  "preflight": {
    "expect_prefix_cache": "any",
    "require_runtime_p2": false,
    "runtime_base_url": "",
    "runtime_model": "",
    "runtime_stream_probe": false
  }
}
```

profile 会填充 `agentmon_python`、`mini_swe_python`、`vllm_python`、`agentmon_config`
和默认 `events_path`。`events_path` 文件本身可以不存在；当 profile 中
`create_events_parent=true` 时，wrapper 会创建它的父目录再交给 preflight 做写入探测。
`vllm_launch_command_file` 只有在已经记录真实 vLLM 启动命令时填写。

如果 `deep=true` 但 `vllm_python` 还没填，或者 runtime probe 只填了一半 URL/model，
wrapper 会把 `preflight_wrapper.json.status` 标成 `NEEDS_INPUT`，整体状态为
`PARTIAL`，不会伪装成环境失败。

通过标准：

- `preflight_wrapper.json.status` 为 `PASS`。
- `preflight.json.status` 为 `ready`。
- `preflight.json.static_server_preflight_status` 为 `PASS`。
- `preflight.json.eligible_for_runtime_p2_gate` 为 `true`。
- vLLM、vLLM-Ascend、ACL/CANN import 均为 OK。
- `sitecustomize.py` 指向本次解包目录的 `src/sitecustomize.py`。
- 未启用 P2 时不会提前加载 runtime hook。

如果服务器有多个 CANN 安装，preflight 必须显式选择与当前 vLLM 环境匹配的 ACL
Python 目录。当前常用服务器使用：

```bash
export ACL_PY=/usr/local/Ascend/cann-9.0.0/python/site-packages
export AGENTMON_ACL_PYTHON_PATH="$ACL_PY"
export PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}"
```

P2 discovery 的 `--strict-version` 门禁以实际导入的 `vllm.__version__` 为主，
`importlib.metadata` 只作为 fallback 旁证和来源一致性诊断。缺失、导入失败，或者实际选中
版本不兼容时必须返回非 0，不能继续静态服务器 preflight；metadata 与导入模块版本不一致时
记录到 `readiness.vllm_version_evidence.version_source_consistency` 和
`version_identity_status`，但不覆盖 `readiness.compatibility_status` 或兼容旧字段
`readiness.vllm_version_status`。`0.18.0` 与 `0.18.0+empty` 的 release tuple 相同，
应视为兼容版本，local suffix 只保留为构建证据。
该脚本的 stdout 是机器接口：未设置 `--output` 时 stdout 只能是一个 JSON 文档；
设置 `--output` 时 stdout 应为空。vLLM、Ascend platform plugin、Torch 这类导入阶段
打印到 stdout 的 INFO 会被脚本局部捕获，不需要依赖服务器侧手工设置日志环境变量。

### 5.1 发布 Gate 和 Manifest

本地或服务器候选目录都可以使用统一 release gate。`--level quick` 或旧的
`--skip-full-tests` 只代表快速源码冒烟；它通过后只能进入 full source gate。
不带 `--level` 时默认为 full gate。

```bash
PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}" "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_release_gate.py" \
  --source-root "$AGENTMON_REPO" \
  --agentmon-python "$AGENTMON_PY" \
  --level full \
  --provenance-dir "$PROVENANCE_ROOT" \
  --test-manifest "$AGENTMON_REPO/TEST_MANIFEST.json"
```

如果已经有源码包，优先直接验包。包模式会重新校验相邻 `.sha256`、归档路径安全、
macOS 元数据、`__pycache__` 和 `.pyc/.pyo`，并从解出的 marker 自动识别源码根：

```bash
PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}" "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_release_gate.py" \
  --package "$PACKAGE_DIR/$PACKAGE_NAME" \
  --agentmon-python "$AGENTMON_PY" \
  --vllm-python "$VLLM_PY" \
  --level full \
  --require-previous-gate "$PROVENANCE_ROOT/quick-gate.json" \
  --provenance-dir "$PROVENANCE_ROOT" \
  --test-manifest "$PACKAGE_DIR/${PACKAGE_NAME}.TEST_MANIFEST.json"
```

`package_agentmon_source.py` 负责写 `*.BUILD_MANIFEST.json`，release gate 负责写
`*.TEST_MANIFEST.json`、`quick/full-gate.json` 和相邻 `.sha256`。交付 `SHA256SUMS`
只绑定源码包与交付 manifest；`quick/full-gate.json` 这类运行结果不进入交付
`SHA256SUMS`。

## 6. 已启动服务后的 P2 runtime 验证

当 vLLM OpenAI-compatible 服务已经启动，并且你想主动发请求验证 P2 hook、生命周期和
并发事件，可以在 `preflight` 中再填：

正式服务器冒烟不要直接复用旧服务。推荐固定为：

```text
Package/Source Gate
→ Static Preflight
→ Resource Gate
→ Service Start Gate
→ Runtime P2 Gate
→ Experiment
```

每轮 Runtime P2 Gate 使用独立的 `P2_ROOT`、`events.jsonl`、server log、PID file、
端口和设备，并把完整环境写入 `runtime-instance.env` 与自包含启动脚本。Runtime Gate
使用的 `runtime_base_url`、`runtime_model`、`vllm_launch_command_file` 和 `events_path`
必须全部来自同一个实例配置。详细 shell 流程见
`docs/server_release_smoke_zh.md` 的“独立 Runtime P2 实例门禁”。

```json
{
  "server_profile": "examples/server_profiles/chenyang_ascend910b.json",
  "actions": ["preflight"],
  "preflight": {
    "vllm_launch_command_file": "/data/agentmon/p2/<runtime-instance>/start-vllm-p2.sh",
    "expect_prefix_cache": "disabled",
    "deep": true,
    "strict": true,
    "timeout_seconds": 180,
    "require_runtime_p2": true,
    "runtime_base_url": "${VLLM_BASE_URL}",
    "runtime_model": "${SERVED_MODEL}",
    "runtime_stream_probe": true
  }
}
```

注意：

- `events_path` 必须和 vLLM 进程实际写入的 `AGENTMON_EVENTS_PATH` 是同一个文件。
- P2 环境变量必须在 vLLM 服务启动前设置。
- `vllm_launch_command_file` 中保存实际启动命令，用于检查 prefix cache、async scheduling 等配置。

通过标准：

- `preflight.json.status=ready`。
- runtime capability 中 request lifecycle 完整。
- `vllm.request.received/queued/first_scheduled/prefill_completed/first_token/first_output/engine_finished/finished`
  都能看到。
- 如果配置为 prefix cache disabled，则 probe 请求不应出现 cached prompt token。

## 7. 结果文件怎么给我

测试完成后，把以下内容打包或截图即可：

```text
REPORT_DIR/test_summary.json
REPORT_DIR/self_check.json
REPORT_DIR/offline_analysis.json
REPORT_DIR/preflight_wrapper.json
REPORT_DIR/preflight.json
REPORT_DIR/analysis_bundle/run_summary.json
```

如果离线分析失败，再补充：

```text
REPORT_DIR/analysis_bundle/validation/
REPORT_DIR/analysis_bundle/logs/
原始 input_path 的目录结构截图或 find 前两层结果
```

## 8. 常见问题

- `input_path is empty`：配置里启用了 `offline_analysis`，但没有填原始数据路径。
- `ModuleNotFoundError: agentmon`：运行脚本时不在解包目录，或 Python 没使用包内 `src`。推荐直接运行解包目录里的脚本。
- `pyarrow` 缺失：把 `output_format` 设为 `csv`，不要用 parquet。
- `preflight failed`：先看 `preflight_wrapper.json.stderr_tail` 和 `preflight.json.checks`。
- P2 没事件：确认 vLLM 启动前已经设置 `AGENTMON_VLLM_P2_ENABLE=1` 和正确的 `AGENTMON_EVENTS_PATH`。
- NPU mapping 只有 `LEVEL_3`：说明当前只能看到 visible devices，没拿到进程/rank 或 `npu-smi` PID 证据；可以先接受为部分证据，之后在真实服务 shell 中再跑。
