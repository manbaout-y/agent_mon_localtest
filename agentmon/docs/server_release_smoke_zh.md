# AgentMon 服务器发布与一次冒烟教程

本文用于把完整源码包部署到昇腾服务器，并在开始 5/10/更多并发实验前完成
一次可审计的 P0/P1/P2 发布验收。所有命令都在新解包目录执行，不向旧源码树
叠加历史 full patch，也不修改 vLLM 安装目录。

## 1. 解包到新目录

```bash
set -euo pipefail
umask 022

export RELEASE=/path/to/agentmon-server-test-ready-20260806-project-audit-hardening-src.tar.gz
export INSTALL_PARENT=/absolute/path/to/install-parent
export AGENTMON_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/agentmon/bin/python
export MINI_SWE_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/python
export MINI_SWE_BIN=/data/node0_disk3/chenyang/agent-runtime-poc/envs/mini-swe-v243/bin/mini
export VLLM_PY=/data/node0_disk3/chenyang/agent-runtime-poc/envs/verl-cann90/bin/python
export ACL_PY=/usr/local/Ascend/cann-9.0.0/python/site-packages
export AGENTMON_HOME=/data/node0_disk3/chenyang/agentmon
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

cd "$(dirname "$RELEASE")"
sha256sum -c "$(basename "$RELEASE").sha256"
tar -tzf "$RELEASE" > "$PROVENANCE_ROOT/archive-members.txt"

"$AGENTMON_PY" - "$RELEASE" <<'PY'
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

tar --no-same-owner --no-same-permissions -xzf "$RELEASE" -C "$INSTALL_ROOT"
mapfile -t REPOS < <(
  find "$INSTALL_ROOT" -type f -path '*/src/agentmon/__init__.py' -print |
    sed 's#/src/agentmon/__init__.py$##' |
    sort -u
)
test "${#REPOS[@]}" -eq 1
export AGENTMON_REPO="${REPOS[0]}"
export AGENTMON_CONFIG="$AGENTMON_REPO/examples/config.target.toml"

echo "EXTRACTION=PASS"
echo "AGENTMON_REPO_CANDIDATE_COUNT=${#REPOS[@]}"
echo "AGENTMON_REPO=$AGENTMON_REPO"

export AGENTMON_ACL_PYTHON_PATH="$ACL_PY"
export PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1
```

不要覆盖正在运行的旧目录。首次冒烟需要人工确认的是 `RELEASE`、`INSTALL_PARENT`
和三个 Python 路径；`AGENTMON_REPO` 是解包后得到的目录，`P2_ROOT`、`BATCH_ROOT`
和分析输出目录都是后续命令生成的产物路径。先确认包内关键文件：

```bash
test -f "$AGENTMON_REPO/src/sitecustomize.py"
test -f "$AGENTMON_REPO/scripts/agentmon_server_preflight.py"
test -f "$AGENTMON_REPO/scripts/agentmon_batch_runtime.py"
test -f "$AGENTMON_REPO/scripts/agentmon_step_sequence.py"
```

这些 `scripts/agentmon_*.py` 路径是稳定兼容入口。当前实现已迁移到
`src/agentmon/server/preflight.py`、`src/agentmon/batch/` 和
`src/agentmon/step_sequence/`，冒烟命令和参数不需要因此修改。

## 1.1 源码 quick/full gate

10 分钟内只想快速拒绝坏包时，先跑 quick gate：

```bash
set -euo pipefail

export QUICK_GATE_JSON="$PROVENANCE_ROOT/quick-gate.json"
export QUICK_GATE_LOG="$PROVENANCE_ROOT/quick-gate-console.log"

set +e
PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}" "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_release_gate.py" \
  --package "$RELEASE" \
  --installed-source-root "$AGENTMON_REPO" \
  --agentmon-python "$AGENTMON_PY" \
  --level quick \
  --deadline-seconds 600 \
  --provenance-dir "$PROVENANCE_ROOT" \
  --output "$QUICK_GATE_JSON" \
  --test-manifest "${RELEASE}.TEST_MANIFEST.quick.json" \
  2>&1 | tee "$QUICK_GATE_LOG"
QUICK_GATE_RC=${PIPESTATUS[0]}
set -e
echo "QUICK_GATE_EXIT_CODE=$QUICK_GATE_RC"
echo "QUICK_GATE_JSON=$QUICK_GATE_JSON"
echo "CONSOLE_LOG=$QUICK_GATE_LOG"
test "$QUICK_GATE_RC" -eq 0
```

通过时输出 `QUICK_SMOKE=PASS`、`ELIGIBLE_FOR_FULL_SOURCE_GATE=YES` 和
`ELIGIBLE_FOR_NEXT_STAGE=YES`。这只说明包、源码入口和定向门禁值得继续，不代表可以进入服务器 preflight。机器可读报告位于
`$PROVENANCE_ROOT/quick-gate.json`，相邻 `.sha256` 用于校验该次运行结果。
`installed_source_matches_package` 和 `package_source_manifest` 必须为 `PASS`。新源码包内置
`.agentmon/source-manifest.json`；full gate 会拒绝受管源码的缺失、修改和额外文件，并检查实际
Python 导入的 `agentmon`、replay 与 harness 模块均位于 `$AGENTMON_REPO`。没有 package 或
`--installed-source-root` 的 full gate 不能取得 server preflight 资格。

进入真实 vLLM/CANN preflight 前，必须跑 full source gate：

