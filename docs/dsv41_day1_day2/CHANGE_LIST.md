# Day 1 / Day 2 修改清单

日期：2026-10-08。工作目录无 `.git`，无法提供 Git Commit/patch；没有覆盖已有业务脚本或服务器启动配置。

| 文件（相对工作区根） | 类型 | 意图 |
|---|---|---|
| `agentmon/scripts/dsv41_day1_acceptance.py` | 新增 | 默认只读 Docker/NPU/API/版本/配置检查；显式 live 复用原 smoke；保存退出码与证据；生成 Day 1 Gate 报告；支持只读证据 finalization |
| `agentmon/scripts/dsv41_request_discovery.py` | 新增 | 容器标准库 AST 扫描；显式运行时 introspection；实际路径/行号/签名和 ID 源码索引；从快照渲染调用链审查报告 |
| `agentmon/scripts/analyze_request_binding.py` | 新增 | 客户端 sidecar + 多进程旧 P2 envelope 的离线分析 CLI，无网络与 Hook 安装 |
| `agentmon/src/agentmon/analysis/request_binding.py` | 新增 | 显式分阶段绑定与冲突/孤儿/重试/fan-out/进程覆盖/终态审计；隐私白名单输出，严格真实验收门槛 |
| `agentmon/src/agentmon/adapters/inference/vllm_018/__init__.py` | 修改 | 公开 exports 保持原名称，改为按需加载；离线 reader/version 不急切加载 procfs 或 Hook；未改 0.18 兼容函数和 Hook 安装逻辑 |
| `agentmon/tests/unit/test_dsv41_day1_day2.py` | 新增 | 33 项独立离线测试（版本/路径、映射、降级、并发/重试/fan-out、进程、终态、隐私、默认安全、CLI、writer 失败和按需导入） |
| `docs/dsv41_day1_day2/P2_018_TO_V41_DIFF.md` | 新增 | 已有代码复用、需要适配、未实现和 Day 3 暂缓范围，精确说明 0.27 Hook 缺口 |
| `docs/dsv41_day1_day2/REQUEST_CALL_CHAIN.md` | 新增 | 本地 P1 路径及目标容器八项待确认问题；没有把旧版本路径当真实目标证据 |
| `docs/dsv41_day1_day2/SERVER_RUNBOOK.md` | 新增 | 只读预检、人工 Live、目标 Discovery、配置审核、绑定分析、维护窗口/回滚和服务器回归命令 |
| `docs/dsv41_day1_day2/DEVELOPMENT_ACCEPTANCE.md` | 新增 | 本次实际测试结果、失败分类、Day 1/Day 2 Gate 状态及研究边界 |
| `docs/dsv41_day1_day2/CHANGE_LIST.md` | 新增 | 本清单 |

## 复用而未修改的核心

- P1 header injector、mini-SWE instrumentation、原 `deepseek_v41_client_smoke.py`；
- KVReuse `request_bridge.py`，所有冻结数学、split/hash/strict mapping；
- 旧 P2 `version.py`、`hook.py`、Event JSONL envelope 和 `sitecustomize.py`；
- 旧 `preflight.disabled_p2_env/run_command`、P2 alias helper、`paths` 私有文件工具。

没有新增可部署 0.27 Hook。本轮选择任务书允许的“最小绑定分析实现 + 精确服务端缺口分析”；
未经真实目标签名/字段审查，服务端 producer 与 profile 仍待开发和授权验证。

测试产物位于 `outputs/dsv41-day1-day2-development-20261008/`，不属于源码发布包。
