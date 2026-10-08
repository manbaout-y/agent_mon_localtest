# 本轮实际测试结果

环境：Windows、Python `F:/Anaconda/python.exe` 3.10.9；本轮没有服务器访问、模型执行或 Hook 安装。日志均在新目录 `outputs/dsv41-metrics-review-20261008/`。

| 检查 | 实际结果 | 原始结果文件 |
|---|---|---|
| 新增指标/AST 测试 + 原 Day1/Day2、Prometheus、Discovery、Event、clock | **57 passed**，其中新增 13 项、原回归 44 项 | targeted-final.log / targeted-final.xml |
| 初次测试 | 53 passed / 4 failed；config_info 没有 model_name，已按 ENGINE_ONLY 修复并复验 | targeted.log / targeted.xml，未覆盖 |
| 用户指标离线 CLI | exit0，保留原样输入；BLOCK_SIZE=UNRESOLVED、REQUEST_KV_TRACE=NOT_CAPTURED | docs 本目录 evidence/offline_review/metrics_review.json |
| 新 Python 源文件 compileall | PASS | compile.log；workspace-core/compile.log |
| 两个新 Python CLI --help | exit0 | source-help.log / review-help.log |
| Bash 采集脚本语法/服务器执行 | **NOT_RUN**；本机预期路径无 Git Bash；未访问服务器 | bash-syntax.log |
| 完整 KVReuse | 332 passed / 1 failed，缺 paper source 导致 BLOCKED_SOURCE | workspace-core/kv_tests.log |
| 完整 AgentMon | 30 collection errors，Windows 的 Linux API/模块限制 | workspace-core/agentmon_tests.log |
| framework synthetic / recipes | exit0，沿用原工具状态口径 | workspace-core/framework_smoke.log / recipes.log |
| state research synthetic | 10/10 PASS；MODEL_EXECUTION=NOT_RUN | workspace-core/state_smoke.log |
| AgentMon CLI | exit1，Windows 缺 resource | workspace-core/agentmon_cli.log |
| 完整 workspace core | **FAIL**，7 步均完成 | workspace-core/report.json |
| 原报告/旧输出原字节保留 | **PASS：1548 个文件 SHA256 一致，changed=[]** | prior-preservation.json；原哈希在本目录 evidence/prior_artifact_hashes.json |

Linux CPU 测试命令见 SERVER_RUNBOOK。目标容器实际源码采集、32/128 解释、Stats 单位/reset、真实请求绑定、Block 生命周期及迁移仍未测试；AST fixtures 是 synthetic，不能升级为 source/live PASS。

代码回归 PASS 与 workspace FAIL 分开报告。未修复无关的 paper source 缺失或 Linux 专属旧测试，未用跳过测试来拼接完整 PASS。