```bash
set -euo pipefail

export FULL_GATE_JSON="$PROVENANCE_ROOT/full-source-gate.json"
export FULL_GATE_LOG="$PROVENANCE_ROOT/full-source-gate-console.log"

set +e
PYTHONPATH="$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}" "$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_release_gate.py" \
  --package "$RELEASE" \
  --installed-source-root "$AGENTMON_REPO" \
  --agentmon-python "$AGENTMON_PY" \
  --vllm-python "$VLLM_PY" \
  --level full \
  --require-previous-gate "$PROVENANCE_ROOT/quick-gate.json" \
  --provenance-dir "$PROVENANCE_ROOT" \
  --output "$FULL_GATE_JSON" \
  --test-manifest "${RELEASE}.TEST_MANIFEST.json" \
  2>&1 | tee "$FULL_GATE_LOG"
FULL_GATE_RC=${PIPESTATUS[0]}
set -e
echo "FULL_GATE_EXIT_CODE=$FULL_GATE_RC"
echo "FULL_GATE_JSON=$FULL_GATE_JSON"
echo "CONSOLE_LOG=$FULL_GATE_LOG"
test "$FULL_GATE_RC" -eq 0
```

只有看到 `SOURCE_RELEASE_GATE=PASS`、`ELIGIBLE_FOR_SERVER_PREFLIGHT=YES` 和
`ELIGIBLE_FOR_NEXT_STAGE=YES`，并且
`$PROVENANCE_ROOT/full-source-gate.json` 中 `eligible_for_server_preflight=true`，才继续下一节。

固定状态机如下，任何一级失败都必须停止，且对应的 `ELIGIBLE_FOR_*` 为 `NO`：

```text
PACKAGE_SOURCE_GATE=PASS
        ↓
STATIC_SERVER_PREFLIGHT=PASS
ELIGIBLE_FOR_RUNTIME_P2_GATE=YES
        ↓
RESOURCE_GATE=PASS
        ↓
SERVICE_START_GATE=PASS
        ↓
RUNTIME_P2_GATE=PASS
        ↓
ELIGIBLE_FOR_EXPERIMENT=YES
```

从 `Quick Smoke Prepare` 开始，每个阶段都使用同一套终端输出协议：

```text
STEP=<阶段名>
PURPOSE=<这一步验证什么>
STATUS=PASS|PARTIAL|FAIL
REASON=<机器可读原因>
NEXT=<下一阶段或 STOP>
PASS_CONDITION=<通过条件>
JSON=<本阶段报告路径，若有>
LOG=<日志路径，若有>
SUGGESTION=<失败/部分通过时的排查建议，若有>
```

通过条件是 `STATUS=PASS` 且 `NEXT` 指向下一阶段。`PARTIAL` 说明工具已经生成了可审计
产物，但仍缺少不能猜测的服务器事实或人工确认；此时必须停下修复，不进入下一阶段。

`PACKAGE_SOURCE_GATE=PASS` 由 package gate、quick smoke 和 full source gate 共同组成：
`PACKAGE_SHA256=PASS`、`ARCHIVE_SAFETY=PASS`、`ARCHIVE_CLEAN=PASS`、
`QUICK_SMOKE=PASS` 和 `SOURCE_RELEASE_GATE=PASS` 都成立时，才允许进入
`STATIC_SERVER_PREFLIGHT`。之后每一阶段都要重新显式读取本轮实例配置，不依赖上一段
shell 的 `cd`、临时变量或历史服务状态。

交付包目录中的 `SHA256SUMS` 只覆盖 tarball、BUILD_MANIFEST 和 quick/full
TEST_MANIFEST。`quick-gate.json`、`full-source-gate.json`、preflight JSON 和 runtime
P2 gate JSON 属于本次服务器运行结果，不加入交付 `SHA256SUMS`，只保留各自相邻
`.sha256`。

## 2. 在真实服务 shell 做 preflight

preflight 不启动、不停止也不 patch vLLM。它使用指定 Python 导入真实环境，
因此必须在原来能够启动 vLLM/CANN/ACL 的 shell 中执行，不能清除
`LD_LIBRARY_PATH`、`ASCEND_HOME_PATH` 等变量。

普通 preflight 子进程默认超时为 15 秒；使用 `--deep` 时默认提高到 60 秒，以容纳
Ascend 插件初始化。显式传入 `--timeout N` 会覆盖默认值。若 vLLM probe 超时，
结果只报告 `probe_status=timeout` 和 `probe_timeout=true`，不会把未完成的探针误报为
vLLM、ACL 或异步接口缺失。

preflight 会保留命令行传入的三个 venv Python 路径，不会先解析为共同的底层
解释器；因此 mini-SWE 的 `mini` 命令会从 `--mini-swe-python` 对应的 `bin`
目录查找。输出中的 `python_identities` 同时记录 supplied path 和仅供诊断的
resolved interpreter。

如果工作服务通过额外的 Python 目录导入 ACL，显式选择与 vLLM 环境匹配的 CANN
Python 目录。当前常用服务器使用 `cann-9.0.0`，不要从多个 CANN 目录中自动取第一个：

```bash
export ACL_PY=/usr/local/Ascend/cann-9.0.0/python/site-packages
export AGENTMON_ACL_PYTHON_PATH="$ACL_PY"
# 下方命令同时传入：--acl-python-path "$ACL_PY"
```

原有 `PYTHONPATH`、`LD_LIBRARY_PATH` 和 `ASCEND_HOME_PATH` 会继续传给探针子进程。
成功后检查 `checks[vllm_runtime].detail.acl_file` 是否指向实际 `acl` 模块。
full source gate 已经通过 `p2_discovery_directed` 覆盖 P2 discovery 的 strict-version、
stdout 空/纯 JSON 协议和 unsupported 版本不 patch 语义；static preflight 只负责目标服务器
环境和真实 vLLM/CANN/P2 符号发现，不连接旧服务，除非显式传入
`--runtime-base-url` 与 `--runtime-model`。

