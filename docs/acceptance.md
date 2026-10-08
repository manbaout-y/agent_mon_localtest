# 迁移验收 — 2026-09-24

在新工作区用 Python3.11、mini-swe-agent2.4.3、LiteLLM1.90.1、OpenAI2.44.0执行
scripts/validate_workspace.py --suite all；解释器来自已有临时环境，没有复制进工作区。

| 检查 | 结果 |
|---|---|
| kv_ana full pytest | 333 passed |
| AgentMon full pytest | 466 passed、1 skipped、17 subtests passed |
| framework synthetic | PASS，448 records、224 groups |
| state research synthetic | 10/10 PASS |
| recipe registry | 20篇加载成功，保留blocked/component分类 |
| AgentMon CLI | PASS |
| compileall | PASS |
| V4.1 wire | 15/15 PASS，真实serializer、synthetic响应 |
| 633个迁入项目文件 | 与原目录逐字节一致 |
| core/顶层入口对旧绝对路径依赖扫描 | 未发现指定旧项目路径 |

原始日志位于outputs/migration-acceptance，不随源码打包。
AgentMon测试只使用本机loopback fixture。wire validator阻断真实socket连接。
既有1个skip不算服务器PASS；未访问服务器、执行模型/GPU/NPU、应用runtime patch。
依赖环境不随源码分发；可用顶层脚本在其它目录重跑。

## 独立解压验收

候选包解压到/private/tmp下，从项目之外调用顶层入口--suite all，8个步骤全部PASS。
KVReuse333passed、AgentMon466passed/1skipped/17subtests、两个synthetic smoke、
20-paper registry、CLI、compile及15项wire检查均再次通过。
新入口report包含expected_steps，未完成时标RUNNING，不把中间结果当最终PASS。
归档无绝对member路径、../、symlink、cache、raw tensors或outputs，646个文件。
最终发布包仅相对候选包更新本验收文字，执行代码不变。
