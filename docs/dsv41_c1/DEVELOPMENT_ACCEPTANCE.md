# C=1采集工具开发验收与变更清单

日期：2026-10-09。**离线开发验收通过；完整工作区验收有失败；服务器真实C=1待执行。**

本地为Windows、Python3.10.9（`F:/Anaconda/python.exe`）。服务器沿用现有mini-swe-v243 Python3.11。本轮没有SSH连接、没有访问8800、没有运行模型或NPU、没有修改在线DeepSeek服务。测试里的HTTP/NPU记录是合成夹具，不是服务器实测。

## 变更与复用

| 文件 | 内容 |
|---|---|
| agentmon/scripts/dsv41_c1_observe.py | 版本/容器/Host PID/挂载检查；零POST演练；唯一请求、独立采样线程 |
| agentmon/src/agentmon/analysis/c1_observations.py | 910B解析、窗口差分、reset/PID复用检查、严格阶段分类、覆盖统计 |
| agentmon/src/agentmon/analysis/c1_reports.py | 同一数据生成三份报告，失败和演练也报告 |
| agentmon/tests/unit/test_c1_observations.py | 新采集器和报告的行为测试 |
| docs/dsv41_c1/USER_PREFLIGHT_REVIEW.md | 用户最新预检的证据边界 |
| docs/dsv41_c1/SERVER_RUNBOOK.md | 服务器演练、一次正式请求、检查与回传命令 |
| README.md、两份V3计划 | 加入已实现入口链接，保留历史规划 |

复用`sampling/host.py`的meminfo/diskstats读函数和`analysis/prometheus.py`。既有procfs读取失败返回空集合，本入口单独保留PermissionError。现有NPU通用解析可能混入进程表行，本轮新增已提供910B格式的纯函数，不改旧采样器/P1/P2生产core。

kv_ana、冻结算法/数值、论文recipe、vLLM/Ascend源码与镜像无修改。不将旧0.18 Hook套到0.27.1。

## 实际测试

| 检查 | 结果 | 限制 |
|---|---|---|
| 新入口+既有DSV41/Prometheus/NPU/Host定向回归 | **78 passed** | 离线 |
| CLI --help、三个生产文件py_compile | exit0 | 不代表Linux真实采样 |
| 用户原文NPU表解析 | 8卡，58828/58822/58822/58821/58818/58817/58818/58817 | USER_EVIDENCE解析，非本地NPU实测 |
| 联合core：KV测试 | 332 passed、1 failed | 论文14分类BLOCKED_SOURCE，测试期望OFFLINE_COMPONENT_EXECUTABLE |
| 联合core：AgentMon全测试 | 30个collection errors | Windows缺fcntl/resource/os.sysconf等Linux接口 |
| 联合core：framework smoke | PASS，448 records / 224 groups | 合成数据 |
| 联合core：state smoke | 10项PASS | 离线，不是状态捕获/执行 |
| 联合core：recipes | exit0 | 不替代缺失论文源码或模型验收 |
| 联合core：完整AgentMon CLI | FAIL | Linux专用fcntl导入限制 |
| 联合core：compileall | PASS | 语法检查 |
| 服务器演练/请求/实际采样开销 | NOT_OBSERVED | 需操作者执行手册 |

**完整core最终FAIL，没有修改测试期望掩盖失败。** 历史验收未覆盖，本轮另存日志。新入口绕开完整CLI导入链，但真实Linux权限/耗时/指标刷新必须通过dry-run验证。

本机证据目录`outputs/dsv41-c1-development-20261009/`被Git忽略：

- `targeted-final.log` / `targeted-final.xml`：78项回归。
- `cli-help.txt`：实际CLI参数。
- `user_preflight_original.txt` / `user_preflight_parse.json`：原文副本和实际表解析。
- `workspace-core/report.json`与step日志：联合通过/失败依据。

## 关键验证点

1. 默认不POST；正式模式需要已通过演练及独占声明；不覆盖run。
2. 容器、启动实例、PID、代码commit、挂载改变会使旧演练失效。
3. 302/500/连接失败/断流/空正文不重试；完整SSE结束才标请求成功。
4. POST前持久记录尝试；最终响应证据落盘异常也保留已消耗尝试。
5. 真实0与缺失null区分；设备I/O不替代进程I/O。
6. Counter减少、可见中途reset/created变更、epoch/PID改变禁止正常差分。
7. overlap不冒充during；没有during不补峰值，标PARTIAL。
8. 两份MD和JSON数值一致；内存、字节、范围和sector×512口径明确。

## 已知边界

VERIFIED：本地定向测试和用户预检直接读数（限定来源/时间）。INFERRED：窗口差分解释，不能归属具体状态。NOT_OBSERVED：真实请求/曲线、allocation、迁移及采样性能影响。UNRESOLVED：128/32布局、运行模块身份、完整排除外部流量。

采样duration可审查成本；没有无采样对照请求，不能声称零性能影响。不得通过第二次请求补验。

## 下一步Hook方案：未实施

1. 请求绑定：实际0.27.1的API/header、InputProcessor ID改写、IPC、EngineCore.add_request、Scheduler.add_request，记录启动实例/PID/身份/时间。API响应ID不能直接当Engine ID。
2. 逻辑KV：KVCacheManager.get_computed_blocks/allocate_slots/free及实际Coordinator/BlockPool，记录group/block/refcount/缓存/释放/淘汰；逻辑分配不冒充HBM新allocation。
3. 物理映射：先选一个Rank/Layer/group，核验Worker buffer的device/dtype/shape/stride/allocation/bytes；分别计物理allocation、逻辑view与artifact，核算共享与别名。
4. 跨层事件：在实际执行的Engram CPU查表、传输或文件访问路径记录对象、来源/目标、字节、完成时刻。单有配置/RSS/SSD变化不能标迁移。
5. 部署：先离线签名/多进程适配，再提交隔离实例或维护窗口、开关、容量与回滚方案。当前实例不注入、不改参数、不重启。

下一步执行[服务器手册](SERVER_RUNBOOK.md)的零请求演练。正式run的三份报告才是本轮真实实验交付，本文不能替代。