```bash
set -euo pipefail

export P2_ROOT="$AGENTMON_HOME/p2/$(date +%Y%m%d-%H%M%S)-release-smoke"
export STATIC_PREFLIGHT_JSON="$PROVENANCE_ROOT/static-server-preflight.json"
export STATIC_PREFLIGHT_LOG="$PROVENANCE_ROOT/static-server-preflight-console.log"
umask 077
mkdir -m 700 -p "$P2_ROOT"

set +e
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_server_preflight.py" \
  --agentmon-repo "$AGENTMON_REPO" \
  --agentmon-python "$AGENTMON_PY" \
  --mini-swe-python "$MINI_SWE_PY" \
  --vllm-python "$VLLM_PY" \
  --acl-python-path "$ACL_PY" \
  --config "$AGENTMON_CONFIG" \
  --events-path "$P2_ROOT/events.jsonl" \
  --deep \
  --strict \
  --output "$STATIC_PREFLIGHT_JSON" \
  2>&1 | tee "$STATIC_PREFLIGHT_LOG"
STATIC_PREFLIGHT_RC=${PIPESTATUS[0]}
set -e
echo "STATIC_PREFLIGHT_EXIT_CODE=$STATIC_PREFLIGHT_RC"
echo "STATIC_PREFLIGHT_JSON=$STATIC_PREFLIGHT_JSON"
echo "CONSOLE_LOG=$STATIC_PREFLIGHT_LOG"
test "$STATIC_PREFLIGHT_RC" -eq 0

"$AGENTMON_PY" - "$STATIC_PREFLIGHT_JSON" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["static_server_preflight_status"] == "PASS", data
assert data["eligible_for_runtime_p2_gate"] is True, data
print("STATIC_SERVER_PREFLIGHT=PASS")
print("FIRST_STAGE_SMOKE=PASS")
print("ELIGIBLE_FOR_RUNTIME_P2_GATE=YES")
PY
```

硬门槛是退出码 `0`、顶层 `status=ready`，并且：

```text
static_server_preflight_status=PASS
eligible_for_runtime_p2_gate=true
vllm=0.18.0
vllm_ascend import=ok
acl import=ok
vllm.v1.engine.async_llm.AsyncLLM 存在
EngineCore.step 与 step_with_batch_queue 签名均有效
AgentMon hook target 同时声明 step 与 step_with_batch_queue
Scheduler.update_from_output 存在
deep missing required/optional targets 均为空
sitecustomize 指向当前 AGENTMON_REPO/src/sitecustomize.py
P2 disabled 时没有加载 runtime hook
```

第一阶段到这里结束。`STATIC_SERVER_PREFLIGHT=PASS` 只说明源码包和目标服务器静态
vLLM/CANN/P2 环境可用；启动独立 vLLM 服务、发送 P2 probe 请求属于第二阶段
Runtime P2 Gate。

`vllm.engine.async_llm_engine.AsyncLLMEngine` 只作为 discovery alias 记录，默认
hook 目标始终是真实实现 `vllm.v1.engine.async_llm.AsyncLLM`。

## 3. 编译和全量测试

目标测试使用 `PYTHONPATH=src:tests`，全量 discover 只需要 `src`：

```bash
cd "$AGENTMON_REPO"

env -u PYTHONHOME PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/src" \
  "$AGENTMON_PY" -m compileall -q src scripts tests

env -u PYTHONHOME PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/src:$PWD/tests" \
  "$AGENTMON_PY" -m unittest -v \
  unit.test_server_preflight \
  unit.test_p2_vllm_adapter \
  unit.test_p1_adapters \
  unit.test_batch_runtime \
  unit.test_canonical \
  unit.test_batch_compare \
  unit.test_matrix \
  unit.test_host_sampling

env -u PYTHONHOME PYTHONNOUSERSITE=1 PYTHONPATH="$PWD/src" \
  "$AGENTMON_PY" -m unittest discover -s tests -v \
  2>&1 | tee "$P2_ROOT/full-tests.log"
```

日志中不得出现 `FAILED`、`ERROR`、`ResourceWarning`、`TimeoutExpired`、
`Task was destroyed`、`unclosed file` 或 `Exception ignored`。

## 4. 独立 Runtime P2 实例门禁

Runtime P2 Gate 必须验证本轮独立服务实例，不能误连旧服务。每轮创建新的
`P2_ROOT`、`events.jsonl`、server log、PID file、Base URL 和启动脚本。后续每一段命令
都从 `runtime-instance.env` 读取同一组配置，不依赖前序 shell 状态。

推荐先用 quick-smoke synthesizer 生成本轮实例文件，而不是手写全部协议参数：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$AGENTMON_PY" \
  "$AGENTMON_REPO/scripts/agentmon_server_quick_smoke.py" \
  --request "$AGENTMON_REPO/examples/experiment_requests/qwen3_2card_agent_smoke.json" \
  --server-profile "$AGENTMON_REPO/examples/server_profiles/chenyang_ascend910b.json" \
  --source-root "$AGENTMON_REPO" \
  --static-preflight-json "$STATIC_PREFLIGHT_JSON" \
  --output-root "$AGENTMON_HOME/quick-smoke"
