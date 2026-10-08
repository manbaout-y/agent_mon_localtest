# Day 1 / Day 2 开发与实验验收报告

日期：2026-10-08（Asia/Shanghai）。

**结论：已实现的离线组件通过；目标服务器真实验收待执行。完整工作区验收 FAIL，不能报告整体全绿。**

## 环境与证据来源

| 项 | 本次实际证据 |
|---|---|
| 本地工作区 | `E:/agentproject/agent_mon-ver_hetero_state` |
| Git Commit / Branch / dirty state | UNKNOWN：`git status` 和 `git rev-parse HEAD` 均返回 not a git repository；这是源码快照 |
| 实际测试 Python | `F:/Anaconda/python.exe`，3.10.9，Windows |
| 固定 client 依赖 | 本机未安装 mini-swe-agent / LiteLLM / OpenAI；wire/live NOT_RUN |
| 当前真实容器 / 镜像 / Driver / CANN / vLLM / config | UNKNOWN / NOT_RUN，本轮无目标服务器访问 |
| 用户的历史 v0.27.1+empty / 8×910B / 256K 记录 | historical；本次未复核，不替代 observed_runtime |
| 模型执行、Hook 注入、服务重启、NPU 清理 | 均未执行 |

## 已交付的软件验收

1. Day 1 包装器默认只读、显式 Live 复用原 smoke；模型请求不另造。记录出口状态、退出码、当前配置来源和私有文件。
2. Discovery 默认 AST 无 vLLM 导入，可经容器 Python stdin 执行；显式 runtime 模式校验实际 version/path 并读取真实签名。
3. 分析器复用 P2 envelope/alias helper，不做时间最近推断，不从相同字符串补造 alias。
4. Direct 必须有 API headers + Async 显式 alias + EngineCore boundary + Scheduler boundary。
5. API/Engine/Scheduler 三个 native ID 可不同；重试和 fan-out 不覆盖；service/host/clock scope 分离。
6. 取消/超时保留对应终态，未记录终态保持 unobserved；冲突、孤儿、缺进程加载、丢事件或 synthetic source 会阻塞真实验收。
7. Live gate 额外要求真实成功 client report、HTTP 200、runtime 来源、每个进程安装证据、Scheduler active_request_count=1 与 finished。
8. 输入/输出控制大小，不落 Prompt/Completion/Secret；日志是明确标记的内容过滤版。

Day 1 工具返回 0 可能只表示预检完成。Day 2 离线构造 direct 也不表示实测 direct；必须读报告中的 evidence_class / real_acceptance。

## 实际运行的测试

所有测试都在本地 CPU 环境执行。模拟事件（包括 runtime gate 的正例）仅验证判断逻辑，不是服务器实验。

| 检查 | 实际结果 | 日志（工作区根相对路径） |
|---|---|---|
| 最终新增测试 + 旧 Discovery + Event + clock | **43 passed，0 failed**，其中新增 33 项 | `outputs/dsv41-day1-day2-development-20261008/targeted-final.log`、`targeted-final.xml` |
| 三个 CLI --help / 新增代码 compileall | PASS，无模型/网络 | 命令执行记录；workspace core compile.log 也为 exit 0 |
| 扩展旧 config / JSONL / server-preflight 回归 | **66 passed / 8 failed** | `outputs/dsv41-day1-day2-development-20261008/targeted-tests.log` |
| 完整 KVReuse tests | **332 passed / 1 failed** | `.../workspace-core/kv_tests.log` |
| 完整 AgentMon tests | **30 collection errors，未完成** | `.../workspace-core/agentmon_tests.log` |
| framework synthetic smoke | exit 0（仍沿用旧 smoke 的状态口径） | `.../workspace-core/framework_smoke.log` |
| state research synthetic smoke | 10/10 PASS；MODEL_EXECUTION=NOT_RUN | `.../workspace-core/state_smoke.log` |
| recipe list | exit 0；不等于 paper source 全部存在 | `.../workspace-core/recipes.log` |
| AgentMon CLI | exit 1（Windows 缺 resource） | `.../workspace-core/agentmon_cli.log` |
| 完整 workspace core | **FAIL**，7 steps 已完成 | `.../workspace-core/report.json` |

最终针对性测试命令（服务器 Bash 等价）：

```bash
cd ~/agent_mon-ver_hetero_state/agentmon
PYTHONPATH=src PYTHONUTF8=1 python -m pytest \
  tests/unit/test_dsv41_day1_day2.py tests/unit/test_p2_discovery.py \
  tests/unit/test_events.py tests/unit/test_clock.py -q -p no:cacheprovider
```

