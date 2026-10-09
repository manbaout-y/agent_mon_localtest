# 源码提取器测试记录

2026-10-09，Windows 本地 Python 3.10.9。仅提取器安全/格式验证，不是逻辑 Hook 验收。

- 新提取器测试：5 passed。
- 与已有 C1 观测、审计、Prometheus、NPU/Host 解析相关测试联合执行：90 passed in 0.78s。
- 日志：`outputs/dsv41-hook-source-probe-20261009/targeted.log`。
- JUnit：`outputs/dsv41-hook-source-probe-20261009/targeted.xml`。

覆盖：嵌入程序编译；不导入模型包；ZIP/无 Git 工作区只调用只读 Docker exec；清除另起提取进程的旧/新 Hook flag 并使用 Python `-I`；已有证据拒绝覆盖；权限/命令失败、格式错误、超过 20KB 时不保存误导性文件。

未执行：真实容器源码提取、EngineCore Hook、模型 POST、Hook 独立进程烟测、完整项目联合验收。因此不标记 `LOCAL_HOOK_PASS` 或 `LIVE_LOGICAL_HOOK_VERIFIED`。

待用户提供当前容器的 KV 源码 TXT 后，继续按真实返回结构和调用点实现适配器。