```

它会写出 `runtime-instance.env`、`start-vllm-p2.sh`、
`resource-gate.sh`、`service-start-gate.sh`、`agentmon_config_runtime.toml`、
`mini_model_runtime.yaml`、`tool-call-probe.json`、`tool-call-gate.sh`、
`mini-swe-noninteractive-probe.sh`、`startup-watchdog.sh` 和
`quick-smoke-contract.json`。若输出：

```text
STEP=QUICK_SMOKE_PREPARE
STATUS=PASS
NEXT=RESOURCE_GATE
```

才继续。若输出 `STATUS=PARTIAL` 或 `STATUS=FAIL`，先看 `REASON`、`SUGGESTION` 和
`BLOCKING_REASONS`，不要启动服务或进入实验。常见例子：

```text
REASON=blocking_reasons_present
BLOCKING_REASONS=prefix_cache_disable_args_unresolved
```

处理方式是传入包含 `prefix_cache_cli_options` 的 static preflight JSON，或显式提供
`--prefix-cache-args`；不能直接猜 vLLM 参数名。

Smoke task 的选择同样不是固定值：request/`--smoke-task` 显式值优先；未提供时从
benchmark zip（包括嵌套 runtime tar.gz）的 `tasks/<task-id>/` 自动发现并记录证据。
发现失败会返回 `smoke_task_unresolved` 和 `NEXT=STOP`，不会默认使用某个历史任务 ID。
模型名到 tool-call parser 的候选关系来自 server profile 的
`preflight.tool_call_parser_candidates`；通用代码不内置模型映射，候选仍必须通过 Tool-call Gate。

此后优先执行生成脚本，而不是继续手写启动协议：

```bash
export QUICK_SMOKE_DIR=/path/to/generated/quick-smoke

# Resource Gate：验证端口、路径、事件文件新鲜度，并保留 npu-smi 证据。
# 若工具无法证明设备空闲，先看 LOG，确认后显式设置 YES。
RESOURCE_GATE_MANUAL_DEVICE_CONFIRM=YES "$QUICK_SMOKE_DIR/resource-gate.sh"

# Service Start Gate：这是启动 vLLM 的步骤。它使用 start-vllm-p2.sh 中的完整环境、
# 设备、端口、模型、P2 开关、prefix-cache 和 tool-call 配置。
"$QUICK_SMOKE_DIR/service-start-gate.sh"

# Tool-call Gate：仅验证协议，不跑任务集。要求工具调用时必须通过。
"$QUICK_SMOKE_DIR/tool-call-gate.sh"

# mini-SWE 非交互 Gate：确认隔离 HOME/XDG 下不会卡在交互式初始化。
"$QUICK_SMOKE_DIR/mini-swe-noninteractive-probe.sh"
```

每个脚本都输出 `STEP/STATUS/REASON/NEXT/PASS_CONDITION/JSON/LOG/SUGGESTION`，并保留
兼容标记如 `RESOURCE_GATE=PASS`。失败时不要继续，例如：

```text
STEP=SERVICE_START_GATE
STATUS=FAIL
REASON=model_not_listed
NEXT=STOP
LOG=.../vllm-server.log
SUGGESTION=Check LOG for slow startup or wrong SERVED_MODEL/Base URL...
```

下面的 4.1、4.2 手工命令是生成脚本的等价展开，主要用于排查或审计。日常快速冒烟时，
优先执行上面的生成脚本；只有当脚本输出 `STATUS=FAIL/PARTIAL` 且需要定位细节时，再参考
手工展开版。

先固定本轮实例输入。`VLLM_PREFIX_CACHE_ARGS` 必须来自第一次 static preflight JSON 中
`checks[vllm_runtime].detail.prefix_cache_cli_options` 报告的负向选项；不要猜参数名。

```bash
set -euo pipefail
umask 077

export TARGET_DEVICES=0,1
export TP_SIZE=2
export VLLM_HOST=127.0.0.1
export VLLM_PORT=8001
export VLLM_BASE_URL="http://$VLLM_HOST:$VLLM_PORT/v1"
export VLLM_BIN="${VLLM_BIN:-$(dirname "$VLLM_PY")/vllm}"
export MODEL_PATH=/data/node0_disk3/chenyang/qwen3_coder_30b
export SERVED_MODEL=qwen3-coder-30b
export MAX_MODEL_LEN=32768
export PREFIX_CACHE_EXPECTATION=disabled
export VLLM_PREFIX_CACHE_ARGS="--replace-with-prefix-cache-disable-option-from-preflight"

export P2_ROOT="$AGENTMON_HOME/p2/$(date +%Y%m%d-%H%M%S)-runtime-p2-gate"
export AGENTMON_EVENTS_PATH="$P2_ROOT/events.jsonl"
export SERVICE_START_SCRIPT="$P2_ROOT/start-vllm-p2.sh"
export SERVICE_LOG="$P2_ROOT/vllm-server.log"
export PID_FILE="$P2_ROOT/vllm-server.pid"
export INSTANCE_ENV="$P2_ROOT/runtime-instance.env"
mkdir -m 700 -p "$P2_ROOT"

test -x "$AGENTMON_PY"
test -x "$VLLM_PY"
test -x "$VLLM_BIN"
test -d "$ACL_PY"
test -d "$MODEL_PATH"
test "$VLLM_PREFIX_CACHE_ARGS" != "--replace-with-prefix-cache-disable-option-from-preflight"