完整 core 是通过工作区既有 `scripts/validate_workspace.py --suite core` 执行的，没有跳过失败来拼接 PASS。

## 失败原因与未覆盖范围

- KVReuse 的一个失败：`test_paper_capability_audit_is_20_unique_and_conservative` 预期论文组件可执行，实际 BLOCKED_SOURCE。
  现有 paper spec source 缺失，与此前仓库审查一致；本轮没有改冻结代码或恢复历史资产。
- AgentMon collection：Windows 缺 Linux 的 os.sysconf/resource/fcntl。旧 P1/P2 Hook 回归未能在本机运行；需 Linux 服务器 CPU 复验。
- 扩展回归的 8 个失败：Windows TOML 测试生成未转义路径（Python 3.10 fallback）、旧 JSONL 多进程实际 114/120 行、
  两项 symlink 权限、POSIX 私有权限、两个旧 preflight readiness/CLI、一个 Linux ACL 路径期望。
  不把旧 JSONL 丢行改写成 PASS，也未改现有跨进程 writer；目标 Linux 多进程写入仍需原测试验收。
- 当前没有 0.27 Hook，因此“新 Hook 包装器原函数只调用一次、观测异常不影响 serving”的实际适配测试 **NOT_RUN**。
  新分析器的写盘失败测试通过，不能替代未来 Hook 的故障隔离测试。
- POSIX 0600/0700 和实际 API/EngineCore 多进程加载未在 Windows 验收；程序使用已有私有输出工具，手册给出服务器验证方式。
- wire 需固定 client 依赖，当前未安装，不运行安装或网络下载。

## Day 1 Gate（本次实验，不是历史结果）

| Gate | 状态 | 原因 |
|---|---|---|
| D1-G1 当前容器身份/运行状态 | NOT_RUN | 没有访问服务器 |
| D1-G2 实际客户端 API 路径 | NOT_RUN | 离线覆盖 host-only/container-only 情形，未探测目标 |
| D1-G3 真实模型请求 | NOT_RUN | 需人工 --live |
| D1-G4 AgentMon Live Smoke | NOT_RUN | 历史 PASS 不替代本次 |
| D1-G5 当前 Client Evidence | NOT_RUN | 新 run 尚未生成 |
| D1-G6 当前镜像/版本/配置/源码快照 | NOT_RUN | 脚本就绪；actual module/config 需 Discovery+review |

没有 DAY1_REAL_PASS。

## Day 2 Gate

| Gate | 状态 | 原因 |
|---|---|---|
| D2-G1 目标真实 Source Discovery | NOT_RUN | 交付了工具，未在目标容器执行 |
| D2-G2 真实 API→IPC→Scheduler 调用链 | BLOCKED | 依赖真实源码/分支/签名；本地报告明确 UNKNOWN |
| D2-G3 0.18→0.27 兼容差异 | PASS | `P2_018_TO_V41_DIFF.md`，旧版本门槛未绕过 |
| D2-G4 全部 Day 2 离线验收 | BLOCKED（已实现组件 PASS） | 43 项组件测试通过；新 Hook 尚无目标 profile，Linux 旧 Hook 回归和其故障隔离测试未完成 |
| D2-G5 实际 API/EngineCore/Scheduler 事件 | NOT_RUN | 没有注入服务端 Hook |
| D2-G6 当前真实 direct binding | NOT_RUN | 未取得目标 server stream |

结论：`OFFLINE_COMPONENTS_PASS / REAL_ACCEPTANCE_PENDING`。
不报告 DAY2_SOURCE_PASS、完整 DAY2_OFFLINE_PASS 或 DAY2_LIVE_BINDING_PASS。

## 接下来的人工操作

完整命令在 `SERVER_RUNBOOK.md`。先只读预检，然后操作者决定是否恢复原容器/运行短 Live。
真实 Discovery 返回后，逐层审查 API header、InputProcessor ID 改写、CoreClient IPC 与 Scheduler 对象，再补独立 0.27 Hook。
维护窗口、注入、重启和回滚均由操作者批准与执行，不自动启动竞争 TP8。

Day 3 尚未准备好：缺真实 request binding、加载覆盖和 v0.27 适配故障隔离证据。
接口已预留 engine_request_id/scheduler_request_id/service scope/event IDs，未启用 KVCacheManager 热路径或 Tensor capture。
