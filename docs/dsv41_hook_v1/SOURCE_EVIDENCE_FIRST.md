# 先取得定制源码证据，再完成逻辑 Hook

2026-10-09。用户最新明确要求：以真实服务器源码为依据，不用反复模拟测试代替源码与真实运行证据。

## 为什么不能只凭版本号

现场为 `vllm 0.27.1+empty`，Git `6e448d0ea9bf3d88d898b65449ca6dc2aec170ac`，Ascend `0.1.dev5097+ge43cf1e9f`，并使用 W4A16 / Engram patch。版本标签不包含所有本地修改。公开版本用于参考；实现兼容性必须核对当前容器源码。

还要区分两个事实：读取容器磁盘源码是 STATIC_SOURCE 证据；Hook 启动后报告真实 EngineCore PID、已加载模块路径/哈希、函数及调用事件，才是运行时证据。单独 `docker exec python` 的读取进程不是在线 EngineCore。

## 第一步：提取一份小 TXT

这是普通用户可执行的只读命令，不需 sudo，不需要服务器工作区有 `.git`。脚本不会导入 vLLM / torch，不发 POST，不安装 Hook，不重启、不修改容器文件。

若新脚本已随更新 ZIP 放入工作区：

```bash
cd /home/yangbolun/agent_mon_localtest-main_109
python3 agentmon/scripts/dsv41_hook_source_probe.py \
  --focus kv \
  --output "outputs/hook-source-evidence/KV_SOURCE_$(date +%Y%m%d-%H%M%S).txt"
```

只需要新增的单个脚本，也可以从自己的仓库下载，避免重新部署整个项目：

```bash
mkdir -p /tmp/agentmon-hook-source-probe
curl -fL --connect-timeout 10 --max-time 60 \
  https://raw.githubusercontent.com/manbaout-y/agent_mon_localtest/main/agentmon/scripts/dsv41_hook_source_probe.py \
  -o /tmp/agentmon-hook-source-probe/dsv41_hook_source_probe.py
python3 /tmp/agentmon-hook-source-probe/dsv41_hook_source_probe.py \
  --focus kv \
  --output "$HOME/KV_SOURCE_$(date +%Y%m%d-%H%M%S).txt"
```

下载失败时不执行尚未下载的脚本；可用更新 ZIP 中的同名文件。下载不涉及模型/镜像。运行前可阅读脚本确认行为。

脚本默认提取 `KVCacheManager.get_computed_blocks / allocate_slots / free / get_blocks`、`KVCacheBlocks.get_block_ids`，以及相关类声明。每项有实际路径、函数、原行号、参数、返回类型注解、文件/函数 SHA256。完整可执行函数体仅省略文档字符串、整行注释和空行；不截断代码后假称完整。

输出限 20KB，已有文件拒绝覆盖。若实际版本函数过大，脚本明确失败而不是截断，按提示仅选一个函数：

```bash
python3 agentmon/scripts/dsv41_hook_source_probe.py \
  --function KVCacheManager.allocate_slots \
  --output "outputs/hook-source-evidence/KV_ALLOCATE_$(date +%Y%m%d-%H%M%S).txt"
```

使用 `cat` 显示新生成的 TXT，提供完整内容。先提供这一份即可，不需拉全仓、模型、权重或大型日志。

## 这份证据将决定什么

1. `get_computed_blocks` 的返回值是否为 `(KVCacheBlocks, computed_tokens)`，命中 Block 在哪里。
2. `allocate_slots` 返回新分配部分还是所有持有 Block；分配失败/无新增时如何表示。
3. `get_blocks` 或 Coordinator 中哪个 getter 能只读取得请求当前持有的分组 Block。
4. `free` 解除的引用和前缀缓存保留之间的关系，避免误报物理释放。
5. group 顺序、Block 对象 ID/ref_count 和 null block 的真实结构。

之后依据这些函数的实际调用补取对应的 ID/Scheduler/Coordinator 小段源码，而不是继续做泛化搜索。

## 状态与边界

| 项目 | 状态 |
|---|---|
| 历史服务器 C=1 请求成功 | 用户已有实验，不是 Hook 验收 |
| 新只读源码提取脚本 | 本地实现，安全/格式测试单独记录 |
| 当前容器完整函数体 | NOT_OBSERVED，等待服务器 TXT |
| 首版 Hook 对定制源码兼容 | UNRESOLVED，尚不能宣称完成 |
| 当前在线 EngineCore Hook | NOT_OBSERVED / SERVER_LIVE_PENDING |
| 服务重启、注入、一次真实 POST | 未执行，仍需审批 |

该脚本测试只验证提取器不会覆盖/截断/导入模型；不等于 Hook 测试，不等于真实模型观测。