cat > "$INSTANCE_ENV" <<EOF
export AGENTMON_REPO=$(printf '%q' "$AGENTMON_REPO")
export AGENTMON_CONFIG=$(printf '%q' "$AGENTMON_CONFIG")
export AGENTMON_HOME=$(printf '%q' "$AGENTMON_HOME")
export AGENTMON_PY=$(printf '%q' "$AGENTMON_PY")
export VLLM_PY=$(printf '%q' "$VLLM_PY")
export VLLM_BIN=$(printf '%q' "$VLLM_BIN")
export ACL_PY=$(printf '%q' "$ACL_PY")
export TARGET_DEVICES=$(printf '%q' "$TARGET_DEVICES")
export TP_SIZE=$(printf '%q' "$TP_SIZE")
export VLLM_HOST=$(printf '%q' "$VLLM_HOST")
export VLLM_PORT=$(printf '%q' "$VLLM_PORT")
export VLLM_BASE_URL=$(printf '%q' "$VLLM_BASE_URL")
export MODEL_PATH=$(printf '%q' "$MODEL_PATH")
export SERVED_MODEL=$(printf '%q' "$SERVED_MODEL")
export MAX_MODEL_LEN=$(printf '%q' "$MAX_MODEL_LEN")
export PREFIX_CACHE_EXPECTATION=$(printf '%q' "$PREFIX_CACHE_EXPECTATION")
export VLLM_PREFIX_CACHE_ARGS=$(printf '%q' "$VLLM_PREFIX_CACHE_ARGS")
export P2_ROOT=$(printf '%q' "$P2_ROOT")
export AGENTMON_EVENTS_PATH=$(printf '%q' "$AGENTMON_EVENTS_PATH")
export SERVICE_START_SCRIPT=$(printf '%q' "$SERVICE_START_SCRIPT")
export SERVICE_LOG=$(printf '%q' "$SERVICE_LOG")
export PID_FILE=$(printf '%q' "$PID_FILE")
export AGENTMON_ACL_PYTHON_PATH=$(printf '%q' "$ACL_PY")
export PYTHONPATH=$(printf '%q' "$AGENTMON_REPO/src:$ACL_PY${PYTHONPATH:+:$PYTHONPATH}")
export PYTHONNOUSERSITE=1
EOF
chmod 600 "$INSTANCE_ENV"
```

### 4.1 Resource Gate

Resource Gate 只验证本轮资源是否可以被独立服务使用，不启动 vLLM。端口占用是硬失败；
NPU 占用至少要留证据。因为不同 CANN/驱动版本的 `npu-smi` 输出不完全一致，目标设备
是否空闲需要显式确认；如果设备已经被其他实验使用，先换 `TARGET_DEVICES` 或
`VLLM_PORT`。

```bash
set -euo pipefail
set -a
. "$INSTANCE_ENV"
set +a

export RESOURCE_GATE_JSON="$P2_ROOT/resource-gate.json"
export RESOURCE_GATE_LOG="$P2_ROOT/resource-gate-console.log"

{
  echo "INSTANCE_ENV=$INSTANCE_ENV"
  echo "P2_ROOT=$P2_ROOT"
  echo "TARGET_DEVICES=$TARGET_DEVICES"
  echo "VLLM_BASE_URL=$VLLM_BASE_URL"
  echo "AGENTMON_EVENTS_PATH=$AGENTMON_EVENTS_PATH"
  readlink -f "$AGENTMON_PY" || true
  readlink -f "$VLLM_PY" || true
  readlink -f "$VLLM_BIN" || true
  ss -ltnp || true
  npu-smi info || true
  ps -eo pid,ppid,etime,%cpu,%mem,rss,cmd | grep -E '[v]llm|[a]pi_server' || true
} 2>&1 | tee "$RESOURCE_GATE_LOG"

if ss -ltnp | grep -Eq "(:|\\])$VLLM_PORT\\b"; then
  echo "RESOURCE_GATE=FAIL"
  echo "REASON=port_in_use"
  exit 1
fi

test ! -e "$AGENTMON_EVENTS_PATH"
touch "$AGENTMON_EVENTS_PATH"
test -w "$AGENTMON_EVENTS_PATH"
rm -f "$AGENTMON_EVENTS_PATH"

# 查看上面的 npu-smi/ps 证据后，确认 TARGET_DEVICES 没有被旧服务或其他实验占用，
# 再把下一行改成 YES。
export RESOURCE_GATE_MANUAL_DEVICE_CONFIRM=NO
test "$RESOURCE_GATE_MANUAL_DEVICE_CONFIRM" = YES

