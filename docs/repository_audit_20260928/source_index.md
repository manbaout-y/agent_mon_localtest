# Source and documentation navigation

Static index plus targeted semantic review; not a claim of line-by-line verification.

## agentmon/agentmon_implementation_spec/AGENTMON_TECHNICAL_SPEC_ZH.md

Lines: 2307; encoding: utf-8

- # AgentMon：Coding Agent 全栈负载观测工具技术规格
- ## 0. 文档用途与强制约束
- ### 0.1 已确认运行环境
- ### 0.2 不可违反的工程约束
- ## 1. 项目目标、非目标与成功定义
- ## 1.1 总体目标
- ## 1.2 非目标
- ## 1.3 成功定义
- ## 2. 术语和时间模型
- ## 2.1 核心术语
- ## 2.2 时间规范
- ## 2.3 区间计算
- ## 3. 总体架构
- ## 3.1 进程模型
- ## 3.2 线程与队列
- ## 4. 分阶段路线图
- ## 4.1 P0：无侵入、只读、低依赖 MVP
- ### P0 功能
- ### P0 不做
- ## 4.2 P1：mini-SWE 观测适配层
- ### P1 功能
- ## 4.3 P2：vLLM 观测扩展
- ### P2 功能
- ## 4.4 P3：深度 Profiler 与 MoE/通信分析
- ### P3 功能
- ## 5. 仓库结构
- ## 6. 配置规范
- ## 7. Run 目录与产物契约
- ## 8. 统一事件协议
- ### 8.1 事件设计规则
- ### 8.2 P1 Span ID 层级
- ## 9. SQLite 数据模型
- ### 9.1 表定义
- ### 9.2 数据库要求
- ## 10. P0 详细设计
- ## 10.1 `agentmon doctor`
- ### 目标
- ### 探测项
- #### 主机
- #### Ascend / CANN
- #### vLLM
- #### mini-SWE
- ### Doctor 输出状态
- ### 验收
- ## 10.2 Run Controller
- ### 启动流程
- ### 子进程创建
- ### 信号语义
- ### 退出码
- ## 10.3 ProcFS Sampler
- ### 进程发现
- ### `/proc` 字段
- ### CPU 计算
- ### 内存聚合
- ### 性能要求
- ## 10.4 Host / PSI Sampler
- ## 10.5 vLLM Metrics Scraper
- ### HTTP
- ### Prometheus Text Parser
- ### 指标别名注册表
- ### 必须识别的语义指标
- ### Counter 差分
- ### Gauge 汇总
- ### Histogram 差分
- ### 服务重启检测
- ## 10.6 Ascend `npu-smi` Sampler
- ### 设计原则
- ### 命令探测顺序
- ### 外部命令执行
- ### 归一字段
- ### 解析策略
- ### NPU 派生指标
- ## 10.7 mini-SWE Trajectory Importer
- ### 文件定位
- ### 导入字段
- ### 正规化
- ### 轨迹指标
- ## 10.8 理论 KV Cache 计算
- ## 10.9 报告与实时显示
- ### `live`
- ### `report`
- ### `compare`
- ## 11. P1：mini-SWE 精确埋点设计
- ## 11.1 实现方式
- ## 11.2 InstrumentedAgent
- ## 11.3 InstrumentedModel
- ## 11.4 InstrumentedEnvironment
- ### PID/PGID 难点
- #### 首选：实现兼容 LocalEnvironment 的 InstrumentedLocalEnvironment
- #### 备选：上游贡献 Hook
- ### 工具进程事件
- ## 11.5 P1 工具资源归因
- ## 11.6 P1 trajectory 扩展
- ## 12. P2：vLLM 请求级与 KV Cache 扩展
- ## 12.1 设计原则
- ## 12.2 请求 ID 关联
- ## 12.3 请求级事件
- ## 12.4 KV Cache 事件
- ### Prefix Cache 指标
- ### 当前环境
- ## 12.5 实际 KV Pool 大小
- ## 12.6 NPU 请求级近似归因
- ## 12.7 Prefix Cache A/B 实验
- ## 13. P3：Profiler 和 Expert Parallel 分析
- ## 13.1 Profiler 控制
- ## 13.2 摘要指标
- ## 13.3 Expert Parallel 实验指标
- ## 14. 指标目录与公式
- ## 14.1 Run 级
- ## 14.2 Agent/工具
- ## 14.3 vLLM
- ## 14.4 Host
- ## 14.5 NPU
- ## 14.6 数据质量指标
- ## 15. 异常处理和降级策略
- ## 16. 性能与开销预算
- ## 17. 隐私与安全
- ## 18. 测试策略
- ## 18.1 单元测试
- ## 18.2 Fixture
- ## 18.3 集成测试
- ## 18.4 Golden Report
- ## 19. 分阶段验收清单
- ## 19.1 P0 验收
- ## 19.2 P1 验收
- ## 19.3 P2 验收
- ## 19.4 P3 验收
- ## 20. Codex 实施顺序
- ### Iteration 0：仓库与契约
- ### Iteration 1：Doctor
- ### Iteration 2：Run Controller
- ### Iteration 3：Proc + Host
- ### Iteration 4：vLLM Metrics
- ### Iteration 5：Ascend
- ### Iteration 6：Trajectory + Report
- ### Iteration 7：P1 model/agent spans
- ### Iteration 8：P1 environment/PID
- ### Iteration 9：P2 request ID
- ### Iteration 10：P2 KV
- ### Iteration 11：P3 Profiler
- ## 21. Codex 工作规则
- ## 22. 目标服务器首次执行流程
- # 1. 解压离线包
- # 2. 使用系统 Python运行 doctor
- # 3. 空闲基线
- # 4. 包装 mini-SWE 任务
- ## 23. 推荐最终报告结构
- ## 24. 参考资料
- ## 25. 最终交付定义

## agentmon/agentmon_implementation_spec/CODEX_MASTER_PROMPT_ZH.md

Lines: 45; encoding: utf-8

- # 交给 Codex 的主执行指令
- ## 工作方式
- ## 当前 iteration
- ## Iteration 0 验收

## agentmon/agentmon_implementation_spec/README.md

Lines: 7; encoding: utf-8

- # AgentMon Implementation Spec

## agentmon/artifacts/README.md

Lines: 37; encoding: utf-8

- # AgentMon Local Artifacts
- ## Layout
- ## Latest Package

## agentmon/docs/adding_new_profile.md

Lines: 13; encoding: utf-8

- # Adding A New Analysis Profile

## agentmon/docs/agent_runtime_architecture_design_zh.md

Lines: 1452; encoding: utf-8

- # AgentMon Agent Runtime 架构设计方案
- ## 1. 总体结论
- ## 2. 设计目标与边界
- ### 2.1 目标
- ### 2.2 非目标
- ## 3. 现有资产如何进入新架构
- ## 4. 总体模块设计
- ## 5. 核心领域模型
- ### 5.1 对象层级
- ### 5.2 Run 状态机
- ### 5.3 RunSpec
- ### 5.4 WorkloadSpec
- ## 6. 核心协议
- ### 6.1 AgentRuntimeBackend
- ### 6.2 ModelProvider
- ### 6.3 InferenceTelemetryProvider
- ### 6.4 ToolExecutor
- ## 7. 与 Agent 框架协同
- ### 7.1 接入等级
- ### 7.2 启动协同
- ### 7.3 事件协同
- ### 7.4 控制协同
- ## 8. 与推理框架协同
- ### 8.1 两条路径
- ### 8.2 请求 ID 链路
- ### 8.3 推理能力声明
- ## 9. Telemetry 与事件规范
- ### 9.1 Canonical Event
- ### 9.2 事件族
- ### 9.3 Telemetry Aggregator
- ## 10. Orchestrator、Scheduler 与 Optimizer
- ### 10.1 责任边界
- ### 10.2 Scheduler 队列
- ### 10.3 Optimizer 输入与输出
- ### 10.4 第一阶段策略
- ## 11. 控制面设计
- ### 11.1 ControlAction
- ### 11.2 控制 vLLM batch 的方式
- ### 11.3 控制 Agent Tool CPU
- ### 11.4 模型请求与工具请求联合编排
- ## 12. 策略、配置与插件化
- ### 12.1 配置分层
- ### 12.2 插件扩展点
- ### 12.3 调度算法插件
- ### 12.4 Optimizer 算法插件
- ### 12.5 上下文压缩插件
- ### 12.6 决策合并
- ### 12.7 Replay 与 Conformance
- ## 13. API 与 CLI
- ### 13.1 CLI
- ### 13.2 Python SDK
- ## 14. 安全、降级与运维
- ### 14.1 降级策略
- ### 14.2 安全红线
- ### 14.3 进程与工具隔离
- ## 15. 落地路线
- ### Phase 1：Runtime Core 骨架
- ### Phase 2：mini-SWE Adapter 解耦
- ### Phase 3：Telemetry 快照与 Optimizer Advise
- ### Phase 4：Scheduler 与 Model Gate
- ### Phase 5：Tool Pool 与资源控制
- ### Phase 6：推理 Provider/Telemetry 分离
- ### Phase 7：Replay、插件和灰度
- ### Phase 8：协作式 Agent 控制
- ## 16. 最小 POC
- ## 17. 测试策略
- ### 17.1 单元测试
- ### 17.2 集成测试
- ### 17.3 压力与回放测试
- ## 18. 推荐第一批插件

## agentmon/docs/agent_runtime_full_usage_zh.md

Lines: 836; encoding: utf-8

- # AgentMon Agent Runtime 完整使用教程
- ## 1. 解压与环境变量
- ## 2. 启动完整 P2 vLLM 服务
- ### 2.1 可选 MoE 路由监测
- ## Runtime 优化总开关
- ### Coding Agent 上下文压缩
- ## 3. 准备任务表
- ## 4. 跑 5 并发或 10 并发
- ### 4.1 批跑工作区与源码防污染门禁
- ### 执行和分析分离
- # 在上面的完整 runner 命令中增加：
- ## 5. P2 并发关联为什么不用 byte offset
- ## 6. Batch 输出
- ### 单 Run Finalization Barrier
- ## 7. 自动分析瓶颈
- ## 8. P2 时间定义
- ## 9. 空值与 Prefix Cache 语义
- ## 10. 批次时间轴与吞吐
- ## 11. Canonical 表、配对比较和重复实验
- ## 12. 跨批次比较
- ## 13. 完整验收标准

## agentmon/docs/agentmon_metrics_catalog_zh.md

Lines: 257; encoding: utf-8

- # AgentMon 监测指标中文手册
- ## P0、P1、P2 分别是什么
- ### P1 应该怎样理解
- ### P2 应该怎样理解
- ### P1 和 P2 如何配合
- ### P1/P2 状态含义
- ## 指标目录
- ## 统一判读规则
- ## 对应源码和文档

## agentmon/docs/agentmon_project_handoff_and_server_usage_zh.md

Lines: 1471; encoding: utf-8

- # AgentMon/AGENTRUN 项目介绍、服务器使用与模型交接手册
- ## 1. 一句话定位
- ## 2. 当前能力边界
- ### 2.1 已实现并经过本地自动测试的能力
- ### 2.2 需要服务器真实环境验证的能力
- ### 2.3 不应误解为已经完成的能力
- ## 3. 分层架构
- ### 3.1 通用核心
- ### 3.2 Agent 框架适配层
- ### 3.3 推理框架适配层
- ### 3.4 分析三层约束
- ## 4. 核心数据契约
- ### 4.1 原始数据权威性
- ### 4.2 时间与身份
- ### 4.3 输入目录
- ## 5. 源码包部署
- ### 5.1 设置包和安装目录
- ### 5.2 校验 SHA256
- ### 5.3 解压
- ### 5.4 选择独立 Python
- ### 5.5 无安装运行
- ### 5.6 可编辑安装
- ### 5.7 本地重建干净源码包
- ## 6. 已有原始轨迹的推荐流程
- ### 6.0 10 分钟冒烟快线
- ### 6.1 设置本次路径
- ### 6.2 轻量环境和目录清点
- ### 6.3 Preflight
- ### 6.4 Dry-run
- ### 6.5 Fast smoke
- ### 6.6 观察长任务
- ### 6.7 Smoke 验收
- ### 6.8 全量分析
- ## 7. 恢复、阶段控制和资源保护
- ### 7.1 正常恢复
- ### 7.2 Stage 控制
- ### 7.3 超时、内存和磁盘
- ### 7.4 可选的重型校验
- ## 8. Baseline 和严格退出码
- ## 9. Analysis Bundle 说明
- ## 10. 状态语义和结论纪律
- ### 10.1 状态
- ### 10.2 不可越过的结论门禁
- ## 11. 模型报告工作流
- ## 12. 常见故障定位
- ### 12.1 找不到 `smoke.log`
- ### 12.2 `RUNS_PARSED=0`
- ### 12.3 `RUNS_PARSED>0` 但请求为 0
- ### 12.4 运行很慢且只有一个核
- ### 12.5 Bundle 很大
- ### 12.6 `NPU_MAPPING_STATUS=UNRESOLVED`
- ### 12.7 Resume 被拒绝
- ## 13. 采集新的 Agent/vLLM 实验
- ### 13.1 环境 Doctor
- ### 13.2 P0 包裹任意 Agent 命令
- ### 13.3 查看单次运行
- ### 13.4 P1 和 P2
- ### 13.5 批量并发运行
- ## 14. Agent Runtime、调度和控制
- ### 14.1 提交普通任务
- ### 14.2 调度与 Optimizer
- ### 14.3 控制 vLLM batch 的推荐方式
- ### 14.4 调度 CPU 给 Agent Tool
- ### 14.5 配置原则
- ## 15. 即插即用扩展
- ## 16. 本地或服务器源码自检
- ## 17. 给另一个模型的排障材料
- ### 17.1 环境
- ### 17.2 数据
- ### 17.3 执行现场
- ### 17.4 一次性生成诊断清单
- ## 18. 可直接交给指导模型的提示词
- ## 19. 相关文档导航
- ## 20. 推荐的首次服务器验收顺序

## agentmon/docs/analysis_failure_diagnosis.md

Lines: 16; encoding: utf-8

- # Analysis Failure Diagnosis

## agentmon/docs/analysis_replay_structural_refactor_design_zh.md

Lines: 426; encoding: utf-8

- # 数据分析与模拟重建结构重构设计
- ## 1. 设计结论
- ## 2. 当前代码事实
- ### 2.1 可复用部分
- ### 2.2 仍需结构重构的部分
- ## 3. R1：通用阶段状态机与真实 DAG
- ### 3.1 模块边界
- ### 3.2 状态语义
- ### 3.3 Optimizer Pipeline DAG
- ### 3.4 阻断规则
- ### 3.5 产物目录
- ## 4. R2：有界内存流式 Replay
- ### 4.1 目标约束
- ### 4.2 输入层
- ### 4.3 时间归一化与排序
- ### 4.4 Snapshot 流
- ### 4.5 Decision 与在线 reducer
- ### 4.6 恢复与确定性
- ## 5. R3：不可变源码身份链与 Golden Gate
- ### 5.1 包内源码清单
- ### 5.2 安装与导入验证
- ### 5.3 历史数据 Golden Gate
- ## 6. 实施顺序
- ### Phase 0：行为冻结
- ### Phase 1：抽取通用阶段内核
- ### Phase 2：流式数据路径
- ### Phase 3：源码身份链
- ### Phase 4：Golden 与服务器资格
- ## 7. 验收标准
- ### 7.1 当前验收账本
- ## 8. 风险与控制
- ## 9. 工作量判断

## agentmon/docs/analysis_schema.md

Lines: 121; encoding: utf-8

- # AgentMon Analysis Schema
- ## Manifest
- ## Normalized Tables
- ## Inventory And Contract Tables
- ## Validation Tables
- ## Semantics Tables
- ## Handoff Tables
- ## Metric Registry

## agentmon/docs/analysis_sop.md

Lines: 336; encoding: utf-8

- # AgentMon Analysis SOP
- ## Run After An Experiment
- ## Fast Smoke For Large Exports
- ## Preflight, Resume, And Stage Control
- ## Input Directory
- ## Incomplete Experiments
- ## Data Foundation And Basic Analysis
- ## Repeated Experiments
- ## Compare Experiments
- ## Fine-Grained Runtime Evidence
- ## Optimizer Experiment Harness
- ### Planned Deeper Optimization
- ## Model Report Workflow
- ## Verify A Model Report
- ## When Not To Write Strong Conclusions
- ## New Runtime-Aware Outputs

## agentmon/docs/batch_p2_step_sequence_usage.md

Lines: 690; encoding: utf-8

- # AgentMon Batch P2 Import and Step Analysis Usage
- ## 1. Environment
- ## 2. P2 Service Events Path
- ## 3. Run One Serial, Service-Exclusive Task
- ## 4. Import a Serial, Service-Exclusive P2 Slice
- ## 5. Import mini-SWE Trajectory
- ## 6. Per-Step Runtime Analysis
- ## 7. Minimal Batch Loop Pattern
- ## 8. Common Problems
- ### `p2_status = missing`
- ### `p2_request_count = 0`
- ### Step analysis has no P2 fields
- ### NPU target metrics are N/A
- ### Prefix cache disabled
- ## 9. Concurrent Batch Runner
- ### Workspace and runtime-source safety gate
- ### Shared-Service P2 Attribution
- ### Full KV mode and automatic analysis

## agentmon/docs/DEEPSEEK_V41_JOINT_CLIENT.md

Lines: 105; encoding: utf-8

- # V4.1 client / KV research joint integration v1
- ## What changes
- ## Exact source semantics (client versions pinned for acceptance)
- ## Timeouts and capability boundaries
- ## Run from the joint bundle
- # Set PY to your existing mini-SWE Python 3.11 environment.
- # It must contain mini-swe-agent 2.4.3, litellm 1.90.1, openai 2.44.0,
- # PyYAML, and numpy. No package installation is performed by these scripts.
- # Offline real-client wire validation, all network connections blocked:
- # Only on the server, with the existing service already running; C=1:

## agentmon/docs/existing_data_offline_analysis_zh.md

Lines: 181; encoding: utf-8

- # AgentMon 已有数据离线分析
- ## 1. 输入要求
- ## 2. 运行命令
- ## 3. 先看哪些文件
- ## 4. 请求指标
- ## 5. 时间轴校验
- ## 6. Workload 与 System 分解
- ## 7. Tail、NPU 和 Host
- ## 8. 无法从旧数据补出的内容
- ## 9. 本地验证

## agentmon/docs/experiment_intent_to_contract_zh.md

Lines: 225; encoding: utf-8

- # 实验意图到快速冒烟 Contract
- ## 用户只需要表达什么
- ## 生成快速冒烟实例
- ## 阶段输出协议
- ## Contract 里如何区分事实和候选
- ## 后续 Gate

## agentmon/docs/experiment_orchestration_zh.md

Lines: 343; encoding: utf-8

- # AgentMon 实验编排与正式矩阵门禁
- ## 10 分钟冒烟推荐流程
- ## 推荐位置
- ## Request 到 Contract
- ## 输出
- ## Contract 示例
- ## 门禁语义
- ## 默认机制激活证据
- ## 执行路径门禁
- ## 自动分析路由
- ## 推荐实验顺序
- ## 边界

## agentmon/docs/metric_dictionary.md

Lines: 32; encoding: utf-8

- # Metric Dictionary

## agentmon/docs/model_pack_contract.md

Lines: 58; encoding: utf-8

- # Model Input Pack Contract

## agentmon/docs/optimizer_experiment_harness_research_line_handoff_zh.md

Lines: 483; encoding: utf-8

- # AgentMon Optimization Experiment Harness 研究线交接
- ## 1. 当前稳定线基线
- ## 1.1 当前研究线交付包
- ## 2. 已有 optimizer 能力边界
- ## 3. 研究目标
- ## 4. 推荐总体设计
- ## 5. 建议代码组织
- ## 6. 里程碑
- ### M1：离线证据底座
- ### M2：Optimizer Advice Replay
- ### 服务器历史数据 qualification
- ### M3：机制评测与消融
- ### M4：在线控制接入
- ## 7. 新对话建议开场 Prompt

## agentmon/docs/p1_mini_swe_adapter.md

Lines: 305; encoding: utf-8

- # P1 mini-SWE Adapter
- ## Context Compression
- ## Preflight
- ## Success Checks
- ## Storage and Abandoned Spans

## agentmon/docs/p2_deep_agent_workload_zh.md

Lines: 191; encoding: utf-8

- # P2 深度采集与 Agent Workload 使用说明
- ## 1. 开关与兼容性
- ## 2. 真实 prefill 边界
- ## 3. Scheduler 与 Continuous Batching
- ## 4. KV Lifecycle
- ## 5. Agent Workload
- ## 6. Client / Server 分段
- ## 7. 服务器启动与检查

## agentmon/docs/p2_vllm_adapter.md

Lines: 1161; encoding: utf-8

- # P2 vLLM Request and KV Observability Requirements
- ## Status
- ## Success Definition
- ## Non-Goals
- ## Deployment Model
- ## Runtime Hook Loader
- ## Discovery Snapshot Semantics
- ## Request ID Propagation
- ## Event Contract
- ## MoE Logical Routing Capture
- ### Capability and boundary
- ### Enabling collection
- # Add this to the existing, otherwise unchanged vLLM serve command:
- ### Event contract
- ### Report and batch semantics
- ## Request Metrics
- ## KV Cache Requirements
- ## KV Pool Configuration
- ## NPU Request Attribution
- ## Report and Summary Additions
- ## Prefix Cache A/B Planning
- ## Capability Discovery
- ## Failure Isolation
- ## Test Requirements
- ## Acceptance Checklist
- ## Suggested Implementation Order
- ### P2.0 Discovery Snapshot
- ### P2.1 Request ID and Mapping
- ### P2.2 Request Latency and Token Metrics
- ### P2.3 KV Pool and NPU Attribution
- ### Prefix Cache Configuration Gate
- ### P2.4 Prefix Cache Experiment Planner

## agentmon/docs/report_validation.md

Lines: 34; encoding: utf-8

- # Report Validation

## agentmon/docs/runtime_speed_optimization_algorithms_zh.md

Lines: 848; encoding: utf-8

- # AGENTRUN Runtime 速度优化策略算法说明
- ## 1. 总体性能模型
- ## 2. 优化目标
- ## 3. 策略总览
- ### 3.1 Runtime 优化总开关
- ## 4. 固定并发准入
- ### 4.1 算法思想
- ### 4.2 为什么能提速
- ### 4.3 调参原则
- ### 4.4 局限性
- ## 5. Workload-aware 任务调度
- ### 5.1 算法思想
- ### 5.2 P90 token budget 检查
- ### 5.3 Long-context 限制
- ### 5.4 Abnormal-path 风险限制
- ### 5.5 缺失预测的处理
- ### 5.6 为什么能提速
- ## 6. 优先级与队列选择
- ## 7. Optimizer 反馈控制
- ### 7.1 算法角色
- ### 7.2 Static-threshold 压力判断
- ### 7.3 Workload-aware Optimizer
- ### 7.4 为什么能提速
- ## 8. Model Gate 请求限流
- ### 8.1 算法思想
- ### 8.2 为什么能提速
- ### 8.3 对端到端时间的影响
- ### 8.4 使用边界
- ## 9. Tool Pool 工具资源调度
- ### 9.1 算法思想
- ### 9.2 命令分类原则
- ### 9.3 为什么能提速
- ## 10. 工具子进程资源约束
- ### 10.1 算法思想
- ### 10.2 为什么能提速
- ### 10.3 使用边界
- ## 11. TaskState 低进展治理
- ### 11.1 算法思想
- ### 11.2 上下文治理建议
- ### 11.3 为什么能提速
- ## 12. 主动上下文压缩
- ### 12.1 触发与目标
- ### 12.2 内置 `model_summary` 算法
- ### 12.3 请求副本与失败语义
- ### 12.4 插件契约
- ### 12.5 为什么可能提速
- ## 13. P2/遥测在速度优化中的作用
- ## 14. 策略之间的相互作用
- ### 14.1 Scheduler 与 Model Gate
- ### 14.2 Scheduler 与 Tool Pool
- ### 14.3 Optimizer 与 Control Plane
- ## 15. 当前不能做到的优化
- ## 16. 验证方法
- ### 16.1 对照组
- ### 16.2 实验组
- ### 16.3 关键指标
- ### 16.4 判定标准
- ## 17. 推荐调优顺序
- ## 18. 通用性原则
- ## 19. 一句话总结

## agentmon/docs/server_release_smoke_zh.md

Lines: 906; encoding: utf-8

- # AgentMon 服务器发布与一次冒烟教程
- ## 1. 解包到新目录
- ## 1.1 源码 quick/full gate
- ## 2. 在真实服务 shell 做 preflight
- # 下方命令同时传入：--acl-python-path "$ACL_PY"
- ## 3. 编译和全量测试
- ## 4. 独立 Runtime P2 实例门禁
- # Resource Gate：验证端口、路径、事件文件新鲜度，并保留 npu-smi 证据。
- # 若工具无法证明设备空闲，先看 LOG，确认后显式设置 YES。
- # Service Start Gate：这是启动 vLLM 的步骤。它使用 start-vllm-p2.sh 中的完整环境、
- # 设备、端口、模型、P2 开关、prefix-cache 和 tool-call 配置。
- # Tool-call Gate：仅验证协议，不跑任务集。要求工具调用时必须通过。
- # mini-SWE 非交互 Gate：确认隔离 HOME/XDG 下不会卡在交互式初始化。
- ### 4.1 Resource Gate
- # 查看上面的 npu-smi/ps 证据后，确认 TARGET_DEVICES 没有被旧服务或其他实验占用，
- # 再把下一行改成 YES。
- ### 4.2 Service Start Gate
- #!/usr/bin/env bash
- ### 4.3 Runtime P2 Gate
- ## 5. 单任务 P1/P2 冒烟
- ## 6. 再开始并发 batch
- ## 7. 故障定位顺序

## agentmon/docs/server_test_from_package_no_cli_zh.md

Lines: 634; encoding: utf-8

- # AgentMon 最新源码包服务器测试说明（少 CLI 版）
- ## 1. 包内关键文件
- ## 2. 最少操作流程
- ### 2.1 路径填写边界
- ### 2.2 只有源码包路径时的 quick smoke
- ### 2.3 内置源码自检
- ## 3. 只做源码自检
- ## 4. 已有原始轨迹/批次目录时的离线分析
- ## 4.1 大目录快速冒烟分析
- ## 5. 真实 vLLM/NPU 环境 preflight
- ### 5.1 发布 Gate 和 Manifest
- ## 6. 已启动服务后的 P2 runtime 验证
- ## 7. 结果文件怎么给我
- ## 8. 常见问题

## agentmon/docs/step_csv_analyzer_usage.md

Lines: 139; encoding: utf-8

- # AgentMon Step CSV Analyzer
- ## Basic Usage
- ## Output Files
- ## What It Diagnoses
- ## Interpretation Notes

## agentmon/docs/troubleshooting.md

Lines: 82; encoding: utf-8

- # Analysis Troubleshooting
- ## Bundle Is PARTIAL
- ## Parquet Missing
- ## Analysis Is Slow Or Appears Single-Core
- ## Resume Was Rejected
- ## P2 Audit Fails
- ## P1 Capture Is PARTIAL Or FAIL
- ## Token Timeseries Is UNRESOLVED
- ## NPU Mapping Is UNRESOLVED
- ## Pipeline Reconciliation Is PARTIAL
- ## Report Validation Fails

## agentmon/offline_ana/lifemem_kv_reuse_analysis.py

Lines: 1497; encoding: utf-8

Offline exact-content reuse analysis for LifeMem retrieval results.

- L189: FunctionDef `normalize_content`
- L195: FunctionDef `content_hash`
- L201: FunctionDef `classify_chunk_type`
- L214: FunctionDef `_normalize_with_boundaries`
- L235: FunctionDef `find_all_occurrences`
- L258: FunctionDef `extract_period_summaries`
- L297: FunctionDef `_looks_like_record`
- L316: FunctionDef `_validate_record_list`
- L326: FunctionDef `records_from_json_value`
- L359: FunctionDef `parse_records_text`
- L386: FunctionDef `load_records`
- L394: FunctionDef `_warning`
- L412: FunctionDef `_optional_scalar`
- L432: FunctionDef `_is_retrieved_wrapper`
- L442: FunctionDef `_occurrence_details`
- L466: FunctionDef `_choose_canonical_occurrence`
- L481: FunctionDef `_preview`
- L486: FunctionDef `_unique_in_order`
- L497: FunctionDef `_logical_memory_identity`
- L521: FunctionDef `_pair_classification`
- L538: FunctionDef `compare_logical_memory_contexts`
- L677: FunctionDef `_public_chunk_row`
- L681: FunctionDef `analyze_records`
- L1205: FunctionDef `_ratio`
- L1209: FunctionDef `_csv_value`
- L1219: FunctionDef `write_csv`
- L1227: FunctionDef `_markdown_cell`
- L1234: FunctionDef `render_context_variation_report`
- L1316: FunctionDef `write_outputs`
- L1348: FunctionDef `print_terminal_summary`
- L1409: FunctionDef `positive_int`
- L1416: FunctionDef `build_parser`
- L1438: FunctionDef `main`
- L1467: FunctionDef `build_direct_run_arguments`
- L1238: FunctionDef `percentage`

## agentmon/offline_ana/README.md

Lines: 136; encoding: utf-8

- # LifeMem exact-content KV reuse offline analysis
- ## 直接修改路径并运行
- ## 可选命令行方式
- ## Exact-match and context rules
- ## 上下文变化三分类
- ## Character metrics
- ## Known limitations

## agentmon/offline_ana/test_lifemem_kv_reuse_analysis.py

Lines: 237; encoding: utf-8

- L19: FunctionDef `result`
- L27: FunctionDef `record`
- L43: FunctionDef `wrapped`
- L47: ClassDef `LifeMemKVReuseAnalysisTests`
- L48: FunctionDef `test_normalize_hash_type_and_occurrence_helpers`
- L66: FunctionDef `test_load_records_supports_json_array_object_container_and_jsonl`
- L83: FunctionDef `test_period_summary_uses_retrieved_wrapper_as_canonical_position`
- L119: FunctionDef `test_cross_question_different_preceding_sequence_is_non_prefix_candidate`
- L142: FunctionDef `test_logical_memory_context_pairs_have_mutually_exclusive_variation_counts`
- L186: FunctionDef `test_missing_results_is_skipped_and_missing_context_is_warned`
- L198: FunctionDef `test_cli_writes_all_outputs_and_honors_limit`

## agentmon/README.md

Lines: 1228; encoding: utf-8

- # AgentMon Ascend
- ## Server Paths
- ## Offline Setup
- ## Required mini-SWE Config
- ## Doctor
- ## Run mini-SWE
- ## Enable P1 mini-SWE Spans
- ## Enable P2 vLLM Hooks
- # Use enabled/disabled only when the experiment requires a fixed cache state.
- # Scheduler iteration, execution-anchored prefill, and full KV lifecycle.
- # Deep is already the default; keep this explicit for experiment provenance.
- ### P1 Preflight
- ### P1 Event Sink
- ### P1 Hashing and Privacy
- ### P1 Success Checks
- ## Inspect Runs
- ## P0 Limits
- ## Existing-Data Offline Analysis
- ## Storage Contract
- ## Development

## agentmon/scripts/agentmon_analyze_steps.py

Lines: 21; encoding: utf-8


## agentmon/scripts/agentmon_batch_runtime.py

Lines: 85; encoding: utf-8

- L25: FunctionDef `_apply_runtime_overrides`
- L38: FunctionDef `_restore_runtime_overrides`
- L43: FunctionDef `run_batch`
- L51: FunctionDef `run_one_task`
- L64: FunctionDef `import_p2`
- L72: FunctionDef `main`

## agentmon/scripts/agentmon_compare_batches.py

Lines: 360; encoding: utf-8

- L29: FunctionDef `main`
- L46: FunctionDef `build_parser`
- L55: FunctionDef `compare_batches`
- L80: FunctionDef `load_batch`
- L93: FunctionDef `compare_to_baseline`
- L171: FunctionDef `batch_statistics`
- L186: FunctionDef `paired_comparison`
- L208: FunctionDef `metric_statistics`
- L255: FunctionDef `bootstrap_mean_ci`
- L267: FunctionDef `repeat_statistics`
- L289: FunctionDef `percentile`
- L304: FunctionDef `format_table`
- L328: FunctionDef `as_dict`
- L332: FunctionDef `to_float`
- L342: FunctionDef `metric_value`
- L350: FunctionDef `parse_bool`
- L106: FunctionDef `check`

## agentmon/scripts/agentmon_existing_data_analysis.py

Lines: 21; encoding: utf-8


## agentmon/scripts/agentmon_experiment.py

Lines: 21; encoding: utf-8


## agentmon/scripts/agentmon_experiment_init.py

Lines: 21; encoding: utf-8


## agentmon/scripts/agentmon_experiment_orchestrator.py

Lines: 21; encoding: utf-8


## agentmon/scripts/agentmon_optimizer_experiment.py

Lines: 21; encoding: utf-8


## agentmon/scripts/agentmon_optimizer_qualification.py

Lines: 240; encoding: utf-8

- L20: FunctionDef `main`
- L111: FunctionDef `build_parser`
- L129: FunctionDef `qualification_payload`
- L170: FunctionDef `target_import_environment`
- L179: FunctionDef `analysis_identity_matches_gate`
- L189: FunctionDef `qualification_failure_reason`
- L201: FunctionDef `parse_last_json_object`
- L213: FunctionDef `read_json`
- L221: FunctionDef `write_json`
- L227: FunctionDef `sha256_file`
- L235: FunctionDef `sha256_text`

## agentmon/scripts/agentmon_p1_smoke_check.py

Lines: 278; encoding: utf-8

- L12: FunctionDef `main`
- L26: FunctionDef `build_parser`
- L35: FunctionDef `load_summary`
- L47: FunctionDef `summarize_report`
- L113: FunctionDef `build_smoke_result`
- L162: FunctionDef `data_integrity_pass`
- L172: FunctionDef `count_nonzero_commands`
- L179: FunctionDef `count_instrumentation_errors`
- L196: FunctionDef `count_submission_control_flow`
- L208: FunctionDef `rc_value`
- L217: FunctionDef `as_dict`
- L221: FunctionDef `number_or_none`
- L229: FunctionDef `positive`
- L234: FunctionDef `data_quality_issues`
- L256: FunctionDef `token_fields_missing`
- L266: FunctionDef `nested_quality`
- L273: FunctionDef `numeric_or_none`

## agentmon/scripts/agentmon_p2_discovery.py

Lines: 737; encoding: utf-8

- L110: FunctionDef `main`
- L132: FunctionDef `build_parser`
- L176: FunctionDef `collect_snapshot`
- L208: FunctionDef `inspect_package`
- L248: FunctionDef `apply_version_selection`
- L276: FunctionDef `inspect_module`
- L303: FunctionDef `inspect_object`
- L326: FunctionDef `scan_package_modules`
- L378: FunctionDef `module_name_from_path`
- L391: FunctionDef `interesting_name_parts`
- L396: FunctionDef `hook_resolution_inputs`
- L436: FunctionDef `python_snapshot`
- L449: FunctionDef `environment_snapshot`
- L475: FunctionDef `readiness_snapshot`
- L524: FunctionDef `vllm_version_status`
- L532: FunctionDef `base_version`
- L539: FunctionDef `release_version`
- L548: FunctionDef `version_source_consistency`
- L560: FunctionDef `version_identity_status`
- L572: FunctionDef `none_if_empty`
- L579: FunctionDef `safe_find_spec`
- L586: FunctionDef `import_module_without_stdout_noise`
- L590: FunctionDef `call_without_stdout_noise`
- L601: FunctionDef `spec_snapshot`
- L612: FunctionDef `module_candidate_symbols`
- L628: FunctionDef `object_metadata`
- L637: FunctionDef `object_kind`
- L653: FunctionDef `safe_signature`
- L662: FunctionDef `safe_object_file`
- L669: FunctionDef `class_methods`
- L685: FunctionDef `source_snapshot`
- L701: FunctionDef `split_ref`
- L710: FunctionDef `resolve_qualname`
- L717: FunctionDef `exception_summary`
- L721: FunctionDef `unique`
- L732: FunctionDef `extend`

## agentmon/scripts/agentmon_perf_summary.py

Lines: 372; encoding: utf-8

- L20: FunctionDef `main`
- L37: FunctionDef `build_parser`
- L57: FunctionDef `build_perf_summary`
- L153: FunctionDef `load_report`
- L160: FunctionDef `read_jsonl_events`
- L171: FunctionDef `flatten_p2_summary`
- L246: FunctionDef `resolve_agent_token_fields`
- L293: FunctionDef `format_summary`
- L309: FunctionDef `display_value`
- L317: FunctionDef `ascend_peak`
- L330: FunctionDef `latency_ms_stats`
- L339: FunctionDef `ns_delta_ms`
- L348: FunctionDef `sum_numeric`
- L353: FunctionDef `numeric_or_none`
- L361: FunctionDef `none_if_empty`
- L367: FunctionDef `as_dict`

## agentmon/scripts/agentmon_release_gate.py

Lines: 1034; encoding: utf-8

- L28: FunctionDef `main`
- L211: FunctionDef `build_parser`
- L247: FunctionDef `gate_level_for_args`
- L253: FunctionDef `agentmon_python_for_args`
- L258: FunctionDef `deadline_seconds_for_args`
- L264: FunctionDef `remaining_deadline_seconds`
- L270: FunctionDef `inspect_package`
- L280: FunctionDef `check_installed_source_matches_package`
- L364: FunctionDef `formal_source_identity_checks`
- L381: FunctionDef `load_package_source_manifest`
- L401: FunctionDef `check_imported_source_root`
- L425: FunctionDef `sha256_stream`
- L432: FunctionDef `check_package_sha256`
- L456: FunctionDef `package_sha256_from_checks`
- L464: FunctionDef `check_previous_gate`
- L504: FunctionDef `expected_sha256`
- L514: FunctionDef `sha256_file`
- L522: FunctionDef `check_archive_members`
- L562: FunctionDef `unsafe_member_reason`
- L575: FunctionDef `forbidden_member`
- L584: FunctionDef `resolve_source_root`
- L615: FunctionDef `find_source_roots`
- L625: FunctionDef `safe_extract`
- L650: FunctionDef `planned_tests`
- L689: FunctionDef `run_test_item`
- L727: FunctionDef `deadline_expired_result`
- L741: FunctionDef `probe_python`
- L806: FunctionDef `last_json`
- L817: FunctionDef `manifest_payload`
- L904: FunctionDef `isoformat_wall_time_ns`
- L908: FunctionDef `attach_manifest_integrity`
- L921: FunctionDef `gate_output_name`
- L925: FunctionDef `output_destinations`
- L941: FunctionDef `delivery_checksum_destinations`
- L954: FunctionDef `write_gate_outputs`
- L973: FunctionDef `write_sha256_sidecar`
- L981: FunctionDef `update_checksum_manifest`
- L998: FunctionDef `checksum_label`
- L1006: FunctionDef `emit_stage_markers`
- L1022: FunctionDef `cleanup_extraction`

## agentmon/scripts/agentmon_server_preflight.py

Lines: 21; encoding: utf-8


## agentmon/scripts/agentmon_server_quick_smoke.py

Lines: 21; encoding: utf-8


## agentmon/scripts/agentmon_step_sequence.py

Lines: 71; encoding: utf-8

- L21: FunctionDef `_apply_core_overrides`
- L33: FunctionDef `_restore_core_overrides`
- L38: FunctionDef `build_step_sequence`
- L46: FunctionDef `write_output_bundle`
- L54: FunctionDef `format_output`
- L62: FunctionDef `main`

## agentmon/scripts/deepseek_v41_client_smoke.py

Lines: 133; encoding: utf-8

Explicit, sequential client smoke against an already-running local endpoint.

- L23: FunctionDef `main`
- L67: FunctionDef `observed_send`

## agentmon/scripts/package_agentmon_source.py

Lines: 345; encoding: utf-8

- L45: FunctionDef `main`
- L56: FunctionDef `build_parser`
- L69: FunctionDef `build_source_package`
- L153: FunctionDef `build_manifest_payload`
- L194: FunctionDef `git_snapshot`
- L213: FunctionDef `run_git`
- L233: FunctionDef `iter_package_files`
- L255: FunctionDef `default_archive_prefix`
- L264: FunctionDef `parent_archive_dirs`
- L272: FunctionDef `add_directory_entry`
- L280: FunctionDef `add_file_entry`
- L288: FunctionDef `add_bytes_entry`
- L298: FunctionDef `normalize_tarinfo`
- L307: FunctionDef `is_executable`
- L311: FunctionDef `sha256_file`
- L319: FunctionDef `archive_members`
- L324: FunctionDef `forbidden_archive_members`
- L332: FunctionDef `member_is_forbidden`

## agentmon/scripts/run_agentmon_analysis_from_path.py

Lines: 123; encoding: utf-8

- L38: FunctionDef `main`
- L108: FunctionDef `bool_from_env`
- L115: FunctionDef `int_from_env`

## agentmon/scripts/run_agentmon_test_from_config.py

Lines: 499; encoding: utf-8

- L26: FunctionDef `main`
- L94: FunctionDef `build_parser`
- L105: FunctionDef `default_self_check_config`
- L114: FunctionDef `seed_config`
- L130: FunctionDef `default_report_dir`
- L134: FunctionDef `resolve_test_config`
- L155: FunctionDef `load_server_profile`
- L171: FunctionDef `default_variables`
- L179: FunctionDef `deep_merge`
- L190: FunctionDef `expand_placeholders`
- L203: FunctionDef `run_self_check`
- L242: FunctionDef `run_unit_tests`
- L259: FunctionDef `run_offline_analysis`
- L346: FunctionDef `run_preflight`
- L411: FunctionDef `preflight_missing_fields`
- L427: FunctionDef `maybe_create_events_parent`
- L436: FunctionDef `add_optional_arg`
- L442: FunctionDef `failed_unit_tests`
- L446: FunctionDef `as_bool`
- L454: FunctionDef `as_dict`
- L458: FunctionDef `as_list`
- L462: FunctionDef `int_or_default`
- L469: FunctionDef `read_json_if_exists`
- L477: FunctionDef `write_json`
- L482: ClassDef `TeeText`
- L483: FunctionDef `__init__`
- L487: FunctionDef `text`
- L490: FunctionDef `write`
- L494: FunctionDef `flush`

## agentmon/src/agentmon/__init__.py

Lines: 3; encoding: utf-8

AgentMon base package.


## agentmon/src/agentmon/__main__.py

Lines: 5; encoding: utf-8


## agentmon/src/agentmon/adapters/__init__.py

Lines: 5; encoding: utf-8


## agentmon/src/agentmon/adapters/agent_frameworks/__init__.py

Lines: 1; encoding: utf-8


## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/__init__.py

Lines: 43; encoding: utf-8

mini-SWE-agent P1 instrumentation adapters.


## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/adapter.py

Lines: 138; encoding: utf-8

- L19: ClassDef `MiniSweAgentFrameworkAdapter`
- L62: FunctionDef `configure_p1_command`
- L103: FunctionDef `adapter`
- L107: FunctionDef `extract_trajectory_response_ids`
- L127: FunctionDef `response_id_from_message`
- L22: FunctionDef `descriptor`
- L55: FunctionDef `prepare_command`
- L58: FunctionDef `extract_request_ids`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/agent.py

Lines: 313; encoding: utf-8

- L12: ClassDef `InstrumentedAgentMixin`
- L196: FunctionDef `make_instrumented_agent_class`
- L201: FunctionDef `_safe_getattr`
- L208: FunctionDef `_copy_messages`
- L214: FunctionDef `_integer_or_none`
- L223: FunctionDef `_action_types`
- L243: FunctionDef `_safe_emit`
- L250: FunctionDef `_safe_context_public`
- L262: FunctionDef `_safe_step_workload`
- L18: FunctionDef `_agentmon_init`
- L23: FunctionDef `run`
- L41: FunctionDef `step`
- L112: FunctionDef `query`
- L118: FunctionDef `execute_actions`
- L177: FunctionDef `serialize`
- L298: ClassDef `InstrumentedDefaultAgent`
- L312: ClassDef `InstrumentedInteractiveAgent`
- L292: ClassDef `InstrumentedDefaultAgent`
- L306: ClassDef `InstrumentedInteractiveAgent`
- L293: FunctionDef `__init__`
- L307: FunctionDef `__init__`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/context.py

Lines: 55; encoding: utf-8

- L9: ClassDef `AgentMonContext`
- L19: FunctionDef `__post_init__`
- L24: FunctionDef `from_env`
- L40: FunctionDef `child`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/context_compression.py

Lines: 146; encoding: utf-8

- L21: ClassDef `MiniSweContextCompressor`
- L45: FunctionDef `build_message_compression_metadata`
- L86: FunctionDef `summary_message`
- L93: FunctionDef `group_mini_swe_messages`
- L122: FunctionDef `estimate_message_tokens`
- L136: FunctionDef `message_role`
- L142: FunctionDef `is_message_sequence`
- L24: FunctionDef `__init__`
- L28: FunctionDef `prepare`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/environment.py

Lines: 751; encoding: utf-8

- L33: ClassDef `InstrumentedEnvironment`
- L74: ClassDef `LocalExecutionResult`
- L95: ClassDef `ToolProcessObserver`
- L182: ClassDef `InstrumentedLocalEnvironment`
- L333: ClassDef `MiniSweLocalExecutionMixin`
- L485: FunctionDef `child_rusage_snapshot`
- L501: FunctionDef `child_rusage_delta`
- L527: FunctionDef `tool_sample_interval_seconds`
- L535: FunctionDef `unavailable_tool_resources`
- L549: FunctionDef `io_delta`
- L564: FunctionDef `action_to_argv`
- L578: FunctionDef `action_metadata`
- L590: FunctionDef `command_shape`
- L611: FunctionDef `result_summary`
- L630: FunctionDef `tool_result_observation`
- L661: FunctionDef `command_category`
- L679: FunctionDef `test_result_counts`
- L695: FunctionDef `text_sha256`
- L701: FunctionDef `integer_or_none`
- L710: FunctionDef `terminate_process_group`
- L723: FunctionDef `truncate_text`
- L732: FunctionDef `_bytes_len`
- L36: FunctionDef `__init__`
- L46: FunctionDef `execute`
- L69: FunctionDef `__getattr__`
- L83: FunctionDef `to_dict`
- L98: FunctionDef `__init__`
- L109: FunctionDef `start`
- L116: FunctionDef `stop`
- L122: FunctionDef `_run`
- L126: FunctionDef `_sample`
- L160: FunctionDef `summary`
- L185: FunctionDef `__init__`
- L202: FunctionDef `execute`
- L336: FunctionDef `_agentmon_sink`
- L343: FunctionDef `execute`
- L750: ClassDef `InstrumentedMiniSweLocalEnvironment`
- L744: ClassDef `InstrumentedMiniSweLocalEnvironment`
- L745: FunctionDef `__init__`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/event_sink.py

Lines: 240; encoding: utf-8

- L19: ClassDef `AdapterEventSink`
- L175: FunctionDef `get_sink`
- L182: FunctionDef `set_sink_for_tests`
- L187: FunctionDef `infer_parent_span_id`
- L205: FunctionDef `span_start_event_type`
- L209: FunctionDef `span_finish_event_type`
- L215: FunctionDef `mini_swe_submitted_control_flow_payload`
- L230: FunctionDef `submitted_exit_status`
- L22: FunctionDef `__init__`
- L46: FunctionDef `from_env`
- L49: FunctionDef `emit`
- L87: FunctionDef `next_sequence`
- L93: FunctionDef `span`
- L167: FunctionDef `close`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/ids.py

Lines: 19; encoding: utf-8

- L6: FunctionDef `new_span_id`
- L10: FunctionDef `new_step_id`
- L14: FunctionDef `new_request_id`
- L18: FunctionDef `new_tool_call_id`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/model.py

Lines: 789; encoding: utf-8

- L21: ClassDef `InstrumentedModel`
- L127: ClassDef `InstrumentedModelMixin`
- L316: FunctionDef `inject_request_headers`
- L344: FunctionDef `accepts_explicit_keyword`
- L358: FunctionDef `accepts_keyword`
- L374: FunctionDef `summarize_messages`
- L378: FunctionDef `text_summary_target`
- L394: FunctionDef `build_model_summary_provider`
- L506: FunctionDef `summary_request_messages`
- L526: FunctionDef `extract_response_text`
- L543: FunctionDef `integer_usage`
- L557: FunctionDef `extract_usage`
- L588: FunctionDef `extract_response_identifiers`
- L613: FunctionDef `_get_model_name`
- L625: FunctionDef `_get_nested`
- L631: FunctionDef `safe_emit`
- L638: FunctionDef `load_adapter_config`
- L653: FunctionDef `safe_prepare_context`
- L677: FunctionDef `compression_telemetry`
- L696: FunctionDef `emit_context_compression_decision`
- L719: FunctionDef `safe_begin_workload`
- L743: FunctionDef `safe_finish_workload`
- L24: FunctionDef `__init__`
- L40: FunctionDef `query`
- L123: FunctionDef `__getattr__`
- L130: FunctionDef `_agentmon_sink`
- L137: FunctionDef `_agentmon_tracker`
- L144: FunctionDef `_agentmon_transport_context`
- L151: FunctionDef `_agentmon_compressor`
- L159: FunctionDef `query`
- L256: FunctionDef `_query`
- L384: FunctionDef `query_summary`
- L407: FunctionDef `summarize`
- L788: ClassDef `InstrumentedLitellmModel`
- L782: ClassDef `InstrumentedLitellmModel`
- L783: FunctionDef `__init__`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/profiled_model.py

Lines: 27; encoding: utf-8

Opt-in profile bridge; no model-specific request parameters are introduced.

- L13: FunctionDef `profile_request_kwargs`
- L21: ClassDef `ProfiledLitellmModel`
- L23: FunctionDef `model_name`
- L26: FunctionDef `query`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/runtime_control.py

Lines: 102; encoding: utf-8

- L14: ClassDef `RuntimeControlRejected`
- L18: ClassDef `RuntimeControlledModel`
- L59: ClassDef `RuntimeControlledToolResult`
- L72: ClassDef `RuntimeControlledLocalEnvironment`
- L21: FunctionDef `__init__`
- L36: FunctionDef `query`
- L54: FunctionDef `__getattr__`
- L64: FunctionDef `to_dict`
- L75: FunctionDef `__init__`
- L88: FunctionDef `execute`

## agentmon/src/agentmon/adapters/agent_frameworks/mini_swe/workload.py

Lines: 409; encoding: utf-8

- L17: ClassDef `ContextSnapshot`
- L23: ClassDef `ModelWorkloadTracker`
- L84: FunctionDef `capture_context`
- L153: FunctionDef `compare_context`
- L214: FunctionDef `summarize_agent_step`
- L266: FunctionDef `infer_model_semantic_phase`
- L279: FunctionDef `message_action_count`
- L288: FunctionDef `compaction_signals`
- L315: FunctionDef `message_fingerprint`
- L351: FunctionDef `content_char_count`
- L362: FunctionDef `token_estimate_chars_per_token`
- L371: FunctionDef `estimate_tokens`
- L375: FunctionDef `common_prefix_length`
- L384: FunctionDef `difference_or_none`
- L390: FunctionDef `integer_or_none`
- L399: FunctionDef `sequence_length`
- L408: FunctionDef `is_message_sequence`
- L26: FunctionDef `__init__`
- L33: FunctionDef `begin`
- L55: FunctionDef `finish`

## agentmon/src/agentmon/adapters/inference/__init__.py

Lines: 1; encoding: utf-8


## agentmon/src/agentmon/adapters/inference/vllm_018/__init__.py

Lines: 50; encoding: utf-8

P2 adapter helpers for vLLM 0.18.x.


## agentmon/src/agentmon/adapters/inference/vllm_018/adapter.py

Lines: 74; encoding: utf-8

- L9: ClassDef `Vllm018InferenceTelemetryProvider`
- L73: FunctionDef `provider`
- L12: FunctionDef `descriptor`
- L46: FunctionDef `build_index`
- L54: FunctionDef `select_events`

## agentmon/src/agentmon/adapters/inference/vllm_018/events.py

Lines: 533; encoding: utf-8

- L24: ClassDef `RequestMapping`
- L61: ClassDef `VllmP2EventSink`
- L532: FunctionDef `quality_from_correlation`
- L41: FunctionDef `to_payload`
- L68: FunctionDef `__init__`
- L100: FunctionDef `from_env`
- L103: FunctionDef `emit`
- L138: FunctionDef `request_received`
- L166: FunctionDef `request_queued`
- L184: FunctionDef `request_first_scheduled`
- L202: FunctionDef `request_prefill_completed`
- L232: FunctionDef `request_prompt_cache_accounted`
- L263: FunctionDef `request_first_token`
- L281: FunctionDef `request_preempted`
- L298: FunctionDef `request_finished`
- L352: FunctionDef `adapter_capability`
- L360: FunctionDef `adapter_error`
- L366: FunctionDef `kv_snapshot`
- L386: FunctionDef `scheduler_snapshot`
- L409: FunctionDef `scheduler_iteration`
- L423: FunctionDef `kv_block_event`
- L463: FunctionDef `kv_reset`
- L483: FunctionDef `request_npu_attribution`
- L506: FunctionDef `_request_event`
- L522: FunctionDef `next_sequence`
- L527: FunctionDef `close`

## agentmon/src/agentmon/adapters/inference/vllm_018/hook.py

Lines: 2492; encoding: utf-8

- L47: ClassDef `VllmP2HookState`
- L309: FunctionDef `install_from_env`
- L315: FunctionDef `install`
- L491: FunctionDef `installed_distribution_version`
- L502: FunctionDef `uninstall_for_tests`
- L511: FunctionDef `hook_targets`
- L576: FunctionDef `resolve_class`
- L588: FunctionDef `patch_method`
- L600: FunctionDef `wrap_async_add_request`
- L632: FunctionDef `wrap_async_generate`
- L675: FunctionDef `wrap_async_abort`
- L692: FunctionDef `wrap_llm_add_request`
- L724: FunctionDef `wrap_llm_step`
- L741: FunctionDef `wrap_engine_core_add_request`
- L757: FunctionDef `wrap_engine_core_step`
- L761: FunctionDef `wrap_engine_core_step_with_batch_queue`
- L767: FunctionDef `wrap_engine_core_output_method`
- L791: FunctionDef `wrap_scheduler_init`
- L809: FunctionDef `wrap_scheduler_add_request`
- L832: FunctionDef `wrap_scheduler_schedule`
- L865: FunctionDef `wrap_scheduler_update_from_output`
- L887: FunctionDef `wrap_scheduler_make_stats`
- L907: FunctionDef `wrap_kv_manager_init`
- L925: FunctionDef `wrap_kv_make_prefix_cache_stats`
- L954: FunctionDef `wrap_kv_get_computed_blocks`
- L979: FunctionDef `wrap_kv_allocate_slots`
- L1009: FunctionDef `wrap_kv_cache_blocks`
- L1039: FunctionDef `wrap_kv_free`
- L1071: FunctionDef `wrap_kv_evict_blocks`
- L1098: FunctionDef `wrap_kv_reset_prefix_cache`
- L1125: FunctionDef `emit_received`
- L1167: FunctionDef `emit_queued_from_request`
- L1189: FunctionDef `emit_prompt_cache_accounting_from_request`
- L1214: FunctionDef `emit_kv_block_from_request`
- L1249: FunctionDef `observe_request_output`
- L1362: FunctionDef `observe_engine_core_step_result`
- L1377: FunctionDef `observe_engine_core_outputs`
- L1437: FunctionDef `scheduler_request_state`
- L1460: FunctionDef `observe_deep_scheduler_output`
- L1621: FunctionDef `observe_scheduler_iteration_completed`
- L1690: FunctionDef `observe_scheduler_output`
- L1717: FunctionDef `observe_scheduler_stats`
- L1781: FunctionDef `observe_prefix_cache_stats`
- L1802: FunctionDef `emit_scheduler_kv_config`
- L1822: FunctionDef `emit_scheduler_runtime_capability`
- L1920: FunctionDef `validate_prefix_cache_observation`
- L1949: FunctionDef `emit_cancelled`
- L1971: FunctionDef `emit_request_payload`
- L1980: FunctionDef `observe_moe_routing`
- L2007: FunctionDef `emit_moe_routing_capability`
- L2059: FunctionDef `request_payload`
- L2080: FunctionDef `kv_pool_inputs_from_scheduler`
- L2103: FunctionDef `prefix_status_from_scheduler`
- L2109: FunctionDef `agent_request_id_from_headers`
- L2113: FunctionDef `call_argument`
- L2129: FunctionDef `agentmon_context_from_headers`
- L2170: FunctionDef `request_id_from_call`
- L2177: FunctionDef `request_id_from_generate_call`
- L2184: FunctionDef `request_id_from_request_like`
- L2190: FunctionDef `as_request_ids`
- L2200: FunctionDef `generation_token_count`
- L2215: FunctionDef `generation_tokens_from_usage`
- L2227: FunctionDef `engine_core_new_token_count`
- L2236: FunctionDef `prompt_token_count`
- L2240: FunctionDef `prompt_token_accounting`
- L2263: FunctionDef `finish_reason_from_output`
- L2272: FunctionDef `model_name_from_engine`
- L2282: FunctionDef `safe_model_value`
- L2295: FunctionDef `discover_moe_expert_count`
- L2317: FunctionDef `attr_chain`
- L2326: FunctionDef `first_present`
- L2333: FunctionDef `sequence_length`
- L2342: FunctionDef `scheduler_scheduled_tokens`
- L2360: FunctionDef `scheduler_waiting_count`
- L2369: FunctionDef `normalized_cached_tokens`
- L2376: FunctionDef `engine_output_request_count`
- L2390: FunctionDef `kv_block_count`
- L2425: FunctionDef `kv_blocks_for_request`
- L2440: FunctionDef `numeric_or_none`
- L2456: FunctionDef `boolean_or_none`
- L2469: FunctionDef `normalize_output_kind`
- L2483: FunctionDef `none_if_empty`
- L2489: FunctionDef `safe_str`
- L48: FunctionDef `__init__`
- L90: FunctionDef `mark_seen`
- L100: FunctionDef `emit_error`
- L106: FunctionDef `emit_capability`
- L112: FunctionDef `next_scheduler_iteration_id`
- L117: FunctionDef `remember_scheduler_iteration`
- L123: FunctionDef `pop_scheduler_iteration`
- L127: FunctionDef `record_request_id_alias`
- L140: FunctionDef `resolve_engine_request_id`
- L155: FunctionDef `clear_request_ids`
- L173: FunctionDef `record_last_token`
- L179: FunctionDef `pop_last_token`
- L183: FunctionDef `add_generation_tokens`
- L191: FunctionDef `pop_generation_tokens`
- L195: FunctionDef `generation_tokens`
- L199: FunctionDef `record_api_generation_tokens`
- L226: FunctionDef `enter_engine_core_observation`
- L231: FunctionDef `leave_engine_core_observation`
- L241: FunctionDef `record_request_context`
- L270: FunctionDef `enrich_payload`
- L300: FunctionDef `_copy_request_context_unlocked`
- L601: FunctionDef `factory`
- L633: FunctionDef `factory`
- L676: FunctionDef `factory`
- L693: FunctionDef `factory`
- L725: FunctionDef `factory`
- L742: FunctionDef `factory`
- L771: FunctionDef `factory`
- L792: FunctionDef `factory`
- L810: FunctionDef `factory`
- L833: FunctionDef `factory`
- L868: FunctionDef `factory`
- L888: FunctionDef `factory`
- L908: FunctionDef `factory`
- L926: FunctionDef `factory`
- L955: FunctionDef `factory`
- L980: FunctionDef `factory`
- L1010: FunctionDef `factory`
- L1040: FunctionDef `factory`
- L1072: FunctionDef `factory`
- L1101: FunctionDef `factory`
- L603: AsyncFunctionDef `wrapper`
- L635: AsyncFunctionDef `wrapper`
- L678: AsyncFunctionDef `wrapper`
- L695: FunctionDef `wrapper`
- L727: FunctionDef `wrapper`
- L744: FunctionDef `wrapper`
- L773: FunctionDef `wrapper`
- L794: FunctionDef `wrapper`
- L812: FunctionDef `wrapper`
- L835: FunctionDef `wrapper`
- L870: FunctionDef `wrapper`
- L890: FunctionDef `wrapper`
- L910: FunctionDef `wrapper`
- L928: FunctionDef `wrapper`
- L957: FunctionDef `wrapper`
- L982: FunctionDef `wrapper`
- L1012: FunctionDef `wrapper`
- L1042: FunctionDef `wrapper`
- L1074: FunctionDef `wrapper`
- L1103: FunctionDef `wrapper`

## agentmon/src/agentmon/adapters/inference/vllm_018/hook_config.py

Lines: 61; encoding: utf-8

- L21: FunctionDef `deep_batch_request_limit`
- L30: FunctionDef `normalized_prefix_cache_expectation`
- L35: FunctionDef `prefix_cache_cli_arguments_from_process`
- L45: FunctionDef `prefix_cache_expectation_status`
- L53: FunctionDef `truthy`
- L57: FunctionDef `environment_flag`

## agentmon/src/agentmon/adapters/inference/vllm_018/importer.py

Lines: 128; encoding: utf-8

- L14: FunctionDef `is_p2_global_event`
- L32: FunctionDef `is_p2_run_capability_event`
- L36: FunctionDef `summarize_p2_imported_events`
- L82: FunctionDef `normalize_imported_p2_event`
- L123: FunctionDef `as_dict`
- L127: FunctionDef `string_or_none`

## agentmon/src/agentmon/adapters/inference/vllm_018/kv.py

Lines: 72; encoding: utf-8

- L31: FunctionDef `calculate_kv_pool`
- L68: FunctionDef `cache_dtype_bytes`

## agentmon/src/agentmon/adapters/inference/vllm_018/moe.py

Lines: 396; encoding: utf-8

- L19: FunctionDef `moe_mode_from_env`
- L26: FunctionDef `moe_chunk_tokens_from_env`
- L36: FunctionDef `routed_expert_events`
- L115: FunctionDef `as_int32_route_array`
- L130: FunctionDef `summarize_route_array`
- L240: FunctionDef `layer_balance_summary`
- L280: FunctionDef `encode_route_chunks`
- L319: FunctionDef `decode_route_chunk`
- L334: FunctionDef `expected_routed_token_count`
- L360: FunctionDef `unresolved_summary`
- L389: FunctionDef `max_or_none`
- L394: FunctionDef `min_or_none`

## agentmon/src/agentmon/adapters/inference/vllm_018/p2_index.py

Lines: 943; encoding: utf-8

- L44: FunctionDef `is_p2_run_capability_event_type`
- L50: FunctionDef `build_or_load_p2_batch_index`
- L101: FunctionDef `build_p2_batch_index`
- L192: FunctionDef `compact_index`
- L224: FunctionDef `serialize_compact_index`
- L244: FunctionDef `hydrate_compact_index`
- L288: FunctionDef `add_time_indexes`
- L310: FunctionDef `sorted_time_rows`
- L326: FunctionDef `indexed_events_in_window`
- L362: FunctionDef `select_indexed_p2_events`
- L454: FunctionDef `select_indexed_p2_global_events`
- L523: FunctionDef `filter_p2_events_for_response_ids`
- L585: FunctionDef `p2_service_bounds`
- L589: FunctionDef `p2_service_bounds_from_events`
- L639: FunctionDef `ambiguous_api_request_ids`
- L655: FunctionDef `request_ids_from_events`
- L670: FunctionDef `request_identity_count`
- L675: FunctionDef `request_ids_in_window`
- L693: FunctionDef `unique_events`
- L704: FunctionDef `read_jsonl_events`
- L708: FunctionDef `read_jsonl_events_with_offsets`
- L782: FunctionDef `parse_jsonl_event`
- L792: FunctionDef `write_jsonl_events`
- L797: FunctionDef `is_p2_event`
- L801: FunctionDef `is_p2_request_event_type`
- L805: FunctionDef `is_p2_request_or_keyed_event_type`
- L809: FunctionDef `p2_payload_matches`
- L820: FunctionDef `p2_alias_ids`
- L839: FunctionDef `event_in_window`
- L851: FunctionDef `event_time_ns`
- L867: FunctionDef `as_list_dict`
- L871: FunctionDef `as_dict`
- L875: FunctionDef `string_or_none`
- L881: FunctionDef `is_blank_value`
- L885: FunctionDef `integer_or_none`
- L903: FunctionDef `source_fingerprint`
- L931: FunctionDef `file_hash_if_exists`
- L941: FunctionDef `write_json_private`
- L196: FunctionDef `event_positions`
- L201: FunctionDef `mapping_positions`
- L254: FunctionDef `event_refs`
- L264: FunctionDef `mapping_refs`

## agentmon/src/agentmon/adapters/inference/vllm_018/version.py

Lines: 190; encoding: utf-8

- L13: ClassDef `UnsupportedVllmVersion`
- L18: ClassDef `VersionResolution`
- L42: FunctionDef `base_version`
- L49: FunctionDef `release_version`
- L58: FunctionDef `is_supported_vllm_version`
- L62: FunctionDef `resolve_vllm_version`
- L141: FunctionDef `detected_vllm_version`
- L145: FunctionDef `version_source_consistency`
- L157: FunctionDef `version_identity_status`
- L169: FunctionDef `require_supported_vllm_version`
- L186: FunctionDef `normalized_version`
- L38: FunctionDef `to_dict`

## agentmon/src/agentmon/adapters/mini_swe/__init__.py

Lines: 3; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/adapter.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/agent.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/context.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/context_compression.py

Lines: 8; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/environment.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/event_sink.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/ids.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/model.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/runtime_control.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/mini_swe/workload.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/registry.py

Lines: 85; encoding: utf-8

- L6: ClassDef `AdapterCapabilityRegistry`
- L84: FunctionDef `adapter_capability_catalog`
- L7: FunctionDef `__init__`
- L10: FunctionDef `register`
- L15: FunctionDef `list`
- L21: FunctionDef `catalog`

## agentmon/src/agentmon/adapters/vllm_018/__init__.py

Lines: 3; encoding: utf-8


## agentmon/src/agentmon/adapters/vllm_018/events.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/vllm_018/hook.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/vllm_018/kv.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/vllm_018/p2_index.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/adapters/vllm_018/version.py

Lines: 6; encoding: utf-8


## agentmon/src/agentmon/analysis/__init__.py

Lines: 16; encoding: utf-8

Deterministic AgentMon experiment analysis SOP.

- L8: FunctionDef `__getattr__`

## agentmon/src/agentmon/analysis/analyzers.py

Lines: 1593; encoding: utf-8

- L61: FunctionDef `analyze_bundle`
- L195: FunctionDef `stage_output_tables`
- L208: FunctionDef `first_existing_analysis_table`
- L216: FunctionDef `load_analysis_tables`
- L235: FunctionDef `analyze_throughput`
- L298: FunctionDef `analyze_concurrency`
- L349: FunctionDef `analyze_request_lifecycle`
- L423: FunctionDef `workload_baseline_key`
- L428: FunctionDef `analyze_workload`
- L497: FunctionDef `analyze_tail`
- L559: FunctionDef `analyze_pipeline`
- L654: FunctionDef `analyze_scheduler`
- L813: FunctionDef `engine_window_metrics`
- L897: FunctionDef `engine_sensitivity`
- L925: FunctionDef `resample_engine_items`
- L950: FunctionDef `sample_intervals`
- L961: FunctionDef `weighted_mean`
- L981: FunctionDef `longest_matching_interval`
- L1000: FunctionDef `analyze_resources`
- L1085: FunctionDef `analyze_p1_capture`
- L1139: FunctionDef `analyze_p2`
- L1198: FunctionDef `write_methodology_docs`
- L1233: FunctionDef `maybe_write_figures`
- L1257: FunctionDef `parse_json_value`
- L1271: FunctionDef `steady_state_token_row`
- L1312: FunctionDef `token_timeseries_rows`
- L1343: FunctionDef `configured_concurrency_decay_rows`
- L1382: FunctionDef `effective_capacity_drain_rows`
- L1418: FunctionDef `first_sustained_below`
- L1438: FunctionDef `first_point_sustained_below`
- L1462: FunctionDef `trajectory_model_attempts`
- L1487: FunctionDef `is_trajectory_model_record`
- L1498: FunctionDef `flat_keys`
- L1510: FunctionDef `deep_first`
- L1527: FunctionDef `request_identity_index`
- L1545: FunctionDef `first_non_empty`
- L1552: FunctionDef `is_missing`
- L1556: FunctionDef `group_by_batch`
- L1563: FunctionDef `workload_window_s`
- L1575: FunctionDef `ns_to_s`
- L1582: FunctionDef `none_delta`
- L1590: FunctionDef `truthy`

## agentmon/src/agentmon/analysis/artifacts.py

Lines: 184; encoding: utf-8

- L13: FunctionDef `build_artifact_manifest`
- L78: FunctionDef `add_file`
- L93: FunctionDef `add_table`
- L122: FunctionDef `add_manifest_tables`
- L132: FunctionDef `add_optional_dir`
- L150: FunctionDef `artifact_row`
- L172: FunctionDef `table_entries`
- L178: FunctionDef `profile_table_mentions_vllm`

## agentmon/src/agentmon/analysis/batch_compare.py

Lines: 247; encoding: utf-8

- L27: FunctionDef `compare_batch_steps`
- L61: FunctionDef `load_batch`
- L80: FunctionDef `locate_step_csv`
- L88: FunctionDef `comparability_checks`
- L113: FunctionDef `check`
- L123: FunctionDef `model_rows_with_tasks`
- L134: FunctionDef `pair_step_rows`
- L147: FunctionDef `pair_task_aggregates`
- L159: FunctionDef `aggregate_task`
- L169: FunctionDef `pair_row`
- L193: FunctionDef `step_key`
- L202: FunctionDef `statistics_bundle`
- L217: FunctionDef `read_json`
- L224: FunctionDef `read_csv`
- L231: FunctionDef `as_dict`
- L235: FunctionDef `number_or_none`
- L245: FunctionDef `integer_or_none`

## agentmon/src/agentmon/analysis/claims.py

Lines: 158; encoding: utf-8

- L16: FunctionDef `build_claim_registry`
- L93: FunctionDef `superseded_history`
- L114: FunctionDef `infer_claim_module`
- L133: FunctionDef `module_validator_status`
- L145: FunctionDef `statement_hash`
- L150: FunctionDef `deterministic_timestamp`
- L155: FunctionDef `as_list`

## agentmon/src/agentmon/analysis/cli.py

Lines: 358; encoding: utf-8

- L21: FunctionDef `add_analysis_parser`
- L83: FunctionDef `add_common_analysis_args`
- L130: FunctionDef `dispatch_analysis`
- L274: FunctionDef `build_config_from_args`
- L332: FunctionDef `default_omitted_tables`
- L336: FunctionDef `strict_exit_code`
- L355: FunctionDef `parse_modules`

## agentmon/src/agentmon/analysis/compare.py

Lines: 198; encoding: utf-8

- L9: FunctionDef `compare_bundles`
- L48: FunctionDef `compare_dimensions`
- L71: FunctionDef `comparison_key`
- L80: FunctionDef `comparison_index`
- L90: FunctionDef `build_normalized_deltas`
- L117: FunctionDef `build_paired_deltas`
- L147: FunctionDef `build_warnings`
- L170: FunctionDef `delta`
- L178: FunctionDef `relative_delta`
- L186: FunctionDef `comparison_markdown`

## agentmon/src/agentmon/analysis/config.py

Lines: 225; encoding: utf-8

- L15: ClassDef `IntegrityThresholds`
- L22: ClassDef `ValidationThresholds`
- L35: ClassDef `AnalysisWindows`
- L44: ClassDef `StatisticsConfig`
- L50: ClassDef `OutputConfig`
- L64: ClassDef `RuntimeConfig`
- L78: ClassDef `BaselineConfig`
- L86: ClassDef `ProfileDetectionConfig`
- L93: ClassDef `EngineMetricConfig`
- L107: ClassDef `AnalysisConfig`
- L124: FunctionDef `default_analysis_config`
- L128: FunctionDef `load_analysis_config`
- L138: FunctionDef `read_config_file`
- L149: FunctionDef `write_resolved_config`
- L153: FunctionDef `config_from_dict`
- L169: FunctionDef `filtered`
- L175: FunctionDef `deep_update`
- L183: FunctionDef `parse_minimal_yaml`
- L208: FunctionDef `parse_yaml_scalar`
- L120: FunctionDef `to_dict`

## agentmon/src/agentmon/analysis/contract.py

Lines: 308; encoding: utf-8

- L31: FunctionDef `default_contract_template`
- L80: FunctionDef `load_experiment_contract`
- L89: FunctionDef `write_contract_template`
- L95: FunctionDef `write_experiment_contract`
- L126: FunctionDef `build_contract`
- L156: FunctionDef `fill_discovery_fields`
- L182: FunctionDef `validate_contract`
- L201: FunctionDef `research_question_rows`
- L243: FunctionDef `expected_artifacts_payload`
- L260: FunctionDef `field_sources`
- L274: FunctionDef `has_contract_value`
- L290: FunctionDef `contract_limitations`
- L301: FunctionDef `deep_merged`

## agentmon/src/agentmon/analysis/discovery.py

Lines: 680; encoding: utf-8

- L30: ClassDef `DiscoveryResult`
- L44: FunctionDef `discover_experiment`
- L108: FunctionDef `discover_run_dirs`
- L120: FunctionDef `discover_batch_dirs`
- L128: FunctionDef `discover_p2_streams`
- L140: FunctionDef `file_inventory_row`
- L155: FunctionDef `classify_file`
- L184: FunctionDef `run_inventory_row`
- L238: FunctionDef `batch_inventory_row`
- L320: FunctionDef `phase_timings_from_events`
- L363: FunctionDef `iter_jsonl_simple`
- L373: FunctionDef `phase_duration`
- L381: FunctionDef `synthetic_batch_row`
- L413: FunctionDef `build_trial_inventory`
- L437: FunctionDef `build_manifest`
- L501: FunctionDef `schema_version_rows`
- L515: FunctionDef `lightweight_jsonl_row`
- L534: FunctionDef `source_snapshot_hash`
- L540: FunctionDef `line_count_if_requested`
- L548: FunctionDef `read_tsv`
- L555: FunctionDef `task_set_sha256`
- L569: FunctionDef `infer_experiment_id`
- L574: FunctionDef `infer_trial_id`
- L582: FunctionDef `infer_batch_id`
- L590: FunctionDef `infer_concurrency_from_path`
- L603: FunctionDef `inventory_status`
- L613: FunctionDef `source_for_values`
- L623: FunctionDef `infer_common_value`
- L630: FunctionDef `missing_value`
- L634: FunctionDef `first_existing`
- L641: FunctionDef `first_non_null`
- L648: FunctionDef `min_text`
- L653: FunctionDef `max_text`
- L658: FunctionDef `text_or_none`
- L664: FunctionDef `integer_from_many`
- L675: FunctionDef `is_relative_to`

## agentmon/src/agentmon/analysis/domain_profiles/__init__.py

Lines: 5; encoding: utf-8


## agentmon/src/agentmon/analysis/domain_profiles/agentmon.py

Lines: 70; encoding: utf-8

- L9: FunctionDef `builtin_agentmon_profiles`

## agentmon/src/agentmon/analysis/evidence.py

Lines: 281; encoding: utf-8

- L16: FunctionDef `build_evidence_bundle`
- L73: FunctionDef `verified_facts`
- L156: FunctionDef `statistical_associations`
- L185: FunctionDef `mechanism_inferences`
- L203: FunctionDef `unresolved_questions`
- L219: FunctionDef `excluded_hypotheses`
- L231: FunctionDef `build_evidence_index`
- L249: FunctionDef `fact`
- L275: FunctionDef `index_row`

## agentmon/src/agentmon/analysis/execution.py

Lines: 617; encoding: utf-8

- L25: ClassDef `StageRangeComplete`
- L34: ClassDef `StageRecord`
- L82: ClassDef `AnalysisRunContext`
- L459: FunctionDef `now_iso`
- L463: FunctionDef `process_cpu_seconds`
- L468: FunctionDef `peak_rss_bytes`
- L473: FunctionDef `disk_used_bytes`
- L480: FunctionDef `filesystem_usage_fields`
- L496: FunctionDef `disk_delta`
- L500: FunctionDef `output_hashes`
- L512: FunctionDef `as_list`
- L520: FunctionDef `build_stage_execution_plan`
- L586: FunctionDef `stage_cache_artifacts_ready`
- L615: FunctionDef `stop_after_stage`
- L28: FunctionDef `__init__`
- L56: FunctionDef `to_dict`
- L83: FunctionDef `__init__`
- L149: FunctionDef `start_stage`
- L169: FunctionDef `set_input_hashes`
- L175: FunctionDef `attach_stage_outputs`
- L181: FunctionDef `finish_stage`
- L213: FunctionDef `fail_stage`
- L229: FunctionDef `update_progress`
- L234: FunctionDef `heartbeat`
- L244: FunctionDef `check_timeout`
- L249: FunctionDef `complete`
- L265: FunctionDef `write_status`
- L269: FunctionDef `_write_status_unlocked`
- L298: FunctionDef `_periodic_heartbeat`
- L307: FunctionDef `write_stage`
- L314: FunctionDef `write_stage_dag`
- L322: FunctionDef `write_shared_pipeline_manifest`
- L342: FunctionDef `_pipeline_result_for`
- L379: FunctionDef `_assert_dependencies_satisfied`
- L395: FunctionDef `write_runtime_summary`
- L415: FunctionDef `skip_pending_after`
- L426: FunctionDef `log`
- L430: FunctionDef `install_signal_handlers`
- L440: FunctionDef `restore_signal_handlers`
- L450: FunctionDef `_handle_signal`

## agentmon/src/agentmon/analysis/existing_data.py

Lines: 3022; encoding: utf-8

- L30: FunctionDef `main`
- L53: FunctionDef `build_parser`
- L72: FunctionDef `parse_batch_spec`
- L82: FunctionDef `rate_argument`
- L89: FunctionDef `analyze_existing_batches`
- L189: FunctionDef `load_batch`
- L224: FunctionDef `locate_step_csvs`
- L247: FunctionDef `load_batch_run_events`
- L263: FunctionDef `build_p2_event_index`
- L321: FunctionDef `merge_p2_context`
- L354: FunctionDef `derive_request_metrics`
- L453: FunctionDef `p2_candidates_for_row`
- L469: FunctionDef `inferred_candidate_count`
- L478: FunctionDef `normalized_match_status`
- L499: FunctionDef `lifecycle_status_from_context`
- L507: FunctionDef `normalized_missing_reason`
- L518: FunctionDef `derive_one_request_metric`
- L663: FunctionDef `context_delta_s`
- L682: FunctionDef `client_server_proxies`
- L716: FunctionDef `request_metric_quality`
- L729: FunctionDef `analyze_timeline`
- L951: FunctionDef `completion_offsets_s`
- L958: FunctionDef `find_per_run_model_overlaps`
- L983: FunctionDef `sweep_concurrency`
- L1028: FunctionDef `integral_check`
- L1054: FunctionDef `summarize_runs`
- L1135: FunctionDef `decompose_paired_runs`
- L1168: FunctionDef `decompose_run_pair`
- L1233: FunctionDef `product_decomposition`
- L1255: FunctionDef `pair_model_calls`
- L1309: FunctionDef `analyze_inflight_thresholds`
- L1423: FunctionDef `detect_inflight_change_point`
- L1502: FunctionDef `percentile_values`
- L1509: FunctionDef `align_sequences`
- L1548: FunctionDef `model_distance`
- L1558: FunctionDef `model_pair_quality`
- L1571: FunctionDef `attach_host_pressure`
- L1596: FunctionDef `classify_tails`
- L1757: FunctionDef `analyze_device_imbalance`
- L1797: FunctionDef `metric_by_device`
- L1806: FunctionDef `imbalance_fields`
- L1827: FunctionDef `summarize_device_imbalance`
- L1850: FunctionDef `expand_device_metrics`
- L1891: FunctionDef `metric_coverage_quality`
- L1899: FunctionDef `analyze_host_metrics`
- L2034: FunctionDef `parse_loadavg`
- L2044: FunctionDef `pressure_value`
- L2048: FunctionDef `cpu_utilization_by_core`
- L2068: FunctionDef `cpu_iowait_percent`
- L2080: FunctionDef `aggregate_diskstats`
- L2098: FunctionDef `counter_delta`
- L2108: FunctionDef `counter_rate`
- L2118: FunctionDef `build_inventory`
- L2143: FunctionDef `build_coverage_rows`
- L2202: FunctionDef `summarize_request_metrics`
- L2243: FunctionDef `summarize_host_metrics`
- L2291: FunctionDef `summarize_tails`
- L2311: FunctionDef `summarize_correlation`
- L2322: FunctionDef `summarize_correlation_slice`
- L2344: FunctionDef `summarize_decomposition`
- L2364: FunctionDef `build_validation_checks`
- L2444: FunctionDef `build_quality_bundle`
- L2490: FunctionDef `write_analysis`
- L2548: FunctionDef `format_data_gaps`
- L2569: FunctionDef `format_report`
- L2686: FunctionDef `timeline_check`
- L2692: FunctionDef `quality_check`
- L2702: FunctionDef `timeline_violation`
- L2706: FunctionDef `step_run_bounds`
- L2722: FunctionDef `summarize_numbers`
- L2733: FunctionDef `percentile`
- L2748: FunctionDef `percentile_rank`
- L2757: FunctionDef `request_order`
- L2761: FunctionDef `subtract_if_present`
- L2769: FunctionDef `delta`
- L2775: FunctionDef `sum_if_complete`
- L2782: FunctionDef `sum_if_any`
- L2788: FunctionDef `first_present`
- L2795: FunctionDef `first_number`
- L2803: FunctionDef `first_integer`
- L2811: FunctionDef `first_text`
- L2819: FunctionDef `ratio`
- L2827: FunctionDef `ns_delta_seconds`
- L2835: FunctionDef `number`
- L2839: FunctionDef `number_or_none`
- L2855: FunctionDef `integer_or_none`
- L2860: FunctionDef `integer_if_whole`
- L2866: FunctionDef `text_or_none`
- L2873: FunctionDef `has_value`
- L2877: FunctionDef `is_true`
- L2881: FunctionDef `parse_bool`
- L2894: FunctionDef `is_nonzero`
- L2899: FunctionDef `nonempty_values`
- L2903: FunctionDef `value_counts`
- L2910: FunctionDef `as_dict`
- L2914: FunctionDef `as_set_dict`
- L2926: FunctionDef `parse_json_dict`
- L2939: FunctionDef `json_compact`
- L2943: FunctionDef `format_percent`
- L2948: FunctionDef `format_metric`
- L2953: FunctionDef `read_json`
- L2963: FunctionDef `read_jsonl`
- L2982: FunctionDef `read_delimited`
- L2989: FunctionDef `write_json`
- L2993: FunctionDef `write_csv`
- L3009: FunctionDef `write_text`
- L3014: FunctionDef `chmod_private`
- L1644: FunctionDef `add`
- L1939: FunctionDef `rate`

## agentmon/src/agentmon/analysis/failure_diagnosis.py

Lines: 113; encoding: utf-8

- L10: FunctionDef `write_failure_diagnosis`
- L69: FunctionDef `failure_category`
- L99: FunctionDef `proposed_fix`

## agentmon/src/agentmon/analysis/handoff.py

Lines: 411; encoding: utf-8

- L12: FunctionDef `build_handoff_package`
- L55: FunctionDef `load_basic_tables`
- L69: FunctionDef `observation_register_rows`
- L148: FunctionDef `observation_row`
- L170: FunctionDef `anomaly_register_rows`
- L201: FunctionDef `anomaly_row`
- L221: FunctionDef `negative_scaling_rows`
- L250: FunctionDef `first_numeric_metric`
- L265: FunctionDef `workload_drift_rows`
- L283: FunctionDef `contradiction_register_rows`
- L322: FunctionDef `unresolved_question_rows`
- L371: FunctionDef `analysis_backlog_rows`

## agentmon/src/agentmon/analysis/intervals.py

Lines: 21; encoding: utf-8

- L6: FunctionDef `merge_intervals`

## agentmon/src/agentmon/analysis/inventory.py

Lines: 277; encoding: utf-8

- L17: ClassDef `BaselineExpectations`
- L24: FunctionDef `build_input_inventory`
- L96: FunctionDef `write_input_inventory`
- L101: FunctionDef `check_input_baseline`
- L145: FunctionDef `add_count_check`
- L168: FunctionDef `discover_lightweight_run_dirs`
- L180: FunctionDef `first_existing_event_file`
- L188: FunctionDef `is_event_jsonl`
- L193: FunctionDef `sample_jsonl`
- L223: FunctionDef `read_run_id`
- L233: FunctionDef `input_metadata_fingerprint`
- L255: FunctionDef `boundary_content_sha256`
- L269: FunctionDef `as_int`

## agentmon/src/agentmon/analysis/io.py

Lines: 382; encoding: utf-8

- L18: FunctionDef `ensure_dir`
- L22: FunctionDef `write_json`
- L29: FunctionDef `read_json`
- L38: FunctionDef `write_markdown`
- L42: FunctionDef `write_plain_csv`
- L58: FunctionDef `file_sha256`
- L66: FunctionDef `stable_source_hash`
- L78: FunctionDef `stable_metadata_hash`
- L98: FunctionDef `line_count`
- L106: FunctionDef `open_maybe_compressed`
- L125: FunctionDef `iter_jsonl`
- L140: FunctionDef `inspect_jsonl`
- L206: FunctionDef `write_table`
- L231: FunctionDef `write_parquet`
- L242: FunctionDef `write_csv_gz`
- L256: FunctionDef `read_table`
- L269: FunctionDef `read_bundle_table`
- L278: FunctionDef `write_table_manifest`
- L297: FunctionDef `normalize_rows`
- L304: FunctionDef `normalize_scalar`
- L314: FunctionDef `csv_scalar`
- L323: FunctionDef `integer_or_none`
- L341: FunctionDef `float_or_none`
- L357: FunctionDef `parse_json_text`
- L370: FunctionDef `hardlink_or_copy`

## agentmon/src/agentmon/analysis/model_pack.py

Lines: 328; encoding: utf-8

- L85: FunctionDef `build_model_input_pack`
- L139: FunctionDef `build_module_packs`
- L203: FunctionDef `filter_evidence`
- L211: FunctionDef `evidence_id`
- L218: FunctionDef `module_status_value`
- L231: FunctionDef `write_plain_csv`
- L242: FunctionDef `csv_value`
- L248: FunctionDef `module_readme`
- L259: FunctionDef `module_report_prompt`
- L278: FunctionDef `first_existing_table`
- L286: FunctionDef `model_pack_readme`
- L303: FunctionDef `report_prompt`

## agentmon/src/agentmon/analysis/normalize.py

Lines: 1726; encoding: utf-8

- L58: ClassDef `NormalizedDataset`
- L65: FunctionDef `normalize_experiment`
- L184: FunctionDef `normalized_omitted_tables`
- L191: FunctionDef `normalize_jobs`
- L211: FunctionDef `event_file_size`
- L220: FunctionDef `process_pool_launch_safe`
- L227: FunctionDef `enforce_memory_limit`
- L237: FunctionDef `normalize_one_run`
- L300: FunctionDef `merge_table_rows`
- L305: FunctionDef `run_progress`
- L328: FunctionDef `load_normalized_dataset`
- L335: FunctionDef `build_run_batch_context`
- L367: FunctionDef `default_run_context`
- L384: FunctionDef `append_task_results`
- L409: FunctionDef `append_workers`
- L427: FunctionDef `append_worker_intervals_from_batch`
- L455: FunctionDef `append_run_tables`
- L536: FunctionDef `append_request_tables`
- L730: FunctionDef `append_tool_and_span_tables`
- L844: FunctionDef `append_event_tables`
- L928: FunctionDef `append_scheduler_and_resource_tables`
- L1222: FunctionDef `minimal_run_row`
- L1263: FunctionDef `lifecycle_residual`
- L1276: FunctionDef `ns_interval`
- L1284: FunctionDef `ns_delta_s`
- L1291: FunctionDef `ns_value_s`
- L1296: FunctionDef `sum_int`
- L1308: FunctionDef `aliases`
- L1319: FunctionDef `aliases_json`
- L1323: FunctionDef `p2_eligibility`
- L1339: FunctionDef `request_identity_audit_row`
- L1408: FunctionDef `append_request_parse_audit`
- L1523: FunctionDef `deduplicate_events`
- L1541: FunctionDef `request_candidate_identities`
- L1558: FunctionDef `add_cross_run_identity_audit`
- L1650: FunctionDef `refresh_run_p2_coverage`
- L1663: FunctionDef `p2_entity_id`
- L1673: FunctionDef `request_audit_summary`
- L1691: FunctionDef `first_text`
- L1698: FunctionDef `parse_bool`
- L1706: FunctionDef `parse_json`
- L1718: FunctionDef `first_payload_value`
- L1725: FunctionDef `update_present`

## agentmon/src/agentmon/analysis/p2_index.py

Lines: 660; encoding: utf-8

- L20: ClassDef `P2IndexResult`
- L35: FunctionDef `build_or_reuse_p2_index`
- L278: FunctionDef `p2_build_metrics`
- L317: FunctionDef `query_p2_events`
- L371: FunctionDef `event_aliases`
- L376: FunctionDef `initialize_schema`
- L417: FunctionDef `insert_batch`
- L470: FunctionDef `iter_jsonl_events`
- L489: FunctionDef `iter_jsonl_events_from_offset`
- L509: FunctionDef `parse_line`
- L519: FunctionDef `offset_progress`
- L524: FunctionDef `source_fingerprint`
- L547: FunctionDef `incremental_append_sources`
- L591: FunctionDef `source_prefix_matches`
- L608: FunctionDef `indexed_source_line_count`
- L613: FunctionDef `sqlite_index_ready`
- L621: FunctionDef `empty_metrics`
- L643: FunctionDef `first_value`
- L652: FunctionDef `text_value`
- L656: FunctionDef `int_value`
- L26: FunctionDef `to_dict`

## agentmon/src/agentmon/analysis/pipeline.py

Lines: 1499; encoding: utf-8

- L32: FunctionDef `prepare_analysis_bundle`
- L260: FunctionDef `run_all_analysis`
- L409: FunctionDef `_run_all_analysis_impl`
- L999: FunctionDef `baseline_expectations`
- L1008: FunctionDef `normalized_stage_tables`
- L1026: FunctionDef `normalized_stage_output_files`
- L1050: FunctionDef `existing_paths`
- L1054: FunctionDef `shared_p2_sources`
- L1067: FunctionDef `finalize_p2_index_metrics`
- L1083: FunctionDef `combine_final_status`
- L1092: FunctionDef `cached_stage_names`
- L1102: FunctionDef `build_optimizer_harness_stage_bundle`
- L1159: FunctionDef `safe_artifact_name`
- L1164: FunctionDef `early_result`
- L1195: FunctionDef `write_failure_contract`
- L1245: FunctionDef `write_skipped_contract`
- L1273: FunctionDef `prepared_result_from_cached`
- L1304: FunctionDef `skipped_output`
- L1322: FunctionDef `write_manifest`
- L1329: FunctionDef `write_inventory`
- L1358: FunctionDef `validate_existing_bundle`
- L1373: FunctionDef `analyze_existing_bundle`
- L1390: FunctionDef `build_status_lines`
- L1470: FunctionDef `status_from_table`
- L1480: FunctionDef `write_readme`
- L125: FunctionDef `p2_index_progress`
- L161: FunctionDef `normalization_progress`

## agentmon/src/agentmon/analysis/preflight.py

Lines: 266; encoding: utf-8

- L23: FunctionDef `run_analysis_preflight`
- L121: FunctionDef `dependency_checks`
- L133: FunctionDef `module_check`
- L166: FunctionDef `parquet_smoke_check`
- L187: FunctionDef `check_path`
- L205: FunctionDef `disk_report`
- L226: FunctionDef `check_row`
- L236: FunctionDef `input_has_zstd`
- L245: FunctionDef `preflight_status_lines`
- L265: FunctionDef `preflight_json_for_stdout`

## agentmon/src/agentmon/analysis/profiles.py

Lines: 143; encoding: utf-8

- L15: ClassDef `SchemaProfile`
- L37: FunctionDef `available_profiles`
- L61: FunctionDef `resolve_profile`
- L117: FunctionDef `is_event_type`
- L121: FunctionDef `values_for_keys`
- L132: FunctionDef `schema_versions`
- L136: FunctionDef `tuple_text`

## agentmon/src/agentmon/analysis/prometheus.py

Lines: 156; encoding: utf-8

- L10: ClassDef `MetricSample`
- L24: FunctionDef `parse_prometheus_text`
- L52: FunctionDef `parse_labels`
- L99: FunctionDef `parse_prometheus_float`
- L110: FunctionDef `_metric_type_for`
- L121: FunctionDef `samples_to_json`
- L148: FunctionDef `match_semantic_metrics`

## agentmon/src/agentmon/analysis/report_validation.py

Lines: 309; encoding: utf-8

- L19: FunctionDef `verify_model_report`
- L69: FunctionDef `collect_pack_numbers`
- L87: FunctionDef `collect_numbers_from_obj`
- L100: FunctionDef `find_forbidden_claims`
- L114: FunctionDef `find_numeric_mismatches`
- L135: FunctionDef `find_unsupported_claims`
- L151: FunctionDef `find_superseded_claims`
- L174: FunctionDef `find_semantic_mismatches`
- L210: FunctionDef `find_metric_unit_mismatches`
- L241: FunctionDef `collect_named_values`
- L263: FunctionDef `collect_named_values_from_object`
- L274: FunctionDef `normalize_number`
- L283: FunctionDef `first_distinct_phrase`
- L299: FunctionDef `validation_markdown`

## agentmon/src/agentmon/analysis/resume.py

Lines: 261; encoding: utf-8

- L22: ClassDef `ResumeDecision`
- L38: FunctionDef `build_analysis_manifest`
- L76: FunctionDef `write_analysis_manifest`
- L80: FunctionDef `decide_resume`
- L121: FunctionDef `compare_manifest`
- L148: FunctionDef `normalized_cache_ready`
- L175: FunctionDef `artifact_cache_ready`
- L197: FunctionDef `config_fingerprint`
- L217: FunctionDef `source_fingerprint`
- L234: FunctionDef `now_iso`
- L238: FunctionDef `dependency_versions`
- L248: FunctionDef `artifact_inventory`
- L29: FunctionDef `to_dict`

## agentmon/src/agentmon/analysis/schemas.py

Lines: 131; encoding: utf-8

- L29: ClassDef `MetricDefinition`
- L98: FunctionDef `load_schema_profile`
- L107: FunctionDef `load_metric_definitions`
- L116: FunctionDef `metric_dictionary_markdown`
- L50: FunctionDef `from_dict`
- L75: FunctionDef `to_dict`

## agentmon/src/agentmon/analysis/semantics.py

Lines: 197; encoding: utf-8

- L14: FunctionDef `write_semantics_bundle`
- L60: FunctionDef `metric_dictionary_rows`
- L72: FunctionDef `entity_set_definition_rows`
- L133: FunctionDef `entity_row`
- L145: FunctionDef `clock_domain_audit_rows`
- L175: FunctionDef `entity_definitions_markdown`
- L188: FunctionDef `clock_audit_markdown`

## agentmon/src/agentmon/analysis/stages.py

Lines: 100; encoding: utf-8

- L11: ClassDef `AnalysisStageResult`
- L21: ClassDef `AnalysisStageContext`
- L27: ClassDef `AnalysisStage`
- L72: FunctionDef `stage_spec`
- L80: FunctionDef `stage_dag_nodes`
- L95: FunctionDef `stage_dag_edges`
- L30: FunctionDef `run`

## agentmon/src/agentmon/analysis/statistics.py

Lines: 151; encoding: utf-8

- L10: FunctionDef `percentile`
- L25: FunctionDef `mean`
- L32: FunctionDef `median`
- L36: FunctionDef `iqr`
- L44: FunctionDef `safe_div`
- L52: FunctionDef `relative_error`
- L61: FunctionDef `interval_union`
- L77: FunctionDef `overlap_duration`
- L94: FunctionDef `time_weighted_summary`
- L120: FunctionDef `concurrency_series`
- L141: FunctionDef `wilson_interval`
- L102: FunctionDef `weighted_percentile`

## agentmon/src/agentmon/analysis/step_csv.py

Lines: 1507; encoding: utf-8

- L167: FunctionDef `main`
- L187: FunctionDef `build_parser`
- L218: FunctionDef `analyze_step_csv`
- L315: FunctionDef `read_csv_rows`
- L321: FunctionDef `unique_items`
- L331: FunctionDef `item_key`
- L358: FunctionDef `has_model_signal`
- L368: FunctionDef `model_hotspot_duration`
- L381: FunctionDef `has_tool_signal`
- L391: FunctionDef `merge_rows`
- L400: FunctionDef `summarize_run`
- L627: FunctionDef `resolve_run_wall`
- L652: FunctionDef `classify_run`
- L780: FunctionDef `summarize_overview`
- L818: FunctionDef `summarize_quality`
- L899: FunctionDef `model_has_p2_evidence`
- L916: FunctionDef `model_has_auditable_p2_match`
- L932: FunctionDef `build_analysis_quality_bundle`
- L1041: FunctionDef `analysis_quality_check`
- L1051: FunctionDef `summarize_tool_timeouts`
- L1065: FunctionDef `field_coverage`
- L1071: FunctionDef `field_coverage_any`
- L1077: FunctionDef `preferred_metric_values`
- L1089: FunctionDef `preferred_field_coverage`
- L1100: FunctionDef `accepted_quality_coverage`
- L1115: FunctionDef `enough_coverage`
- L1120: FunctionDef `build_recommendations`
- L1172: FunctionDef `format_analysis`
- L1180: FunctionDef `format_report`
- L1226: FunctionDef `format_run_table`
- L1241: FunctionDef `format_model_table`
- L1257: FunctionDef `format_tool_table`
- L1270: FunctionDef `format_quality`
- L1291: FunctionDef `write_outputs`
- L1316: FunctionDef `rows_to_csv`
- L1325: FunctionDef `select_fields`
- L1329: FunctionDef `normalize_value`
- L1340: FunctionDef `count_labels`
- L1351: FunctionDef `count_values`
- L1361: FunctionDef `rate_argument`
- L1371: FunctionDef `as_dict`
- L1375: FunctionDef `coverage`
- L1381: FunctionDef `percentile`
- L1396: FunctionDef `kv_pressure_score`
- L1404: FunctionDef `sum_numbers`
- L1409: FunctionDef `mean_number`
- L1416: FunctionDef `max_number`
- L1421: FunctionDef `ratio`
- L1429: FunctionDef `number`
- L1434: FunctionDef `to_float`
- L1446: FunctionDef `as_float`
- L1453: FunctionDef `round_or_none`
- L1458: FunctionDef `has_value`
- L1465: FunctionDef `first_text`
- L1472: FunctionDef `is_nonzero_returncode`
- L1477: FunctionDef `truthy`
- L1481: FunctionDef `display_number`
- L1490: FunctionDef `fmt_s`
- L1495: FunctionDef `fmt_pct`
- L1500: FunctionDef `truncate`

## agentmon/src/agentmon/analysis/validators.py

Lines: 722; encoding: utf-8

- L31: FunctionDef `validate_analysis_bundle`
- L104: FunctionDef `build_conservation_checks`
- L132: FunctionDef `build_data_quality_gate`
- L182: FunctionDef `gate_limitations`
- L190: FunctionDef `load_normalized_tables`
- L212: FunctionDef `build_integrity_issues`
- L250: FunctionDef `build_metric_coverage`
- L278: FunctionDef `validate_time_conservation`
- L315: FunctionDef `validate_lifecycle_conservation`
- L352: FunctionDef `validate_little_law`
- L402: FunctionDef `validate_pipeline`
- L473: FunctionDef `validate_request_ids`
- L496: FunctionDef `validate_request_conservation`
- L520: FunctionDef `validate_profile_detection`
- L536: FunctionDef `validate_resource_sampling`
- L558: FunctionDef `p2_status`
- L582: FunctionDef `npu_mapping_status`
- L603: FunctionDef `p1_capture_status`
- L615: FunctionDef `token_timeseries_status`
- L624: FunctionDef `conclusion_policy`
- L662: FunctionDef `inventory_status_from_discovery`
- L673: FunctionDef `rows_status`
- L678: FunctionDef `combine_status`
- L689: FunctionDef `status_from_relative_error`
- L699: FunctionDef `ns_to_s`
- L706: FunctionDef `truthy`
- L712: FunctionDef `parse_json_value`

## agentmon/src/agentmon/batch/__init__.py

Lines: 19; encoding: utf-8

Batch execution and analysis orchestration package.


## agentmon/src/agentmon/batch/analysis_runner.py

Lines: 668; encoding: utf-8

- L45: FunctionDef `analyze_one_task`
- L344: FunctionDef `run_one_task`
- L356: FunctionDef `copy_p2_result_fields`
- L385: FunctionDef `detect_native_p1_status`
- L407: FunctionDef `import_trajectory`
- L432: FunctionDef `import_p2`
- L528: FunctionDef `regenerate_report`
- L559: FunctionDef `write_step_sequence_outputs`
- L621: FunctionDef `load_step_sequence_module`
- L636: FunctionDef `extract_trajectory_response_ids`
- L657: FunctionDef `trajectory_messages`

## agentmon/src/agentmon/batch/artifacts.py

Lines: 441; encoding: utf-8

- L11: FunctionDef `read_step_totals`
- L41: FunctionDef `read_report_validation`
- L59: FunctionDef `build_task_validation`
- L97: FunctionDef `validation_check`
- L112: FunctionDef `write_task_validation`
- L118: FunctionDef `write_analysis_quality_bundle`
- L199: FunctionDef `write_results_tsv`
- L326: FunctionDef `read_results_tsv`
- L413: FunctionDef `combine_step_csvs`

## agentmon/src/agentmon/batch/commands.py

Lines: 91; encoding: utf-8

- L17: FunctionDef `task_command`
- L25: FunctionDef `configure_p1_command`
- L29: FunctionDef `run_judge`
- L47: FunctionDef `append_error`
- L51: FunctionDef `agentmon_command`
- L61: FunctionDef `run_logged`
- L80: FunctionDef `parse_run_id`
- L85: FunctionDef `parse_run_dir`

## agentmon/src/agentmon/batch/constants.py

Lines: 4; encoding: utf-8


## agentmon/src/agentmon/batch/io.py

Lines: 114; encoding: utf-8

- L10: FunctionDef `read_tasks`
- L31: FunctionDef `read_env_file`
- L49: FunctionDef `batch_context`
- L57: FunctionDef `render_template`
- L64: FunctionDef `write_json_private`
- L73: FunctionDef `sanitize_task_id`
- L78: FunctionDef `make_private_dir`
- L86: FunctionDef `read_text`
- L93: FunctionDef `as_dict`
- L97: FunctionDef `string_or_none`
- L103: FunctionDef `is_blank_value`
- L107: FunctionDef `integer_or_none`

## agentmon/src/agentmon/batch/manifest.py

Lines: 394; encoding: utf-8

- L25: FunctionDef `update_manifest_p2_metadata`
- L54: FunctionDef `build_batch_manifest`
- L208: FunctionDef `parse_metadata`
- L221: FunctionDef `package_version`
- L243: FunctionDef `git_commit`
- L254: FunctionDef `model_config_hash`
- L271: FunctionDef `detect_cann_version`
- L289: FunctionDef `safe_scheduler_environment`
- L305: FunctionDef `file_hash_if_exists`
- L315: FunctionDef `path_hash_if_exists`
- L330: FunctionDef `parse_vllm_launch_settings`
- L383: FunctionDef `parse_scalar`

## agentmon/src/agentmon/batch/metrics.py

Lines: 122; encoding: utf-8

- L5: FunctionDef `count_result_values`
- L13: FunctionDef `number`
- L22: FunctionDef `optional_number`
- L34: FunctionDef `aggregate_optional_metric`
- L55: FunctionDef `ratio`
- L59: FunctionDef `throughput_metric`
- L79: FunctionDef `steady_state_completion_rate`
- L112: FunctionDef `percentile_linear`

## agentmon/src/agentmon/batch/policy.py

Lines: 56; encoding: utf-8

- L7: FunctionDef `analysis_defer_reason`
- L35: FunctionDef `p2_index_cache_root`
- L39: FunctionDef `should_write_p2_service_global_copy`
- L45: FunctionDef `p2_service_global_missing_window_policy`
- L51: FunctionDef `should_build_combined_step_csv`
- L55: FunctionDef `should_build_timeline`

## agentmon/src/agentmon/batch/resources.py

Lines: 172; encoding: utf-8

- L13: FunctionDef `capture_pipeline_resources`
- L40: FunctionDef `collect_npu_process_mapping`
- L53: FunctionDef `capture_file_inventory`
- L81: FunctionDef `aggregate_task_stage_profiles`
- L118: FunctionDef `pipeline_resource_delta`
- L147: FunctionDef `read_proc_self_io`
- L167: FunctionDef `numeric_delta`

## agentmon/src/agentmon/batch/runtime.py

Lines: 1334; encoding: utf-8

- L170: FunctionDef `result_is_failure`
- L188: FunctionDef `evaluate_runtime_source_integrity`
- L255: FunctionDef `annotate_integrity_violation`
- L280: FunctionDef `main`
- L292: FunctionDef `build_parser`
- L462: FunctionDef `run_batch`
- L1058: FunctionDef `finalize_deferred_analysis_batch`

## agentmon/src/agentmon/batch/safety.py

Lines: 435; encoding: utf-8

- L32: ClassDef `WorkspacePolicyError`
- L36: FunctionDef `is_relative_to`
- L44: FunctionDef `paths_overlap`
- L50: FunctionDef `looks_like_agentmon_source_root`
- L55: FunctionDef `protected_source_roots`
- L78: FunctionDef `_absolute_rendered_path`
- L87: FunctionDef `_validate_workspace_contract`
- L145: FunctionDef `render_workspace_contract`
- L172: FunctionDef `build_workspace_contracts`
- L198: FunctionDef `revalidate_workspace_contract`
- L213: FunctionDef `write_workspace_contracts`
- L233: FunctionDef `_iter_source_files`
- L252: FunctionDef `capture_source_manifest`
- L281: FunctionDef `compare_source_manifests`
- L310: FunctionDef `_snapshot_ignore`
- L318: FunctionDef `_make_tree_read_only`
- L326: FunctionDef `prepare_runtime_source_snapshot`
- L356: FunctionDef `_path_exposes_protected_source`
- L363: FunctionDef `build_isolated_runtime_env`
- L397: FunctionDef `verify_runtime_snapshot`

## agentmon/src/agentmon/batch/summary.py

Lines: 288; encoding: utf-8

- L25: FunctionDef `build_batch_completion`
- L98: FunctionDef `build_batch_summary`
- L274: FunctionDef `restore_execution_measurements`

## agentmon/src/agentmon/batch/workload.py

Lines: 306; encoding: utf-8

- L21: FunctionDef `run_workload_task`
- L127: FunctionDef `initial_task_result`
- L230: FunctionDef `failed_worker_result`
- L258: FunctionDef `find_trajectory`
- L285: FunctionDef `find_run_trajectory`
- L302: FunctionDef `has_trajectory_sample`

## agentmon/src/agentmon/capabilities.py

Lines: 14; encoding: utf-8

- L8: ClassDef `Capability`
- L13: FunctionDef `to_dict`

## agentmon/src/agentmon/cli.py

Lines: 1666; encoding: utf-8

- L69: FunctionDef `build_parser`
- L423: FunctionDef `main`
- L476: FunctionDef `add_config_args`
- L486: FunctionDef `_load_cfg`
- L497: FunctionDef `_doctor`
- L520: FunctionDef `_run`
- L545: FunctionDef `_runtime`
- L650: FunctionDef `_runtime_daemon_loop`
- L694: FunctionDef `_optimizer`
- L754: FunctionDef `read_optional_json_object`
- L763: FunctionDef `_strategy`
- L794: FunctionDef `_adapter`
- L816: FunctionDef `_control`
- L913: FunctionDef `_report`
- L925: FunctionDef `_export`
- L935: FunctionDef `_canonical`
- L965: FunctionDef `_summary`
- L977: FunctionDef `_aggregate`
- L1013: FunctionDef `_timeline`
- L1030: FunctionDef `_matrix`
- L1049: FunctionDef `_experiment`
- L1056: FunctionDef `_experiment_plan_prefix_cache`
- L1076: FunctionDef `_compare`
- L1098: FunctionDef `load_report_for_run`
- L1115: FunctionDef `parse_text_report`
- L1152: FunctionDef `resolve_aggregate_run_dirs`
- L1161: FunctionDef `resolve_run_arg`
- L1168: FunctionDef `parse_time_filter`
- L1182: FunctionDef `as_dict`
- L1186: FunctionDef `string_or_none`
- L1192: FunctionDef `integer_or_none`
- L1202: FunctionDef `format_summary_output`
- L1210: FunctionDef `format_aggregate_output`
- L1218: FunctionDef `_import_trajectory`
- L1245: FunctionDef `_import_p2`
- L1351: FunctionDef `read_p2_event_slice`
- L1400: FunctionDef `load_p2_api_request_id_filters`
- L1411: FunctionDef `split_request_ids`
- L1415: FunctionDef `read_request_id_file`
- L1432: FunctionDef `extract_response_ids_from_trajectory`
- L1445: FunctionDef `trajectory_messages`
- L1461: FunctionDef `select_p2_events_for_api_request_ids`
- L1519: FunctionDef `p2_selection_diagnostics`
- L1569: FunctionDef `p2_payload_matches_ids`
- L1580: FunctionDef `p2_event_in_window`
- L1592: FunctionDef `file_sha256`
- L1600: FunctionDef `p2_event_time_ns`
- L1617: FunctionDef `p2_event_matches_run`
- L1626: FunctionDef `_live`
- L1650: FunctionDef `format_live_p1`

## agentmon/src/agentmon/clock.py

Lines: 55; encoding: utf-8

- L13: FunctionDef `source_host`
- L17: FunctionDef `host_boot_id`
- L25: FunctionDef `monotonic_clock_domain`
- L36: ClassDef `ClockAnchor`
- L44: FunctionDef `capture`
- L50: FunctionDef `to_dict`

## agentmon/src/agentmon/config.py

Lines: 710; encoding: utf-8

- L13: ClassDef `ConfigError`
- L18: ClassDef `GeneralConfig`
- L26: ClassDef `AgentConfig`
- L43: ClassDef `VllmConfig`
- L57: ClassDef `ModelConfig`
- L66: ClassDef `ProcConfig`
- L75: ClassDef `AscendConfig`
- L84: ClassDef `StorageConfig`
- L95: ClassDef `FinalizationConfig`
- L109: ClassDef `PrivacyConfig`
- L117: ClassDef `RuntimeOptimizationConfig`
- L124: ClassDef `ContextCompressionConfig`
- L140: ClassDef `SchedulerConfig`
- L153: ClassDef `OptimizerConfig`
- L164: ClassDef `TaskStateConfig`
- L179: ClassDef `ControlConfig`
- L185: ClassDef `ModelGateConfig`
- L195: ClassDef `ToolPoolConfig`
- L213: ClassDef `AgentMonConfig`
- L261: FunctionDef `load_config`
- L290: FunctionDef `validate_config`
- L444: FunctionDef `_select_config_path`
- L469: FunctionDef `_load_toml_file`
- L482: FunctionDef `_parse_basic_toml`
- L511: FunctionDef `_strip_toml_comment`
- L532: FunctionDef `_parse_basic_toml_value`
- L550: FunctionDef `_split_array`
- L575: FunctionDef `_toml_bracket_delta`
- L601: FunctionDef `_merge_nested`
- L624: FunctionDef `_env_overrides`
- L636: FunctionDef `_expand_dotted_overrides`
- L646: FunctionDef `_coerce_value`
- L677: FunctionDef `_expand_env_vars`
- L687: FunctionDef `_from_dict`
- L236: FunctionDef `to_dict`

## agentmon/src/agentmon/context_compression/__init__.py

Lines: 40; encoding: utf-8


## agentmon/src/agentmon/context_compression/registry.py

Lines: 174; encoding: utf-8

- L25: ClassDef `ContextCompressionStrategy`
- L42: ClassDef `ContextCompressionTrigger`
- L97: FunctionDef `build_context_compression_trigger`
- L109: FunctionDef `build_context_compression_strategy`
- L121: FunctionDef `load_context_compression_plugins`
- L131: FunctionDef `register_context_compression_strategy`
- L146: FunctionDef `register_context_compression_trigger`
- L161: FunctionDef `unregister_context_compression_strategy`
- L165: FunctionDef `unregister_context_compression_trigger`
- L169: FunctionDef `context_compression_strategy_catalog`
- L173: FunctionDef `context_compression_trigger_catalog`
- L28: FunctionDef `measure`
- L35: FunctionDef `compress`
- L45: FunctionDef `evaluate`

## agentmon/src/agentmon/context_compression/service.py

Lines: 365; encoding: utf-8

- L22: ClassDef `ContextCompressionService`
- L313: FunctionDef `resolve_window_tokens`
- L318: FunctionDef `item_count_or_none`
- L325: FunctionDef `positive_int_or_none`
- L335: FunctionDef `metadata_for_result`
- L364: FunctionDef `is_sequence`
- L23: FunctionDef `__init__`
- L34: FunctionDef `prepare`
- L266: FunctionDef `_decision`

## agentmon/src/agentmon/context_compression/strategies.py

Lines: 402; encoding: utf-8

- L24: ClassDef `TokenRatioContextCompressionTrigger`
- L59: ClassDef `RecentGroupsContextCompression`
- L186: ClassDef `ModelSummaryContextCompression`
- L345: FunctionDef `normalized_groups`
- L367: FunctionDef `normalized_estimates`
- L376: FunctionDef `group_tokens`
- L380: FunctionDef `is_sequence`
- L386: FunctionDef `integer_or_none`
- L395: FunctionDef `nonnegative_int`
- L400: FunctionDef `positive_int`
- L29: FunctionDef `__init__`
- L37: FunctionDef `evaluate`
- L64: FunctionDef `__init__`
- L76: FunctionDef `measure`
- L117: FunctionDef `compress`
- L191: FunctionDef `__init__`
- L203: FunctionDef `compress`

## agentmon/src/agentmon/context_compression/types.py

Lines: 196; encoding: utf-8

- L8: ClassDef `ContextMeasurement`
- L16: ClassDef `ContextCompressionRequest`
- L26: ClassDef `ContextSummaryRequest`
- L35: ClassDef `ContextSummaryResult`
- L53: ClassDef `ContextCompressionResult`
- L62: ClassDef `ContextCompressionTriggerResult`
- L69: ClassDef `ContextCompressionDecision`
- L183: FunctionDef `optional_difference`
- L189: FunctionDef `optional_ratio`
- L195: FunctionDef `unique`
- L98: FunctionDef `applied`
- L114: FunctionDef `telemetry`

## agentmon/src/agentmon/contracts/__init__.py

Lines: 33; encoding: utf-8


## agentmon/src/agentmon/contracts/adapters.py

Lines: 81; encoding: utf-8

- L13: ClassDef `AdapterCapability`
- L31: ClassDef `AdapterDescriptor`
- L48: ClassDef `AgentFrameworkAdapter`
- L61: ClassDef `InferenceTelemetryProvider`
- L74: ClassDef `ResourceTelemetryProvider`
- L23: FunctionDef `to_dict`
- L38: FunctionDef `to_dict`
- L51: FunctionDef `descriptor`
- L54: FunctionDef `prepare_command`
- L57: FunctionDef `extract_request_ids`
- L64: FunctionDef `descriptor`
- L67: FunctionDef `build_index`
- L70: FunctionDef `select_events`
- L77: FunctionDef `descriptor`
- L80: FunctionDef `snapshot`

## agentmon/src/agentmon/contracts/runtime.py

Lines: 38; encoding: utf-8

- L8: ClassDef `ModelTarget`
- L19: ClassDef `ResourceBudget`
- L33: ClassDef `RetryPolicy`
- L14: FunctionDef `to_dict`
- L28: FunctionDef `to_dict`
- L37: FunctionDef `to_dict`

## agentmon/src/agentmon/contracts/telemetry.py

Lines: 51; encoding: utf-8

- L11: ClassDef `TelemetryCapability`
- L25: ClassDef `RequestIdentity`
- L37: FunctionDef `identity_aliases`
- L17: FunctionDef `to_dict`
- L33: FunctionDef `to_dict`

## agentmon/src/agentmon/control/__init__.py

Lines: 23; encoding: utf-8


## agentmon/src/agentmon/control/model_gate.py

Lines: 161; encoding: utf-8

- L13: ClassDef `ModelGateStatus`
- L34: ClassDef `ModelGateLease`
- L54: ClassDef `ModelRequestGate`
- L25: FunctionDef `to_dict`
- L46: FunctionDef `to_dict`
- L62: FunctionDef `__init__`
- L67: FunctionDef `status`
- L104: FunctionDef `try_acquire`
- L141: FunctionDef `release`
- L152: FunctionDef `_target_for_pressure`

## agentmon/src/agentmon/control/model_provider.py

Lines: 147; encoding: utf-8

- L18: ClassDef `ModelProviderResult`
- L35: ClassDef `GatedOpenAICompatibleProvider`
- L140: FunctionDef `_decode_response_body`
- L26: FunctionDef `to_dict`
- L38: FunctionDef `__init__`
- L43: FunctionDef `chat_completions`
- L120: FunctionDef `_post_json`

## agentmon/src/agentmon/control/resource_actuator.py

Lines: 111; encoding: utf-8

- L11: ClassDef `ToolResourceControlPlan`
- L24: ClassDef `LocalToolResourceActuator`
- L98: FunctionDef `_parse_cpu_list`
- L18: FunctionDef `to_dict`
- L27: FunctionDef `__init__`
- L30: FunctionDef `plan`
- L67: FunctionDef `preexec_fn`
- L72: FunctionDef `apply_controls`

## agentmon/src/agentmon/control/tool_executor.py

Lines: 167; encoding: utf-8

- L16: FunctionDef `run_tool_process`
- L44: ClassDef `ToolExecutionResult`
- L63: ClassDef `PooledToolExecutor`
- L54: FunctionDef `to_dict`
- L64: FunctionDef `__init__`
- L69: FunctionDef `execute`

## agentmon/src/agentmon/control/tool_pool.py

Lines: 218; encoding: utf-8

- L14: ClassDef `ToolPoolStatus`
- L27: ClassDef `ToolPoolAssignment`
- L44: ClassDef `ToolPoolLease`
- L64: ClassDef `ToolPoolManager`
- L200: FunctionDef `command_shape`
- L22: FunctionDef `to_dict`
- L35: FunctionDef `to_dict`
- L56: FunctionDef `to_dict`
- L71: FunctionDef `__init__`
- L81: FunctionDef `status`
- L102: FunctionDef `try_acquire`
- L142: FunctionDef `release`
- L153: FunctionDef `classify`
- L183: FunctionDef `_pool`
- L192: FunctionDef `_enabled`

## agentmon/src/agentmon/doctor.py

Lines: 475; encoding: utf-8

- L28: FunctionDef `run_doctor`
- L66: FunctionDef `probe_python_and_output`
- L88: FunctionDef `probe_host`
- L125: FunctionDef `probe_ascend`
- L154: FunctionDef `probe_vllm`
- L172: FunctionDef `probe_mini_swe`
- L200: FunctionDef `probe_p1`
- L288: FunctionDef `probe_p2`
- L318: FunctionDef `run_command`
- L369: FunctionDef `build_p1_probe_env`
- L384: FunctionDef `expected_agentmon_pythonpath`
- L391: FunctionDef `probe_p1_events_path_writable`
- L415: FunctionDef `probe_child_pythonpath`
- L427: FunctionDef `probe_http_endpoint`
- L463: FunctionDef `compact_command_result`
- L470: FunctionDef `summarize_status`

## agentmon/src/agentmon/events.py

Lines: 93; encoding: utf-8

- L15: FunctionDef `sanitize_for_json`
- L48: FunctionDef `safe_json_dumps`
- L60: ClassDef `Event`
- L72: FunctionDef `to_dict`
- L92: FunctionDef `to_json`

## agentmon/src/agentmon/experiments/__init__.py

Lines: 54; encoding: utf-8

Experiment planning, evidence, and offline replay helpers.

- L18: FunctionDef `__getattr__`

## agentmon/src/agentmon/experiments/evaluation.py

Lines: 808; encoding: utf-8

- L36: FunctionDef `build_mechanism_evaluation`
- L72: FunctionDef `build_mechanism_evaluation_from_timeline`
- L218: FunctionDef `normalize_timeline_row`
- L226: FunctionDef `write_evaluation_outputs`
- L248: FunctionDef `write_conflict_audit_from_jsonl`
- L276: FunctionDef `build_mechanism_comparison`
- L310: FunctionDef `build_observed_workload_summary`
- L367: FunctionDef `build_strategy_mechanisms`
- L389: FunctionDef `build_ablation_rows`
- L436: FunctionDef `build_contribution_rows`
- L502: FunctionDef `build_conflict_audit`
- L573: FunctionDef `build_evaluation_report`
- L631: FunctionDef `select_baseline_strategy`
- L640: FunctionDef `proxy_counts`
- L652: FunctionDef `summarize_ablation_rows`
- L662: FunctionDef `component_for_risk_flag`
- L669: FunctionDef `fuse_pressure`
- L676: FunctionDef `summarize_first_fields`
- L687: FunctionDef `numeric_summary`
- L707: FunctionDef `build_tool_quality_summary`
- L740: FunctionDef `merge_mechanism_profile`
- L756: FunctionDef `percentile`
- L767: FunctionDef `any_has_value`
- L771: FunctionDef `truthy`
- L775: FunctionDef `first_value`
- L782: FunctionDef `count_values`
- L789: FunctionDef `as_dict`
- L793: FunctionDef `write_json`
- L798: FunctionDef `write_csv`
- L97: FunctionDef `flush_snapshot`

## agentmon/src/agentmon/experiments/evidence.py

Lines: 978; encoding: utf-8

- L27: ClassDef `RunEventData`
- L41: ClassDef `BatchExperimentData`
- L69: ClassDef `BatchExperimentIndex`
- L84: FunctionDef `load_batch_experiment`
- L143: FunctionDef `iter_batch_results`
- L148: FunctionDef `iter_step_rows`
- L153: FunctionDef `build_evidence_outputs`
- L172: FunctionDef `write_evidence_outputs`
- L192: FunctionDef `build_experiment_manifest`
- L237: FunctionDef `build_evidence_graph`
- L339: FunctionDef `build_critical_path_rows`
- L384: FunctionDef `build_bottleneck_attribution`
- L451: FunctionDef `build_workload_normalized_metrics`
- L506: FunctionDef `build_data_quality`
- L551: FunctionDef `build_source_status`
- L599: FunctionDef `locate_step_csvs`
- L624: FunctionDef `load_run_events`
- L665: FunctionDef `iter_run_events`
- L683: FunctionDef `resolve_run_dir`
- L688: FunctionDef `resolve_run_directory`
- L717: FunctionDef `build_run_search_index`
- L734: FunctionDef `source_file_inventory`
- L762: FunctionDef `overall_quality_status`
- L772: FunctionDef `_append_model_phase_edges`
- L795: FunctionDef `_add_component`
- L822: FunctionDef `_file_status`
- L830: FunctionDef `_read_optional_json`
- L838: FunctionDef `_read_delimited`
- L847: FunctionDef `_scan_jsonl`
- L861: FunctionDef `count_values`
- L869: FunctionDef `_write_json`
- L874: FunctionDef `_write_csv`
- L887: FunctionDef `file_sha256`
- L897: FunctionDef `step_row_sort_key`
- L905: FunctionDef `run_wall_from_result`
- L917: FunctionDef `duration_from_row`
- L928: FunctionDef `sum_numeric`
- L934: FunctionDef `sum_first_numeric`
- L940: FunctionDef `first_value`
- L947: FunctionDef `first_int`
- L955: FunctionDef `first_float`
- L963: FunctionDef `int_or_none`
- L972: FunctionDef `float_or_none`
- L55: FunctionDef `iter_results`
- L58: FunctionDef `iter_step_rows`

## agentmon/src/agentmon/experiments/experiment_cli.py

Lines: 228; encoding: utf-8

- L29: FunctionDef `run_prepare`
- L47: FunctionDef `run_smoke`
- L68: FunctionDef `run_pilot`
- L88: FunctionDef `run_formal`
- L108: FunctionDef `run_analyze`
- L128: FunctionDef `ready_for_pilot`
- L137: FunctionDef `as_status`
- L142: FunctionDef `command_failures`
- L150: FunctionDef `phase_result`
- L169: FunctionDef `write_phase_outputs`
- L182: FunctionDef `build_parser`
- L211: FunctionDef `add_contract_stage_args`
- L217: FunctionDef `main`

## agentmon/src/agentmon/experiments/golden.py

Lines: 116; encoding: utf-8

- L44: FunctionDef `semantic_hash`
- L61: FunctionDef `normalize_semantic_value`
- L78: FunctionDef `compare_semantic_artifacts`
- L113: FunctionDef `public_artifact_paths`

## agentmon/src/agentmon/experiments/graph.py

Lines: 9; encoding: utf-8

- L8: FunctionDef `build_optimizer_evidence_graph`

## agentmon/src/agentmon/experiments/matrix.py

Lines: 180; encoding: utf-8

- L20: FunctionDef `run_repeated_matrix`
- L108: FunctionDef `statistics_bundle`
- L127: FunctionDef `write_results_csv`
- L137: FunctionDef `render_arg`
- L144: FunctionDef `read_json`
- L154: FunctionDef `as_dict`
- L158: FunctionDef `number_or_none`
- L168: FunctionDef `percentile`

## agentmon/src/agentmon/experiments/mechanisms/__init__.py

Lines: 9; encoding: utf-8


## agentmon/src/agentmon/experiments/mechanisms/registry.py

Lines: 153; encoding: utf-8

- L12: FunctionDef `mechanism_registry`
- L22: FunctionDef `mechanism_definition`
- L27: FunctionDef `mechanism_catalog`
- L39: FunctionDef `normalize_definition`
- L58: FunctionDef `load_definition_file`
- L69: FunctionDef `load_yaml_definition`
- L80: FunctionDef `parse_simple_yaml`
- L111: FunctionDef `split_yaml_key_value`
- L118: FunctionDef `parse_scalar`
- L139: FunctionDef `strip_quotes`
- L146: FunctionDef `as_list`

## agentmon/src/agentmon/experiments/orchestration.py

Lines: 924; encoding: utf-8

- L91: FunctionDef `orchestrate_experiment`
- L170: FunctionDef `validate_experiment_contract`
- L215: FunctionDef `validate_execution_paths`
- L254: FunctionDef `evaluate_activation_probes`
- L277: FunctionDef `evaluate_activation_probe`
- L335: FunctionDef `evaluate_pilot_gate`
- L369: FunctionDef `select_analysis_route`
- L441: FunctionDef `determine_ready_for_formal`
- L463: FunctionDef `run_configured_commands`
- L531: FunctionDef `execute_command_stage`
- L569: FunctionDef `activation_probe_configs`
- L600: FunctionDef `activation_profile`
- L628: FunctionDef `event_matches_probe`
- L643: FunctionDef `negative_activation_reason`
- L654: FunctionDef `select_baseline_variant`
- L666: FunctionDef `normalize_variants`
- L682: FunctionDef `expected_mechanisms`
- L691: FunctionDef `merge_execution_path_requirements`
- L704: FunctionDef `flatten_mapping`
- L715: FunctionDef `path_allowed`
- L724: FunctionDef `expand_evidence_paths`
- L745: FunctionDef `iter_jsonl_events`
- L761: FunctionDef `read_batch_results`
- L769: FunctionDef `successful_result_row`
- L774: FunctionDef `is_known_batch_root`
- L782: FunctionDef `command_from_spec`
- L792: FunctionDef `add_optional_arg`
- L798: FunctionDef `get_path`
- L808: FunctionDef `as_dict`
- L812: FunctionDef `as_list`
- L816: FunctionDef `as_list_or_tuple`
- L824: FunctionDef `as_bool`
- L832: FunctionDef `int_or_default`
- L839: FunctionDef `float_or_none`
- L848: FunctionDef `write_json`
- L853: FunctionDef `write_ready_env`
- L868: FunctionDef `load_contract`
- L875: FunctionDef `build_parser`
- L905: FunctionDef `main`

## agentmon/src/agentmon/experiments/prediction.py

Lines: 174; encoding: utf-8

- L22: FunctionDef `build_prediction_variable_validation`
- L47: FunctionDef `build_prediction_validation_rows`
- L105: FunctionDef `future_model_observations`
- L129: FunctionDef `next_outcome_value`
- L143: FunctionDef `pearson`
- L158: FunctionDef `association_direction`
- L168: FunctionDef `number_or_none`

## agentmon/src/agentmon/experiments/prefix_cache.py

Lines: 170; encoding: utf-8

- L13: FunctionDef `plan_prefix_cache_experiment`
- L78: FunctionDef `variant_config_snapshot`
- L93: FunctionDef `detect_prefix_cache_hint`
- L109: FunctionDef `build_checks`
- L143: FunctionDef `check`
- L147: FunctionDef `format_prefix_cache_plan_table`
- L160: FunctionDef `plan_to_json`
- L164: FunctionDef `nested`

## agentmon/src/agentmon/experiments/preflight.py

Lines: 122; encoding: utf-8

- L20: FunctionDef `build_optimizer_experiment_preflight`
- L118: FunctionDef `write_optimizer_experiment_preflight`

## agentmon/src/agentmon/experiments/replay.py

Lines: 699; encoding: utf-8

- L25: FunctionDef `run_optimizer_advice_replay`
- L75: FunctionDef `run_optimizer_advice_replay_streaming`
- L250: ClassDef `OnlineStrategySummary`
- L287: ClassDef `OnlineRiskAudit`
- L376: ClassDef `FutureModelObservationIndex`
- L421: ClassDef `OnlinePredictionValidation`
- L470: ClassDef `OnlineCovariance`
- L495: FunctionDef `merge_prediction_profile`
- L501: FunctionDef `snapshot_part_metadata`
- L507: FunctionDef `file_sha256`
- L515: FunctionDef `atomic_json`
- L522: FunctionDef `write_replay_outputs`
- L562: FunctionDef `evaluate_replay_strategy`
- L574: FunctionDef `build_replay_strategies`
- L587: FunctionDef `decision_timeline_row`
- L629: FunctionDef `summarize_strategy_decisions`
- L669: FunctionDef `validate_strategy_names`
- L676: FunctionDef `count_values`
- L684: FunctionDef `write_json`
- L689: FunctionDef `write_csv`
- L251: FunctionDef `__init__`
- L254: FunctionDef `observe`
- L265: FunctionDef `finalize`
- L288: FunctionDef `__init__`
- L293: FunctionDef `observe`
- L319: FunctionDef `finalize`
- L348: FunctionDef `summary`
- L370: FunctionDef `close`
- L379: FunctionDef `__init__`
- L406: FunctionDef `next_at_or_after`
- L417: FunctionDef `close`
- L422: FunctionDef `__init__`
- L438: FunctionDef `observe`
- L457: FunctionDef `finalize`
- L466: FunctionDef `close`
- L471: FunctionDef `__init__`
- L479: FunctionDef `add`
- L489: FunctionDef `pearson`

## agentmon/src/agentmon/experiments/reports.py

Lines: 108; encoding: utf-8

- L9: FunctionDef `build_optimizer_replay_report`
- L97: FunctionDef `write_optimizer_replay_report`

## agentmon/src/agentmon/experiments/request.py

Lines: 399; encoding: utf-8

- L20: FunctionDef `load_experiment_request`
- L37: FunctionDef `load_yaml_request`
- L48: FunctionDef `generate_experiment_contract`
- L150: FunctionDef `write_generated_contract`
- L157: FunctionDef `requested_mechanisms`
- L174: FunctionDef `mechanism_name`
- L180: FunctionDef `infer_execution_path`
- L198: FunctionDef `merge_required_config`
- L206: FunctionDef `apply_required_config`
- L216: FunctionDef `apply_common_mode`
- L235: FunctionDef `build_pilot_section`
- L252: FunctionDef `build_formal_section`
- L264: FunctionDef `build_analysis_section`
- L279: FunctionDef `set_dotted_path`
- L291: FunctionDef `deep_merge`
- L301: FunctionDef `prune_empty_contract`
- L313: FunctionDef `keep_non_empty`
- L317: FunctionDef `unique_strings`
- L327: FunctionDef `as_dict`
- L331: FunctionDef `as_list_or_scalar`
- L341: FunctionDef `as_bool`
- L349: FunctionDef `int_or_none`
- L358: FunctionDef `slugify`
- L364: FunctionDef `path_allowed`
- L373: FunctionDef `empty_value`
- L377: FunctionDef `build_parser`
- L385: FunctionDef `main`

## agentmon/src/agentmon/experiments/snapshots.py

Lines: 1016; encoding: utf-8

- L38: ClassDef `OptimizerReplaySnapshot`
- L60: ClassDef `RunInterval`
- L70: ClassDef `ModelServiceSampleIndex`
- L76: ClassDef `ModelStepInterval`
- L86: ClassDef `ActiveMaximum`
- L106: ClassDef `DiskOrderStatistic`
- L251: FunctionDef `build_snapshot_timeline`
- L260: FunctionDef `iter_snapshot_timeline`
- L399: FunctionDef `build_model_step_intervals`
- L429: FunctionDef `group_intervals_by_time`
- L441: FunctionDef `group_model_intervals_by_time`
- L453: FunctionDef `model_service_snapshot_from_state`
- L490: FunctionDef `model_service_evidence_source`
- L498: FunctionDef `build_run_intervals`
- L551: FunctionDef `step_item_timepoints`
- L555: FunctionDef `iter_step_item_timepoints`
- L565: FunctionDef `iter_static_timepoints`
- L624: FunctionDef `iter_merged_unique_timestamp_parts`
- L646: FunctionDef `active_runs_at`
- L656: FunctionDef `scheduler_capacity`
- L663: FunctionDef `model_service_snapshot_at`
- L742: FunctionDef `build_model_service_event_samples`
- L749: FunctionDef `iter_model_service_event_samples`
- L819: FunctionDef `write_sorted_sample_part`
- L829: FunctionDef `merge_sorted_sample_parts`
- L857: FunctionDef `deduplicated_timestamp_samples`
- L869: FunctionDef `canonical_sample_sort_key`
- L873: FunctionDef `read_next_sample`
- L881: FunctionDef `model_service_sample_identity`
- L895: FunctionDef `annotate_service_sample_conflicts`
- L912: FunctionDef `build_model_service_sample_index`
- L926: FunctionDef `model_service_sample_sort_key`
- L936: FunctionDef `latest_model_service_sample_from_index`
- L946: FunctionDef `latest_model_service_sample`
- L959: FunctionDef `merge_model_service_sample`
- L982: FunctionDef `row_active_at`
- L988: FunctionDef `max_optional_int`
- L999: FunctionDef `percentile`
- L1012: FunctionDef `source_counts`
- L46: FunctionDef `to_dict`
- L87: FunctionDef `__init__`
- L91: FunctionDef `add`
- L97: FunctionDef `remove`
- L100: FunctionDef `maximum`
- L115: FunctionDef `__init__`
- L128: FunctionDef `add`
- L140: FunctionDef `percentile`
- L155: FunctionDef `close`
- L158: FunctionDef `_insert`
- L185: FunctionDef `_rotate_right`
- L196: FunctionDef `_rotate_left`
- L207: FunctionDef `_value_at_rank`
- L221: FunctionDef `_node`
- L233: FunctionDef `_size`
- L238: FunctionDef `_set_child`
- L241: FunctionDef `_update_size`
- L247: FunctionDef `_priority`
- L587: FunctionDef `flush`
- L605: FunctionDef `add`

## agentmon/src/agentmon/optimizer/__init__.py

Lines: 28; encoding: utf-8


## agentmon/src/agentmon/optimizer/harness/__init__.py

Lines: 20; encoding: utf-8

Offline optimizer experiment harness helpers.

- L11: FunctionDef `__getattr__`

## agentmon/src/agentmon/optimizer/harness/audit.py

Lines: 106; encoding: utf-8

- L9: FunctionDef `audit_decision_timeline`
- L31: FunctionDef `audit_strategy`
- L77: FunctionDef `starvation_status`
- L87: FunctionDef `over_limit_status`
- L97: FunctionDef `max_consecutive`

## agentmon/src/agentmon/optimizer/harness/cli.py

Lines: 111; encoding: utf-8

- L16: FunctionDef `build_parser`
- L69: FunctionDef `main`
- L105: FunctionDef `read_optional_json_object`

## agentmon/src/agentmon/optimizer/harness/runner.py

Lines: 506; encoding: utf-8

- L52: FunctionDef `run_optimizer_experiment_harness`
- L319: FunctionDef `write_json`
- L326: FunctionDef `canonical_sha256`
- L331: FunctionDef `build_replay_identity`
- L349: FunctionDef `optimizer_pipeline_status`
- L367: FunctionDef `collect_public_artifacts`
- L385: FunctionDef `build_artifact_provenance`
- L442: FunctionDef `load_completed_replay`
- L81: FunctionDef `source_stage`
- L87: FunctionDef `inventory_stage`
- L109: FunctionDef `preflight_stage`
- L128: FunctionDef `evidence_stage`
- L154: FunctionDef `replay_stage`
- L203: FunctionDef `evaluation_stage`
- L216: FunctionDef `report_stage`
- L241: FunctionDef `validation_stage`

## agentmon/src/agentmon/optimizer/harness/schemas.py

Lines: 207; encoding: utf-8

- L44: FunctionDef `output_paths`
- L192: FunctionDef `merge_schema_profile`
- L199: FunctionDef `merge_profile_dict`

## agentmon/src/agentmon/optimizer/registry.py

Lines: 58; encoding: utf-8

- L13: ClassDef `OptimizerStrategy`
- L39: FunctionDef `build_optimizer_strategy`
- L43: FunctionDef `register_optimizer_strategy`
- L53: FunctionDef `unregister_optimizer_strategy`
- L57: FunctionDef `optimizer_strategy_catalog`
- L16: FunctionDef `evaluate`

## agentmon/src/agentmon/optimizer/service.py

Lines: 113; encoding: utf-8

- L17: ClassDef `OptimizerService`
- L37: FunctionDef `queue_snapshot_from_scheduler`
- L64: FunctionDef `evaluate_strategy`
- L75: FunctionDef `model_service_snapshot_from_file`
- L82: FunctionDef `model_service_snapshot_from_payload`
- L96: FunctionDef `_optional_int`
- L102: FunctionDef `_optional_float`
- L108: FunctionDef `runtime_task_classification`
- L18: FunctionDef `__init__`
- L24: FunctionDef `queue_snapshot`
- L28: FunctionDef `decision`

## agentmon/src/agentmon/optimizer/strategies.py

Lines: 199; encoding: utf-8

- L8: ClassDef `StaticThresholdOptimizer`
- L114: ClassDef `WorkloadAwareOptimizer`
- L11: FunctionDef `__init__`
- L14: FunctionDef `evaluate`
- L98: FunctionDef `_model_pressure`
- L117: FunctionDef `evaluate`

## agentmon/src/agentmon/optimizer/types.py

Lines: 99; encoding: utf-8

- L11: ClassDef `NodeSnapshot`
- L22: ClassDef `ModelServiceSnapshot`
- L36: ClassDef `RunRuntimeProfile`
- L49: ClassDef `QueueSnapshot`
- L69: ClassDef `OptimizerDecision`
- L17: FunctionDef `to_dict`
- L31: FunctionDef `to_dict`
- L44: FunctionDef `to_dict`
- L57: FunctionDef `to_dict`
- L83: FunctionDef `to_dict`

## agentmon/src/agentmon/paths.py

Lines: 62; encoding: utf-8

- L11: FunctionDef `output_root`
- L15: FunctionDef `find_run_dir`
- L22: FunctionDef `make_private_dir`
- L32: FunctionDef `write_private_text`
- L51: FunctionDef `open_private_text`
- L58: FunctionDef `chmod_private_file`

## agentmon/src/agentmon/pipeline/__init__.py

Lines: 32; encoding: utf-8

Shared deterministic pipeline contracts for offline AgentMon workflows.


## agentmon/src/agentmon/pipeline/executor.py

Lines: 307; encoding: utf-8

- L18: ClassDef `PipelineContext`
- L50: ClassDef `PipelineExecutor`
- L244: FunctionDef `stage_nodes`
- L260: FunctionDef `stage_edges`
- L268: FunctionDef `topological_order`
- L284: FunctionDef `artifact_matches`
- L291: FunctionDef `file_sha256`
- L299: FunctionDef `atomic_json`
- L306: FunctionDef `now_iso`
- L25: FunctionDef `stage_root`
- L30: FunctionDef `heartbeat`
- L53: FunctionDef `__init__`
- L82: FunctionDef `run`
- L149: FunctionDef `_blocking_result`
- L175: FunctionDef `_record`
- L189: FunctionDef `_load_cached`
- L216: FunctionDef `_write_manifest`
- L237: FunctionDef `_validate_specs`

## agentmon/src/agentmon/pipeline/identity.py

Lines: 140; encoding: utf-8

- L26: FunctionDef `build_source_manifest`
- L50: FunctionDef `iter_managed_files`
- L71: FunctionDef `is_excluded`
- L84: FunctionDef `load_source_manifest`
- L93: FunctionDef `verify_source_tree`
- L117: FunctionDef `detect_source_identity`
- L135: FunctionDef `file_sha256`

## agentmon/src/agentmon/pipeline/model.py

Lines: 84; encoding: utf-8

- L22: ClassDef `ArtifactRef`
- L42: ClassDef `StageSpec`
- L54: ClassDef `PipelineStageResult`
- L29: FunctionDef `from_path`
- L32: FunctionDef `to_dict`
- L67: FunctionDef `__post_init__`
- L71: FunctionDef `to_dict`

## agentmon/src/agentmon/profile_data.py

Lines: 24; encoding: utf-8

- L8: FunctionDef `load_analysis_profile_document`
- L17: FunctionDef `profile_quality_gate`

## agentmon/src/agentmon/replay/__init__.py

Lines: 5; encoding: utf-8


## agentmon/src/agentmon/replay/simulator.py

Lines: 162; encoding: utf-8

- L13: ClassDef `ReplaySimulator`
- L41: FunctionDef `replay_runtime_events`
- L86: FunctionDef `_queue_state_for_event`
- L104: FunctionDef `_read_jsonl`
- L115: FunctionDef `queue_snapshot_from_payload`
- L126: FunctionDef `_queue_snapshot_from_queue_like_payload`
- L151: FunctionDef `_state_counts`
- L16: FunctionDef `__init__`
- L20: FunctionDef `replay_optimizer_snapshot`
- L30: FunctionDef `replay_optimizer_snapshot_file`
- L36: FunctionDef `replay_runtime_events_file`

## agentmon/src/agentmon/reporting/__init__.py

Lines: 1; encoding: utf-8


## agentmon/src/agentmon/reporting/_legacy_report.py

Lines: 3109; encoding: utf-8

- L29: FunctionDef `build_report_quality_bundle`
- L184: FunctionDef `report_quality_check`
- L194: FunctionDef `summarize_semantic_integrity`
- L256: FunctionDef `read_json`
- L262: FunctionDef `sqlite_event_count`
- L276: FunctionDef `sqlite_event_ids`
- L289: FunctionDef `summarize_event_log`
- L330: FunctionDef `summarize_event_snapshot`
- L345: FunctionDef `summarize_analysis_state`
- L399: FunctionDef `duplicate_event_id_count`
- L423: FunctionDef `is_p1_event`
- L430: FunctionDef `is_p2_event`
- L434: FunctionDef `is_jsonl_only_trajectory_event`
- L446: FunctionDef `event_is_in_sqlite`
- L453: FunctionDef `ns_to_s`
- L459: FunctionDef `summarize_trajectory_events`
- L505: FunctionDef `summarize_p1_model_usage`
- L537: FunctionDef `token_quality_from_sources`
- L549: FunctionDef `token_count_difference`
- L559: FunctionDef `summarize_p1_spans`
- L644: FunctionDef `summarize_agent_workload`
- L746: FunctionDef `summarize_context_compression`
- L907: FunctionDef `count_spans_by_type`
- L916: FunctionDef `span_lifecycle`
- L933: FunctionDef `summarize_tool_attribution`
- L973: FunctionDef `collect_tool_records`
- L1028: FunctionDef `attribute_tool`
- L1099: FunctionDef `direct_tool_resources`
- L1126: FunctionDef `max_optional`
- L1131: FunctionDef `first_present`
- L1138: FunctionDef `text_or_none`
- L1144: FunctionDef `first_text`
- L1152: FunctionDef `summarize_host_events`
- L1167: FunctionDef `summarize_proc_events`
- L1197: FunctionDef `summarize_vllm_events`
- L1219: FunctionDef `summarize_vllm_p2_events`
- L1341: FunctionDef `capability_required_failure`
- L1352: FunctionDef `summarize_p2_moe_routing`
- L1433: FunctionDef `summarize_p2_scheduler`
- L1504: FunctionDef `request_key`
- L1542: FunctionDef `request_row_for_payload`
- L1572: FunctionDef `unique_request_rows`
- L1583: FunctionDef `merge_request_rows`
- L1601: FunctionDef `finalize_request_lifecycle`
- L1722: FunctionDef `add_request_identity_fields`
- L1739: FunctionDef `canonical_request_identity`
- L1747: FunctionDef `request_identity_aliases`
- L1762: FunctionDef `merge_scheduler_iteration_rows`
- L1779: FunctionDef `add_moe_routing_summary`
- L1833: FunctionDef `add_kv_residency`
- L1869: FunctionDef `merge_request_payload`
- L2085: FunctionDef `count_request_correlations`
- L2095: FunctionDef `summarize_p2_request_latencies`
- L2145: FunctionDef `summarize_client_server_stages`
- L2332: FunctionDef `build_client_server_request_identity_audit`
- L2375: FunctionDef `unique_text_values`
- L2387: FunctionDef `sorted_unique_text`
- L2391: FunctionDef `cross_event_timestamp_quality`
- L2410: FunctionDef `verified_ns_delta_s`
- L2416: FunctionDef `summarize_timestamp_pair_quality`
- L2462: FunctionDef `timestamp_pair_quality`
- L2486: FunctionDef `p2_decode_end_ns`
- L2494: FunctionDef `request_tpot_s`
- L2510: FunctionDef `summarize_p2_request_tokens`
- L2541: FunctionDef `summarize_p2_kv_cache`
- L2633: FunctionDef `resolve_p2_prefix_cache_status`
- L2709: FunctionDef `normalize_prefix_cache_status`
- L2724: FunctionDef `count_p2_kv_states`
- L2767: FunctionDef `summarize_p2_npu_attribution`
- L2787: FunctionDef `p2_latency_stats`
- L2799: FunctionDef `p2_total_stats`
- L2810: FunctionDef `percentile`
- L2825: FunctionDef `ns_delta_s`
- L2834: FunctionDef `ns_to_seconds`
- L2841: FunctionDef `sum_numeric_payloads`
- L2845: FunctionDef `sum_optional`
- L2850: FunctionDef `max_or_none`
- L2855: FunctionDef `min_or_none`
- L2860: FunctionDef `numeric_or_none`
- L2868: FunctionDef `ratio_or_none`
- L2874: FunctionDef `sum_values`
- L2880: FunctionDef `count_values_from_payloads`
- L2889: FunctionDef `summarize_kv_cache`
- L2912: FunctionDef `flatten_metric_samples`
- L2929: FunctionDef `summarize_ascend_events`
- L2969: FunctionDef `integer_or_none`
- L2979: FunctionDef `as_dict`
- L2983: FunctionDef `summarize_errors`
- L3000: FunctionDef `format_text_report`
- L3047: FunctionDef `write_csv_report`
- L3065: FunctionDef `export_run`
- L3080: FunctionDef `compare_reports`
- L3105: FunctionDef `delta`

## agentmon/src/agentmon/reporting/ascend.py

Lines: 272; encoding: utf-8

- L13: FunctionDef `summarize_ascend_devices`
- L61: FunctionDef `resolve_ascend_target_devices`
- L82: FunctionDef `target_devices_from_p2_metadata`
- L110: FunctionDef `target_devices_from_proc_environ`
- L138: FunctionDef `read_proc_environ`
- L164: FunctionDef `proc_starttime_ticks`
- L174: FunctionDef `parse_device_list`
- L196: FunctionDef `normalize_device_ids`
- L207: FunctionDef `target_resolution`
- L222: FunctionDef `peak_device_metric`
- L233: FunctionDef `visible_devices_from_env`
- L242: FunctionDef `device_sort_key`
- L248: FunctionDef `numeric_or_none`
- L256: FunctionDef `integer_or_none`
- L271: FunctionDef `as_dict`

## agentmon/src/agentmon/reporting/builder.py

Lines: 12; encoding: utf-8

- L11: FunctionDef `build_run_report`

## agentmon/src/agentmon/reporting/canonical.py

Lines: 886; encoding: utf-8

- L92: FunctionDef `build_canonical_tables`
- L142: FunctionDef `build_span_rows`
- L190: FunctionDef `build_request_rows`
- L229: FunctionDef `canonical_request_row`
- L474: FunctionDef `canonical_tool_row`
- L528: FunctionDef `canonical_event_row`
- L554: FunctionDef `canonical_system_sample_row`
- L572: FunctionDef `write_canonical_dataset`
- L591: FunctionDef `write_run_canonical_tables`
- L607: FunctionDef `write_canonical_tables`
- L663: FunctionDef `write_jsonl`
- L669: FunctionDef `write_csv`
- L680: FunctionDef `write_parquet_tables`
- L697: FunctionDef `request_identity_values`
- L706: FunctionDef `request_scopes`
- L718: FunctionDef `scoped_identity`
- L722: FunctionDef `unique_text`
- L726: FunctionDef `unique_dicts`
- L736: FunctionDef `request_metric_quality`
- L750: FunctionDef `request_metric_source`
- L754: FunctionDef `request_clock_domains`
- L766: FunctionDef `verified_p2_delta`
- L778: FunctionDef `verified_cross_delta`
- L792: FunctionDef `pair_quality`
- L810: FunctionDef `token_period_seconds`
- L828: FunctionDef `is_engine_first_token`
- L839: FunctionDef `csv_scalar`
- L845: FunctionDef `json_text`
- L849: FunctionDef `as_dict`
- L853: FunctionDef `text_or_none`
- L857: FunctionDef `first_text`
- L861: FunctionDef `first_present`
- L865: FunctionDef `integer_or_none`
- L878: FunctionDef `ns_to_s`
- L883: FunctionDef `delta_ns_s`

## agentmon/src/agentmon/reporting/event_log.py

Lines: 64; encoding: utf-8

- L9: FunctionDef `load_events`
- L13: FunctionDef `read_event_log`
- L54: FunctionDef `open_event_log`

## agentmon/src/agentmon/reporting/p1.py

Lines: 19; encoding: utf-8


## agentmon/src/agentmon/reporting/p2.py

Lines: 25; encoding: utf-8


## agentmon/src/agentmon/reporting/quality.py

Lines: 13; encoding: utf-8


## agentmon/src/agentmon/reporting/report.py

Lines: 73; encoding: utf-8

- L21: FunctionDef `_apply_legacy_overrides`
- L33: FunctionDef `_restore_legacy_overrides`
- L38: FunctionDef `build_report`
- L47: FunctionDef `write_reports`
- L60: FunctionDef `export_run`
- L68: FunctionDef `compare_reports`

## agentmon/src/agentmon/reporting/report_builder.py

Lines: 110; encoding: utf-8

- L9: FunctionDef `build_report`
- L64: FunctionDef `write_reports`

## agentmon/src/agentmon/reporting/summary.py

Lines: 1236; encoding: utf-8

- L288: FunctionDef `build_run_summary`
- L600: FunctionDef `build_aggregate`
- L810: FunctionDef `strict_failures`
- L831: FunctionDef `data_integrity_pass`
- L843: FunctionDef `passes_filters`
- L865: FunctionDef `format_summary_table`
- L870: FunctionDef `format_aggregate_table`
- L876: FunctionDef `summaries_to_csv`
- L880: FunctionDef `aggregate_to_csv`
- L886: FunctionDef `rows_to_csv`
- L899: FunctionDef `render_table`
- L912: FunctionDef `span_durations`
- L924: FunctionDef `latency_stats`
- L935: FunctionDef `percentile_stats`
- L943: FunctionDef `percentile`
- L959: FunctionDef `total_mean`
- L969: FunctionDef `p2_metric`
- L975: FunctionDef `p2_latency_stats`
- L992: FunctionDef `p2_token_total`
- L998: FunctionDef `prefix_cache_hit_rate_aggregate`
- L1016: FunctionDef `count_spans`
- L1027: FunctionDef `count_nonzero_commands`
- L1041: FunctionDef `count_tool_timeouts`
- L1051: FunctionDef `count_instrumentation_errors`
- L1068: FunctionDef `count_submission_control_flow`
- L1080: FunctionDef `summarize_npu_devices`
- L1095: FunctionDef `capability_events_from_report`
- L1104: FunctionDef `summarize_data_quality_status`
- L1115: FunctionDef `count_failures_by_exit_status`
- L1125: FunctionDef `count_data_quality_failures`
- L1134: FunctionDef `count_runs_with_positive`
- L1138: FunctionDef `count_values`
- L1147: FunctionDef `sum_count_maps`
- L1158: FunctionDef `find_counter_delta`
- L1173: FunctionDef `values`
- L1182: FunctionDef `flatten`
- L1189: FunctionDef `get_nested`
- L1198: FunctionDef `as_dict`
- L1202: FunctionDef `numeric_or_none`
- L1210: FunctionDef `none_if_unavailable`
- L1214: FunctionDef `positive`
- L1219: FunctionDef `display_value`
- L1231: FunctionDef `csv_value`

## agentmon/src/agentmon/reporting/timeline.py

Lines: 668; encoding: utf-8

- L15: FunctionDef `generate_batch_timeline`
- L112: FunctionDef `build_service_resource_windows`
- L177: FunctionDef `resolve_step_csv`
- L195: FunctionDef `build_run_intervals`
- L240: FunctionDef `build_item_intervals`
- L280: FunctionDef `project_run_timeline`
- L293: FunctionDef `build_bucket_timeline`
- L339: FunctionDef `build_transition_gaps`
- L363: FunctionDef `build_phase_summary`
- L384: FunctionDef `build_tail_summary`
- L401: FunctionDef `build_integral_checks`
- L430: FunctionDef `build_timeline_validation`
- L488: FunctionDef `check`
- L492: FunctionDef `format_timeline_report`
- L526: FunctionDef `format_timeline_table`
- L539: FunctionDef `overlap_seconds`
- L547: FunctionDef `active_count`
- L551: FunctionDef `percentile_linear`
- L566: FunctionDef `nonempty_values`
- L570: FunctionDef `read_delimited`
- L577: FunctionDef `read_json`
- L587: FunctionDef `write_csv`
- L603: FunctionDef `write_json`
- L607: FunctionDef `private`
- L614: FunctionDef `bucket_label`
- L618: FunctionDef `ns_offset`
- L622: FunctionDef `integer_or_none`
- L631: FunctionDef `number_or_none`
- L641: FunctionDef `text_or_none`
- L645: FunctionDef `first_nonempty`
- L652: FunctionDef `parse_bool`
- L656: FunctionDef `parse_optional_bool`
- L662: FunctionDef `as_dict`
- L666: FunctionDef `format_number`

## agentmon/src/agentmon/reporting/writers.py

Lines: 15; encoding: utf-8


## agentmon/src/agentmon/run_controller.py

Lines: 801; encoding: utf-8

- L36: FunctionDef `make_run_id`
- L40: ClassDef `RunController`
- L633: FunctionDef `status_from_exit`
- L639: FunctionDef `expand_run_id_placeholders`
- L646: FunctionDef `detect_trajectory_output_paths`
- L663: FunctionDef `wait_for_stable_file`
- L729: FunctionDef `build_child_env`
- L754: FunctionDef `infer_data_quality`
- L782: FunctionDef `toml_from_dict`
- L792: FunctionDef `toml_value`
- L41: FunctionDef `__init__`
- L74: FunctionDef `run`
- L209: FunctionDef `_finalize_run`
- L341: FunctionDef `_stop_collectors`
- L383: FunctionDef `_prepare_dirs`
- L395: FunctionDef `_initial_manifest`
- L433: FunctionDef `_write_manifest`
- L439: FunctionDef `_write_resolved_config`
- L445: FunctionDef `_snapshot_vllm`
- L474: FunctionDef `_import_trajectory`
- L552: FunctionDef `_trajectory_candidate_paths`
- L568: FunctionDef `_resolve_p2_events_path`
- L586: FunctionDef `_import_p2_window`
- L120: FunctionDef `forward_signal`

## agentmon/src/agentmon/runtime/__init__.py

Lines: 52; encoding: utf-8


## agentmon/src/agentmon/runtime/audit.py

Lines: 38; encoding: utf-8

- L13: ClassDef `RuntimeAuditLog`
- L14: FunctionDef `__init__`
- L17: FunctionDef `emit`

## agentmon/src/agentmon/runtime/backend.py

Lines: 31; encoding: utf-8

- L15: ClassDef `AgentRuntimeBackend`
- L18: FunctionDef `capabilities`
- L21: FunctionDef `prepare`
- L24: FunctionDef `start`
- L27: FunctionDef `poll`
- L30: FunctionDef `cancel`

## agentmon/src/agentmon/runtime/backends/__init__.py

Lines: 5; encoding: utf-8


## agentmon/src/agentmon/runtime/backends/process.py

Lines: 165; encoding: utf-8

- L21: ClassDef `ProcessBackend`
- L150: FunctionDef `read_jsonl_events`
- L24: FunctionDef `__init__`
- L27: FunctionDef `capabilities`
- L41: FunctionDef `prepare`
- L52: FunctionDef `start`
- L73: FunctionDef `poll`
- L99: FunctionDef `cancel`
- L102: FunctionDef `_annotate_manifest`
- L132: FunctionDef `_build_task_state`

## agentmon/src/agentmon/runtime/core.py

Lines: 347; encoding: utf-8

- L31: ClassDef `RuntimeService`
- L317: FunctionDef `manifest_to_runtime_state`
- L332: FunctionDef `read_jsonl_events`
- L39: FunctionDef `__init__`
- L53: FunctionDef `with_default_backends`
- L58: FunctionDef `backend_capabilities`
- L61: FunctionDef `submit_blocking`
- L71: FunctionDef `enqueue`
- L74: FunctionDef `drain_queue_once`
- L149: FunctionDef `recover_queue_orphans`
- L158: FunctionDef `_run_queued_record`
- L183: FunctionDef `_heartbeat_until_stopped`
- L193: FunctionDef `_run_admitted`
- L205: FunctionDef `status`
- L233: FunctionDef `task_state`
- L262: FunctionDef `queue_snapshot`
- L303: FunctionDef `_backend`
- L310: FunctionDef `_resolve_run_dir`

## agentmon/src/agentmon/runtime/p2_import.py

Lines: 495; encoding: utf-8

- L22: ClassDef `P2RunWindow`
- L35: FunctionDef `open_p2_run_window`
- L48: FunctionDef `import_p2_run_window`
- L189: FunctionDef `read_p2_event_slice`
- L238: FunctionDef `extract_response_ids_from_trajectory`
- L251: FunctionDef `extract_response_ids_from_trajectories`
- L263: FunctionDef `trajectory_messages`
- L279: FunctionDef `select_p2_events_for_api_request_ids`
- L337: FunctionDef `p2_selection_diagnostics`
- L387: FunctionDef `expand_request_ids_by_suffix`
- L399: FunctionDef `request_ids_equivalent`
- L411: FunctionDef `p2_payload_matches_ids`
- L422: FunctionDef `p2_event_in_window`
- L434: FunctionDef `p2_event_time_ns`
- L451: FunctionDef `p2_event_matches_run`
- L460: FunctionDef `count_distinct_api_ids`
- L464: FunctionDef `distinct_api_ids`
- L478: FunctionDef `as_dict`
- L482: FunctionDef `string_or_none`
- L488: FunctionDef `integer_or_none`
- L27: FunctionDef `to_dict`

## agentmon/src/agentmon/runtime/phases.py

Lines: 182; encoding: utf-8

- L30: ClassDef `BatchPhaseRecorder`
- L102: FunctionDef `validate_phase_timings`
- L147: FunctionDef `as_dict`
- L151: FunctionDef `integer_or_none`
- L169: FunctionDef `optional_number`
- L31: FunctionDef `__init__`
- L43: FunctionDef `mark`
- L69: FunctionDef `duration_s`
- L76: FunctionDef `phase_timings`

## agentmon/src/agentmon/runtime/queue.py

Lines: 348; encoding: utf-8

- L25: ClassDef `QueuedRunRecord`
- L90: ClassDef `RuntimeQueueStore`
- L39: FunctionDef `from_dict`
- L56: FunctionDef `to_dict`
- L72: FunctionDef `summary`
- L91: FunctionDef `__init__`
- L97: FunctionDef `enqueue`
- L127: FunctionDef `list`
- L137: FunctionDef `get`
- L143: FunctionDef `next_pending`
- L150: FunctionDef `claim_running`
- L184: FunctionDef `heartbeat`
- L205: FunctionDef `recover_orphans`
- L233: FunctionDef `mark_deferred`
- L253: FunctionDef `mark_finished`
- L277: FunctionDef `mark_error`
- L287: FunctionDef `snapshot`
- L302: FunctionDef `_path`
- L305: FunctionDef `_read`
- L314: FunctionDef `_write`
- L322: FunctionDef `_replace`
- L328: FunctionDef `_is_claimable`
- L331: FunctionDef `_is_orphan`
- L339: FunctionDef `_locked`

## agentmon/src/agentmon/runtime/task_state.py

Lines: 715; encoding: utf-8

- L16: ClassDef `TaskStatePolicy`
- L48: ClassDef `ToolResultRecord`
- L70: ClassDef `TaskStateSnapshot`
- L103: FunctionDef `build_task_state_from_events`
- L156: FunctionDef `fingerprint_tool_result`
- L189: FunctionDef `evaluate_progress`
- L229: FunctionDef `extract_costs`
- L294: FunctionDef `extract_context_state`
- L346: FunctionDef `merge_prediction`
- L377: FunctionDef `build_budget`
- L397: FunctionDef `extract_engine_state`
- L414: FunctionDef `build_control_advice`
- L475: FunctionDef `collect_positive_signals`
- L494: FunctionDef `collect_negative_signals`
- L514: FunctionDef `max_consecutive_tool_signature`
- L530: FunctionDef `max_consecutive_error_signature`
- L549: FunctionDef `tool_signature`
- L557: FunctionDef `context_growth_is_high`
- L567: FunctionDef `budget_exceeded`
- L588: FunctionDef `infer_phase`
- L604: FunctionDef `collect_evidence_refs`
- L615: FunctionDef `stable_state_version`
- L633: FunctionDef `event_sort_key`
- L645: FunctionDef `first_event_value`
- L656: FunctionDef `latest_bool_payload`
- L664: FunctionDef `as_dict`
- L668: FunctionDef `first_int`
- L679: FunctionDef `optional_float`
- L688: FunctionDef `subtract_optional`
- L699: FunctionDef `ns_to_s`
- L704: FunctionDef `string_or_none`
- L708: FunctionDef `intervention_level`
- L29: FunctionDef `from_config`
- L43: FunctionDef `to_dict`
- L57: FunctionDef `to_dict`
- L84: FunctionDef `to_dict`

## agentmon/src/agentmon/runtime/types.py

Lines: 270; encoding: utf-8

- L26: ClassDef `RunSpec`
- L103: ClassDef `RuntimeContext`
- L111: ClassDef `PreparedRun`
- L118: ClassDef `RunHandle`
- L134: ClassDef `BackendStatus`
- L144: ClassDef `BackendCapabilities`
- L172: ClassDef `RuntimeRunResult`
- L188: ClassDef `RuntimeSubmitResult`
- L216: ClassDef `RuntimeEnqueueResult`
- L238: ClassDef `RuntimeDaemonResult`
- L258: ClassDef `RuntimeRecoveryResult`
- L42: FunctionDef `to_dict`
- L60: FunctionDef `to_storage_dict`
- L67: FunctionDef `from_dict`
- L124: FunctionDef `to_dict`
- L139: FunctionDef `to_dict`
- L156: FunctionDef `to_dict`
- L177: FunctionDef `to_dict`
- L193: FunctionDef `to_dict`
- L207: FunctionDef `exit_code`
- L224: FunctionDef `to_dict`
- L245: FunctionDef `to_dict`
- L263: FunctionDef `to_dict`

## agentmon/src/agentmon/sampling/__init__.py

Lines: 5; encoding: utf-8


## agentmon/src/agentmon/sampling/ascend_smi.py

Lines: 318; encoding: utf-8

- L18: ClassDef `ParseResult`
- L25: ClassDef `NpuSmiParser`
- L33: ClassDef `UsageKeyValueParser`
- L55: ClassDef `Table910Parser`
- L93: ClassDef `ChineseKeyValueParser`
- L100: ClassDef `RawOnlyParser`
- L118: ClassDef `AscendSmiSampler`
- L174: FunctionDef `resolve_npu_smi`
- L180: FunctionDef `run_npu_smi`
- L222: FunctionDef `parse_npu_smi_text`
- L227: FunctionDef `normalize_key`
- L269: FunctionDef `parse_number_with_unit`
- L298: FunctionDef `parse_aicore_util`
- L310: FunctionDef `parse_last_memory_ratio`
- L28: FunctionDef `detect`
- L30: FunctionDef `parse`
- L36: FunctionDef `detect`
- L40: FunctionDef `parse`
- L58: FunctionDef `detect`
- L63: FunctionDef `parse`
- L96: FunctionDef `detect`
- L103: FunctionDef `detect`
- L106: FunctionDef `parse`
- L121: FunctionDef `__init__`
- L129: FunctionDef `sample`

## agentmon/src/agentmon/sampling/base.py

Lines: 82; encoding: utf-8

- L13: ClassDef `SamplerStopStatus`
- L34: ClassDef `PeriodicSampler`
- L22: FunctionDef `to_dict`
- L37: FunctionDef `__init__`
- L43: FunctionDef `start`
- L46: FunctionDef `stop`
- L60: FunctionDef `sample`
- L63: FunctionDef `_run`

## agentmon/src/agentmon/sampling/host.py

Lines: 228; encoding: utf-8

- L9: ClassDef `HostSampler`
- L38: FunctionDef `read_text`
- L45: FunctionDef `read_proc_stat`
- L63: FunctionDef `read_key_value_kb`
- L83: FunctionDef `read_key_value_int`
- L98: FunctionDef `parse_pressure`
- L119: FunctionDef `read_diskstats`
- L169: FunctionDef `read_schedstat`
- L194: FunctionDef `read_numa_metrics`
- L12: FunctionDef `__init__`
- L15: FunctionDef `sample`

## agentmon/src/agentmon/sampling/npu_mapping.py

Lines: 341; encoding: utf-8

- L19: ClassDef `NpuProcessMappingProvider`
- L71: FunctionDef `provider`
- L75: FunctionDef `collect_npu_process_mapping`
- L127: FunctionDef `visible_npu_devices`
- L131: FunctionDef `classify_mapping_evidence`
- L148: FunctionDef `evidence_status`
- L156: FunctionDef `discover_service_process_rows`
- L209: FunctionDef `discover_vllm_process_rows`
- L223: FunctionDef `read_proc_environ`
- L237: FunctionDef `first_env`
- L245: FunctionDef `collect_npu_smi_process_rows`
- L264: FunctionDef `parse_npu_smi_process_rows`
- L288: FunctionDef `write_mapping_evidence_csv`
- L339: FunctionDef `write_json_private`
- L22: FunctionDef `__init__`
- L39: FunctionDef `descriptor`
- L60: FunctionDef `snapshot`

## agentmon/src/agentmon/sampling/procfs.py

Lines: 248; encoding: utf-8

- L18: ClassDef `ProcIdentity`
- L23: FunctionDef `parse_proc_stat_line`
- L49: FunctionDef `read_proc_stat`
- L56: FunctionDef `read_key_value_file`
- L68: FunctionDef `read_proc_io`
- L78: FunctionDef `read_proc_schedstat`
- L95: FunctionDef `read_smaps_rollup`
- L107: FunctionDef `read_cmdline`
- L115: FunctionDef `list_proc_stats`
- L126: FunctionDef `descendants_and_pgid`
- L146: ClassDef `ProcSampler`
- L235: FunctionDef `aggregate_processes`
- L149: FunctionDef `__init__`
- L162: FunctionDef `sample`
- L185: FunctionDef `_sample_pid`
- L222: FunctionDef `_match_vllm_pids`

## agentmon/src/agentmon/sampling/vllm_metrics.py

Lines: 108; encoding: utf-8

- L18: ClassDef `VllmMetricsSampler`
- L63: FunctionDef `fetch_metrics`
- L78: FunctionDef `probe_metrics`
- L21: FunctionDef `__init__`
- L25: FunctionDef `sample`

## agentmon/src/agentmon/scheduler/__init__.py

Lines: 21; encoding: utf-8


## agentmon/src/agentmon/scheduler/registry.py

Lines: 46; encoding: utf-8

- L27: FunctionDef `build_scheduler`
- L31: FunctionDef `register_scheduler_strategy`
- L41: FunctionDef `unregister_scheduler_strategy`
- L45: FunctionDef `scheduler_strategy_catalog`

## agentmon/src/agentmon/scheduler/service.py

Lines: 370; encoding: utf-8

- L19: ClassDef `FixedConcurrencyScheduler`
- L118: ClassDef `WorkloadAwareScheduler`
- L214: FunctionDef `read_manifest`
- L224: FunctionDef `manifest_to_scheduler_state`
- L242: FunctionDef `prediction_from_spec`
- L258: FunctionDef `budget_check`
- L293: FunctionDef `long_context_check`
- L328: FunctionDef `abnormal_path_check`
- L353: FunctionDef `task_prediction_value`
- L359: FunctionDef `task_context_tokens`
- L364: FunctionDef `optional_number`
- L29: FunctionDef `__init__`
- L32: FunctionDef `decide`
- L71: FunctionDef `running_count`
- L74: FunctionDef `snapshot`
- L93: FunctionDef `runs`
- L127: FunctionDef `decide`
- L185: FunctionDef `_workload_checks`

## agentmon/src/agentmon/scheduler/types.py

Lines: 35; encoding: utf-8

- L14: ClassDef `SchedulingDecision`
- L28: ClassDef `SchedulerStrategy`
- L24: FunctionDef `to_dict`
- L31: FunctionDef `decide`
- L34: FunctionDef `snapshot`

## agentmon/src/agentmon/script_compat.py

Lines: 74; encoding: utf-8

- L9: FunctionDef `install_module_facade`
- L64: FunctionDef `_default_public_names`
- L19: FunctionDef `apply_overrides`
- L31: FunctionDef `restore_overrides`
- L35: FunctionDef `call_implementation`
- L42: FunctionDef `make_wrapper`
- L44: FunctionDef `wrapper`

## agentmon/src/agentmon/security.py

Lines: 58; encoding: utf-8

- L9: FunctionDef `command_hash`
- L26: FunctionDef `read_command_hash_salt`
- L35: FunctionDef `redacted_argv`
- L46: FunctionDef `exception_fingerprint`

## agentmon/src/agentmon/server/__init__.py

Lines: 1; encoding: utf-8

Server-side validation and smoke-test helpers.


## agentmon/src/agentmon/server/preflight.py

Lines: 1925; encoding: utf-8

- L43: FunctionDef `build_parser`
- L140: FunctionDef `effective_timeout_seconds`
- L146: FunctionDef `make_check`
- L161: FunctionDef `cli_path`
- L166: FunctionDef `python_identity`
- L176: FunctionDef `configured_acl_python_paths`
- L199: FunctionDef `clean_python_env`
- L218: FunctionDef `disabled_p2_env`
- L234: FunctionDef `run_command`
- L276: FunctionDef `_bounded_text`
- L286: FunctionDef `last_json_object`
- L297: FunctionDef `check_repo`
- L316: FunctionDef `check_agentmon_python`
- L343: FunctionDef `check_agentmon_cli`
- L358: FunctionDef `check_config`
- L380: FunctionDef `check_sitecustomize`
- L409: FunctionDef `check_mini_swe`
- L599: FunctionDef `check_vllm`
- L768: FunctionDef `missing_methods`
- L782: FunctionDef `resolve_boolean_launch_setting`
- L829: FunctionDef `parse_bool`
- L838: FunctionDef `check_events_path`
- L887: FunctionDef `read_p2_event_stream`
- L936: FunctionDef `event_stream_integrity`
- L956: FunctionDef `chat_completions_url`
- L963: FunctionDef `parse_streaming_response_lines`
- L999: FunctionDef `send_chat_completion`
- L1065: FunctionDef `runtime_http_failure`
- L1088: FunctionDef `run_concurrent_api_requests`
- L1122: FunctionDef `payload_of`
- L1127: FunctionDef `engine_request_id_from_event`
- L1136: FunctionDef `api_request_ids_from_event`
- L1149: FunctionDef `engine_ids_for_api_request`
- L1160: FunctionDef `request_event_audit`
- L1252: FunctionDef `event_timestamp`
- L1258: FunctionDef `audit_stream_probe`
- L1298: FunctionDef `event_relates_to_engine_ids`
- L1310: FunctionDef `audit_concurrency_probe`
- L1395: FunctionDef `audit_prefix_cache_probe`
- L1437: FunctionDef `latest_runtime_capability`
- L1448: FunctionDef `wait_for_probe_events`
- L1481: FunctionDef `skipped_active_runtime_result`
- L1493: FunctionDef `run_active_runtime_probes`
- L1600: FunctionDef `integer_or_none`
- L1609: FunctionDef `none_if_empty`
- L1615: FunctionDef `check_runtime_p2_events`
- L1748: FunctionDef `check_runtime_probe_arguments`
- L1787: FunctionDef `build_report`
- L1906: FunctionDef `main`
- L1098: FunctionDef `worker`

## agentmon/src/agentmon/server/quick_smoke.py

Lines: 1285; encoding: utf-8

- L21: FunctionDef `stage_result`
- L46: FunctionDef `print_stage_result`
- L64: FunctionDef `suggestion_for_blockers`
- L91: FunctionDef `main`
- L119: FunctionDef `build_parser`
- L156: FunctionDef `prepare_quick_smoke`
- L322: FunctionDef `load_request`
- L341: FunctionDef `load_yaml_request`
- L352: FunctionDef `load_toml_request`
- L366: FunctionDef `load_profile`
- L379: FunctionDef `output_directory`
- L387: FunctionDef `synthesized_paths`
- L410: FunctionDef `select_devices`
- L422: FunctionDef `resolve_smoke_tasks`
- L446: FunctionDef `discover_benchmark_task_ids`
- L473: FunctionDef `task_ids_from_archive_names`
- L481: FunctionDef `task_id_from_archive_name`
- L492: FunctionDef `resolve_prefix_cache_args`
- L507: FunctionDef `prefix_cache_args_from_preflight`
- L525: FunctionDef `resolve_tool_call_required`
- L536: FunctionDef `resolve_tool_call_parser`
- L569: FunctionDef `blocking_reasons`
- L581: FunctionDef `write_instance_env`
- L624: FunctionDef `write_start_script`
- L671: FunctionDef `write_tool_call_probe`
- L695: FunctionDef `write_resource_gate_script`
- L778: FunctionDef `write_service_start_gate_script`
- L873: FunctionDef `write_tool_call_gate_script`
- L980: FunctionDef `write_mini_swe_probe`
- L1046: FunctionDef `write_mini_model_config`
- L1067: FunctionDef `write_agentmon_config`
- L1089: FunctionDef `default_agentmon_config`
- L1120: FunctionDef `set_toml_value`
- L1139: FunctionDef `encode_toml_value`
- L1145: FunctionDef `write_watchdog_script`
- L1196: FunctionDef `config_consistency`
- L1220: FunctionDef `first_present`
- L1227: FunctionDef `dotted`
- L1236: FunctionDef `as_dict`
- L1240: FunctionDef `as_list`
- L1250: FunctionDef `acl_from_profile`
- L1255: FunctionDef `string_map`
- L1259: FunctionDef `expand_vars`
- L1266: FunctionDef `slug`
- L1271: FunctionDef `write_json`
- L1275: FunctionDef `write_private_text`

## agentmon/src/agentmon/step_sequence/__init__.py

Lines: 19; encoding: utf-8

Per-agent-step sequence analysis package.


## agentmon/src/agentmon/step_sequence/constants.py

Lines: 104; encoding: utf-8


## agentmon/src/agentmon/step_sequence/core.py

Lines: 1385; encoding: utf-8

- L64: FunctionDef `main`
- L87: FunctionDef `build_parser`
- L112: FunctionDef `write_output_bundle`
- L131: FunctionDef `build_step_sequence`
- L192: FunctionDef `annotate_sequence_times`
- L219: FunctionDef `first_value_from_items`
- L226: FunctionDef `add_count_sources`
- L253: FunctionDef `is_service_global_p2_event`
- L268: FunctionDef `assemble_sequence`
- L328: FunctionDef `build_trajectory_fallback_sequence`
- L422: FunctionDef `find_mini_swe_trajectory`
- L444: FunctionDef `mini_swe_trajectory_steps`
- L475: FunctionDef `trajectory_messages`
- L491: FunctionDef `tool_messages_by_call_id`
- L502: FunctionDef `pop_tool_message`
- L511: FunctionDef `trajectory_tool_item`
- L533: FunctionDef `trajectory_timeout_details`
- L561: FunctionDef `trajectory_command`
- L581: FunctionDef `p2_rows_by_api_request_id`
- L590: FunctionDef `p2_row_sort_key`
- L594: FunctionDef `next_unmatched_p2_row`
- L607: FunctionDef `make_trajectory_step`
- L665: FunctionDef `make_extra_p2_step`
- L701: FunctionDef `make_trajectory_model_item`
- L755: FunctionDef `make_trajectory_tool_sequence_item`
- L790: FunctionDef `make_step`
- L832: FunctionDef `synthetic_step`
- L854: FunctionDef `finalize_step`
- L909: FunctionDef `make_item`
- L915: FunctionDef `make_model_item`
- L1106: FunctionDef `make_tool_item`
- L1136: FunctionDef `tool_proc_summary`
- L1201: FunctionDef `base_item`
- L1221: FunctionDef `step_id_for_child`

## agentmon/src/agentmon/step_sequence/formatters.py

Lines: 582; encoding: utf-8

- L11: FunctionDef `format_output`
- L20: FunctionDef `format_table_one`
- L87: FunctionDef `format_csv`

## agentmon/src/agentmon/step_sequence/io.py

Lines: 34; encoding: utf-8

- L7: FunctionDef `read_jsonl`
- L25: FunctionDef `read_json_file`
- L29: FunctionDef `read_optional_json`

## agentmon/src/agentmon/step_sequence/p2.py

Lines: 1084; encoding: utf-8

- L34: FunctionDef `build_p2_index`
- L90: FunctionDef `p2_row_identity_compatible`
- L104: FunctionDef `p2_row_key`
- L112: FunctionDef `p2_row_keys`
- L121: FunctionDef `merge_p2_rows`
- L142: FunctionDef `finalize_p2_lifecycle`
- L263: FunctionDef `add_kv_residency`
- L288: FunctionDef `unique_p2_rows`
- L300: FunctionDef `merge_p2_payload`
- L411: FunctionDef `find_p2_match`
- L463: FunctionDef `find_p2_row`
- L468: FunctionDef `p2_projection`
- L701: FunctionDef `verified_timestamp_quality`
- L705: FunctionDef `token_period_seconds`
- L718: FunctionDef `client_server_stage_projection`
- L759: FunctionDef `cross_process_timestamp_quality`
- L777: FunctionDef `verified_delta_s`
- L783: FunctionDef `timestamp_pair_quality`
- L809: FunctionDef `kv_snapshot_row`
- L823: FunctionDef `nearest_kv_snapshot`
- L834: FunctionDef `kv_snapshot_projection`
- L855: FunctionDef `step_kv_summary`
- L879: FunctionDef `build_totals`
- L915: FunctionDef `build_p2_identity_audit`
- L949: FunctionDef `orphan_p2_request_audit_records`
- L978: FunctionDef `request_audit_record`
- L996: FunctionDef `stable_model_request_id`
- L1013: FunctionDef `request_id_kind_from_item`
- L1029: FunctionDef `canonical_p2_request_identity`
- L1037: FunctionDef `p2_request_identity_aliases`
- L1053: FunctionDef `sorted_unique_text`
- L1065: FunctionDef `build_warnings`

## agentmon/src/agentmon/step_sequence/resources.py

Lines: 438; encoding: utf-8

- L24: FunctionDef `build_resource_context`
- L62: FunctionDef `scheduler_window_summary`
- L120: FunctionDef `scheduler_field_value`
- L134: FunctionDef `proc_window_summary`
- L231: FunctionDef `ascend_window_summary`
- L294: FunctionDef `summarize_per_device_samples`
- L336: FunctionDef `events_in_window`
- L352: FunctionDef `proc_process_matches`
- L368: FunctionDef `device_sample_summary`
- L394: FunctionDef `device_identity`
- L402: FunctionDef `parse_device_ids`
- L409: FunctionDef `infer_p2_visible_devices`
- L421: FunctionDef `normalize_device_list`
- L430: FunctionDef `window_duration_s`

## agentmon/src/agentmon/step_sequence/spans.py

Lines: 90; encoding: utf-8

- L8: FunctionDef `build_spans`
- L42: FunctionDef `span_lifecycle`
- L55: FunctionDef `span_identifier`
- L60: FunctionDef `merge_span`

## agentmon/src/agentmon/step_sequence/utils.py

Lines: 213; encoding: utf-8

- L6: FunctionDef `as_dict`
- L10: FunctionDef `text_or_none`
- L16: FunctionDef `list_dicts`
- L20: FunctionDef `run_id_from_events`
- L28: FunctionDef `sort_time`
- L33: FunctionDef `ns_to_s`
- L39: FunctionDef `ns_delta`
- L45: FunctionDef `first_int`
- L52: FunctionDef `first_value`
- L59: FunctionDef `min_int`
- L64: FunctionDef `max_int`
- L69: FunctionDef `integer_or_none`
- L79: FunctionDef `number_or_none`
- L87: FunctionDef `parse_timestamp_seconds`
- L107: FunctionDef `byte_count`
- L117: FunctionDef `cached_tokens_from_usage`
- L125: FunctionDef `sum_present`
- L136: FunctionDef `sum_numeric`
- L147: FunctionDef `mean_present`
- L152: FunctionDef `max_present`
- L157: FunctionDef `min_present`
- L162: FunctionDef `count_values`
- L170: FunctionDef `ratio_or_none`
- L178: FunctionDef `nonzero`
- L183: FunctionDef `fmt_s`
- L188: FunctionDef `fmt_na`
- L192: FunctionDef `fmt_pct`
- L197: FunctionDef `fmt_pct_value`
- L202: FunctionDef `fmt_bytes`

## agentmon/src/agentmon/storage/__init__.py

Lines: 1; encoding: utf-8


## agentmon/src/agentmon/storage/event_queue.py

Lines: 209; encoding: utf-8

- L14: ClassDef `EventSink`
- L15: FunctionDef `__init__`
- L40: FunctionDef `emit`
- L60: FunctionDef `emit_event`
- L76: FunctionDef `stop_accepting`
- L80: FunctionDef `pending_queue_size`
- L83: FunctionDef `drain`
- L103: FunctionDef `flush`
- L112: FunctionDef `fsync`
- L115: FunctionDef `close`
- L170: FunctionDef `_run`
- L200: FunctionDef `_record_writer_failure`

## agentmon/src/agentmon/storage/jsonl.py

Lines: 66; encoding: utf-8

- L11: ClassDef `JsonlWriter`
- L14: FunctionDef `__init__`
- L26: FunctionDef `write_event`
- L38: FunctionDef `flush`
- L49: FunctionDef `fsync`
- L54: FunctionDef `close`
- L62: FunctionDef `__enter__`
- L65: FunctionDef `__exit__`

## agentmon/src/agentmon/storage/sqlite.py

Lines: 95; encoding: utf-8

- L50: ClassDef `SqliteEventWriter`
- L51: FunctionDef `__init__`
- L65: FunctionDef `write_event`
- L88: FunctionDef `commit`
- L92: FunctionDef `close`

## agentmon/src/agentmon/strategy.py

Lines: 75; encoding: utf-8

- L11: ClassDef `StrategyDescriptor`
- L20: ClassDef `StrategyRegistry`
- L16: FunctionDef `to_dict`
- L27: FunctionDef `__init__`
- L32: FunctionDef `register`
- L48: FunctionDef `unregister`
- L53: FunctionDef `create`
- L64: FunctionDef `names`
- L67: FunctionDef `catalog`

## agentmon/src/agentmon/trajectory/__init__.py

Lines: 1; encoding: utf-8


## agentmon/src/agentmon/trajectory/mini_swe.py

Lines: 155; encoding: utf-8

- L12: FunctionDef `snapshot_trajectory_glob`
- L18: FunctionDef `find_new_or_modified_trajectories`
- L33: FunctionDef `import_trajectory`
- L57: FunctionDef `summarize_trajectory`
- L117: FunctionDef `usage_from_message`
- L136: FunctionDef `usage_int`
- L148: FunctionDef `cached_tokens_from_usage`

## agentmon/src/sitecustomize.py

Lines: 17; encoding: utf-8

- L7: FunctionDef `_truthy`

## agentmon/tests/__init__.py

Lines: 8; encoding: utf-8


## agentmon/tests/integration/__init__.py

Lines: 1; encoding: utf-8


## agentmon/tests/integration/test_vllm_018_runtime_hook.py

Lines: 68; encoding: utf-8

- L21: ClassDef `Vllm018RuntimeHookIntegrationTests`
- L22: FunctionDef `tearDown`
- L25: FunctionDef `test_real_engine_core_contract_and_hook_installation`

## agentmon/tests/unit/__init__.py

Lines: 8; encoding: utf-8


## agentmon/tests/unit/test_adapter_namespaces.py

Lines: 96; encoding: utf-8

- L9: ClassDef `AdapterNamespaceTests`
- L10: FunctionDef `test_mini_swe_new_namespace_reexports_existing_adapter`
- L17: FunctionDef `test_mini_swe_legacy_namespace_reexports_adapter_entrypoint`
- L23: FunctionDef `test_vllm_new_namespace_reexports_existing_adapter`
- L30: FunctionDef `test_vllm_legacy_namespace_reexports_p2_index`
- L36: FunctionDef `test_adapter_registry_uses_contract_capability_type`
- L42: FunctionDef `test_mini_swe_adapter_prepares_p1_command`
- L52: FunctionDef `test_vllm_provider_exposes_inference_descriptor`
- L61: FunctionDef `test_adapter_capability_catalog_reports_agent_and_inference_adapters`
- L77: FunctionDef `test_adapter_capability_catalog_filters_by_domain`
- L87: FunctionDef `test_adapter_capability_catalog_reports_resource_provider`

## agentmon/tests/unit/test_analysis_repair.py

Lines: 729; encoding: utf-8

- L31: ClassDef `AnalysisRepairTests`
- L675: FunctionDef `write_request_experiment`
- L715: FunctionDef `event`
- L32: FunctionDef `test_required_dependency_check_fails_without_import`
- L38: FunctionDef `test_csv_preflight_does_not_report_missing_parquet_engine_as_smoke_pass`
- L46: FunctionDef `test_preflight_required_failure_does_not_scan_input_inventory`
- L64: FunctionDef `test_strict_baseline_mismatch_stops_before_normalization`
- L80: FunctionDef `test_repeated_request_ids_across_runs_keep_all_fifty_attempts`
- L103: FunctionDef `test_within_run_duplicate_and_malformed_request_candidates_are_explained`
- L122: FunctionDef `test_error_eligibility_tracks_whether_the_api_boundary_was_crossed`
- L145: FunctionDef `test_shared_p2_index_scans_once_then_reuses`
- L178: FunctionDef `test_unscoped_shared_p2_with_cross_run_repeated_alias_is_not_double_attributed`
- L205: FunctionDef `test_shared_p2_index_incrementally_reads_appended_bytes_and_time_window`
- L223: FunctionDef `test_p2_index_scope_fields_are_supplied_by_schema_profile`
- L255: FunctionDef `test_p2_index_resumes_after_last_committed_source`
- L291: FunctionDef `test_gzip_run_events_are_parsed`
- L302: FunctionDef `test_zstd_run_file_is_discovered_before_optional_decompression`
- L312: FunctionDef `test_resume_rejects_changed_input_without_overwriting_compatibility_manifest`
- L333: FunctionDef `test_resume_rejects_profile_change_and_partial_normalization_cache`
- L348: FunctionDef `test_resume_reuses_completed_normalization_but_reruns_after_partial_validation`
- L363: FunctionDef `test_resume_rejects_source_fingerprint_change`
- L374: FunctionDef `test_source_fingerprint_includes_schema_and_profile_data`
- L389: FunctionDef `test_module_packs_claim_registry_and_artifact_categories_exist`
- L408: FunctionDef `test_claim_statement_change_supersedes_prior_claim`
- L425: FunctionDef `test_dry_run_writes_plan_without_reading_input`
- L436: FunctionDef `test_to_stage_stops_real_execution_and_writes_checkpoint`
- L452: FunctionDef `test_failed_stage_invalidates_all_linear_downstream_cache_entries`
- L478: FunctionDef `test_stage_cache_rejects_an_output_hash_mismatch`
- L495: FunctionDef `test_profile_auto_uses_all_events_and_reports_ambiguity`
- L515: FunctionDef `test_mixed_profile_input_can_be_explicitly_rejected`
- L529: FunctionDef `test_allow_baseline_mismatch_continues_with_recorded_partial_status`
- L539: FunctionDef `test_strict_cli_returns_nonzero_for_non_pass_analysis`
- L551: FunctionDef `test_analysis_does_not_modify_authoritative_input`
- L567: FunctionDef `test_configured_timeout_writes_checkpointed_failure_diagnosis`
- L586: FunctionDef `test_process_pool_failure_is_audited_before_serial_fallback`
- L611: FunctionDef `test_engine_metrics_keep_windows_distinct_and_gate_token_budget`
- L630: FunctionDef `test_long_context_state_requires_configured_threshold_and_sustained_waiting`
- L652: FunctionDef `test_engine_sensitivity_time_slice_changes_the_computation`
- L274: FunctionDef `interrupt_after_first_source`

## agentmon/tests/unit/test_analysis_sop.py

Lines: 612; encoding: utf-8

- L24: ClassDef `AnalysisSopTests`
- L503: FunctionDef `explicit_contract`
- L531: FunctionDef `write_synthetic_experiment`
- L592: FunctionDef `make_minimal_bundle`
- L25: FunctionDef `test_schema_registry_loads_metric_semantics`
- L34: FunctionDef `test_time_weighted_and_interval_union_are_deterministic`
- L43: FunctionDef `test_wilson_interval_uses_configured_confidence`
- L50: FunctionDef `test_p2_gate_counts_error_requests_only_when_evidence_eligible`
- L62: FunctionDef `test_p1_capture_audit_matches_trajectory_attempts_to_p1_requests`
- L93: FunctionDef `test_token_timeseries_requires_actual_generated_deltas_for_steady_state`
- L141: FunctionDef `test_deep_p2_scheduler_membership_and_kv_events_are_standardized`
- L254: FunctionDef `test_pipeline_validation_prefers_batch_phase_timings`
- L280: FunctionDef `test_pipeline_estimate_cannot_be_reported_as_direct_pass`
- L300: FunctionDef `test_analysis_run_all_cli_creates_bundle_and_model_pack`
- L339: FunctionDef `test_analysis_run_all_materializes_contract_semantics_quality_gate_and_handoff`
- L380: FunctionDef `test_missing_experiment_contract_is_recorded_as_partial_not_imputed`
- L407: FunctionDef `test_analysis_fast_smoke_parallel_skips_heavy_outputs`
- L442: FunctionDef `test_contract_template_cli_writes_required_fields`
- L453: FunctionDef `test_report_validator_flags_forbidden_claim`
- L469: FunctionDef `test_report_validator_flags_metric_unit_mismatch`
- L490: FunctionDef `test_compare_marks_different_models_non_comparable`

## agentmon/tests/unit/test_ascend_attribution.py

Lines: 116; encoding: utf-8

- L11: ClassDef `AscendAttributionTests`
- L90: FunctionDef `make_ascend_report`
- L107: FunctionDef `capability_event`
- L111: FunctionDef `p2_event_with_pid`
- L12: FunctionDef `test_target_devices_do_not_mix_host_wide_aicore`
- L28: FunctionDef `test_missing_target_devices_do_not_fall_back_to_host_wide`
- L37: FunctionDef `test_explicit_devices_take_priority_over_metadata_and_proc_environ`
- L49: FunctionDef `test_p2_capability_metadata_resolves_target_devices`
- L57: FunctionDef `test_proc_environ_starttime_mismatch_is_not_used`
- L73: FunctionDef `test_proc_environ_matching_starttime_resolves_devices`

## agentmon/tests/unit/test_ascend_smi.py

Lines: 58; encoding: utf-8

- L6: ClassDef `AscendSmiTests`
- L7: FunctionDef `test_key_value_parser_normalizes_usage_fields`
- L25: FunctionDef `test_table_parser_uses_hbm_ratio_not_hugepages_zero_ratio`
- L46: FunctionDef `test_usage_rate_percent_is_not_reported_as_hbm_bytes`

## agentmon/tests/unit/test_batch_compare.py

Lines: 94; encoding: utf-8

- L12: ClassDef `BatchCompareTests`
- L45: FunctionDef `write_batch`
- L13: FunctionDef `test_pairs_same_task_step_and_reports_metric_deltas`
- L30: FunctionDef `test_task_set_mismatch_blocks_comparability`

## agentmon/tests/unit/test_batch_runtime.py

Lines: 1159; encoding: utf-8

- L18: FunctionDef `load_module`
- L26: ClassDef `BatchRuntimeTests`
- L1131: FunctionDef `assistant`
- L1139: FunctionDef `p2_event`
- L1154: FunctionDef `ns`
- L1158: FunctionDef `write_jsonl`
- L27: FunctionDef `test_analyze_existing_does_not_require_a_workload_command`
- L81: FunctionDef `test_pipeline_profile_avoids_implicit_recursive_file_scan`
- L92: FunctionDef `test_main_fails_when_execution_failed_even_if_analysis_succeeded`
- L115: FunctionDef `test_main_fails_execution_only_workload_failure_before_analysis`
- L139: FunctionDef `test_execution_only_missing_run_id_marks_batch_invalid`
- L183: FunctionDef `test_workspace_preflight_rejects_agentmon_source_before_workload`
- L217: FunctionDef `test_defer_analysis_alias_sets_execution_only`
- L235: FunctionDef `test_large_p2_threshold_defers_analysis_without_indexing`
- L291: FunctionDef `test_analyze_existing_ignores_large_p2_auto_defer_threshold`
- L327: FunctionDef `test_task_validation_consumes_run_report_publishability`
- L350: FunctionDef `test_missing_run_report_validation_is_unpublishable`
- L358: FunctionDef `test_configure_p1_command_injects_native_adapter_classes`
- L382: FunctionDef `test_configure_p1_command_refuses_unsafe_shell_composition`
- L394: FunctionDef `test_batch_summary_separates_pipeline_stages_and_computes_throughput`
- L475: FunctionDef `test_phase_recorder_writes_precise_phase_events_and_validation`
- L500: FunctionDef `test_p2_index_cache_records_alias_offsets_and_metrics`
- L530: FunctionDef `test_p2_index_cache_can_be_shared_across_batch_roots`
- L551: FunctionDef `test_prefiltered_p2_import_reuses_events_without_subprocess`
- L607: FunctionDef `test_p2_index_cache_persists_one_event_table_and_hydrates_references`
- L641: FunctionDef `test_p2_request_window_uses_time_index_instead_of_full_stream_scan`
- L679: FunctionDef `test_p2_global_window_keeps_prior_state_and_drops_distant_events`
- L715: FunctionDef `test_p2_global_window_can_skip_full_fallback_when_window_missing`
- L739: FunctionDef `test_quick_analysis_preset_skips_large_batch_artifacts`
- L784: FunctionDef `test_collect_npu_process_mapping_writes_environment_level_evidence`
- L816: FunctionDef `test_batch_manifest_records_reproducibility_metadata`
- L888: FunctionDef `test_vllm_launch_settings_parse_disabled_prefix_cache_flag`
- L899: FunctionDef `test_reads_tasks_env_and_renders_templates`
- L920: FunctionDef `test_extracts_mini_swe_response_ids_from_assistant_tool_steps`
- L945: FunctionDef `test_filters_p2_events_by_response_ids_without_concurrent_noise`
- L988: FunctionDef `test_p2_service_bounds_reads_runtime_capability_metadata`
- L1030: FunctionDef `test_combines_per_task_step_csvs_for_batch_analysis`
- L1051: FunctionDef `test_can_optionally_include_time_window_extra_p2_requests`
- L1084: FunctionDef `test_batch_index_reports_one_api_id_mapped_to_multiple_engine_requests`

## agentmon/tests/unit/test_batch_safety.py

Lines: 210; encoding: utf-8

- L20: FunctionDef `args_for`
- L30: ClassDef `BatchSafetyTests`
- L31: FunctionDef `test_default_workspace_is_isolated_and_resolved`
- L50: FunctionDef `test_workspace_cannot_point_to_or_contain_protected_source`
- L78: FunctionDef `test_symlink_escape_to_protected_source_is_rejected`
- L99: FunctionDef `test_two_tasks_cannot_share_a_workspace`
- L111: FunctionDef `test_source_manifest_reports_added_modified_and_removed_files`
- L133: FunctionDef `test_isolated_env_hides_original_source_paths`
- L157: FunctionDef `test_runtime_snapshot_is_an_exact_read_only_source_copy`
- L174: FunctionDef `test_missing_agentmon_run_id_invalidates_experiment_even_with_zero_exit`
- L197: FunctionDef `test_unknown_source_integrity_keeps_validity_unresolved`

## agentmon/tests/unit/test_canonical.py

Lines: 165; encoding: utf-8

- L11: ClassDef `CanonicalDatasetTests`
- L150: FunctionDef `event`
- L12: FunctionDef `test_builds_stable_request_ids_and_exact_proxy_boundaries`
- L126: FunctionDef `test_writer_always_emits_jsonl_csv_and_audits_parquet`

## agentmon/tests/unit/test_cli.py

Lines: 441; encoding: utf-8

- L20: ClassDef `CliTests`
- L424: FunctionDef `p2_line`
- L21: FunctionDef `run_agentmon`
- L35: FunctionDef `test_version_command`
- L41: FunctionDef `test_doctor_returns_machine_readable_status`
- L70: FunctionDef `test_canonical_cli_writes_machine_readable_tables`
- L107: FunctionDef `test_matrix_cli_propagates_repeat_failures`
- L120: FunctionDef `test_live_p1_summary_reads_events_jsonl`
- L150: FunctionDef `test_import_p2_imports_offset_slice_and_regenerates_report`
- L236: FunctionDef `test_import_p2_filters_by_mini_swe_trajectory_response_ids`
- L359: FunctionDef `test_import_p2_filter_run_id_matches_agentmon_run_id_payload`

## agentmon/tests/unit/test_clock.py

Lines: 24; encoding: utf-8

- L7: ClassDef `ClockAnchorTests`
- L8: FunctionDef `test_clock_anchor_contains_wall_and_monotonic_time`

## agentmon/tests/unit/test_config.py

Lines: 297; encoding: utf-8

- L8: ClassDef `ConfigTests`
- L9: FunctionDef `test_context_compression_config_defaults_off_and_supports_env_overrides`
- L26: FunctionDef `test_context_compression_rejects_invalid_ratios_and_options`
- L47: FunctionDef `test_python_310_toml_fallback_parses_literal_json_strings`
- L62: FunctionDef `test_runtime_optimization_master_switch_defaults_on_and_has_env_override`
- L70: FunctionDef `test_sqlite_cache_is_opt_in_and_environment_can_enable_it`
- L74: FunctionDef `test_config_override_order_is_cli_env_file_defaults`
- L108: FunctionDef `test_agentmon_config_env_selects_file`
- L123: FunctionDef `test_unknown_config_key_is_rejected_by_default`
- L137: FunctionDef `test_target_config_example_loads`
- L154: FunctionDef `test_scheduler_config_loads`
- L182: FunctionDef `test_optimizer_config_loads`
- L203: FunctionDef `test_task_state_config_loads`
- L228: FunctionDef `test_finalization_config_loads`
- L258: FunctionDef `test_control_config_loads`

## agentmon/tests/unit/test_context_compression.py

Lines: 496; encoding: utf-8

- L31: ClassDef `CapturingModel`
- L48: ClassDef `SummarizingModel`
- L81: ClassDef `ContextCompressionTests`
- L482: FunctionDef `compression_config`
- L34: FunctionDef `__init__`
- L37: FunctionDef `query`
- L51: FunctionDef `__init__`
- L54: FunctionDef `query`
- L82: FunctionDef `test_model_summary_strategy_replaces_old_groups_with_provider_output`
- L121: FunctionDef `test_instrumented_model_uses_model_generated_summary_and_tracks_usage`
- L165: FunctionDef `test_recent_groups_triggers_at_eighty_percent_and_targets_twenty_percent`
- L187: FunctionDef `test_context_below_trigger_is_unchanged`
- L204: FunctionDef `test_required_and_recent_groups_can_make_target_partial`
- L220: FunctionDef `test_runtime_master_switch_bypasses_compression`
- L231: FunctionDef `test_custom_strategy_can_be_registered_and_selected`
- L270: FunctionDef `test_custom_trigger_can_replace_token_ratio_policy`
- L315: FunctionDef `test_strategy_module_can_be_loaded_from_config`
- L406: FunctionDef `test_mini_swe_grouping_keeps_assistant_and_tool_results_atomic`
- L421: FunctionDef `test_instrumented_model_compresses_only_request_copy_and_emits_no_content`
- L463: FunctionDef `test_child_process_reads_resolved_run_config`
- L474: FunctionDef `test_mini_swe_non_sequence_context_stays_unresolved`
- L232: ClassDef `TestStrategy`
- L271: ClassDef `AlwaysTrigger`
- L235: FunctionDef `__init__`
- L238: FunctionDef `measure`
- L245: FunctionDef `compress`
- L274: FunctionDef `__init__`
- L277: FunctionDef `evaluate`

## agentmon/tests/unit/test_control.py

Lines: 364; encoding: utf-8

- L16: ClassDef `ControlCliTests`
- L313: FunctionDef `write_control_config`
- L17: FunctionDef `test_model_gate_status_reflects_optimizer_pressure`
- L32: FunctionDef `test_tool_pool_status_reports_static_pools`
- L48: FunctionDef `test_tool_pool_classify_assigns_cpu_heavy_command`
- L74: FunctionDef `test_tool_pool_classify_assigns_io_heavy_shell_command`
- L99: FunctionDef `test_model_gate_acquire_denies_in_enforce_mode_when_full`
- L128: FunctionDef `test_tool_pool_acquire_denies_in_enforce_mode_when_full`
- L156: FunctionDef `test_tool_pool_exec_runs_command_when_slot_is_available`
- L189: FunctionDef `test_tool_pool_exec_reports_resource_control_plan`
- L220: FunctionDef `test_tool_pool_exec_does_not_run_when_pool_is_full`
- L250: FunctionDef `test_gated_provider_rejects_before_http_when_full`
- L266: FunctionDef `test_gated_provider_posts_openai_compatible_chat_request`
- L267: ClassDef `Handler`
- L272: FunctionDef `do_POST`
- L284: FunctionDef `log_message`

## agentmon/tests/unit/test_events.py

Lines: 56; encoding: utf-8

- L8: ClassDef `EventTests`
- L9: FunctionDef `test_event_serializes_to_valid_json`
- L30: FunctionDef `test_nan_and_inf_are_safely_converted_to_null`

## agentmon/tests/unit/test_existing_data_analysis.py

Lines: 632; encoding: utf-8

- L17: FunctionDef `load_module`
- L25: ClassDef `ExistingDataAnalysisTests`
- L429: FunctionDef `make_batch`
- L508: FunctionDef `model_row`
- L537: FunctionDef `tool_row`
- L557: FunctionDef `p2_event`
- L586: FunctionDef `complete_p2_events`
- L597: FunctionDef `host_event`
- L619: FunctionDef `write_rows`
- L26: FunctionDef `test_inflight_change_point_detects_unit_latency_slowdown`
- L45: FunctionDef `test_script_help_uses_real_cli_entrypoint`
- L58: FunctionDef `test_request_metrics_are_null_safe_and_use_effective_decode_tokens`
- L100: FunctionDef `test_p2_candidate_index_reports_multiple_without_choosing_one`
- L116: FunctionDef `test_raw_p2_evidence_overrides_stale_matched_label_and_audits_extra`
- L141: FunctionDef `test_validation_applies_p2_threshold_to_each_batch`
- L163: FunctionDef `test_client_server_proxy_requires_matching_clock_domain`
- L191: FunctionDef `test_request_metrics_recover_from_same_clock_raw_p2_events`
- L213: FunctionDef `test_timeline_finds_overlapping_models_and_item_outside_run`
- L243: FunctionDef `test_timeline_requires_started_and_completed_counts_to_match`
- L267: FunctionDef `test_product_decomposition_reconciles_duration_delta`
- L280: FunctionDef `test_tail_classifier_uses_paired_baseline_evidence`
- L308: FunctionDef `test_device_imbalance_handles_partial_metrics_without_rank_claim`
- L341: FunctionDef `test_host_counter_reset_is_null_not_negative`
- L349: FunctionDef `test_host_metrics_parse_load_memory_and_pressure`
- L376: FunctionDef `test_empty_batch_writes_diagnostics_and_strict_fails`
- L393: FunctionDef `test_end_to_end_existing_batch_analysis_writes_all_artifacts`

## agentmon/tests/unit/test_experiment_orchestration.py

Lines: 306; encoding: utf-8

- L18: ClassDef `ExperimentOrchestrationTests`
- L243: FunctionDef `base_contract`
- L280: FunctionDef `make_batch`
- L301: FunctionDef `write_jsonl`
- L19: FunctionDef `test_ready_for_formal_when_contract_activation_and_pilot_pass`
- L55: FunctionDef `test_formal_is_blocked_when_activation_probe_observes_disabled_gate`
- L81: FunctionDef `test_disallowed_diff_and_wrong_execution_path_block_formal`
- L107: FunctionDef `test_unknown_history_directory_routes_to_analysis_runtime`
- L118: FunctionDef `test_mechanism_registry_drives_activation_profile_and_coverage`
- L151: FunctionDef `test_request_generator_creates_contract_from_toml`
- L195: FunctionDef `test_unified_prepare_and_smoke_entrypoints`

## agentmon/tests/unit/test_host_sampling.py

Lines: 43; encoding: utf-8

- L10: ClassDef `HostSamplingTests`
- L11: FunctionDef `test_diskstats_preserves_units_and_io_counters`
- L27: FunctionDef `test_schedstat_aggregates_per_cpu_runqueue_delay`

## agentmon/tests/unit/test_intervals.py

Lines: 11; encoding: utf-8

- L6: ClassDef `IntervalTests`
- L7: FunctionDef `test_merge_intervals_uses_half_open_ranges`

## agentmon/tests/unit/test_jsonl.py

Lines: 101; encoding: utf-8

- L12: FunctionDef `write_jsonl_events_for_process`
- L30: ClassDef `JsonlWriterTests`
- L31: FunctionDef `test_concurrent_event_writes_are_valid_json_lines`
- L72: FunctionDef `test_multi_process_writes_are_valid_json_lines`
- L39: FunctionDef `write_events`

## agentmon/tests/unit/test_matrix.py

Lines: 36; encoding: utf-8

- L12: ClassDef `MatrixExperimentTests`
- L13: FunctionDef `test_repeats_command_in_isolated_directories`

## agentmon/tests/unit/test_moe_routing.py

Lines: 188; encoding: utf-8

- L23: ClassDef `MoeRoutingTests`
- L24: FunctionDef `test_capability_scope_is_owned_by_the_vllm_adapter`
- L32: FunctionDef `test_mode_and_chunk_configuration_are_explicit_and_bounded`
- L42: FunctionDef `test_trace_chunks_round_trip_without_losing_logical_expert_ids`
- L71: FunctionDef `test_missing_routed_experts_stays_unresolved`
- L85: FunctionDef `test_single_run_import_selection_keeps_moe_request_and_capability_events`
- L120: FunctionDef `test_hook_report_and_batch_index_keep_moe_events_request_attributed`
- L86: FunctionDef `event`

## agentmon/tests/unit/test_optimizer.py

Lines: 148; encoding: utf-8

- L11: ClassDef `OptimizerCliTests`
- L115: FunctionDef `write_optimizer_config`
- L12: FunctionDef `test_optimizer_decision_reports_available_capacity`
- L27: FunctionDef `test_optimizer_decision_reports_unavailable_scheduler_capacity`
- L40: FunctionDef `test_optimizer_decision_reports_near_capacity_from_run_manifests`
- L70: FunctionDef `test_optimizer_decision_uses_model_service_snapshot_fixture`

## agentmon/tests/unit/test_optimizer_experiment_harness.py

Lines: 907; encoding: utf-8

- L24: ClassDef `OptimizerExperimentHarnessTests`
- L690: FunctionDef `make_batch`
- L753: FunctionDef `model_row`
- L778: FunctionDef `tool_row`
- L794: FunctionDef `write_events`
- L834: FunctionDef `write_custom_p2_events`
- L856: FunctionDef `write_config`
- L885: FunctionDef `write_rows`
- L897: FunctionDef `read_csv`
- L902: FunctionDef `ns`
- L25: FunctionDef `test_empty_batch_is_unresolved_and_does_not_emit_replay`
- L57: FunctionDef `test_explicit_run_search_root_resolves_stale_batch_paths`
- L78: FunctionDef `test_ambiguous_run_search_root_does_not_choose_a_candidate`
- L96: FunctionDef `test_m1_builds_evidence_outputs_without_content_payloads`
- L118: FunctionDef `test_m2_replays_multiple_optimizer_strategies_and_writes_audit`
- L171: FunctionDef `test_repeated_streaming_runs_have_matching_semantic_golden_outputs`
- L212: FunctionDef `test_schema_profile_overrides_p2_event_mapping`
- L243: FunctionDef `test_snapshot_timeline_uses_indexed_model_service_samples`
- L268: FunctionDef `test_lazy_step_index_replays_with_same_snapshot_semantics`
- L287: FunctionDef `test_incremental_snapshot_timeline_matches_reference_semantics`
- L321: FunctionDef `test_replay_strategy_instances_are_built_once_per_strategy`
- L338: FunctionDef `test_harness_uses_streaming_replay_not_materialized_snapshot_list`
- L368: FunctionDef `test_streaming_replay_matches_materialized_compatibility_outputs`
- L403: FunctionDef `test_disk_order_statistic_matches_exact_linear_percentile_with_duplicates`
- L415: FunctionDef `test_snapshot_stream_uses_external_static_timepoint_merge`
- L439: FunctionDef `test_streaming_replay_rejects_nonpositive_memory_budget`
- L458: FunctionDef `test_cli_returns_nonzero_when_replay_stage_fails`
- L483: FunctionDef `test_pipeline_validates_m3_outputs_before_writing_provenance`
- L503: FunctionDef `test_resume_reuses_completed_replay_only_when_input_identity_matches`
- L531: FunctionDef `test_resume_rejects_changed_strategy_identity`
- L556: FunctionDef `test_resume_rejects_corrupted_completed_artifact`
- L582: FunctionDef `test_conflicting_shared_service_samples_remain_partial`
- L614: FunctionDef `test_missing_step_and_event_data_remains_partial_or_unknown`
- L642: FunctionDef `test_partial_run_event_coverage_does_not_report_complete`
- L654: FunctionDef `test_cli_optimizer_experiment_entrypoint`

## agentmon/tests/unit/test_optimizer_qualification.py

Lines: 81; encoding: utf-8

- L16: FunctionDef `load_module`
- L24: ClassDef `OptimizerQualificationTests`
- L25: FunctionDef `test_parses_pretty_printed_final_harness_json_after_progress_output`
- L31: FunctionDef `test_nonqualified_manifest_only_records_diagnostic_hashes`
- L54: FunctionDef `test_target_environment_puts_verified_source_before_inherited_pythonpath`
- L62: FunctionDef `test_analysis_identity_must_match_full_gate_source_tree`

## agentmon/tests/unit/test_p1_adapters.py

Lines: 641; encoding: utf-8

- L28: ClassDef `FakeModel`
- L39: ClassDef `FakeHeaderModel`
- L45: ClassDef `FakeEnvironment`
- L50: ClassDef `BaseAgent`
- L71: ClassDef `Submitted`
- L83: ClassDef `SubmittedAgent`
- L89: ClassDef `FakeInstrumentedAgent`
- L93: ClassDef `FakeSubmittedInstrumentedAgent`
- L97: ClassDef `RetryAwareBaseModel`
- L130: ClassDef `RetryAwareInstrumentedModel`
- L134: ClassDef `WorkloadBaseAgent`
- L159: ClassDef `WorkloadInstrumentedAgent`
- L163: ClassDef `FakeMiniSweLocalEnvironment`
- L174: ClassDef `FakeMiniSweLocalEnvironmentWithFailingChecker`
- L180: ClassDef `FakeMiniSweLocalEnvironmentWithSubmittedChecker`
- L186: ClassDef `P1AdapterTests`
- L604: FunctionDef `write_runtime_control_config`
- L31: FunctionDef `__init__`
- L34: FunctionDef `query`
- L40: FunctionDef `query`
- L46: FunctionDef `execute`
- L54: FunctionDef `run`
- L57: FunctionDef `step`
- L61: FunctionDef `query`
- L64: FunctionDef `execute_actions`
- L67: FunctionDef `serialize`
- L72: FunctionDef `__init__`
- L84: FunctionDef `step`
- L100: FunctionDef `__init__`
- L103: FunctionDef `query`
- L112: FunctionDef `_query`
- L135: FunctionDef `__init__`
- L140: FunctionDef `step`
- L150: FunctionDef `execute_actions`
- L164: FunctionDef `__init__`
- L169: FunctionDef `_check_finished`
- L175: FunctionDef `_check_finished`
- L181: FunctionDef `_check_finished`
- L187: FunctionDef `make_sink`
- L196: FunctionDef `read_events`
- L203: FunctionDef `test_runtime_controlled_model_rejects_when_gate_is_full`
- L215: FunctionDef `test_runtime_controlled_local_environment_executes_through_tool_pool`
- L227: FunctionDef `test_instrumented_model_emits_span_and_injects_headers`
- L254: FunctionDef `test_header_injection_skips_var_keyword_only_model`
- L267: FunctionDef `test_model_workload_tracks_context_tool_results_and_transport_retries_without_content`
- L303: FunctionDef `test_model_workload_detects_structural_truncation_and_explicit_summarization_signal`
- L330: FunctionDef `test_instrumented_environment_decorator_does_not_store_action_content`
- L344: FunctionDef `test_instrumented_local_environment_records_pid_pgid_and_output_bytes`
- L372: FunctionDef `test_tool_process_observer_aggregates_process_group_memory_and_io`
- L410: FunctionDef `test_instrumented_local_environment_classifies_timeout`
- L426: FunctionDef `test_instrumented_local_environment_keeps_nonzero_as_tool_end`
- L447: FunctionDef `test_mini_swe_local_environment_passes_result_mapping_to_checker`
- L478: FunctionDef `test_mini_swe_local_environment_preserves_checker_exception`
- L497: FunctionDef `test_mini_swe_local_environment_treats_submitted_checker_as_control_flow`
- L519: FunctionDef `test_agent_mixin_emits_spans_and_adds_serialize_metadata`
- L541: FunctionDef `test_agent_step_records_semantic_phase_action_fanout_and_path_depth`
- L563: FunctionDef `test_agent_step_treats_submitted_exception_as_control_flow`
- L585: FunctionDef `test_nested_span_parent_id_uses_agent_step`
- L383: FunctionDef `fake_io`

## agentmon/tests/unit/test_p2_discovery.py

Lines: 289; encoding: utf-8

- L17: FunctionDef `load_discovery_module`
- L25: ClassDef `P2DiscoveryTests`
- L235: FunctionDef `write_fake_vllm`
- L276: FunctionDef `write_fake_vllm_distribution`
- L286: FunctionDef `clear_fake_vllm_modules`
- L26: FunctionDef `test_vllm_version_status_accepts_local_suffix_only_for_target_version`
- L34: FunctionDef `test_collect_snapshot_records_module_object_signature_and_source`
- L83: FunctionDef `test_cli_writes_json_and_strict_version_fails_on_mismatch`
- L120: FunctionDef `test_strict_version_uses_imported_module_version_over_distribution_metadata`
- L165: FunctionDef `test_metadata_local_suffix_is_source_evidence_not_compatibility_failure`
- L192: FunctionDef `test_cli_subprocess_strict_version_returns_nonzero_on_mismatch`
- L226: FunctionDef `test_default_discovery_targets_current_vllm_018_scheduler_path`

## agentmon/tests/unit/test_p2_vllm_adapter.py

Lines: 1966; encoding: utf-8

- L23: ClassDef `P2VllmAdapterTests`
- L1659: FunctionDef `build_report_from_events`
- L1673: FunctionDef `read_event_rows`
- L1677: FunctionDef `scheduler_output_for`
- L1690: FunctionDef `build_p2_kv_report`
- L1724: ClassDef `FakeCompletion`
- L1730: ClassDef `FakeRequestOutput`
- L1739: ClassDef `FakeRequest`
- L1752: ClassDef `FakeNewRequestData`
- L1759: ClassDef `FakeSchedulerOutput`
- L1773: ClassDef `FakePrefixStats`
- L1780: ClassDef `FakeSchedulerStats`
- L1791: ClassDef `FakeCacheConfig`
- L1799: ClassDef `FakeParallelConfig`
- L1803: ClassDef `FakeModelConfig`
- L1816: ClassDef `FakeVllmConfig`
- L1824: FunctionDef `install_fake_vllm_modules`
- L1964: FunctionDef `clear_fake_modules`
- L24: FunctionDef `test_deep_collection_defaults_on_and_can_be_explicitly_disabled`
- L40: FunctionDef `test_request_lifecycle_dedup_state_is_bounded`
- L50: FunctionDef `test_engine_core_step_paths_handle_none_empty_and_preserve_identity`
- L81: FunctionDef `test_engine_core_async_path_accumulates_tokens_across_engines_and_requests`
- L152: FunctionDef `test_engine_core_nested_wrappers_deduplicate_and_preserve_exceptions`
- L199: FunctionDef `test_engine_core_observation_failure_is_fail_open`
- L223: FunctionDef `test_final_only_api_output_does_not_masquerade_as_first_token`
- L281: FunctionDef `test_streaming_generation_tokens_accumulate_and_engine_count_is_authoritative`
- L339: FunctionDef `test_streaming_empty_terminal_chunk_does_not_replace_accumulated_tokens`
- L373: FunctionDef `test_report_preserves_cross_process_engine_generation_token_authority`
- L409: FunctionDef `test_async_generate_wrapper_reads_delta_output_kind_and_cleans_state`
- L458: FunctionDef `test_runtime_capability_reports_async_hook_and_prefix_cache_failures`
- L493: FunctionDef `test_install_capability_is_partial_when_async_method_exists_but_hook_fails`
- L524: FunctionDef `test_disabled_prefix_cache_expectation_detects_runtime_cache_hit`
- L555: FunctionDef `test_version_gate_accepts_only_vllm_018_base_version`
- L561: FunctionDef `test_api_finished_time_is_not_inferred_as_engine_decode_end`
- L583: FunctionDef `test_kv_pool_formula_preserves_inputs_and_bytes_per_rank`
- L601: FunctionDef `test_p2_sink_emits_request_mapping_events`
- L641: FunctionDef `test_report_summary_and_p0_sqlite_check_understand_p2_mapping_events`
- L687: FunctionDef `test_report_exposes_stable_request_ids_and_client_server_audit`
- L812: FunctionDef `test_report_and_summary_compute_p2_request_latencies_and_tokens`
- L909: FunctionDef `test_report_summary_and_aggregate_include_p2_kv_pool_states_and_npu_attribution`
- L978: FunctionDef `test_p2_prefix_cache_disabled_zero_hit_and_no_sample_semantics`
- L1008: FunctionDef `test_p2_prefix_cache_conflict_when_expected_disabled_but_snapshot_enabled`
- L1042: FunctionDef `test_request_lifecycle_keeps_engine_id_when_api_response_id_differs`
- L1091: FunctionDef `test_request_lifecycle_preserves_agentmon_header_context_across_engine_events`
- L1154: FunctionDef `test_request_id_aliases_do_not_cross_interleaved_requests`
- L1185: FunctionDef `test_request_id_aliases_clean_up_after_abort_and_generate_exception`
- L1249: FunctionDef `test_runtime_hook_disabled_env_has_no_events_or_state`
- L1269: FunctionDef `test_runtime_hook_patches_real_v1_targets_and_preserves_return_values`
- L1384: FunctionDef `test_runtime_observation_failures_never_change_original_results`
- L1431: FunctionDef `test_deep_capability_separates_required_and_optional_targets`
- L1460: FunctionDef `test_runtime_hook_optional_kv_block_hot_path_records_request_usage_and_blocks`
- L1505: FunctionDef `test_get_computed_blocks_records_cache_accounting_not_prefill_completion`
- L1537: FunctionDef `test_deep_hook_records_execution_anchored_prefill_scheduler_batch_and_kv_lifecycle`
- L1625: FunctionDef `test_runtime_hook_unsupported_version_does_not_patch`
- L1637: FunctionDef `test_runtime_hook_prefers_imported_module_version_over_metadata`
- L1725: FunctionDef `__init__`
- L1731: FunctionDef `__init__`
- L1740: FunctionDef `__init__`
- L1753: FunctionDef `__init__`
- L1760: FunctionDef `__init__`
- L1774: FunctionDef `__init__`
- L1781: FunctionDef `__init__`
- L1792: FunctionDef `__init__`
- L1806: FunctionDef `get_num_layers_by_block_type`
- L1809: FunctionDef `get_num_kv_heads`
- L1812: FunctionDef `get_head_size`
- L1817: FunctionDef `__init__`
- L1843: ClassDef `LLMEngine`
- L1852: ClassDef `EngineCore`
- L1874: ClassDef `AsyncLLM`
- L1884: ClassDef `Scheduler`
- L1920: ClassDef `KVCacheManager`
- L51: ClassDef `Core`
- L82: ClassDef `Core`
- L89: FunctionDef `output`
- L171: ClassDef `Core`
- L175: ClassDef `FailingCore`
- L202: ClassDef `Core`
- L282: ClassDef `Completion`
- L287: ClassDef `Output`
- L340: ClassDef `Completion`
- L345: ClassDef `Output`
- L410: ClassDef `Completion`
- L415: ClassDef `Output`
- L424: ClassDef `Engine`
- L497: FunctionDef `selective_patch`
- L1046: ClassDef `Engine`
- L1101: ClassDef `Engine`
- L1186: ClassDef `Aborter`
- L1190: ClassDef `ErrorEngine`
- L1385: ClassDef `HostileResult`
- L1390: ClassDef `HostileRequest`
- L1395: ClassDef `AsyncEngine`
- L1399: ClassDef `Core`
- L1403: ClassDef `KVManager`
- L1846: FunctionDef `add_request`
- L1849: FunctionDef `step`
- L1853: FunctionDef `add_request`
- L1856: FunctionDef `step`
- L1871: FunctionDef `step_with_batch_queue`
- L1875: AsyncFunctionDef `add_request`
- L1878: AsyncFunctionDef `generate`
- L1881: AsyncFunctionDef `abort`
- L1885: FunctionDef `__init__`
- L1895: FunctionDef `add_request`
- L1900: FunctionDef `schedule`
- L1910: FunctionDef `update_from_output`
- L1917: FunctionDef `make_stats`
- L1921: FunctionDef `__init__`
- L1927: FunctionDef `make_prefix_cache_stats`
- L1930: FunctionDef `get_computed_blocks`
- L1933: FunctionDef `allocate_slots`
- L1938: FunctionDef `cache_blocks`
- L1941: FunctionDef `get_block_ids`
- L1944: FunctionDef `free`
- L1948: FunctionDef `evict_blocks`
- L1951: FunctionDef `reset_prefix_cache`
- L52: FunctionDef `__init__`
- L55: FunctionDef `step`
- L58: FunctionDef `step_with_batch_queue`
- L83: FunctionDef `__init__`
- L86: FunctionDef `step_with_batch_queue`
- L172: FunctionDef `step_with_batch_queue`
- L176: FunctionDef `step_with_batch_queue`
- L203: FunctionDef `step_with_batch_queue`
- L283: FunctionDef `__init__`
- L288: FunctionDef `__init__`
- L341: FunctionDef `__init__`
- L346: FunctionDef `__init__`
- L411: FunctionDef `__init__`
- L416: FunctionDef `__init__`
- L425: AsyncFunctionDef `generate`
- L443: AsyncFunctionDef `consume`
- L1047: AsyncFunctionDef `add_request`
- L1050: AsyncFunctionDef `generate`
- L1102: AsyncFunctionDef `add_request`
- L1105: AsyncFunctionDef `generate`
- L1187: AsyncFunctionDef `abort`
- L1191: AsyncFunctionDef `add_request`
- L1194: AsyncFunctionDef `generate`
- L1387: FunctionDef `request_id`
- L1392: FunctionDef `request_id`
- L1396: AsyncFunctionDef `add_request`
- L1400: FunctionDef `add_request`
- L1405: FunctionDef `usage`
- L1408: FunctionDef `allocate_slots`
- L1064: AsyncFunctionDef `consume`
- L1121: AsyncFunctionDef `consume`
- L1217: AsyncFunctionDef `consume_error`

## agentmon/tests/unit/test_perf_summary.py

Lines: 243; encoding: utf-8

- L16: ClassDef `PerfSummaryTests`
- L223: FunctionDef `write_report`
- L227: FunctionDef `write_jsonl`
- L231: FunctionDef `p2_event`
- L17: FunctionDef `test_perf_summary_uses_canonical_proc_and_ascend_keys`
- L86: FunctionDef `test_perf_summary_without_target_devices_keeps_target_metrics_na`
- L119: FunctionDef `test_perf_summary_reads_service_scoped_p2_stream`
- L188: FunctionDef `test_perf_summary_p2_service_exclusive_can_backfill_agent_tokens`

## agentmon/tests/unit/test_pipeline.py

Lines: 80; encoding: utf-8

- L12: ClassDef `PipelineExecutorTests`
- L13: FunctionDef `test_capability_contract_allows_partial_evidence_and_blocks_missing_capability`
- L34: FunctionDef `test_resume_uses_artifact_hash_and_source_identity`
- L49: FunctionDef `test_pipeline_status_preserves_unresolved_outcome`
- L60: FunctionDef `test_stage_can_explicitly_accept_failed_dependency_for_diagnostics`

## agentmon/tests/unit/test_prefix_cache_experiment.py

Lines: 87; encoding: utf-8

- L13: ClassDef `PrefixCacheExperimentTests`
- L83: FunctionDef `run_cli`
- L14: FunctionDef `test_prefix_cache_hint_accepts_discovered_negative_option_without_fixed_spelling`
- L20: FunctionDef `test_prefix_cache_planner_outputs_non_mutating_json_plan`
- L52: FunctionDef `test_prefix_cache_planner_table_output`

## agentmon/tests/unit/test_procfs.py

Lines: 18; encoding: utf-8

- L6: ClassDef `ProcfsTests`
- L7: FunctionDef `test_parse_proc_stat_handles_comm_with_space_and_parens`

## agentmon/tests/unit/test_project_audit_regressions.py

Lines: 378; encoding: utf-8

- L35: ClassDef `BatchMeasurementTests`
- L92: ClassDef `AdapterSemanticsTests`
- L170: ClassDef `ExperimentGateTests`
- L246: ClassDef `AnalysisComparisonTests`
- L290: ClassDef `ToolLifecycleTests`
- L327: ClassDef `GeneratedShellGateTests`
- L36: FunctionDef `test_reanalysis_preserves_execution_evidence_across_repeated_runs`
- L77: FunctionDef `test_historical_batch_without_timing_does_not_invent_throughput`
- L93: FunctionDef `test_environment_preserves_config_and_per_call_directory_and_timeout`
- L113: FunctionDef `test_summary_accepts_text_but_main_query_keeps_action_validation`
- L171: FunctionDef `test_formal_rechecks_evidence_created_by_prior_stage`
- L187: FunctionDef `test_command_start_failure_and_timeout_write_failed_reports`
- L202: FunctionDef `test_preflight_failure_blocks_formal_and_ready_marker`
- L220: FunctionDef `test_generated_control_modes_load_as_runtime_config`
- L232: FunctionDef `test_required_coverage_needs_a_measured_denominator`
- L247: FunctionDef `test_identical_bundles_pair_tasks_by_concurrency_and_trial`
- L263: FunctionDef `test_ambiguous_trial_metadata_is_not_silently_overwritten`
- L268: FunctionDef `test_concurrency_quantiles_are_invariant_to_interval_splitting`
- L275: FunctionDef `test_workload_baseline_is_the_lowest_numeric_concurrency`
- L282: FunctionDef `test_workload_deltas_use_the_same_trial`
- L291: FunctionDef `test_advise_keeps_resource_plan_without_applying_it`
- L309: FunctionDef `test_timeout_captures_text_and_stops_descendants_before_releasing_capacity`
- L328: FunctionDef `test_service_gate_exits_on_first_success`
- L358: FunctionDef `test_mini_probe_requires_zero_exit_code`
- L94: ClassDef `Environment`
- L114: ClassDef `ActionModel`
- L136: ClassDef `MixedModel`
- L48: FunctionDef `workload`
- L95: FunctionDef `_check_finished`
- L115: FunctionDef `__init__`
- L119: FunctionDef `_query`
- L125: FunctionDef `_parse_actions`
- L130: FunctionDef `query`

## agentmon/tests/unit/test_prometheus.py

Lines: 23; encoding: utf-8

- L7: ClassDef `PrometheusTests`
- L8: FunctionDef `test_parser_handles_labels_escapes_histogram_and_nan`

## agentmon/tests/unit/test_refactor_boundaries.py

Lines: 198; encoding: utf-8

- L25: ClassDef `RefactorBoundaryTests`
- L26: FunctionDef `test_lightweight_analysis_import_does_not_load_pipeline_or_orchestration`
- L50: FunctionDef `test_experiment_evidence_import_does_not_load_orchestration`
- L73: FunctionDef `test_main_cli_parser_does_not_load_optimizer_harness_or_orchestration`
- L97: FunctionDef `test_analysis_stage_catalog_is_execution_source_of_truth`
- L108: FunctionDef `test_analysis_compatibility_facade_emits_shared_pipeline_result_contract`
- L128: FunctionDef `test_batch_policy_module_matches_runtime_flags`
- L158: FunctionDef `test_reporting_event_log_module_is_public_boundary`
- L170: FunctionDef `test_step_sequence_io_module_feeds_core_without_script_import`
- L182: FunctionDef `test_script_implementations_live_under_src_packages`

## agentmon/tests/unit/test_release_gate.py

Lines: 250; encoding: utf-8

- L22: FunctionDef `load_release_gate_module`
- L30: ClassDef `ReleaseGateTests`
- L241: FunctionDef `add_bytes`
- L31: FunctionDef `test_python_probe_rejects_interpreter_below_project_minimum`
- L60: FunctionDef `test_installed_source_must_match_package_files`
- L79: FunctionDef `test_dry_run_writes_test_manifest_with_planned_gates`
- L105: FunctionDef `test_archive_member_checks_reject_traversal_and_macos_metadata`
- L121: FunctionDef `test_dry_run_writes_provenance_and_delivery_checksum_manifest`
- L158: FunctionDef `test_quick_deadline_failure_marks_no_next_stage`
- L195: FunctionDef `test_previous_gate_requires_matching_package_sha256`
- L216: FunctionDef `test_embedded_manifest_rejects_unexpected_managed_source_file`

## agentmon/tests/unit/test_replay.py

Lines: 150; encoding: utf-8

- L11: ClassDef `ReplayCliTests`
- L112: FunctionDef `runtime_event`
- L124: FunctionDef `write_replay_config`
- L12: FunctionDef `test_optimizer_replay_uses_snapshot_file`
- L67: FunctionDef `test_runtime_replay_events_reconstructs_queue_state`

## agentmon/tests/unit/test_report_p1.py

Lines: 623; encoding: utf-8

- L23: ClassDef `P1ReportTests`
- L604: FunctionDef `proc_sample`
- L24: FunctionDef `test_write_reports_reuses_supplied_event_log`
- L45: FunctionDef `test_write_reports_emits_quality_bundle_and_blocks_invalid_json`
- L73: FunctionDef `test_default_report_p2_quality_gates_do_not_block_when_unconfigured`
- L118: FunctionDef `test_quality_bundle_blocks_abandoned_spans`
- L156: FunctionDef `test_mini_swe_trajectory_schema_extracts_response_usage_once`
- L183: FunctionDef `test_trajectory_prompt_completion_survive_missing_cached_tokens`
- L195: FunctionDef `test_event_log_skips_invalid_json_lines`
- L209: FunctionDef `test_report_marks_unclosed_span_abandoned_after_run_finish`
- L250: FunctionDef `test_event_log_compares_sqlite_against_p0_events_only`
- L361: FunctionDef `test_analysis_state_uses_post_run_trajectory_import_without_overwriting_finalization`
- L403: FunctionDef `test_trajectory_jsonl_only_classifier_requires_import_summary_evidence`
- L436: FunctionDef `test_trajectory_summary_written_by_run_sink_does_not_create_false_p0_mismatch`
- L489: FunctionDef `test_agent_summary_falls_back_to_p1_model_request_usage`
- L534: FunctionDef `test_agent_summary_marks_missing_token_source_and_reason`
- L557: FunctionDef `test_proc_summary_keeps_last_max_and_active_last_process_counts`
- L575: FunctionDef `test_ascend_summary_marks_unparsed_metrics_partial_not_zero_complete`

## agentmon/tests/unit/test_run_controller.py

Lines: 434; encoding: utf-8

- L14: ClassDef `RunControllerTests`
- L256: FunctionDef `run_agentmon_fake_child`
- L291: FunctionDef `write_fast_config`
- L322: FunctionDef `write_fake_mini_swe_child`
- L422: FunctionDef `latest_trajectory_sample`
- L15: FunctionDef `test_run_wraps_command_and_preserves_exit_code`
- L56: FunctionDef `test_run_can_opt_in_to_sqlite_cache_with_environment_override`
- L69: FunctionDef `test_run_cleans_child_pythonpath_and_expands_run_id_placeholder`
- L156: FunctionDef `test_run_imports_detected_trajectory_output_with_run_id_placeholder`
- L180: FunctionDef `test_run_imports_detected_absolute_output_path`
- L198: FunctionDef `test_run_marks_detected_missing_trajectory_without_failing_child`
- L232: FunctionDef `test_run_reports_p1_spans_and_imported_trajectory_together`

## agentmon/tests/unit/test_run_finalization.py

Lines: 358; encoding: utf-8

- L21: ClassDef `RunFinalizationTests`
- L160: FunctionDef `run_agentmon`
- L198: FunctionDef `write_config`
- L238: FunctionDef `write_delayed_trajectory_child`
- L280: FunctionDef `write_p1_trajectory_and_p2_child`
- L330: FunctionDef `p2_line`
- L344: FunctionDef `read_events`
- L352: FunctionDef `span_event`
- L22: FunctionDef `test_run_waits_for_delayed_trajectory_rename_before_report`
- L52: FunctionDef `test_run_imports_p2_byte_window_filtered_by_trajectory_response_id`
- L89: FunctionDef `test_sampler_final_event_is_in_report_snapshot`
- L140: FunctionDef `test_error_cancelled_and_timeout_events_close_spans`
- L90: ClassDef `FakeSampler`
- L93: FunctionDef `__init__`
- L96: FunctionDef `start`
- L99: FunctionDef `stop`

## agentmon/tests/unit/test_runtime.py

Lines: 411; encoding: utf-8

- L16: ClassDef `RuntimeCliTests`
- L372: FunctionDef `write_fast_config`
- L399: FunctionDef `claim_queue_record_worker`
- L17: FunctionDef `test_runtime_reexports_contract_budget_type`
- L22: FunctionDef `test_runtime_capabilities_reports_process_backend`
- L36: FunctionDef `test_runtime_submit_process_backend_and_status`
- L99: FunctionDef `test_runtime_submit_defers_when_scheduler_capacity_is_full`
- L130: FunctionDef `test_runtime_submit_queue_and_daemon_executes_later`
- L212: FunctionDef `test_runtime_daemon_defers_persisted_queue_when_capacity_is_full`
- L254: FunctionDef `test_runtime_daemon_loop_exits_after_idle_cycle`
- L280: FunctionDef `test_runtime_queue_heartbeat_extends_running_lease`
- L302: FunctionDef `test_runtime_recover_defers_expired_running_record`
- L342: FunctionDef `test_runtime_queue_claim_allows_only_one_process`

## agentmon/tests/unit/test_runtime_optimization_switch.py

Lines: 81; encoding: utf-8

- L14: ClassDef `RuntimeOptimizationSwitchTests`
- L15: FunctionDef `disabled_config`
- L29: FunctionDef `test_master_switch_bypasses_scheduler_optimizer_and_active_controls`
- L57: FunctionDef `test_mini_swe_model_wrapper_directly_bypasses_optimizer_when_disabled`
- L58: ClassDef `Model`
- L59: FunctionDef `__init__`
- L62: FunctionDef `query`

## agentmon/tests/unit/test_security.py

Lines: 11; encoding: utf-8

- L6: ClassDef `SecurityTests`
- L7: FunctionDef `test_command_hash_is_stable_and_salt_changes_digest`

## agentmon/tests/unit/test_server_preflight.py

Lines: 960; encoding: utf-8

- L19: FunctionDef `load_module`
- L27: ClassDef `ServerPreflightTests`
- L728: FunctionDef `complete_vllm_payload`
- L798: FunctionDef `p2_cached_event`
- L809: FunctionDef `p2_lifecycle_events`
- L837: FunctionDef `concurrent_api_results`
- L849: FunctionDef `api_result`
- L864: FunctionDef `clean_snapshot`
- L876: FunctionDef `release_lifecycle_events`
- L915: FunctionDef `release_event`
- L933: FunctionDef `scheduler_event`
- L946: FunctionDef `integrity_event`
- L28: FunctionDef `test_stream_parser_keeps_usage_from_final_empty_choices_chunk`
- L43: FunctionDef `test_stream_probe_requires_api_engine_and_finished_token_agreement`
- L65: FunctionDef `test_concurrency_probe_accepts_four_exact_request_mappings`
- L89: FunctionDef `test_concurrency_probe_rejects_one_api_id_mapping_to_multiple_engines`
- L118: FunctionDef `test_request_audit_rejects_missing_and_duplicate_lifecycle_events`
- L131: FunctionDef `test_stream_probe_rejects_duplicate_engine_finished`
- L152: FunctionDef `test_event_stream_integrity_detects_invalid_duplicate_and_sequence_regression`
- L174: FunctionDef `test_concurrency_probe_fails_when_requests_do_not_overlap`
- L194: FunctionDef `test_old_command_does_not_execute_active_runtime_probes`
- L213: FunctionDef `test_active_runtime_gate_runs_prefix_stream_and_concurrency_probes`
- L291: FunctionDef `test_strict_active_runtime_argument_failure_returns_json_and_nonzero`
- L318: FunctionDef `test_local_source_tree_and_agentmon_python_are_ready`
- L343: FunctionDef `test_build_report_preserves_symlinked_venv_python_path`
- L380: FunctionDef `test_mini_binary_is_resolved_next_to_supplied_symlinked_python`
- L414: FunctionDef `test_vllm_probe_preserves_cann_environment_and_appends_acl_paths`
- L466: FunctionDef `test_deep_probe_accepts_result_after_15_seconds_with_60_second_default`
- L493: FunctionDef `test_vllm_probe_timeout_is_explicit_and_does_not_invent_missing_capabilities`
- L540: FunctionDef `test_deep_mode_requires_actual_vllm_python`
- L557: FunctionDef `test_vllm_probe_audits_true_async_target_and_optional_deep_methods`
- L584: FunctionDef `test_vllm_probe_fails_when_async_path_hook_is_not_declared`
- L608: FunctionDef `test_prefix_cache_launch_validation_uses_discovered_cli_option`
- L636: FunctionDef `test_runtime_p2_check_fails_on_async_hook_missing_and_disabled_cache_hit`
- L674: FunctionDef `test_runtime_p2_check_accepts_hooked_async_path_and_zero_cached_tokens`
- L713: FunctionDef `test_events_path_checks_write_and_private_permissions`
- L420: FunctionDef `fake_run`
- L472: FunctionDef `fake_run`

## agentmon/tests/unit/test_server_quick_smoke.py

Lines: 264; encoding: utf-8

- L16: ClassDef `ServerQuickSmokeTests`
- L212: FunctionDef `write_profile`
- L251: FunctionDef `write_nested_benchmark`
- L17: FunctionDef `test_smoke_task_is_discovered_from_nested_runtime_archive`
- L28: FunctionDef `test_smoke_task_remains_unresolved_without_configuration_or_benchmark`
- L34: FunctionDef `test_prepare_synthesizes_self_contained_runtime_instance`
- L159: FunctionDef `test_prepare_keeps_unknown_prefix_cache_args_as_blocker`

## agentmon/tests/unit/test_server_test_config_runner.py

Lines: 133; encoding: utf-8

- L14: FunctionDef `load_runner_module`
- L22: ClassDef `ServerTestConfigRunnerTests`
- L23: FunctionDef `test_main_without_default_config_runs_builtin_self_check`
- L38: FunctionDef `test_missing_explicit_config_requires_init_without_creating_file`
- L47: FunctionDef `test_init_config_writes_template_with_optional_server_profile`
- L64: FunctionDef `test_server_profile_expands_stable_paths_and_dynamic_repo`
- L88: FunctionDef `test_offline_analysis_missing_input_needs_input_without_failure`
- L98: FunctionDef `test_preflight_deep_missing_vllm_python_needs_input`
- L108: FunctionDef `test_main_reports_partial_for_actions_waiting_on_input`

## agentmon/tests/unit/test_smoke_check.py

Lines: 86; encoding: utf-8

- L17: FunctionDef `load_smoke_module`
- L25: ClassDef `SmokeCheckTests`
- L82: FunctionDef `run_script`
- L26: FunctionDef `test_smoke_check_allows_nonzero_commands_as_metric_only`
- L43: FunctionDef `test_smoke_check_fails_on_final_test_rc_not_error_suffix`
- L58: FunctionDef `test_smoke_check_report_fallback_populates_quality_criteria`

## agentmon/tests/unit/test_source_package.py

Lines: 89; encoding: utf-8

- L15: FunctionDef `load_package_module`
- L23: ClassDef `SourcePackageTests`
- L63: FunctionDef `write_fixture_repo`
- L24: FunctionDef `test_source_package_excludes_macos_metadata_and_bytecode`

## agentmon/tests/unit/test_step_csv_analyzer.py

Lines: 454; encoding: utf-8

- L16: FunctionDef `load_module`
- L24: ClassDef `StepCsvAnalyzerTests`
- L268: FunctionDef `write_step_csv`
- L407: FunctionDef `write_rows`
- L415: FunctionDef `write_low_coverage_csv`
- L25: FunctionDef `test_analyzer_classifies_model_tool_queue_and_quality`
- L53: FunctionDef `test_analyzer_writes_derived_outputs`
- L75: FunctionDef `test_analyzer_does_not_make_strong_diagnosis_from_low_coverage_fields`
- L93: FunctionDef `test_missing_metrics_remain_null_instead_of_zero`
- L123: FunctionDef `test_analyzer_separates_explicit_prefill_and_shared_scheduler_pressure`
- L174: FunctionDef `test_strict_returns_nonzero_for_low_p2_match_rate`

## agentmon/tests/unit/test_step_sequence.py

Lines: 994; encoding: utf-8

- L18: FunctionDef `load_module`
- L26: ClassDef `StepSequenceTests`
- L413: FunctionDef `write_events`
- L417: FunctionDef `event`
- L439: FunctionDef `ns`
- L443: FunctionDef `epoch_ns`
- L447: FunctionDef `write_trajectory`
- L482: FunctionDef `trajectory_step`
- L507: FunctionDef `kv_snapshot_event`
- L521: FunctionDef `p2_request_events`
- L615: FunctionDef `p2_clock`
- L623: FunctionDef `sequence_events`
- L947: FunctionDef `proc_event`
- L959: FunctionDef `proc_sample`
- L984: FunctionDef `ascend_event`
- L27: FunctionDef `test_step_sequence_projects_request_attributed_moe_routing_summary`
- L65: FunctionDef `test_step_sequence_reuses_supplied_event_logs`
- L82: FunctionDef `test_step_sequence_correlates_model_tool_and_p2`
- L197: FunctionDef `test_step_sequence_outputs_stable_request_ids_and_p2_identity_audit`
- L236: FunctionDef `test_step_sequence_falls_back_to_trajectory_and_p2_by_response_id`
- L293: FunctionDef `test_step_sequence_outputs_extra_p2_request_without_trajectory_step`
- L319: FunctionDef `test_duplicate_api_id_is_reported_as_multiple_without_cross_attribution`
- L346: FunctionDef `test_step_sequence_uses_ordinal_fallback_only_when_response_id_missing`
- L365: FunctionDef `test_trajectory_fallback_preserves_explicit_timeout_classification`
- L393: FunctionDef `test_trajectory_without_p2_keeps_missing_durations_null`

## agentmon/tests/unit/test_strategy_registry.py

Lines: 151; encoding: utf-8

- L20: ClassDef `StrategyRegistryTests`
- L119: FunctionDef `write_strategy_config`
- L21: FunctionDef `test_strategy_list_reports_builtin_strategies`
- L38: FunctionDef `test_custom_scheduler_strategy_can_be_registered_and_selected`
- L82: FunctionDef `test_custom_optimizer_strategy_can_be_registered_and_selected`
- L39: ClassDef `AlwaysDeferScheduler`
- L83: ClassDef `AlwaysHighOptimizer`
- L42: FunctionDef `__init__`
- L45: FunctionDef `decide`
- L55: FunctionDef `snapshot`
- L86: FunctionDef `__init__`
- L89: FunctionDef `evaluate`

## agentmon/tests/unit/test_summary.py

Lines: 288; encoding: utf-8

- L12: ClassDef `SummaryTests`
- L176: FunctionDef `run_cli`
- L183: FunctionDef `write_report`
- L190: FunctionDef `make_report`
- L13: FunctionDef `test_success_summary_keeps_prefix_disabled_as_na`
- L38: FunctionDef `test_failed_run_and_integrity_issues_are_strict_failures`
- L70: FunctionDef `test_null_npu_metrics_remain_null`
- L89: FunctionDef `test_aggregate_uses_percentiles_and_coverage`
- L113: FunctionDef `test_percentile_empty_returns_none`
- L116: FunctionDef `test_summary_cli_outputs_json_csv_table_and_strict_exit`
- L137: FunctionDef `test_aggregate_cli_handles_empty_dir_and_missing_report`
- L160: FunctionDef `test_aggregate_cli_csv_table_and_strict`

## agentmon/tests/unit/test_task_state.py

Lines: 156; encoding: utf-8

- L14: ClassDef `TaskStateTests`
- L127: FunctionDef `event`
- L137: FunctionDef `write_config`
- L15: FunctionDef `test_task_state_detects_low_progress_context_and_repeated_tools`
- L58: FunctionDef `test_task_state_keeps_missing_prediction_unknown`
- L65: FunctionDef `test_tool_result_fingerprint_is_content_addressed_without_content_storage`
- L77: FunctionDef `test_runtime_submit_writes_and_reads_task_state`

## agentmon/tests/unit/test_timeline.py

Lines: 165; encoding: utf-8

- L15: ClassDef `TimelineTests`
- L60: FunctionDef `make_batch`
- L122: FunctionDef `item`
- L148: FunctionDef `write_rows`
- L155: FunctionDef `read_csv`
- L160: FunctionDef `ns`
- L16: FunctionDef `test_generates_batch_timeline_and_integral_checks`
- L48: FunctionDef `test_strict_cli_fails_when_clock_domains_are_mixed`

## agentmon/tests/unit/test_v41_profile.py

Lines: 51; encoding: utf-8

- L13: ClassDef `V41ProfileTests`
- L14: FunctionDef `test_target_config_is_independent_and_conservative`
- L27: FunctionDef `test_header_promotion_does_not_mutate_profile_or_add_thinking`
- L36: FunctionDef `test_existing_header_injector_accepts_opt_in_profile`
- L45: FunctionDef `test_no_p2_gate_bypass`
- L37: FunctionDef `query`

## agentmon/tests/unit/test_workload_aware_runtime.py

Lines: 135; encoding: utf-8

- L12: ClassDef `WorkloadAwareRuntimeTests`
- L81: FunctionDef `write_run_manifest`
- L98: FunctionDef `write_config`
- L13: FunctionDef `test_workload_aware_scheduler_defers_when_configured_prompt_budget_is_exceeded`
- L42: FunctionDef `test_workload_aware_scheduler_keeps_unknown_prediction_as_admissible_under_base_capacity`
- L55: FunctionDef `test_workload_aware_optimizer_reports_task_state_budget_recommendations`

## AGENTS.md

Lines: 12; encoding: utf-8

- # 工作区约定

## docs/acceptance.md

Lines: 31; encoding: utf-8

- # 迁移验收 — 2026-09-24
- ## 独立解压验收

## docs/architecture.md

Lines: 20; encoding: utf-8

- # 架构

## docs/migration_report.md

Lines: 30; encoding: utf-8

- # 迁移报告 — 2026-09-24
- ## 来源与方式
- ## 新增顶层内容
- ## 排除
- ## 后续入口

## docs/project_status.md

Lines: 32; encoding: utf-8

- # 项目状态
- ## 已实现
- ## 本地验收
- ## 用户历史服务器报告（本次未重跑）
- ## 待真实验收/未实现

## integration/README.md

Lines: 6; encoding: utf-8

- # 联合验收

## kv_ana/docs/AGENTMON_KVREUSE_COMPLETE_GUIDE_ZH.md

Lines: 336; encoding: utf-8

- # AgentMon × KVReuse 完整源码说明与使用指南
- ## 1. 项目目标、版本与边界
- ## 2. 总体关系
- ## 3. 源码包结构
- ## 4. AgentMon 模块说明
- ## 5. KVReuse 模块说明
- ## 6. 核心数据与数值口径
- ## 7. 环境与解压
- ## 8. 第一轮：纯离线验收
- ## 9. 真实状态：CPU adapter 与 pipeline
- ## 10. Observability 与 forensic
- ## 11. LifeMem 与异构状态离线研究
- ## 12. V4.1 客户端：调用链与使用
- ## 13. AgentMon 正式任务与 batch
- ## 14. 证据、隐私与失败排查
- ## 15. 历史 runtime 工具与尚未完成事项
- ## 16. 维护与重新打包

## kv_ana/docs/AGENTMON_V41_JOINT_ACCEPTANCE.md

Lines: 76; encoding: utf-8

- # AgentMon × kv_ana: V4.1 joint acceptance v1
- ## Scope
- ## Acceptance boundaries
- ## Request evidence
- ## Data needed next (not collected by this extension)
- ## Accounting and measurement
- ## Server sequence

## kv_ana/docs/algorithm_specs.md

Lines: 77; encoding: utf-8

- # Algorithm specifications — v2.0.1
- ## State and evaluation contract
- ## Implemented corrections
- ## Fit and grouping rules
- ## Position correction
- ## Metrics and invariants
- ## Definition decisions and open ambiguities

## kv_ana/docs/COMPLETE_SOURCE_BUNDLE_VALIDATION.md

Lines: 43; encoding: utf-8

- # 完整联合源码包验收记录 — 2026-09-23
- ## 本次改动
- ## 解压目录验收
- ## 归档边界

## kv_ana/docs/experiment_output_schema.md

Lines: 61; encoding: utf-8

- # Experiment output schema — v2.0.1

## kv_ana/docs/HETEROGENEOUS_STATE_BATCH1_PLAN.md

Lines: 61; encoding: utf-8

- # 第一批：LifeMem 上下文与异构状态研究数据底座 v1
- ## 目标和验收边界
- ## 对研究目标的贡献
- ## 工作包
- ## 关键定义
- ## 本批不包含的工作
- ## 运行接口

## kv_ana/docs/KVREUSE_OBSERVABILITY_V1.md

Lines: 207; encoding: utf-8

- # KVReuse Observability v1
- ## Trace levels
- ## Actions
- ## Sources
- ## Cost accounting
- ## Instrumented algorithms
- ## Tensor boundary
- ## Future AgentMon correlation
- ## CPU server smoke
- ## v1.0.1 forensic semantics
- ### Strict summary/detail boundary
- ### Selector degeneration
- ### Correction attribution
- ### Forensic ablations
- ### CacheSlide forensic variants
- ### Compact forensic summary

## kv_ana/docs/LIFEMEM_STATE_RESEARCH_V1.md

Lines: 225; encoding: utf-8

- # LifeMem 异构状态研究数据底座 v1：使用与数据契约
- ## 1. 现在能够完成什么
- ## 2. 最快运行
- ## 3. 原始数据与可选 sidecar
- ## 4. token 对齐
- ## 5. 变化映射与请求顺序
- ## 6. AgentMon 关联与状态清单
- ## 7. 输出与下一批

## kv_ana/docs/method_coverage.md

Lines: 28; encoding: utf-8

- # Literature coverage map

## kv_ana/docs/recipe_readiness_v1.md

Lines: 34; encoding: utf-8

- # Paper Recipe readiness v1
- ## Contract boundary
- ## Representative components
- ## Declarative 20-paper status
- ## Provider work before server integration
- ## Core audit

## kv_ana/docs/server_adapter_contract.md

Lines: 42; encoding: utf-8

- # Future server adapter contract — v2.0.1

## kv_ana/docs/STATE_RESEARCH_AUDIT_V1_0_1.md

Lines: 79; encoding: utf-8

- # 状态研究方案与实现复核 v1.0.1
- ## 结论
- ## 可复现问题与修复
- ## 数值影响边界
- ### 冻结核心最小例外
- ## 与立项稿三个技术点的真实关系
- ## 仍需真实环境的事项
- ## 验证记录

## kv_ana/docs/STATE_RESEARCH_BATCH1_VALIDATION.md

Lines: 63; encoding: utf-8

- # 第一批实施验收
- ## 结果
- ## 可查看的本次产物
- ## 专项覆盖
- ## 已知边界

## kv_ana/docs/STATE_RESEARCH_OFFLINE_PHASES_2_3.md

Lines: 187; encoding: utf-8

- # 第二、第三阶段：离线实现与逐步验收
- ## 完成范围与验收边界
- ## 代码与接口
- ## 先本地验收，再替换真实输入
- # 第一批数据准备器（可单独验收）
- # 第二/三阶段：会生成 inputs/config.json 和所有 synthetic 输入文件
- # 同一批输入重新运行，验证不是 smoke 内置结果
- # 真实资料尚未填入时，应产生 PARTIAL 而非通过
- ## 事件契约
- ## 三个策略 baseline 的可比性
- ## 恢复与状态比较
- ## 输出、故障与 AgentMon
- ## 后续逐步验收，不必一次启动全部系统

## kv_ana/docs/STATE_RESEARCH_OFFLINE_VALIDATION.md

Lines: 93; encoding: utf-8

- # 离线第二、第三阶段交付检查
- ## v1 历史验证结果
- ## 验证产物
- ## 专项覆盖
- ## 分阶段完成状态
- ## v1.0.1 整体审计修复

## kv_ana/docs/V41_JOINT_VALIDATION.md

Lines: 64; encoding: utf-8

- # V4.1 joint-client validation — 2026-09-23
- ## Results
- ## Wire acceptance checks
- ## Change boundary
- ## Remaining acceptance

## kv_ana/README.md

Lines: 39; encoding: utf-8

- # kvreuse_b5 v2.0.1
- ## Heterogeneous-state research extension

## kv_ana/REAL_OFFLINE_PIPELINE.md

Lines: 78; encoding: utf-8

- # REAL-OFFLINE-PIPELINE v1
- ## What runs
- ## Server installation
- ## Smoke command
- ## Full command
- ## Interpreting status
- ## Leakage boundary

## kv_ana/runtime_intervention/dsv4_precheck_v1/dsv4_state_intervention_v1.py

Lines: 352; encoding: utf-8

Fail-closed DeepSeek-V4 compressed-state intervention helper.

- L26: ClassDef `InterventionError`
- L30: FunctionDef `_read_tag`
- L40: FunctionDef `_read_spec`
- L56: FunctionDef `_extract_layer_id`
- L65: FunctionDef `_shape`
- L79: FunctionDef `_to_list`
- L89: FunctionDef `_flatten_numbers`
- L97: FunctionDef `_norm`
- L105: FunctionDef `_select_rows`
- L112: FunctionDef `_subtract`
- L120: FunctionDef `_assign_rows`
- L129: FunctionDef `_zero_rows`
- L138: FunctionDef `_validate_rows`
- L150: FunctionDef `_default_artifact_loader`
- L158: FunctionDef `_append_trace`
- L171: FunctionDef `_base_record`
- L199: FunctionDef `apply_runtime_intervention`

## kv_ana/runtime_intervention/dsv4_precheck_v1/patch_runtime.py

Lines: 147; encoding: utf-8

Install or restore the guarded DeepSeek-V4 intervention hook.

- L25: FunctionDef `sha256`
- L33: FunctionDef `paths`
- L40: FunctionDef `apply_patch`
- L99: FunctionDef `restore_patch`
- L127: FunctionDef `main`

## kv_ana/runtime_intervention/dsv4_precheck_v1/README.md

Lines: 179; encoding: utf-8

- # DeepSeek-V4 Runtime State Intervention Precheck v1
- ## Exact hook
- ## Install and restore
- ## Server start contract
- ### E0: unpatched baseline and capture
- ### E1: identity
- ### E2: zero selected rows
- ### E3: external same-input replay
- ## Fail-closed matching and trace
- ## Known risks and limits

## kv_ana/runtime_intervention/dsv4_precheck_v1/server_precheck_runner.py

Lines: 334; encoding: utf-8

Single-request DeepSeek-V4 state-intervention precheck runner.

- L31: FunctionDef `sha256`
- L39: FunctionDef `parse_rows`
- L46: FunctionDef `load_artifact`
- L57: FunctionDef `prepare`
- L103: FunctionDef `request_payload`
- L123: FunctionDef `send_request`
- L157: FunctionDef `trace_records`
- L165: FunctionDef `canonical_response`
- L169: FunctionDef `read_baseline`
- L175: FunctionDef `run`
- L262: FunctionDef `summarize`
- L290: FunctionDef `parser`
- L313: FunctionDef `main`

## kv_ana/runtime_references/dsv4_runtime_official_reference_v0221/official/vllm/vllm/models/deepseek_v4/compressor.py

Lines: 380; encoding: utf-8

- L37: ClassDef `CompressorBackend`
- L78: ClassDef `CompressorMetadata`
- L86: ClassDef `CompressorMetadataBuilder`
- L121: ClassDef `CompressorStateCache`
- L173: ClassDef `DeepseekCompressor`
- L38: FunctionDef `__init__`
- L42: FunctionDef `get_name`
- L46: FunctionDef `get_supported_kernel_block_sizes`
- L50: FunctionDef `get_supported_head_sizes`
- L54: FunctionDef `get_builder_cls`
- L58: FunctionDef `get_kv_cache_shape`
- L69: FunctionDef `get_kv_cache_stride_order`
- L89: FunctionDef `__init__`
- L101: FunctionDef `build`
- L122: FunctionDef `__init__`
- L157: FunctionDef `get_kv_cache_spec`
- L167: FunctionDef `forward`
- L169: FunctionDef `get_attn_backend`
- L184: FunctionDef `__init__`
- L270: FunctionDef `forward`

## kv_ana/runtime_references/dsv4_runtime_official_reference_v0221/official/vllm-ascend/vllm_ascend/attention/dsa_v1.py

Lines: 2727; encoding: utf-8

- L50: FunctionDef `dsv4_dsa_overlap_stream`
- L60: FunctionDef `hadamard_transform_ref`
- L77: FunctionDef `rotate_activation`
- L82: FunctionDef `hadamard_linear`
- L101: FunctionDef `hadamard_scale`
- L110: FunctionDef `_is_w8a8_dynamic`
- L119: FunctionDef `pad_to_blocks`
- L179: ClassDef `AscendDSABackend`
- L223: ClassDef `AscendDSAPrefillMetadata`
- L249: ClassDef `AscendDSADecodeMetadata`
- L276: ClassDef `AscendDSAMetadata`
- L332: FunctionDef `_require_prefill_metadata`
- L337: FunctionDef `_require_decode_metadata`
- L342: ClassDef `AscendDSAMetadataBuilder`
- L1377: ClassDef `AscendDSAImpl`
- L183: FunctionDef `get_name`
- L190: FunctionDef `get_builder_cls`
- L200: FunctionDef `get_kv_cache_shape`
- L204: FunctionDef `get_scale_shape`
- L208: FunctionDef `get_impl_cls`
- L218: FunctionDef `get_supported_kernel_block_sizes`
- L314: FunctionDef `__post_init__`
- L358: FunctionDef `__init__`
- L443: FunctionDef `get_cudagraph_support`
- L452: FunctionDef `reorder_batch`
- L499: FunctionDef `set_num_actual_tokens`
- L505: FunctionDef `build`
- L603: FunctionDef `build_prefill_metadata`
- L861: FunctionDef `build_decode_metadata`
- L1123: FunctionDef `build_for_drafting`
- L1188: FunctionDef `build_prefill_metadata_for_drafting`
- L1260: FunctionDef `build_decode_metadata_for_drafting`
- L1345: FunctionDef `get_block_table_size`
- L1355: FunctionDef `build_for_graph_capture`
- L1383: FunctionDef `__init__`
- L1484: FunctionDef `_get_indexcache_topk_indices`
- L1492: FunctionDef `_update_indexcache_topk_indices`
- L1504: FunctionDef `process_weights_after_loading`
- L1508: FunctionDef `rope_single`
- L1526: FunctionDef `forward`
- L1627: FunctionDef `_mla_prolog_multistream`
- L1724: FunctionDef `_forward_prefill`
- L2031: FunctionDef `_forward_decode`
- L2339: FunctionDef `_indexer_qkv_prepare`
- L2440: FunctionDef `_indexer_qli_finish`
- L2471: FunctionDef `_indexer_quant_scatter`
- L2490: FunctionDef `_indexer_qli`
- L2536: FunctionDef `indexer_select_qli`
- L2569: FunctionDef `cv_indexer_select_qli`
- L657: FunctionDef `_get_padded_compressed_position`
- L670: FunctionDef `_get_cmp_seq_lens`
- L677: FunctionDef `_get_compressed_decode_token_start_and_end`
- L921: FunctionDef `_get_padded_compressed_position`
- L949: FunctionDef `_get_compressed_decode_token_start`

## kv_ana/runtime_references/dsv4_runtime_official_reference_v0221/official/vllm-ascend/vllm_ascend/models/deepseek_v4.py

Lines: 1511; encoding: utf-8

- L88: FunctionDef `_get_ascend_dsa_backend`
- L95: FunctionDef `_dsv4_block_sizes`
- L103: ClassDef `AscendCompressorStateCache`
- L136: ClassDef `AscendDeepseekV4IndexerCache`
- L172: ClassDef `AscendDeepseekV4SWACache`
- L208: FunctionDef `hadamard_transform_ref`
- L225: FunctionDef `rotate_activation`
- L230: FunctionDef `precompute_freqs_cis_cpu`
- L268: FunctionDef `apply_rotary_emb`
- L292: FunctionDef `get_spec_layer_idx_from_weight_name`
- L298: ClassDef `DeepseekV2MLP`
- L344: ClassDef `DeepseekV4MoE`
- L505: FunctionDef `yarn_get_mscale`
- L513: FunctionDef `_get_llama_4_scaling`
- L521: ClassDef `Indexer`
- L588: ClassDef `Compressor`
- L701: ClassDef `DeepseekV4Attention`
- L903: ClassDef `DeepseekV2DecoderLayer`
- L997: ClassDef `DeepseekV4Model`
- L1162: ClassDef `DeepseekV2MixtureOfExperts`
- L1202: ClassDef `AscendDeepseekV4ForCausalLM`
- L104: FunctionDef `__init__`
- L116: FunctionDef `get_kv_cache_spec`
- L130: FunctionDef `forward`
- L132: FunctionDef `get_attn_backend`
- L137: FunctionDef `__init__`
- L147: FunctionDef `get_kv_cache_spec`
- L166: FunctionDef `forward`
- L168: FunctionDef `get_attn_backend`
- L173: FunctionDef `__init__`
- L186: FunctionDef `get_kv_cache_spec`
- L202: FunctionDef `forward`
- L204: FunctionDef `get_attn_backend`
- L241: FunctionDef `find_correction_dim`
- L244: FunctionDef `find_correction_range`
- L249: FunctionDef `linear_ramp_factor`
- L299: FunctionDef `__init__`
- L337: FunctionDef `forward`
- L345: FunctionDef `__init__`
- L452: FunctionDef `forward`
- L522: FunctionDef `__init__`
- L584: FunctionDef `forward`
- L589: FunctionDef `__init__`
- L658: FunctionDef `overlap_transform`
- L666: FunctionDef `forward`
- L675: FunctionDef `rope_single`
- L702: FunctionDef `__init__`
- L894: FunctionDef `forward`
- L904: FunctionDef `__init__`
- L962: FunctionDef `hc_pre`
- L968: FunctionDef `hc_post`
- L974: FunctionDef `forward`
- L1000: FunctionDef `__init__`
- L1081: FunctionDef `embed_input_ids`
- L1084: FunctionDef `hc_head`
- L1093: FunctionDef `forward`
- L1168: FunctionDef `extract_moe_parameters`
- L1186: FunctionDef `update_physical_experts_metadata`
- L1208: FunctionDef `__init__`
- L1231: FunctionDef `set_moe_parameters`
- L1252: FunctionDef `embed_input_ids`
- L1255: FunctionDef `forward`
- L1265: FunctionDef `compute_logits`
- L1272: FunctionDef `get_expert_mapping`
- L1285: FunctionDef `get_mtp_target_hidden_states`
- L1291: FunctionDef `load_weights`
- L1045: FunctionDef `make_empty_intermediate_tensors`

## kv_ana/scripts/build_complete_source_bundle.py

Lines: 102; encoding: utf-8

Bundle both current source trees, documentation and offline evidence specs.

- L19: FunctionDef `collect`
- L35: FunctionDef `main`

## kv_ana/scripts/build_state_research_bundle.py

Lines: 53; encoding: utf-8

Portable CPU tools bundle; includes one read-only runtime source used by evidence tests.

- L13: FunctionDef `main`

## kv_ana/scripts/build_v41_joint_bundle.py

Lines: 62; encoding: utf-8

Package independent source trees; no dependencies, weights or raw data.

- L13: FunctionDef `main`

## kv_ana/scripts/prepare_state_research.py

Lines: 10; encoding: utf-8

Standalone entrypoint; use the project's existing Python environment.


## kv_ana/scripts/real_adapter_smoke.py

Lines: 54; encoding: utf-8

- L16: FunctionDef `build_parser`
- L28: FunctionDef `main`

## kv_ana/scripts/real_observability_smoke.py

Lines: 231; encoding: utf-8

- L24: FunctionDef `_allow_explicit_target`
- L32: FunctionDef `_stack`
- L36: FunctionDef `_context`
- L50: FunctionDef `build_parser`
- L60: FunctionDef `main`

## kv_ana/scripts/run_real_offline_pipeline.py

Lines: 50; encoding: utf-8

- L15: FunctionDef `build_parser`
- L31: FunctionDef `main`

## kv_ana/scripts/run_real_offline_smoke.py

Lines: 14; encoding: utf-8


## kv_ana/scripts/run_state_research_offline.py

Lines: 9; encoding: utf-8


## kv_ana/scripts/state_research_offline_smoke.py

Lines: 50; encoding: utf-8

- L15: FunctionDef `main`

## kv_ana/scripts/state_research_smoke.py

Lines: 53; encoding: utf-8

- L16: FunctionDef `main`

## kv_ana/scripts/validate_v41_joint_client.py

Lines: 165; encoding: utf-8

Offline-only: real client serialization, synthetic HTTP responses, no sockets.

- L19: FunctionDef `main`
- L63: FunctionDef `intercept`

## kv_ana/src/kvreuse_b5/__init__.py

Lines: 9; encoding: utf-8

Offline algorithms for studying non-prefix KV/state reuse.


## kv_ana/src/kvreuse_b5/adapters/__init__.py

Lines: 18; encoding: utf-8


## kv_ana/src/kvreuse_b5/adapters/common.py

Lines: 27; encoding: utf-8

- L7: FunctionDef `load_torch_payload`
- L24: FunctionDef `require_keys`

## kv_ana/src/kvreuse_b5/adapters/deepseek_c4.py

Lines: 241; encoding: utf-8

- L20: ClassDef `StrictMappingRow`
- L27: FunctionDef `_canonical_pair_id`
- L37: ClassDef `DeepSeekPrefixC4Adapter`
- L38: FunctionDef `__init__`
- L53: FunctionDef `files_for`
- L59: FunctionDef `_integer_tuple_1d`
- L68: FunctionDef `_mapping_rows`
- L97: FunctionDef `_c4_layers`
- L164: FunctionDef `load_pair`

## kv_ana/src/kvreuse_b5/adapters/qwen_rawkv.py

Lines: 205; encoding: utf-8

- L21: ClassDef `QwenRawKVSchema`
- L39: FunctionDef `flatten_qwen_state`
- L59: ClassDef `QwenRawKVAdapter`
- L29: FunctionDef `feature_dim`
- L60: FunctionDef `__init__`
- L73: FunctionDef `file_for`
- L78: FunctionDef `load_pair`

## kv_ana/src/kvreuse_b5/cli/__init__.py

Lines: 2; encoding: utf-8

Command-line entry points.


## kv_ana/src/kvreuse_b5/cli/recipes.py

Lines: 45; encoding: utf-8

- L12: FunctionDef `_jsonable`
- L20: FunctionDef `main`

## kv_ana/src/kvreuse_b5/cli/run.py

Lines: 27; encoding: utf-8

- L8: FunctionDef `main`

## kv_ana/src/kvreuse_b5/cli/smoke.py

Lines: 35; encoding: utf-8

- L13: FunctionDef `main`

## kv_ana/src/kvreuse_b5/data/__init__.py

Lines: 5; encoding: utf-8


## kv_ana/src/kvreuse_b5/data/adapters/__init__.py

Lines: 4; encoding: utf-8


## kv_ana/src/kvreuse_b5/data/adapters/base.py

Lines: 15; encoding: utf-8

- L9: ClassDef `StatePairAdapter`
- L13: FunctionDef `iter_pairs`

## kv_ana/src/kvreuse_b5/data/dataset.py

Lines: 50; encoding: utf-8

- L10: ClassDef `StatePairDataset`
- L13: FunctionDef `__post_init__`
- L40: FunctionDef `__iter__`
- L43: FunctionDef `split`
- L49: FunctionDef `from_iterable`

## kv_ana/src/kvreuse_b5/data/synthetic.py

Lines: 238; encoding: utf-8

- L11: FunctionDef `_pair`
- L39: FunctionDef `zero_residual_fixture`
- L43: FunctionDef `constant_residual_fixture`
- L48: FunctionDef `rank4_fixture`
- L55: FunctionDef `shared_subspace_fixture`
- L66: FunctionDef `concentrated_energy_fixture`
- L72: FunctionDef `static_joint_fixture`
- L92: FunctionDef `detector_fixture`
- L112: FunctionDef `anchor_fixture`
- L140: FunctionDef `rope_fixture`
- L159: FunctionDef `probe_fixture`
- L178: FunctionDef `smoke_dataset`
- L227: FunctionDef `all_fixture_pairs`

## kv_ana/src/kvreuse_b5/data/types.py

Lines: 109; encoding: utf-8

- L17: FunctionDef `as_numpy`
- L29: FunctionDef `stable_matmul`
- L37: ClassDef `StatePair`
- L49: FunctionDef `__post_init__`
- L73: FunctionDef `residual`
- L77: FunctionDef `total_units`
- L81: FunctionDef `feature_dim`
- L85: FunctionDef `fit_key`
- L89: FunctionDef `family_key`
- L93: FunctionDef `state_identity`
- L98: FunctionDef `sample_identity`
- L103: FunctionDef `dataset_group_identity`
- L108: FunctionDef `with_a`

## kv_ana/src/kvreuse_b5/execution/__init__.py

Lines: 5; encoding: utf-8


## kv_ana/src/kvreuse_b5/execution/artifacts.py

Lines: 176; encoding: utf-8

- L17: FunctionDef `_artifact_hash`
- L29: ClassDef `FrozenSubspaceArtifact`
- L44: ClassDef `PairSVDArtifact`
- L52: ClassDef `PrecomputedArtifactProvider`
- L60: ClassDef `ArtifactStore`
- L39: FunctionDef `max_rank`
- L56: FunctionDef `frozen_subspace`
- L63: FunctionDef `__init__`
- L70: FunctionDef `prepare_methods`
- L88: FunctionDef `prepare_frozen`
- L125: FunctionDef `frozen_for`
- L132: FunctionDef `pair_svd_for`
- L148: FunctionDef `_validate_artifact`
- L156: FunctionDef `audit_manifest`
- L73: FunctionDef `visit`

## kv_ana/src/kvreuse_b5/execution/checkpoint.py

Lines: 79; encoding: utf-8

- L13: FunctionDef `atomic_write_json`
- L27: ClassDef `CheckpointStore`
- L28: FunctionDef `__init__`
- L51: FunctionDef `_path`
- L55: FunctionDef `load`
- L70: FunctionDef `save`

## kv_ana/src/kvreuse_b5/execution/config.py

Lines: 49; encoding: utf-8

- L10: FunctionDef `canonical_hash`
- L16: ClassDef `ExperimentConfig`
- L36: FunctionDef `load_config`
- L23: FunctionDef `config_hash`
- L27: FunctionDef `from_mapping`

## kv_ana/src/kvreuse_b5/execution/invariants.py

Lines: 109; encoding: utf-8

- L11: FunctionDef `validate_artifact_audit`
- L20: FunctionDef `validate_sample_costs`
- L41: FunctionDef `validate_result_invariants`

## kv_ana/src/kvreuse_b5/execution/runner.py

Lines: 311; encoding: utf-8

- L27: FunctionDef `_jsonable`
- L39: FunctionDef `_dataset_fingerprint`
- L50: ClassDef `ExperimentRunner`
- L51: FunctionDef `__init__`
- L61: FunctionDef `run`
- L229: FunctionDef `_sample_cost_records`
- L262: FunctionDef `_base_record`

## kv_ana/src/kvreuse_b5/methods/__init__.py

Lines: 4; encoding: utf-8


## kv_ana/src/kvreuse_b5/methods/anchor.py

Lines: 132; encoding: utf-8

- L23: ClassDef `AnchorEntry`
- L28: ClassDef `ResidualAlignmentStrategy`
- L36: ClassDef `NormalizedLinearAlignment`
- L49: ClassDef `AnchorResidualCorrection`
- L32: FunctionDef `align`
- L39: FunctionDef `align`
- L54: FunctionDef `__init__`
- L73: FunctionDef `fit`
- L91: FunctionDef `correct`
- L124: FunctionDef `output_parameters`

## kv_ana/src/kvreuse_b5/methods/base.py

Lines: 197; encoding: utf-8

- L13: ClassDef `MethodCategory`
- L21: ClassDef `StageType`
- L27: ClassDef `MethodCapabilities`
- L55: ClassDef `CostRecord`
- L97: ClassDef `CorrectionResult`
- L107: ClassDef `FitContext`
- L113: ClassDef `EvalContext`
- L117: ClassDef `CapabilityMissingError`
- L123: ClassDef `CorrectionMethod`
- L187: ClassDef `CoefficientEstimator`
- L194: ClassDef `QueryImportanceProvider`
- L43: FunctionDef `executable_offline`
- L66: FunctionDef `exact_compute_units`
- L70: FunctionDef `observed_units`
- L74: FunctionDef `logical_repair_fraction`
- L77: FunctionDef `add`
- L118: FunctionDef `__init__`
- L131: FunctionDef `materializes_state`
- L134: FunctionDef `__init__`
- L139: FunctionDef `fit`
- L144: FunctionDef `freeze`
- L147: FunctionDef `_check_ready`
- L170: FunctionDef `apply`
- L180: FunctionDef `correct`
- L183: FunctionDef `output_parameters`
- L189: FunctionDef `estimate`
- L196: FunctionDef `scores`

## kv_ana/src/kvreuse_b5/methods/baseline.py

Lines: 12; encoding: utf-8

- L7: ClassDef `NoCorrection`
- L10: FunctionDef `correct`

## kv_ana/src/kvreuse_b5/methods/composite.py

Lines: 92; encoding: utf-8

- L18: FunctionDef `_combined_capabilities`
- L23: ClassDef `CompositeCorrection`
- L26: FunctionDef `__init__`
- L58: FunctionDef `fit`
- L70: FunctionDef `correct`
- L91: FunctionDef `output_parameters`

## kv_ana/src/kvreuse_b5/methods/detector.py

Lines: 234; encoding: utf-8

- L16: ClassDef `DetectorScoreProvider`
- L24: ClassDef `DetectorEvaluation`
- L34: FunctionDef `_tie_hash`
- L38: ClassDef `DetectorLayerSelector`
- L163: ClassDef `DetectorLayerCorrection`
- L18: FunctionDef `position_scores`
- L39: FunctionDef `__init__`
- L51: FunctionDef `fit`
- L70: FunctionDef `_validate_group`
- L85: FunctionDef `evaluate_exact`
- L98: FunctionDef `evaluate`
- L102: FunctionDef `evaluate_external`
- L112: FunctionDef `_evaluate_with_scores`
- L167: FunctionDef `__init__`
- L194: FunctionDef `fit`
- L198: FunctionDef `correct`
- L201: FunctionDef `apply_group`
- L226: FunctionDef `output_parameters`

## kv_ana/src/kvreuse_b5/methods/lowrank.py

Lines: 113; encoding: utf-8

- L10: ClassDef `PairLowRankOracle`
- L40: ClassDef `FrozenSubspace`
- L58: ClassDef `_FrozenMethod`
- L69: ClassDef `FrozenSubspaceOracleCoeff`
- L94: ClassDef `FrozenLowRankCorrection`
- L16: FunctionDef `__init__`
- L23: FunctionDef `bind_artifact_store`
- L26: FunctionDef `correct`
- L36: FunctionDef `output_parameters`
- L43: FunctionDef `__init__`
- L47: FunctionDef `fit`
- L50: FunctionDef `artifact_for`
- L53: FunctionDef `basis_for`
- L61: FunctionDef `bind_artifact_store`
- L64: FunctionDef `fit`
- L74: FunctionDef `__init__`
- L79: FunctionDef `correct`
- L90: FunctionDef `output_parameters`
- L99: FunctionDef `__init__`
- L105: FunctionDef `correct`

## kv_ana/src/kvreuse_b5/methods/placeholders.py

Lines: 45; encoding: utf-8

- L14: ClassDef `InterfaceOnlyMethod`
- L17: FunctionDef `__init__`
- L22: FunctionDef `correct`

## kv_ana/src/kvreuse_b5/methods/position.py

Lines: 120; encoding: utf-8

- L14: ClassDef `RoPEMetadata`
- L55: FunctionDef `_rotate`
- L71: FunctionDef `apply_rope_shift`
- L100: ClassDef `PositionCorrectionBase`
- L106: ClassDef `RoPEPositionCorrection`
- L25: FunctionDef `from_mapping`
- L102: FunctionDef `transform`
- L110: FunctionDef `transform`
- L113: FunctionDef `correct`

## kv_ana/src/kvreuse_b5/methods/probe.py

Lines: 118; encoding: utf-8

- L22: FunctionDef `probe_indices`
- L32: ClassDef `CoefficientStrategy`
- L42: ClassDef `LinearDepthStrategy`
- L54: ClassDef `ProbeMean`
- L76: ClassDef `ProbeFrozenBasis`
- L36: FunctionDef `interpolate`
- L45: FunctionDef `interpolate`
- L59: FunctionDef `__init__`
- L63: FunctionDef `correct`
- L72: FunctionDef `output_parameters`
- L82: FunctionDef `__init__`
- L95: FunctionDef `bind_artifact_store`
- L98: FunctionDef `fit`
- L102: FunctionDef `correct`
- L117: FunctionDef `output_parameters`

## kv_ana/src/kvreuse_b5/methods/registry.py

Lines: 187; encoding: utf-8

- L33: FunctionDef `_values`
- L38: FunctionDef `_binner`
- L58: ClassDef `MethodRegistry`
- L81: FunctionDef `default_registry`
- L59: FunctionDef `__init__`
- L62: FunctionDef `register`
- L67: FunctionDef `create`
- L73: FunctionDef `build_all`
- L77: FunctionDef `names`
- L177: FunctionDef `composite_builder`

## kv_ana/src/kvreuse_b5/methods/selective.py

Lines: 375; encoding: utf-8

- L14: FunctionDef `budget_units`
- L20: FunctionDef `_neutral_hash`
- L28: FunctionDef `_neutral_group_hash`
- L32: FunctionDef `repair_rows`
- L42: ClassDef `SelectiveRepair`
- L143: ClassDef `RandomExpected`
- L182: ClassDef `BoundaryFirst`
- L189: ClassDef `JointUnitOracle`
- L198: ClassDef `_StaticSelector`
- L207: ClassDef `StaticLayerSelector`
- L234: ClassDef `PositionBinner`
- L242: ClassDef `FixedEdgesPositionBinner`
- L264: ClassDef `NormalizedDepthBinner`
- L280: ClassDef `UniformAbsoluteBinner`
- L297: ClassDef `_StaticBinnedSelector`
- L319: ClassDef `StaticPositionSelector`
- L346: ClassDef `StaticLayerPositionSelector`
- L46: FunctionDef `__init__`
- L57: FunctionDef `unit_scores`
- L60: FunctionDef `atomic_groups`
- L68: FunctionDef `_select_group`
- L118: FunctionDef `apply_group`
- L131: FunctionDef `correct`
- L134: FunctionDef `output_parameters`
- L148: FunctionDef `__init__`
- L152: FunctionDef `unit_scores`
- L155: FunctionDef `apply_group`
- L185: FunctionDef `unit_scores`
- L194: FunctionDef `unit_scores`
- L202: FunctionDef `_validate_fit`
- L210: FunctionDef `__init__`
- L214: FunctionDef `fit`
- L222: FunctionDef `unit_scores`
- L227: FunctionDef `atomic_groups`
- L238: FunctionDef `bins`
- L245: FunctionDef `__init__`
- L255: FunctionDef `bins`
- L267: FunctionDef `__init__`
- L272: FunctionDef `bins`
- L283: FunctionDef `__init__`
- L288: FunctionDef `bins`
- L298: FunctionDef `__init__`
- L308: FunctionDef `output_parameters`
- L322: FunctionDef `__init__`
- L326: FunctionDef `fit`
- L335: FunctionDef `unit_scores`
- L338: FunctionDef `atomic_groups`
- L349: FunctionDef `__init__`
- L353: FunctionDef `fit`
- L362: FunctionDef `unit_scores`
- L367: FunctionDef `atomic_groups`

## kv_ana/src/kvreuse_b5/methods/shared.py

Lines: 14; encoding: utf-8

- L7: ClassDef `SharedResidualOracle`
- L12: FunctionDef `correct`

## kv_ana/src/kvreuse_b5/metrics/__init__.py

Lines: 5; encoding: utf-8


## kv_ana/src/kvreuse_b5/metrics/aggregate.py

Lines: 78; encoding: utf-8

- L23: FunctionDef `canonical_experiment_key`
- L30: FunctionDef `_summarize`
- L49: FunctionDef `_group`
- L57: FunctionDef `aggregate_records`

## kv_ana/src/kvreuse_b5/metrics/residual.py

Lines: 42; encoding: utf-8

- L9: ClassDef `ResidualMetrics`
- L16: FunctionDef `compute_residual_metrics`

## kv_ana/src/kvreuse_b5/observability/__init__.py

Lines: 16; encoding: utf-8


## kv_ana/src/kvreuse_b5/observability/ablations.py

Lines: 252; encoding: utf-8

- L24: ClassDef `AgentKVShiftForensicAblation`
- L36: FunctionDef `__init__`
- L60: FunctionDef `execute`

## kv_ana/src/kvreuse_b5/observability/collector.py

Lines: 160; encoding: utf-8

- L14: ClassDef `TraceLevel`
- L20: FunctionDef `_optional_sum`
- L34: FunctionDef `_summary_metadata`
- L50: ClassDef `TraceCollector`
- L51: FunctionDef `__init__`
- L67: FunctionDef `from_env`
- L73: FunctionDef `enabled`
- L76: FunctionDef `record`
- L134: FunctionDef `summary_events`
- L151: FunctionDef `detail_events`
- L154: FunctionDef `cost_rows`
- L157: FunctionDef `forensic_rows`

## kv_ana/src/kvreuse_b5/observability/cost.py

Lines: 112; encoding: utf-8

- L10: ClassDef `CostAccountingRecord`
- L33: FunctionDef `recomputed_units_unique_layer_position`
- L37: FunctionDef `unique_positions`
- L41: FunctionDef `selected_unique_positions`
- L44: FunctionDef `add`
- L70: FunctionDef `_fraction`
- L73: FunctionDef `to_row`

## kv_ana/src/kvreuse_b5/observability/events.py

Lines: 106; encoding: utf-8

- L11: FunctionDef `collector_from_context`
- L16: FunctionDef `source_from_provider`
- L27: FunctionDef `_metadata`
- L39: FunctionDef `emit_recipe_event`

## kv_ana/src/kvreuse_b5/observability/forensic.py

Lines: 103; encoding: utf-8

- L26: FunctionDef `_fraction`
- L30: FunctionDef `build_forensic_rows`

## kv_ana/src/kvreuse_b5/observability/method_adapter.py

Lines: 83; encoding: utf-8

- L13: FunctionDef `_row_positions`
- L22: FunctionDef `observe_method_record`

## kv_ana/src/kvreuse_b5/observability/schema.py

Lines: 137; encoding: utf-8

- L14: ClassDef `TraceAction`
- L25: ClassDef `TraceSource`
- L33: FunctionDef `_json_value`
- L48: ClassDef `KVTraceEvent`
- L116: FunctionDef `trace_schema_document`
- L83: FunctionDef `__post_init__`
- L112: FunctionDef `to_dict`

## kv_ana/src/kvreuse_b5/observability/writer.py

Lines: 92; encoding: utf-8

- L16: FunctionDef `_json_line`
- L20: ClassDef `ObservabilityWriter`
- L21: FunctionDef `__init__`
- L25: FunctionDef `_write_json`
- L31: FunctionDef `_write_jsonl`
- L39: FunctionDef `_write_gzip_jsonl`
- L47: FunctionDef `_write_csv`
- L56: FunctionDef `write`

## kv_ana/src/kvreuse_b5/pipeline/__init__.py

Lines: 3; encoding: utf-8


## kv_ana/src/kvreuse_b5/pipeline/b5.py

Lines: 100; encoding: utf-8

- L18: FunctionDef `_scope_is_comparable`
- L28: FunctionDef `build_b5_rows`
- L85: FunctionDef `validate_target_usage`

## kv_ana/src/kvreuse_b5/pipeline/capability_audit.py

Lines: 86; encoding: utf-8

- L18: FunctionDef `audit_paper_capabilities`

## kv_ana/src/kvreuse_b5/pipeline/capsule.py

Lines: 83; encoding: utf-8

- L8: FunctionDef `_safe_number`
- L17: FunctionDef `render_capsule`
- L67: FunctionDef `write_bounded_capsule`

## kv_ana/src/kvreuse_b5/pipeline/io.py

Lines: 34; encoding: utf-8

- L10: FunctionDef `atomic_json`
- L18: FunctionDef `write_csv`
- L32: FunctionDef `read_csv`

## kv_ana/src/kvreuse_b5/pipeline/paper_offline.py

Lines: 134; encoding: utf-8

- L17: FunctionDef `_stack`
- L24: FunctionDef `_allow_explicit_target_provider`
- L33: ClassDef `_MetricAccumulator`
- L74: FunctionDef `run_real_paper_components`
- L34: FunctionDef `__init__`
- L47: FunctionDef `add`
- L55: FunctionDef `_cos`
- L59: FunctionDef `row`

## kv_ana/src/kvreuse_b5/pipeline/provider_plan.py

Lines: 98; encoding: utf-8

- L52: FunctionDef `build_provider_plan`
- L84: FunctionDef `write_provider_plan`

## kv_ana/src/kvreuse_b5/pipeline/providers.py

Lines: 80; encoding: utf-8

- L10: ClassDef `OfflineFullTargetProbeProvider`
- L44: ClassDef `OfflineFullTargetPartialForwardProvider`
- L19: FunctionDef `__init__`
- L22: FunctionDef `checkpoint_states`
- L30: FunctionDef `probe_states`
- L52: FunctionDef `__init__`
- L55: FunctionDef `score`
- L71: FunctionDef `recompute`

## kv_ana/src/kvreuse_b5/pipeline/real_data.py

Lines: 119; encoding: utf-8

- L14: ClassDef `RealTrack`
- L37: ClassDef `_IndexedDeepSeekPrefixC4Adapter`
- L81: ClassDef `RealStateSource`
- L20: FunctionDef `slug`
- L40: FunctionDef `__init__`
- L45: FunctionDef `_pair_number`
- L49: FunctionDef `_build_index`
- L69: FunctionDef `_mapping_rows`
- L82: FunctionDef `__init__`
- L87: FunctionDef `load`
- L101: FunctionDef `load_qwen_kv`
- L109: FunctionDef `load_all_historical`

## kv_ana/src/kvreuse_b5/pipeline/runner.py

Lines: 239; encoding: utf-8

- L27: ClassDef `FatalPipelineError`
- L32: ClassDef `PipelinePaths`
- L40: FunctionDef `_now`
- L44: FunctionDef `_load_config`
- L49: FunctionDef `_energy`
- L53: ClassDef `RealOfflinePipeline`
- L54: FunctionDef `__init__`
- L92: FunctionDef `_load_state`
- L103: FunctionDef `_complete`
- L107: FunctionDef `_done`
- L110: FunctionDef `precheck`
- L160: FunctionDef `run`

## kv_ana/src/kvreuse_b5/providers/__init__.py

Lines: 23; encoding: utf-8


## kv_ana/src/kvreuse_b5/providers/base.py

Lines: 122; encoding: utf-8

- L11: ClassDef `ProviderCapabilities`
- L20: ClassDef `ProviderOutput`
- L26: ClassDef `RecipeProvider`
- L34: ClassDef `PartialForwardProvider`
- L50: ClassDef `QuerySignalProvider`
- L58: ClassDef `AttentionSignalProvider`
- L66: ClassDef `FreshProbeProvider`
- L81: ClassDef `DetectorScoreProvider`
- L89: ClassDef `AnchorStateProvider`
- L101: ClassDef `SemanticEmbeddingProvider`
- L109: ClassDef `PositionTransformProvider`
- L117: ClassDef `CacheRuntimeProvider`
- L30: FunctionDef `capability`
- L38: FunctionDef `score`
- L44: FunctionDef `recompute`
- L54: FunctionDef `query_signal`
- L62: FunctionDef `attention_signal`
- L71: FunctionDef `checkpoint_states`
- L75: FunctionDef `probe_states`
- L85: FunctionDef `scores`
- L93: FunctionDef `candidates`
- L97: FunctionDef `update`
- L105: FunctionDef `embed`
- L113: FunctionDef `transform`
- L121: FunctionDef `cache_event`

## kv_ana/src/kvreuse_b5/providers/synthetic.py

Lines: 122; encoding: utf-8

- L17: ClassDef `SyntheticPartialForwardProvider`
- L50: ClassDef `FullExactFreshProbeProvider`
- L71: ClassDef `ApproximateContextFreshProbeProvider`
- L89: ClassDef `SyntheticAnchor`
- L96: ClassDef `SyntheticAnchorStateProvider`
- L24: FunctionDef `__init__`
- L28: FunctionDef `score`
- L41: FunctionDef `recompute`
- L56: FunctionDef `__init__`
- L59: FunctionDef `checkpoint_states`
- L64: FunctionDef `probe_states`
- L77: FunctionDef `__init__`
- L81: FunctionDef `probe_states`
- L101: FunctionDef `__init__`
- L105: FunctionDef `candidates`
- L113: FunctionDef `update`

## kv_ana/src/kvreuse_b5/recipes/__init__.py

Lines: 4; encoding: utf-8


## kv_ana/src/kvreuse_b5/recipes/agentkvshift.py

Lines: 200; encoding: utf-8

- L15: FunctionDef `_distribution_stats`
- L35: FunctionDef `_recovery`
- L41: FunctionDef `agentkvshift_spec`
- L62: ClassDef `AgentKVShiftRecipe`
- L63: FunctionDef `__init__`
- L66: FunctionDef `execute`

## kv_ana/src/kvreuse_b5/recipes/cacheblend.py

Lines: 70; encoding: utf-8

- L13: FunctionDef `cacheblend_spec`
- L38: ClassDef `CacheBlendRecipe`
- L39: FunctionDef `__init__`
- L42: FunctionDef `execute`

## kv_ana/src/kvreuse_b5/recipes/cacheslide.py

Lines: 221; encoding: utf-8

- L17: ClassDef `CacheSlidePositionModel`
- L25: ClassDef `CacheSlideRuntimeSpec`
- L31: FunctionDef `cacheslide_spec`
- L55: ClassDef `CacheSlideWCARecipe`
- L56: FunctionDef `__init__`
- L61: FunctionDef `execute`

## kv_ana/src/kvreuse_b5/recipes/contract.py

Lines: 201; encoding: utf-8

- L15: ClassDef `FidelityLevel`
- L22: ClassDef `RecipeExecutionStatus`
- L28: ClassDef `RecipeCapabilityMissingError`
- L34: FunctionDef `canonical_hash`
- L40: ClassDef `RecipeCapabilities`
- L47: ClassDef `RecipeVariant`
- L55: ClassDef `RecipeStep`
- L68: ClassDef `PaperRecipeSpec`
- L110: ClassDef `DeclarativeRecipeSpec`
- L130: ClassDef `RecipeCostRecord`
- L141: ClassDef `RecipeTraceEntry`
- L156: ClassDef `RecipeExecutionContext`
- L166: ClassDef `RecipeResult`
- L175: ClassDef `ExecutableRecipe`
- L200: FunctionDef `trace_entry`
- L29: FunctionDef `__init__`
- L62: FunctionDef `__post_init__`
- L89: FunctionDef `__post_init__`
- L95: FunctionDef `canonical_dict`
- L101: FunctionDef `recipe_spec_hash`
- L105: FunctionDef `recipe_variant_hash`
- L125: FunctionDef `recipe_spec_hash`
- L135: FunctionDef `add`
- L161: FunctionDef `copy_states`
- L176: FunctionDef `__init__`
- L179: FunctionDef `validate_context`
- L196: FunctionDef `execute`

## kv_ana/src/kvreuse_b5/recipes/kvcomm.py

Lines: 108; encoding: utf-8

- L23: FunctionDef `kvcomm_spec`
- L49: ClassDef `KVCOMMRecipe`
- L50: FunctionDef `__init__`
- L53: FunctionDef `execute`

## kv_ana/src/kvreuse_b5/recipes/registry.py

Lines: 74; encoding: utf-8

- L10: ClassDef `RecipeRegistry`
- L49: FunctionDef `_load_declarative`
- L59: FunctionDef `default_recipe_registry`
- L11: FunctionDef `__init__`
- L15: FunctionDef `register_declarative`
- L20: FunctionDef `register_executable`
- L25: FunctionDef `list_recipes`
- L28: FunctionDef `list_by_paper`
- L31: FunctionDef `list_by_capability`
- L34: FunctionDef `list_executable`
- L37: FunctionDef `list_blocked`
- L40: FunctionDef `list_by_fidelity`
- L43: FunctionDef `get`

## kv_ana/src/kvreuse_b5/regression/__init__.py

Lines: 5; encoding: utf-8


## kv_ana/src/kvreuse_b5/regression/historical_refs.py

Lines: 13; encoding: utf-8

- L8: FunctionDef `load_historical_references`

## kv_ana/src/kvreuse_b5/regression/historical_suite.py

Lines: 354; encoding: utf-8

- L35: ClassDef `EnergyAccumulator`
- L54: FunctionDef `_summary`
- L70: ClassDef `HistoricalRegressionSuite`
- L41: FunctionDef `add`
- L50: FunctionDef `explained`
- L71: FunctionDef `__init__`
- L96: FunctionDef `fixed_samples`
- L99: FunctionDef `_sv`
- L108: FunctionDef `run_b1`
- L119: FunctionDef `run_b2`
- L163: FunctionDef `_fit_bases`
- L186: FunctionDef `run_b25_and_b3`
- L236: FunctionDef `run_b4`
- L269: FunctionDef `_status`
- L276: FunctionDef `compare`
- L332: FunctionDef `run`
- L280: FunctionDef `candidates`
- L287: FunctionDef `add`

## kv_ana/src/kvreuse_b5/regression/manifest.py

Lines: 31; encoding: utf-8

- L9: ClassDef `ManifestSample`
- L15: FunctionDef `load_lifemem_manifest`

## kv_ana/src/kvreuse_b5/regression/math.py

Lines: 193; encoding: utf-8

- L11: FunctionDef `stable_seed`
- L16: FunctionDef `energy_explained`
- L22: FunctionDef `shared_residual_sse`
- L29: FunctionDef `randomized_singular_values`
- L56: FunctionDef `lowrank_explained_curve`
- L82: FunctionDef `randomized_basis_from_covariance`
- L105: FunctionDef `projection_sse`
- L112: FunctionDef `linear_interpolate_rows`
- L123: FunctionDef `historical_probe_indices`
- L132: FunctionDef `historical_budget_units`
- L141: ClassDef `UnitEnergy`
- L147: FunctionDef `_fractional_group_removed`
- L164: FunctionDef `selective_removed_energy`

## kv_ana/src/kvreuse_b5/state_research/__init__.py

Lines: 4; encoding: utf-8

Offline research-input preparation; independent of runtime and frozen methods.


## kv_ana/src/kvreuse_b5/state_research/agentmon.py

Lines: 104; encoding: utf-8

- L16: FunctionDef `_keys`
- L21: FunctionDef `correlate_events`
- L33: FunctionDef `lookup`
- L39: FunctionDef `match`

## kv_ana/src/kvreuse_b5/state_research/alignment.py

Lines: 123; encoding: utf-8

- L9: ClassDef `Tokenization`
- L42: ClassDef `TokenizerProvider`
- L48: ClassDef `LocalFastTokenizerProvider`
- L70: FunctionDef `occurrences`
- L82: FunctionDef `locate`
- L100: FunctionDef `align_span`
- L116: FunctionDef `edit_map`
- L18: FunctionDef `validate`
- L37: FunctionDef `from_dict`
- L43: FunctionDef `tokenize`
- L51: FunctionDef `__init__`
- L63: FunctionDef `tokenize`

## kv_ana/src/kvreuse_b5/state_research/fixtures.py

Lines: 61; encoding: utf-8

Explicitly synthetic, model-free fixtures; never a fallback tokenizer for real inputs.

- L10: ClassDef `SyntheticCharacterTokenizer`
- L16: FunctionDef `synthetic_records`
- L42: FunctionDef `synthetic_events`
- L55: FunctionDef `synthetic_states`
- L11: FunctionDef `tokenize`

## kv_ana/src/kvreuse_b5/state_research/io.py

Lines: 56; encoding: utf-8

- L8: FunctionDef `load_records`
- L24: FunctionDef `merge_sidecar`
- L40: FunctionDef `write_json`
- L44: FunctionDef `write_jsonl`
- L50: FunctionDef `write_csv`

## kv_ana/src/kvreuse_b5/state_research/lifecycle.py

Lines: 244; encoding: utf-8

Torch-free state lifecycle contract and offline reconstruction, not a runtime hook.

- L17: ClassDef `StateEvent`
- L45: ClassDef `EventRecorder`
- L62: FunctionDef `analyze_lifecycle`
- L32: FunctionDef `__post_init__`
- L48: FunctionDef `__init__`
- L51: FunctionDef `emit`
- L71: FunctionDef `allocation_key`
- L74: FunctionDef `closure`
- L86: FunctionDef `dependency_signature`

## kv_ana/src/kvreuse_b5/state_research/lifemem.py

Lines: 255; encoding: utf-8

- L10: FunctionDef `_identity`
- L14: FunctionDef `_digest`
- L19: FunctionDef `prepare_records`
- L163: FunctionDef `_transitions`
- L239: FunctionDef `_context_changes`
- L28: FunctionDef `issue`

## kv_ana/src/kvreuse_b5/state_research/offline_fixtures.py

Lines: 117; encoding: utf-8

Small synthetic fixtures for offline protocol acceptance, never real runtime evidence.

- L15: FunctionDef `state`
- L22: FunctionDef `lifecycle_fixture`
- L53: FunctionDef `cost_scope`
- L59: FunctionDef `cost_trials`
- L65: FunctionDef `recovery_case`
- L75: FunctionDef `write_fixture_bundle`
- L25: FunctionDef `emit`

## kv_ana/src/kvreuse_b5/state_research/offline_suite.py

Lines: 257; encoding: utf-8

Phases 2/3 offline tools. No server access, model loading, resource enforcement or paper recipe changes.

- L22: FunctionDef `bounded_capsule`
- L45: FunctionDef `run_suite`
- L247: FunctionDef `main`
- L56: FunctionDef `run`
- L69: FunctionDef `records`
- L75: FunctionDef `request_records`
- L82: FunctionDef `lifecycle`
- L142: FunctionDef `recover`
- L167: FunctionDef `compare`
- L199: FunctionDef `impact`
- L113: FunctionDef `replay`

## kv_ana/src/kvreuse_b5/state_research/policy_replay.py

Lines: 137; encoding: utf-8

Fixed-demand cache replay baselines. These policies never access future demands or B.

- L8: FunctionDef `replay_demands`
- L21: FunctionDef `key`
- L24: FunctionDef `pool`
- L27: FunctionDef `limit`
- L30: FunctionDef `used`
- L33: FunctionDef `remove`
- L38: FunctionDef `bundle`

## kv_ana/src/kvreuse_b5/state_research/prepare.py

Lines: 156; encoding: utf-8

- L17: FunctionDef `prepare_dataset`
- L106: FunctionDef `main`

## kv_ana/src/kvreuse_b5/state_research/recovery.py

Lines: 161; encoding: utf-8

Evidence-gated recovery planning. No repair algorithm and no model execution.

- L15: FunctionDef `scope_key`
- L28: FunctionDef `operation_key`
- L37: FunctionDef `missing_runtime_scope`
- L53: ClassDef `CostProfiles`
- L90: FunctionDef `plan_recovery`
- L56: FunctionDef `__init__`
- L76: FunctionDef `lookup`

## kv_ana/src/kvreuse_b5/state_research/replay.py

Lines: 40; encoding: utf-8

- L6: FunctionDef `replay_manifest`
- L23: FunctionDef `representative_cases`

## kv_ana/src/kvreuse_b5/state_research/request_bridge.py

Lines: 100; encoding: utf-8

Content-free HTTP evidence and explicit request/state joins.

- L14: FunctionDef `request_from_wire`
- L53: FunctionDef `bind_state_captures`

## kv_ana/src/kvreuse_b5/state_research/scenarios.py

Lines: 23; encoding: utf-8

Explicit constructed workload schedules; no invented natural concurrency measurements.

- L5: FunctionDef `constructed_schedule`

## kv_ana/src/kvreuse_b5/state_research/snapshot_archive.py

Lines: 56; encoding: utf-8

Save explicitly supplied CPU state views; no model hooks, tensor inference, or device transfer.

- L14: FunctionDef `write_snapshot`

## kv_ana/src/kvreuse_b5/state_research/state_compare.py

Lines: 114; encoding: utf-8

Evaluate supplied aligned states; never generate a correction or infer runtime row mapping.

- L14: FunctionDef `_rows`
- L22: FunctionDef `_positions`
- L31: FunctionDef `compare_aligned_arrays`
- L74: FunctionDef `compare_npz`
- L91: FunctionDef `dependency_impact`

## kv_ana/src/kvreuse_b5/state_research/state_inventory.py

Lines: 126; encoding: utf-8

- L7: ClassDef `StateInstance`
- L56: FunctionDef `account_states`
- L87: FunctionDef `capture_requirements`
- L125: FunctionDef `state_dicts`
- L37: FunctionDef `__post_init__`

## kv_ana/tests/test_capabilities_composite.py

Lines: 60; encoding: utf-8

- L16: FunctionDef `test_registry_contains_required_interface_only_families`
- L21: FunctionDef `test_interface_only_reports_missing_instead_of_metric`
- L28: FunctionDef `test_composite_applies_second_stage_to_first_residual_and_accumulates_cost`
- L39: FunctionDef `test_composite_registry_configuration`
- L54: FunctionDef `test_fit_rejects_test_pair`

## kv_ana/tests/test_data_metrics.py

Lines: 42; encoding: utf-8

- L14: FunctionDef `test_shape_and_finite_fail_fast`
- L21: FunctionDef `test_train_test_identity_overlap_rejected`
- L27: FunctionDef `test_zero_residual_is_nan_and_aggregate_excludes_it`

## kv_ana/tests/test_dsv4_intervention_precheck.py

Lines: 180; encoding: utf-8

- L14: FunctionDef `load_module`
- L27: FunctionDef `environment`
- L41: FunctionDef `base_spec`
- L57: FunctionDef `test_identity_and_zero_selected_are_traced_without_torch`
- L95: FunctionDef `test_external_requires_exact_shape_layer_ratio_rows_and_slot_mapping`
- L138: FunctionDef `test_out_of_scope_layer_is_visible_skip`
- L155: FunctionDef `test_patch_and_restore_use_sha_and_anchor_guards`

## kv_ana/tests/test_lowrank_probe.py

Lines: 50; encoding: utf-8

- L11: FunctionDef `explained`
- L16: FunctionDef `test_constant_shared_residual_is_exact`
- L21: FunctionDef `test_exact_rank4_oracle_and_rank2_gap`
- L29: FunctionDef `test_frozen_shared_subspace_generalizes_without_test_fit`
- L37: FunctionDef `test_probe_linear_depth_smooth_beats_nonsmooth_and_upper_bound`

## kv_ana/tests/test_observability_recipes.py

Lines: 206; encoding: utf-8

- L13: FunctionDef `_metadata`
- L24: FunctionDef `_agent_fixture`
- L32: FunctionDef `test_agent_trace_matches_probe_output_costs_and_does_not_change_numbers`
- L75: FunctionDef `test_official_recipe_hashes_match_observability_v1_bundle`
- L86: FunctionDef `test_agent_all_zero_selector_is_diagnosed_without_changing_stable_selection`
- L107: FunctionDef `test_agent_correction_norm_is_applied_norm_not_estimator_norm`
- L123: FunctionDef `test_agent_forensic_ablations_are_separate_and_have_documented_actions`
- L153: FunctionDef `test_forensic_summary_attributes_agent_recovery_and_work`
- L176: FunctionDef `test_cacheslide_transition_trace_and_q35_prefix_v_forensic_label`

## kv_ana/tests/test_observability_schema_collector.py

Lines: 199; encoding: utf-8

- L22: FunctionDef `_event`
- L55: FunctionDef `test_trace_event_json_schema_and_nullable_runtime_ids`
- L64: FunctionDef `test_zero_applied_correction_norm_remains_explicit_in_summary`
- L81: FunctionDef `test_off_collector_records_nothing_and_writes_nothing`
- L90: FunctionDef `test_summary_aggregation_and_repeated_unique_cost`
- L109: FunctionDef `test_summary_strips_position_arrays_but_detail_preserves_them`
- L141: FunctionDef `test_position_and_layer_position_denominators_are_not_mixed`
- L161: FunctionDef `test_detail_preserves_positions_and_writer_compresses`
- L177: FunctionDef `test_experiment_runner_observability_on_off_preserves_metrics_and_method_hash`
- L178: FunctionDef `run`

## kv_ana/tests/test_position.py

Lines: 13; encoding: utf-8

- L7: FunctionDef `test_rope_synthetic_shift_and_inverse`

## kv_ana/tests/test_real_adapters_deepseek.py

Lines: 180; encoding: utf-8

- L16: FunctionDef `_entry`
- L39: FunctionDef `_write_payloads`
- L59: FunctionDef `_write_mapping`
- L69: FunctionDef `_adapter`
- L75: FunctionDef `_pickle_load`
- L80: FunctionDef `test_deepseek_prefix_a_b_pairing`
- L92: FunctionDef `test_deepseek_filters_to_c4_layers`
- L100: FunctionDef `test_deepseek_strict_row_extraction_reads_row_a_and_row_b_separately`
- L114: FunctionDef `test_deepseek_uses_canonical_x_rel_positions`
- L123: FunctionDef `test_deepseek_anchor_mismatch_fails`
- L130: FunctionDef `test_deepseek_missing_mapping_fails`
- L137: FunctionDef `test_deepseek_missing_prefix_side_fails`
- L145: FunctionDef `test_deepseek_real_query_length_and_start_pos_schema_passes`
- L157: FunctionDef `test_deepseek_start_pos_nonzero_fails`
- L164: FunctionDef `test_deepseek_query_length_vector_wrong_length_fails`
- L171: FunctionDef `test_deepseek_query_length_hidden_shape_mismatch_fails`

## kv_ana/tests/test_real_adapters_qwen.py

Lines: 147; encoding: utf-8

- L12: FunctionDef `_raw`
- L17: FunctionDef `_pickle_load`
- L22: FunctionDef `_write_qwen`
- L89: FunctionDef `test_q8_flatten_exact`
- L97: FunctionDef `test_q35_flatten_exact`
- L105: FunctionDef `test_q35_uses_actual_layer_id_not_attention_index`
- L113: FunctionDef `test_qwen_emits_independent_k_and_v_pairs`
- L123: FunctionDef `test_qwen_preserves_position_offsets_and_relative_position_semantics`
- L132: FunctionDef `test_qwen_filename_payload_condition_mismatch_fails`
- L138: FunctionDef `test_qwen_shape_mismatch_fails`
- L144: FunctionDef `test_qwen_x_tokens_mismatch_fails`

## kv_ana/tests/test_real_offline_pipeline.py

Lines: 61; encoding: utf-8

- L18: FunctionDef `test_paper_capability_audit_is_20_unique_and_conservative`
- L29: FunctionDef `test_missing_provider_fails_without_surrogate`
- L35: FunctionDef `test_target_leakage_audit_rejects_deployment_visible_target`
- L41: FunctionDef `test_provider_plan_contains_future_functional_signals`
- L47: FunctionDef `test_capsule_enforces_utf8_byte_limit`

## kv_ana/tests/test_real_offline_regression.py

Lines: 91; encoding: utf-8

- L25: FunctionDef `test_fixed48_manifest_and_split`
- L34: FunctionDef `test_b1_shared_residual_formula`
- L42: FunctionDef `test_b2_rank_curve_monotonic_and_zero_case`
- L52: FunctionDef `test_b25_manifest_prevents_train_test_contamination`
- L60: FunctionDef `test_b3_probe_indices_and_endpoint_interpolation`
- L72: FunctionDef `test_b4_historical_budget_rounding`
- L79: FunctionDef `test_b4_fractional_final_group`
- L87: FunctionDef `test_b4_zero_residual_is_nan`

## kv_ana/tests/test_recipe_agentkvshift.py

Lines: 43; encoding: utf-8

- L11: FunctionDef `_fixture`
- L19: FunctionDef `test_agentkvshift_requires_explicit_probe_semantics`
- L25: FunctionDef `test_agentkvshift_probe_selection_weighting_and_exact_probe_preservation`
- L37: FunctionDef `test_agentkvshift_exact_and_approximate_providers_are_explicit_variants`

## kv_ana/tests/test_recipe_cacheblend.py

Lines: 32; encoding: utf-8

- L10: FunctionDef `test_cacheblend_runs_layerwise_scatter_and_propagates_updated_state`
- L25: FunctionDef `test_cacheblend_never_reads_target_outside_provider`

## kv_ana/tests/test_recipe_cacheslide.py

Lines: 44; encoding: utf-8

- L10: FunctionDef `test_cacheslide_wca_selection_fusion_gating_budget_and_cost`
- L26: FunctionDef `test_cacheslide_components_are_not_rope_shift_aliases`
- L34: FunctionDef `test_cacheslide_pinned_code_wca_is_not_claimed_executable`
- L40: FunctionDef `test_cacheslide_cksim_inequality_requires_explicit_variant`

## kv_ana/tests/test_recipe_contract.py

Lines: 57; encoding: utf-8

- L14: FunctionDef `test_all20_declarative_recipe_specs_load_and_are_unique`
- L22: FunctionDef `test_recipe_hash_is_deterministic_and_serialization_order_independent`
- L27: FunctionDef `test_missing_provider_fails_without_surrogate`
- L33: FunctionDef `test_forbidden_oracle_provider_is_rejected`
- L42: FunctionDef `test_all_core_steps_have_resolved_evidence_refs_and_variant_hashes_differ`
- L53: FunctionDef `test_skeleton_files_are_canonical_json_yaml`
- L34: ClassDef `OracleProvider`

## kv_ana/tests/test_recipe_kvcomm.py

Lines: 45; encoding: utf-8

- L10: FunctionDef `_anchors`
- L18: FunctionDef `test_kvcomm_selects_anchor_and_keeps_kv_separate`
- L27: FunctionDef `test_kvcomm_pinned_code_preserves_layer0_and_hash_differs`
- L38: FunctionDef `test_kvcomm_fallback_updates_anchor_pool`

## kv_ana/tests/test_request_state_bridge.py

Lines: 83; encoding: utf-8

- L8: FunctionDef `request`
- L17: FunctionDef `binding`
- L22: FunctionDef `capture`
- L29: FunctionDef `test_content_free_post_preparation_evidence`
- L37: FunctionDef `test_retry_and_summary_have_distinct_transport_identity`
- L46: FunctionDef `test_service_restart_does_not_reuse_engine_binding`
- L51: FunctionDef `test_missing_runtime_binding_is_not_guessed`
- L56: FunctionDef `test_capture_model_and_context_must_match`
- L61: FunctionDef `test_synthetic_binding_cannot_become_runtime_evidence`
- L65: FunctionDef `test_offline_synthetic_end_to_end`
- L71: FunctionDef `test_missing_runtime_identity_is_blocked`
- L76: FunctionDef `test_conflicting_binding_rejected`
- L81: FunctionDef `test_duplicate_attempt_rejected`

## kv_ana/tests/test_runner_checkpoint.py

Lines: 45; encoding: utf-8

- L11: FunctionDef `make_config`
- L28: FunctionDef `test_runner_checkpoint_resume_and_capability_report`
- L40: FunctionDef `test_config_hash_mismatch_refuses_reuse`

## kv_ana/tests/test_selective_detector_anchor.py

Lines: 81; encoding: utf-8

- L14: FunctionDef `explained`
- L25: FunctionDef `test_joint_oracle_finds_90_percent_energy_and_full_budget_repairs_all`
- L32: FunctionDef `test_static_layer_position_pattern_generalizes`
- L41: FunctionDef `test_detector_correlated_layers`
- L53: FunctionDef `test_detector_runs_grouped_and_checkpoints_per_layer`
- L70: FunctionDef `test_anchor_knn_beats_distant_anchor`

## kv_ana/tests/test_state_research_alignment.py

Lines: 172; encoding: utf-8

- L10: FunctionDef `prepare`
- L14: FunctionDef `test_no_tokenizer_fallback_and_no_fabricated_full_prompt`
- L24: FunctionDef `test_original_inputs_untouched_and_payload_keeps_query`
- L33: FunctionDef `test_explicit_provider_is_supported_and_synthetic_cannot_be_real_ready`
- L42: FunctionDef `test_unicode_offsets_and_special_tokens_are_character_based`
- L49: FunctionDef `test_bad_offsets_rejected`
- L54: FunctionDef `test_reversed_token_offsets_rejected`
- L59: FunctionDef `test_unicode_byte_fallback_duplicate_offsets_keep_all_tokens`
- L65: FunctionDef `test_scope_mismatch_rejected`
- L70: FunctionDef `test_chunk_inside_token_is_not_exact`
- L75: FunctionDef `test_duplicate_body_is_ambiguous_unless_explicit_or_unique_wrapper`
- L83: FunctionDef `test_no_silent_newline_normalization`
- L87: FunctionDef `test_adjacent_pairs_not_all_pairs_and_version_change`
- L97: FunctionDef `test_memory_context_same_does_not_hide_changed_system_prompt`
- L108: FunctionDef `test_same_body_can_have_different_token_ids`
- L121: FunctionDef `test_different_tokenizer_ids_not_compared`
- L127: FunctionDef `test_missing_session_does_not_cross_link`
- L134: FunctionDef `test_same_memory_id_in_different_sessions_does_not_cross_link`
- L140: FunctionDef `test_missing_subject_uses_body_identity_without_claiming_version_changes`
- L148: FunctionDef `test_repeated_memory_in_request_does_not_arbitrarily_select_one`
- L156: FunctionDef `test_context_duplicate_in_full_prompt_not_arbitrarily_selected`
- L166: FunctionDef `test_edit_maps_cover_both_sequences_and_reconstruct_new`

## kv_ana/tests/test_state_research_artifact_audit.py

Lines: 49; encoding: utf-8

Single frozen-core exception: canonical artifact provenance, not SVD mathematics.

- L15: FunctionDef `test_frozen_artifact_is_identical_across_python_hash_seeds`
- L38: FunctionDef `test_canonical_provenance_keeps_fit_basis_and_audit_ids`

## kv_ana/tests/test_state_research_batch1_audit.py

Lines: 119; encoding: utf-8

Adversarial regressions for input identity and explicit state/trace evidence.

- L13: FunctionDef `requests`
- L17: FunctionDef `bridge`
- L22: FunctionDef `test_engine_id_reuse_is_scoped_to_explicit_service_run`
- L33: FunctionDef `test_engine_alias_does_not_leak_into_a_different_service_run`
- L39: FunctionDef `test_missing_service_run_is_ambiguous_if_engine_id_was_reused`
- L45: FunctionDef `test_agent_run_alias_is_respected_and_conflicting_aliases_fail_join`
- L57: FunctionDef `test_explicit_unknown_but_conflicting_alias_is_not_ignored`
- L64: FunctionDef `test_duplicate_requests_cannot_silently_replace_correlation_identity`
- L72: FunctionDef `test_empty_conversations_are_isolated_not_cross_linked`
- L82: FunctionDef `test_real_conversation_cannot_collide_with_missing_session_marker`
- L91: FunctionDef `test_memory_and_session_identities_are_dataset_scoped`
- L99: FunctionDef `test_explicit_numeric_zero_request_id_is_preserved`
- L107: FunctionDef `test_snapshot_must_match_request_model_and_context`
- L114: FunctionDef `test_matching_snapshot_still_passes_without_inventing_runtime_evidence`

## kv_ana/tests/test_state_research_contract.py

Lines: 268; encoding: utf-8

- L20: FunctionDef `prepared`
- L24: FunctionDef `test_observed_order_uses_timestamps_and_actual_interarrival`
- L32: FunctionDef `test_constructed_order_has_no_invented_timing`
- L43: FunctionDef `test_observed_time_needs_common_clock_and_complete_timestamps`
- L50: FunctionDef `test_timestamp_ties_are_disclosed`
- L56: FunctionDef `test_missing_request_is_a_gap_not_a_pairwise_jump`
- L63: FunctionDef `test_duplicate_request_id_is_rejected`
- L70: FunctionDef `test_agentmon_explicit_bridge_resolves_out_of_order_events`
- L77: FunctionDef `test_agentmon_no_time_or_run_id_only_guess`
- L83: FunctionDef `test_agentmon_conflicting_ids_are_not_joined`
- L92: FunctionDef `test_shared_alias_is_ambiguous_not_arbitrary`
- L100: FunctionDef `test_different_agent_runs_can_disambiguate_same_alias`
- L108: FunctionDef `test_physical_aliases_deduplicated_but_device_replicas_counted`
- L117: FunctionDef `test_conflicting_allocation_bytes_rejected`
- L124: FunctionDef `test_v41_catalog_is_unverified_and_v4_points_to_local_source`
- L133: FunctionDef `test_input_formats`
- L143: FunctionDef `test_sidecar_conflict_not_silently_overwritten`
- L148: FunctionDef `test_sidecar_adds_explicit_metadata_without_changing_source`
- L158: FunctionDef `test_cli_outputs_are_separated_and_existing_output_protected`
- L171: FunctionDef `test_missing_full_prompt_report_is_partial_not_functional_success`
- L181: FunctionDef `test_representative_cases_have_no_target_based_selection`
- L188: FunctionDef `test_empty_dataset_rejected`
- L193: FunctionDef `test_import_does_not_load_torch`
- L200: FunctionDef `test_local_tokenizer_is_local_only_and_keeps_special_token_policy`
- L227: FunctionDef `test_existing_runtime_tokens_win_over_optional_retokenizer`
- L236: FunctionDef `test_state_snapshot_dependencies_preserved_and_missing_dependencies_disclosed`
- L244: FunctionDef `test_state_snapshot_unknown_request_not_silently_attached`
- L250: FunctionDef `test_agentmon_colliding_bridges_keep_engine_only_events_ambiguous`
- L260: FunctionDef `test_synthetic_script_end_to_end`
- L204: ClassDef `FakeTokenizer`
- L211: ClassDef `Auto`
- L228: ClassDef `MustNotCall`
- L207: FunctionDef `__call__`
- L213: FunctionDef `from_pretrained`
- L229: FunctionDef `tokenize`

## kv_ana/tests/test_state_research_lifecycle.py

Lines: 162; encoding: utf-8

- L11: FunctionDef `test_shared_allocation_lifecycle_and_consumption_are_separate`
- L23: FunctionDef `test_move_migrates_all_aliases_and_reports_two_pools_without_double_charge`
- L32: FunctionDef `test_update_changes_all_alias_versions`
- L41: FunctionDef `test_bad_lifecycle_rejected`
- L53: FunctionDef `test_cannot_consume_while_paused`
- L60: FunctionDef `test_release_dependency_and_duplicate_release_rejected`
- L69: FunctionDef `test_restore_is_explicit_new_generation_and_replica_counts_separately`
- L76: FunctionDef `test_missing_dependency_rejected`
- L82: FunctionDef `test_recorder_disabled_no_directory_enabled_json_roundtrip`
- L92: FunctionDef `test_replay_budget_unique_work_and_no_timing`
- L105: FunctionDef `demand`
- L111: FunctionDef `test_future_accesses_cannot_change_replay_prefix`
- L119: FunctionDef `test_bundle_eviction_differs_from_individual_lru`
- L128: FunctionDef `test_oversized_working_set_bypasses_without_fake_residency`
- L135: FunctionDef `test_type_budget_isolation_and_missing_partition`
- L143: FunctionDef `test_stale_version_cannot_hit`
- L151: FunctionDef `test_alias_type_does_not_change_physical_partition_or_duplicate_demand`

## kv_ana/tests/test_state_research_lifecycle_audit.py

Lines: 216; encoding: utf-8

- L14: FunctionDef `event`
- L19: FunctionDef `moved_trace`
- L30: FunctionDef `test_address_migration_does_not_force_alternative_policy_content_miss`
- L44: FunctionDef `test_consumption_on_other_device_is_not_faked_as_same_device_hit`
- L53: FunctionDef `test_same_address_reallocation_cannot_reuse_dead_content`
- L65: FunctionDef `test_update_after_move_still_invalidates_content_hit`
- L72: FunctionDef `test_storage_resize_requires_readmission_not_descriptor_crash`
- L82: FunctionDef `test_implicit_alias_cannot_bypass_share_identity_validation`
- L89: FunctionDef `test_dependency_content_change_is_reported_until_explicit_refresh`
- L105: FunctionDef `test_dependency_storage_move_is_not_a_content_invalidation`
- L114: FunctionDef `test_one_demand_cannot_contain_two_versions_of_one_cache_object`
- L126: FunctionDef `test_inventory_rejects_fractional_capacity_or_invalid_version_geometry`
- L133: FunctionDef `test_bad_snapshot_binding_is_rejected_before_writing_uninterpretable_tensor_file`
- L144: FunctionDef `test_consumption_cannot_upgrade_synthetic_state_to_runtime_evidence`
- L151: FunctionDef `test_dependency_provenance_is_preserved_in_policy_report`
- L162: FunctionDef `test_same_context_content_update_invalidates_dependencies_without_faking_prompt_change`
- L176: FunctionDef `test_cross_request_share_preserves_independent_context_identity_on_content_update`
- L193: FunctionDef `test_legacy_context_only_update_is_disclosed_not_silently_modernized`
- L199: FunctionDef `test_runtime_context_only_update_is_rejected_but_explicit_content_update_works`
- L211: FunctionDef `test_content_version_must_increase_even_with_new_context`

## kv_ana/tests/test_state_research_recovery.py

Lines: 242; encoding: utf-8

- L21: FunctionDef `test_cost_profile_uses_median_and_exact_scope`
- L32: FunctionDef `test_invalid_calibration_rejected`
- L38: FunctionDef `test_unproven_direct_reuse_blocked_and_best_costed_plan_is_not_execution`
- L45: FunctionDef `test_missing_provider_never_becomes_a_surrogate`
- L51: FunctionDef `test_no_cost_does_not_mean_free`
- L56: FunctionDef `test_partial_cost_does_not_claim_global_optimum`
- L61: FunctionDef `test_synthetic_validity_does_not_authorize_runtime_plan`
- L69: FunctionDef `test_fit_evaluation_overlap_rejected`
- L76: FunctionDef `test_target_b_not_a_planner_input_signal`
- L83: FunctionDef `comparison_args`
- L92: FunctionDef `test_explicit_distinct_rows_and_frozen_metrics`
- L100: FunctionDef `test_zero_degradation_is_not_swallowed`
- L108: FunctionDef `test_bad_artifact_mapping_never_inferred`
- L119: FunctionDef `test_dependency_reachability_and_cycle`
- L127: FunctionDef `test_schedule_is_explicit_constructed_no_service_time`
- L135: FunctionDef `test_full_offline_suite_and_npz_interchange`
- L146: FunctionDef `test_missing_real_inputs_not_reported_as_pass`
- L152: FunctionDef `test_corrupted_trace_blocks_replay_but_other_offline_checks_continue`
- L161: FunctionDef `test_capsule_hard_utf8_byte_bound`
- L169: FunctionDef `test_snapshot_aliases_preserve_arrays_but_not_duplicate_physical_accounting`
- L181: FunctionDef `test_snapshot_quantized_values_not_silently_converted`
- L192: FunctionDef `test_snapshot_no_implicit_tensor_or_dtype_conversion`
- L204: FunctionDef `test_suite_rejects_cross_condition_split_leakage`
- L216: FunctionDef `test_suite_empty_trace_and_ambiguous_joins_not_false_pass`
- L225: FunctionDef `test_full_offline_suite_does_not_import_model_frameworks`
- L194: ClassDef `DeviceTensor`
- L195: FunctionDef `cpu`

## kv_ana/tests/test_state_research_recovery_audit.py

Lines: 148; encoding: utf-8

Adversarial checks for comparison scope and externally supplied state alignment.

- L14: FunctionDef `runtime_scope`
- L19: FunctionDef `runtime_trial`
- L24: FunctionDef `runtime_case`
- L33: FunctionDef `test_cost_scope_does_not_discard_experiment_dimensions`
- L44: FunctionDef `test_cost_variant_and_parameters_do_not_share_a_median`
- L55: FunctionDef `test_canonical_scope_and_parameters_ignore_serialization_order`
- L64: FunctionDef `test_incomplete_runtime_granularity_cannot_be_ready`
- L75: FunctionDef `test_incomplete_runtime_candidate_contract_cannot_be_ready`
- L82: FunctionDef `test_top_level_target_b_declaration_is_not_ignored`
- L89: FunctionDef `test_unresolved_runtime_alternative_is_not_complete_plan`
- L99: FunctionDef `comparison`
- L108: FunctionDef `test_distinct_corrected_row_indices_require_matching_canonical_coordinates`
- L119: FunctionDef `test_real_comparison_requires_explicit_corrected_coordinates`
- L126: FunctionDef `test_legacy_synthetic_coordinates_are_explicitly_unverified`
- L134: FunctionDef `test_duplicate_canonical_positions_rejected`
- L142: FunctionDef `test_corrupted_npz_is_a_case_level_value_error`

## kv_ana/tests/test_state_research_suite_audit.py

Lines: 166; encoding: utf-8

Integration adversaries: passing an empty or unrelated experiment is not acceptance.

- L12: FunctionDef `read`
- L16: FunctionDef `test_zero_consumption_cannot_pass_policy_experiment`
- L28: FunctionDef `test_empty_or_duplicate_policy_list_rejected`
- L36: FunctionDef `test_bad_policy_retains_other_policy_results`
- L46: FunctionDef `test_bad_artifact_preserves_good_state_comparison`
- L60: FunctionDef `test_bad_recovery_case_preserves_good_plan`
- L75: FunctionDef `test_manifest_linkage_is_not_assumed`
- L86: FunctionDef `test_duplicate_manifest_request_is_not_silently_collapsed`
- L97: FunctionDef `test_capsule_hard_limit_checked_before_output`
- L103: FunctionDef `test_capsule_utf8_limit_and_evidence_not_hidden`
- L116: FunctionDef `test_legacy_synthetic_mapping_is_not_reported_as_verified`
- L127: FunctionDef `test_runtime_cost_variant_contract_cannot_report_ready_when_missing`
- L145: FunctionDef `test_state_request_manifest_model_and_context_are_checked`
- L156: FunctionDef `test_legacy_context_only_updates_are_visible_as_partial`

## kv_ana/tests/test_v201_correctness_patch.py

Lines: 295; encoding: utf-8

- L23: FunctionDef `_pair`
- L44: FunctionDef `_config`
- L55: FunctionDef `test_v201_aggregation_separates_static_position_binners`
- L83: FunctionDef `test_v201_aggregation_separates_anchor_weighting`
- L94: FunctionDef `test_v201_aggregation_separates_recursive_composite_config`
- L119: FunctionDef `_rope_metadata`
- L131: FunctionDef `test_v201_split_half_multi_head_source_to_target`
- L146: FunctionDef `test_v201_split_half_partial_rotary_preserves_tail`
- L164: FunctionDef `test_v201_rope_odd_rotary_dim_fails_fast`
- L172: FunctionDef `test_v201_same_sample_layers_cannot_cross_split`
- L182: FunctionDef `test_v201_same_sample_all_train_layers_pass`
- L191: FunctionDef `test_v201_base_pair_cannot_cross_split_by_condition_or_state`
- L201: FunctionDef `test_v201_shared_then_frozen_lowrank_fails_fast`
- L207: FunctionDef `test_v201_frozen_lowrank_then_selective_is_allowed`
- L218: FunctionDef `test_v201_shared_then_selective_is_allowed`
- L226: FunctionDef `test_v201_zero_residual_unchanged_remains_zero_residual`
- L236: FunctionDef `test_v201_zero_baseline_degradation_preserves_corrected_sse`
- L245: FunctionDef `_aggregate_row`
- L257: FunctionDef `test_v201_mixed_zero_and_degraded_aggregation`
- L283: ClassDef `_BadZeroCorrection`
- L290: FunctionDef `test_v201_runner_default_policy_rejects_zero_degradation`
- L286: FunctionDef `correct`

## kv_ana/tests/test_v2_regressions.py

Lines: 351; encoding: utf-8

- L23: FunctionDef `pair`
- L50: FunctionDef `config`
- L55: FunctionDef `fit_context`
- L60: FunctionDef `test_v2_state_identity_separates_model_condition_and_state`
- L73: FunctionDef `test_v2_aggregation_rank_isolation`
- L86: FunctionDef `test_v2_aggregation_budget_isolation`
- L97: FunctionDef `test_v2_aggregation_probe_isolation`
- L108: FunctionDef `test_v2_frozen_basis_shared_artifact_single_svd`
- L121: FunctionDef `test_v2_pair_oracle_multi_rank_single_decomposition`
- L130: FunctionDef `_rope_metadata`
- L142: FunctionDef `test_v2_multi_head_rope_shift_is_head_local`
- L157: FunctionDef `test_v2_partial_rotary_dim_preserves_each_head_tail`
- L167: FunctionDef `test_v2_static_layer_neutral_tie_is_order_independent`
- L182: FunctionDef `test_v2_group_atomic_layer_selector_records_overshoot`
- L196: FunctionDef `test_v2_fixed_edge_position_bins_use_x_start`
- L204: FunctionDef `test_v2_normalized_position_bins_cover_depth`
- L210: FunctionDef `_exact_detector_method`
- L217: FunctionDef `test_v2_detector_exact_recompute_corrects_detector_layers`
- L230: FunctionDef `test_v2_detector_cost_is_not_duplicated`
- L242: FunctionDef `test_v2_external_detector_requires_real_provider`
- L253: FunctionDef `test_v2_anchor_missing_descriptor_is_capability_missing`
- L262: FunctionDef `test_v2_composite_rejects_metric_only_stage`
- L267: ClassDef `HalfResidual`
- L277: FunctionDef `test_v2_composite_second_stage_uses_updated_residual`
- L285: FunctionDef `_summary_row`
- L296: FunctionDef `test_v2_mixed_zero_nonzero_pooled_uses_only_valid_residual`
- L304: FunctionDef `test_v2_checkpoint_resume_across_k_v_prefix_position_and_models`
- L321: FunctionDef `test_v2_checkpoint_rejects_changed_dataset_under_same_config`
- L328: ClassDef `StaticProvider`
- L336: FunctionDef `test_v2_precomputed_artifact_provider_loads_without_svd`
- L272: FunctionDef `correct`
- L329: FunctionDef `__init__`
- L332: FunctionDef `frozen_subspace`

## README.md

Lines: 52; encoding: utf-8

- # Heterogeneous Inference State Lab
- ## 使用
- # 或一次运行全部；输出目录必须新建/空
- ## 文档

## scripts/package_workspace.py

Lines: 46; encoding: utf-8

Package current workspace without outputs, environments or raw data.

- L14: FunctionDef `main`

## scripts/validate_workspace.py

Lines: 55; encoding: utf-8

Orchestrate existing offline acceptance; never start a model/service.

- L13: FunctionDef `main`
