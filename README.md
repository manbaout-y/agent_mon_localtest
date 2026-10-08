# Heterogeneous Inference State Lab

异构推理状态管理与复用研究统一工作区，迁移日期2026-09-24。
后续开发从本目录进入；AgentMon 和 KVReuse 保留独立包、配置和测试。
原目录保留为来源快照，不移动/删除、不再双向维护。

```text
hetero_state_lab/
├── agentmon/       Agent执行、P1 spans、资源观测、batch与分析
├── kv_ana/         StatePair、离线复用、recipe/provider、状态研究
├── integration/    联合验收边界
├── scripts/        顶层薄验收/打包入口
├── docs/           架构、状态、迁移与验收
├── outputs/        验收输出，不打包
└── dist/           发布包，不进入源码管理
```

## 使用

建议 Python3.11 + numpy/PyYAML/pytest；AgentMon分析可选依赖见其pyproject。
wire验收另需 mini-swe-agent==2.4.3、litellm==1.90.1、openai==2.44.0。
脚本不自动安装依赖。真实PT读取需要现有CPU torch，本轮验收不运行模型。

```bash
cd /path/to/hetero_state_lab
export PY=python3
export AGENTMON_REPO="$PWD/agentmon"
export KVREUSE_REPO="$PWD/kv_ana"
"$PY" scripts/validate_workspace.py --suite core --output outputs/core-check
"$PY" scripts/validate_workspace.py --suite wire --output outputs/wire-check
# 或一次运行全部；输出目录必须新建/空
"$PY" scripts/validate_workspace.py --suite all --output outputs/all-check
"$PY" scripts/package_workspace.py --output dist/hetero_state_lab_source.tar.gz
```

report.json记录各步骤exit code与日志；失败不能当作通过。现有AgentMon测试有本机loopback fixture，不连接服务器。

## 文档

- [架构](docs/architecture.md)
- [当前能力状态](docs/project_status.md)
- [迁移报告](docs/migration_report.md)
- [本次验收](docs/acceptance.md)
- [完整代码与使用指南](kv_ana/docs/AGENTMON_KVREUSE_COMPLETE_GUIDE_ZH.md)
- [AgentMon说明](agentmon/README.md)
- [CPU real pipeline](kv_ana/REAL_OFFLINE_PIPELINE.md)

子项目旧文档中的包名/原机器路径/P2命令属于历史材料，当前工作区定位以本文件为准。
顶层 BUILD_MANIFEST.json、SOURCE_TREE.txt 是迁移输入快照；新发布包生成 PACKAGE_MANIFEST.json。

客户端成功不等于V4.1状态capture，synthetic PASS不等于服务器PASS。
旧V4 intervention与vLLM0.18 P2不能自动用于V4.1。不自动启动模型/应用patch。