"$AGENTMON_PY" - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "status": "PASS",
    "stage": "RESOURCE_GATE",
    "p2_root": os.environ["P2_ROOT"],
    "events_path": os.environ["AGENTMON_EVENTS_PATH"],
    "target_devices": os.environ["TARGET_DEVICES"],
    "vllm_base_url": os.environ["VLLM_BASE_URL"],
    "service_log": os.environ["SERVICE_LOG"],
    "pid_file": os.environ["PID_FILE"],
    "console_log": os.environ["RESOURCE_GATE_LOG"],
    "manual_device_confirm": os.environ["RESOURCE_GATE_MANUAL_DEVICE_CONFIRM"],
}
Path(os.environ["RESOURCE_GATE_JSON"]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print("RESOURCE_GATE=PASS")
PY
```

### 4.2 Service Start Gate

启动脚本必须自包含：环境、设备、端口、模型、AgentMon P2 开关、事件路径和 prefix-cache
配置都写入脚本，不靠当前终端临时变量。preflight 使用同一个启动脚本作为
`--vllm-launch-command-file`。

```bash
set -euo pipefail
set -a
. "$INSTANCE_ENV"
set +a

cat > "$SERVICE_START_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export ASCEND_RT_VISIBLE_DEVICES=$(printf '%q' "$TARGET_DEVICES")
export AGENTMON_VLLM_P2_ENABLE=1
export AGENTMON_VLLM_P2_DEEP=1
export AGENTMON_VLLM_P2_DEEP_MAX_BATCH_REQUESTS=256
export AGENTMON_VLLM_P2_EXPECT_PREFIX_CACHE=$(printf '%q' "$PREFIX_CACHE_EXPECTATION")
export AGENTMON_EVENTS_PATH=$(printf '%q' "$AGENTMON_EVENTS_PATH")
export AGENTMON_ACL_PYTHON_PATH=$(printf '%q' "$ACL_PY")
export PYTHONPATH=$(printf '%q' "$PYTHONPATH")
export PYTHONNOUSERSITE=1
exec $(printf '%q' "$VLLM_BIN") serve $(printf '%q' "$MODEL_PATH") \\
  --served-model-name $(printf '%q' "$SERVED_MODEL") \\
  --host $(printf '%q' "$VLLM_HOST") \\
  --port $(printf '%q' "$VLLM_PORT") \\
  --tensor-parallel-size $(printf '%q' "$TP_SIZE") \\
  --max-model-len $(printf '%q' "$MAX_MODEL_LEN") \\
  $VLLM_PREFIX_CACHE_ARGS
EOF
chmod 700 "$SERVICE_START_SCRIPT"

export PREFLIGHT_BEFORE_START_JSON="$P2_ROOT/preflight-before-start.json"
export PREFLIGHT_BEFORE_START_LOG="$P2_ROOT/preflight-before-start-console.log"

set +e
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_server_preflight.py" \
  --agentmon-repo "$AGENTMON_REPO" \
  --agentmon-python "$AGENTMON_PY" \
  --vllm-python "$VLLM_PY" \
  --acl-python-path "$ACL_PY" \
  --events-path "$AGENTMON_EVENTS_PATH" \
  --vllm-launch-command-file "$SERVICE_START_SCRIPT" \
  --expect-prefix-cache "$PREFIX_CACHE_EXPECTATION" \
  --deep --strict \
  --output "$PREFLIGHT_BEFORE_START_JSON" \
  2>&1 | tee "$PREFLIGHT_BEFORE_START_LOG"
PREFLIGHT_BEFORE_START_RC=${PIPESTATUS[0]}
set -e
echo "PREFLIGHT_BEFORE_START_EXIT_CODE=$PREFLIGHT_BEFORE_START_RC"
test "$PREFLIGHT_BEFORE_START_RC" -eq 0

nohup "$SERVICE_START_SCRIPT" > "$SERVICE_LOG" 2>&1 &
echo "$!" > "$PID_FILE"
chmod 600 "$PID_FILE"

export SERVICE_START_JSON="$P2_ROOT/service-start-gate.json"
export SERVICE_START_LOG="$P2_ROOT/service-start-gate-console.log"

set +e
"$AGENTMON_PY" - <<'PY' 2>&1 | tee "$SERVICE_START_LOG"
import json
import os
import time
import urllib.request
from pathlib import Path

pid = int(Path(os.environ["PID_FILE"]).read_text().strip())
base_url = os.environ["VLLM_BASE_URL"].rstrip("/")
models_url = f"{base_url}/models"
served_model = os.environ["SERVED_MODEL"]
status = "FAIL"
reason = "service_not_ready"
models_payload = None
for _ in range(120):
    try:
        os.kill(pid, 0)
        with urllib.request.urlopen(models_url, timeout=5) as response:
            models_payload = json.loads(response.read().decode("utf-8"))
        model_ids = [item.get("id") for item in models_payload.get("data", []) if isinstance(item, dict)]
        if served_model in model_ids:
            status = "PASS"
            reason = "ready"
            break
        reason = f"model_not_listed:{model_ids}"
    except Exception as exc:  # noqa: BLE001 - recorded as gate evidence.
        reason = f"{type(exc).__name__}:{exc}"
    time.sleep(5)
payload = {
    "status": status,
    "stage": "SERVICE_START_GATE",
    "reason": reason,
    "pid": pid,
    "base_url": base_url,
    "model": served_model,
    "events_path": os.environ["AGENTMON_EVENTS_PATH"],
    "start_script": os.environ["SERVICE_START_SCRIPT"],
    "service_log": os.environ["SERVICE_LOG"],
    "models_payload": models_payload,
}
Path(os.environ["SERVICE_START_JSON"]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print(f"SERVICE_START_GATE={status}")
if status != "PASS":
    raise SystemExit(1)
PY
SERVICE_START_RC=${PIPESTATUS[0]}
set -e
echo "SERVICE_START_GATE_EXIT_CODE=$SERVICE_START_RC"
echo "SERVICE_START_JSON=$SERVICE_START_JSON"
echo "CONSOLE_LOG=$SERVICE_START_LOG"
test "$SERVICE_START_RC" -eq 0
```

### 4.3 Runtime P2 Gate

Runtime P2 Gate 仍从同一个 `runtime-instance.env` 读取 Base URL、模型名、事件文件和启动脚本。
只有这些输入与实际服务一致，才能声明 `ELIGIBLE_FOR_EXPERIMENT=YES`。

```bash
set -euo pipefail
set -a
. "$INSTANCE_ENV"
set +a

export RUNTIME_P2_JSON="$P2_ROOT/preflight-runtime.json"
export RUNTIME_P2_LOG="$P2_ROOT/preflight-runtime-console.log"

set +e
"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_server_preflight.py" \
  --agentmon-repo "$AGENTMON_REPO" \
  --agentmon-python "$AGENTMON_PY" \
  --vllm-python "$VLLM_PY" \
  --acl-python-path "$ACL_PY" \
  --events-path "$AGENTMON_EVENTS_PATH" \
  --vllm-launch-command-file "$SERVICE_START_SCRIPT" \
  --expect-prefix-cache "$PREFIX_CACHE_EXPECTATION" \
  --require-runtime-p2 \
  --prefix-cache-probe-min-requests 2 \
  --runtime-base-url "$VLLM_BASE_URL" \
  --runtime-model "$SERVED_MODEL" \
  --runtime-stream-probe \
  --runtime-stream-max-tokens 128 \
  --runtime-concurrency 4 \
  --runtime-concurrent-max-tokens 192 \
  --timeout 180 \
  --deep --strict \
  --output "$RUNTIME_P2_JSON" \
  2>&1 | tee "$RUNTIME_P2_LOG"
RUNTIME_P2_RC=${PIPESTATUS[0]}
set -e
echo "RUNTIME_P2_GATE_EXIT_CODE=$RUNTIME_P2_RC"
echo "RUNTIME_P2_JSON=$RUNTIME_P2_JSON"
echo "CONSOLE_LOG=$RUNTIME_P2_LOG"
test "$RUNTIME_P2_RC" -eq 0

"$AGENTMON_PY" - <<'PY'
import json
import os
from pathlib import Path

data = json.loads(Path(os.environ["RUNTIME_P2_JSON"]).read_text())
assert data["status"] == "ready", data
assert Path(os.environ["AGENTMON_EVENTS_PATH"]).is_file(), os.environ["AGENTMON_EVENTS_PATH"]
assert Path(os.environ["SERVICE_START_SCRIPT"]).is_file(), os.environ["SERVICE_START_SCRIPT"]
assert Path(os.environ["SERVICE_LOG"]).is_file(), os.environ["SERVICE_LOG"]
print("RUNTIME_P2_GATE=PASS")
print("ELIGIBLE_FOR_EXPERIMENT=YES")
PY
```

该命令按顺序发送两个相同的 Prefix Cache 请求、一个流式长输出请求和一组通过
线程屏障同时起跑的 4 并发请求。它只审计命令启动后追加的请求事件，但 JSONL
完整性检查覆盖整个文件。未传 `--runtime-base-url/--runtime-model` 时不会发 HTTP
请求，旧的纯静态 preflight 行为保持不变。

要求 runtime capability 中 `async_scheduling_enabled=true`、
`engine_core_step_with_batch_queue_hooked=true`、
`engine_token_boundary_collection=supported`、`prefix_cache_enabled=false`，
两个 probe 请求的 `cached_prompt_tokens` 最大值为 `0`。同时要求
`deep_collection=true`、`deep_prefill_boundary=scheduler.update_from_output`、
`deep_kv_lifecycle_status=complete`，且 required/optional missing 列表为空。
每个完成请求还必须包含 received、queued、first_scheduled、
prefill_completed、first_token、first_output、engine_finished、finished；
first_token 与 engine_finished 各只能出现一次。流式请求的 API usage、finished
和 engine_finished token 数必须一致且大于 1；`last_token_ns` 必须存在且时间顺序
合法。4 个 API ID 与 4 个 engine ID 必须一一对应，输出 token 均不少于 96，
`max_batch_request_count >= 2` 且 `continuous_batching_observed=true`。JSONL 必须没有
非法行、重复 event ID、source PID sequence 重复或回退。
`vllm.adapter.error` 可保留诊断，但不能伴随请求失败或改变原函数结果。

最终 JSON 顶层的 `runtime_prefix_cache_probe`、`runtime_stream_probe`、
`runtime_concurrency_probe` 和 `event_stream_integrity` 必须全部为 `status=pass`。
任何一项失败都会列出机器可读 `failures`；例如请求没有真正重叠时为
`concurrent_requests_did_not_overlap`，并使 strict 命令返回非零。

在真实 vLLM/CANN shell 中还可以先运行不启动模型的 hook 契约集成测试：

```bash
env -u PYTHONHOME PYTHONNOUSERSITE=1 \
  PYTHONPATH="$AGENTMON_REPO/src:$AGENTMON_REPO/tests" \
  AGENTMON_RUN_VLLM_018_INTEGRATION=1 \
  "$VLLM_PY" -m unittest -v \
  integration.test_vllm_018_runtime_hook
```

该测试导入服务器真实的 `AsyncLLM`/`EngineCore`，验证两个 step 方法签名并
临时安装、卸载 hook；它不加载模型，也不能替代上面的真实请求 lifecycle gate。

## 5. 单任务 P1/P2 冒烟

进入实验阶段也不要继承临时 shell 状态。每个新 shell 先显式声明并读取本轮 Runtime
实例配置，后续 `agentmon run`、batch runtime、analysis 都使用同一个
`AGENTMON_EVENTS_PATH`、`VLLM_BASE_URL` 和 `SERVED_MODEL`：

```bash
set -euo pipefail
export INSTANCE_ENV=/absolute/path/to/this-runtime-p2-gate/runtime-instance.env
test -f "$INSTANCE_ENV"
set -a
. "$INSTANCE_ENV"
set +a
```

先设置目标代码仓和 mini-SWE 配置。轨迹文件必须带 `{run_id}`，防止连续任务覆盖：

```bash
export TARGET_REPO=/path/to/calculator/repository
export MINI_SWE_ROOT=/data/node0_disk3/chenyang/agent-runtime-poc/mini-swe
export TRAJECTORY="$MINI_SWE_ROOT/trajectories/release-smoke-{run_id}.traj.json"

env -u PYTHONHOME PYTHONNOUSERSITE=1 PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" -m agentmon run \
  --config "$AGENTMON_CONFIG" \
  --cwd "$TARGET_REPO" \
  --label release-smoke \
  --trajectory "$TRAJECTORY" \
  -- "$MINI_SWE_BIN" \
  -m hosted_vllm/qwen3-coder-30b \
  -c mini.yaml \
  -c "$MINI_SWE_ROOT/configs/local_qwen3_coder.yaml" \
  --agent-class agentmon.adapters.agent_frameworks.mini_swe.agent.InstrumentedInteractiveAgent \
  --model-class agentmon.adapters.agent_frameworks.mini_swe.model.InstrumentedLitellmModel \
  --environment-class agentmon.adapters.agent_frameworks.mini_swe.environment.InstrumentedMiniSweLocalEnvironment \
  -y -l 0 --exit-immediately \
  -t 'Fix the calculator task and run its tests.' \
  -o "$TRAJECTORY" \
  2>&1 | tee "$P2_ROOT/agentmon-smoke.log"

RUN_ID=$(sed -n 's/.*agentmon run_id=\([^ ]*\).*/\1/p' \
  "$P2_ROOT/agentmon-smoke.log" | tail -1)
test -n "$RUN_ID"
export RUN_DIR="$AGENTMON_HOME/runs/$RUN_ID"
test -f "$RUN_DIR/raw/trajectory/mini.traj.json"
```

共享服务必须按 trajectory response id 导入 P2，不能按每任务 byte offset：

```bash
env -u PYTHONHOME PYTHONNOUSERSITE=1 PYTHONPATH="$AGENTMON_REPO/src" \
"$AGENTMON_PY" -m agentmon import-p2 "$AGENTMON_EVENTS_PATH" \
  --config "$AGENTMON_CONFIG" \
  --run-id "$RUN_ID" \
  --filter-response-ids-from-trajectory "$RUN_DIR/raw/trajectory/mini.traj.json" \
  --quality direct \
  --global-event-policy exclude \
  --require-events

"$AGENTMON_PY" -m agentmon report --config "$AGENTMON_CONFIG" "$RUN_ID"
"$AGENTMON_PY" -m agentmon summary --config "$AGENTMON_CONFIG" \
  "$RUN_ID" --format table --strict

"$AGENTMON_PY" "$AGENTMON_REPO/scripts/agentmon_step_sequence.py" \
  "$RUN_DIR" --format csv > "$RUN_DIR/step_sequence.csv"
```

冒烟通过标准：

```text
run/report/summary 文件存在
P1 agent_steps/model_requests/tool_calls > 0
P2 request count > 0
P2 match rate 和 lifecycle coverage 达到阈值
内部 vllm_request_id 在 received/queued/scheduled/finished 间一致
successful tool 产生 tool_call.end
prefix cache disabled 时 hit rate 为 null/N/A，不是 0%
invalid/duplicate/open/negative/orphan span 均为 0
summary --strict 退出码为 0
```

## 6. 再开始并发 batch

单任务通过后再使用 `scripts/agentmon_batch_runtime.py`。5、10 或更高并发共用
同一 runner，只修改 `--concurrency`；实际并发上限由 vLLM queue、KV/HBM 和任务
稳定性决定，不是 AgentMon 的固定限制。

批量模式必须保留这些参数：

```text
--p1-mode required
--p2-events <service events.jsonl>
--p2-filter-mode auto
--min-p2-match-rate <threshold>
--min-p2-lifecycle-rate <threshold>
--analysis-workers <N>
--defer-analysis
--vllm-launch-command-file <actual command file>
--task-package <task manifest/archive/directory>
```

Deep P2 冒烟或正式长跑时，推荐在 workload 阶段增加 `--defer-analysis`，让 batch
先结束任务执行并标记 `analysis_deferred=true`，不要在 vLLM 实验窗口同步构建
GB 级 P2 index 和 step sequence。也可以显式加保护阈值：

```text
--defer-analysis-if-p2-over-bytes <bytes>
```

离线分析时使用同一 `BATCH_ROOT` 和 P2 source 重新执行 runner，并增加
`--analyze-existing`。GB 级 P2 文件先用 `--analysis-workers 1` 或 `2`，避免多个分析
worker 争抢磁盘 I/O。

推荐离线分析参数：

```text
--analysis-preset standard
--p2-index-cache-root <shared cache dir>
```

`standard` 保留每任务 report 和 step sequence，但不复制完整
`p2-service-global.events.jsonl`，也不会在缺少 task P2 窗口时把全局 P2 流 fallback 到
单个任务。只做快速巡检时可以用 `--analysis-preset quick`，但 quick 会跳过 batch
timeline 和 all-steps.csv，不作为最终发布分析。

完整命令模板、并发 P2 过滤规则和 step CSV 用法见
`docs/batch_p2_step_sequence_usage.md` 与 `docs/agent_runtime_full_usage_zh.md`。

## 7. 故障定位顺序

1. preflight 失败：先修 Python、ACL/CANN、真实 hook target 或目录权限。
2. capability 缺失：确认环境变量在 vLLM 进程启动前设置，且
   `sitecustomize.py` 来自当前源码包。
3. P1 missing：确认三个 class 参数实际注入，并且子进程
   `PYTHONPATH=<AGENTMON_REPO>/src`。
4. P2 missing：确认服务写入的 events path 与 import 使用的是同一文件，再检查
   trajectory `extra.response.id` 与 P2 `payload.api_request_id`。
5. step 为 0：先检查 P1 events；没有原生 P1 时，确认
   `raw/trajectory/mini.traj.json` 存在，脚本会启用 trajectory fallback。
6. strict 失败：查看 `report.json` 中 data quality、span integrity 和 P2 match
   missing reason，不要把 null 指标解释为 0。
