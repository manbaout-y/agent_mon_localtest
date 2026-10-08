# 工作区约定

- 从系统角度思考，避免过度防御、罕见场景抽象和重复hash基础设施。
- agentmon/与kv_ana/保留独立包名、pyproject、测试、配置。
- 联合层使用事件与明确身份绑定，不让两边生产core互相导入。
- 新开发仅在此工作区；原目录是来源快照，不自动同步。
- 不随意修改frozen KVReuse2.0.1数值、method_config_hash、split、strict mapping。
- 区分本地synthetic、用户历史服务器报告和本次真实验收，不伪造provider/PASS。
- V4.1 client不等于runtime capture；历史V4 patch/P2不自动兼容V4.1。
- 默认不访问服务器、不启动模型/GPU/NPU、不执行runtime patch，需单独授权。
- 不打包环境、模型、raw tensors、outputs、旧发布包；必要paper specs保留原相对路径。
- 修改后运行子项目测试及联合验收；不在迁移中夹带算法重构。
