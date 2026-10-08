# 迁移报告 — 2026-09-24

## 来源与方式

- AgentMon：/Users/yang/Desktop/project_acs/infra/load_monitor_optimizer_harness
- KVReuse：/Users/yang/Desktop/project_acs/infra/kv_ana
- 新工作区：/Users/yang/Desktop/project_acs/infra/hetero_state_lab

迁移前确认目标不存在。两个来源均非Git工作树。使用当前完整源码清单生成临时
归档后复制到新目录，不以昨日dist包替代当前源码，不移动/删除原目录。
633个项目文件保留原字节内容；顶层BUILD_MANIFEST与SOURCE_TREE保留输入清单。
具体计数应以清单及acceptance实际检查为准。

## 新增顶层内容

README、AGENTS、.gitignore、docs、integration说明、validate_workspace.py、package_workspace.py。
子项目原相对布局和现有联合脚本均保留；只在顶层指定source roots/cwd/PYTHONPATH。
没有修改正式算法、adapter、method hash、provider、配置或数值口径。

## 排除

outputs/reports、原始PT/模型、虚拟环境、cache、下载PDF/第三方完整repo、旧发布包。
paper_specs保留原相对路径；历史runtime reference/patch保留且不自动执行。
文献原材料仍在旧目录，仅需重新审查原文时单独使用，不作为新工作区运行依赖。

## 后续入口

验收通过后在新工作区开发。原目录作为历史快照，不自动双向同步，不改权限、不删除。
未自动git init/commit/push，可后续单独建立统一版本库。
本轮不改变Codex当前对话的工作目录：新对话应选择新文件夹。
