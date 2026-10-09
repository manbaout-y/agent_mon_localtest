# 2026-10-09用户预检：哪些事实可用，哪些还未知

原文件：`C:/Users/ybl/Downloads/V41_PREFLIGHT_20261009_121818.txt`。时间：2026-10-09T12:18:18+08:00。
来源为用户提供的服务器输出，**不是Codex本次远程复测**。同时阅读两份`E:/agentproject/DSV41_C1_…_V2_20261009.md`及仓库内V3计划。文档中的命令是方案，不能当作已经执行的事实；本轮以用户明确授权的单请求、只读采样、禁止改服务为准。

## 已验证的快照

| 项目 | 预检读数 | 范围 |
|---|---|---|
| 容器 | dsv41-smoke-256k，running | 该次用户快照 |
| StartedAt | 2026-10-09T03:31:32.728860664Z | 与10月8日不同启动实例 |
| API Host PID / Engine Host PID | 942797 / 943582 | 不能照搬到新run |
| vLLM版本/磁盘HEAD | 0.27.1+empty / 6e448d0ea9bf3d88d898b65449ca6dc2aec170ac | metadata / SOURCE，不是已加载模块证明 |
| Ascend版本/磁盘HEAD | 0.1.dev5097+ge43cf1e9f / e43cf1e9f5d9bead076853aa6bcacb671465de94 | 同上 |
| 服务 | deepseek-v41，262144，TP8/EP，max_num_seqs=1 | API/argv |
| HBM设备0..7 used | 58828、58822、58822、58821、58818、58817、58818、58817 MB | npu-smi原reported MB，每卡容量65536 |
| Worker NPU进程内存 | 每个55435 MB | 与整卡HBM占用不同口径 |
| Worker RSS | 约1642万kB/进程 | Host驻留内存，共享页可能重复 |
| Host MemTotal / MemAvailable | 1579637336 / 1099684268 kB | 整机；不是Engram或KV对象字节 |
| 模型所在文件系统 | /dev/nvme1n1p1，xfs，/data/node0_disk2 | 选这一个分区计数，不与整盘累加 |
| 空闲服务计数 | running/waiting/KV usage/prefix queries/hits为0 | 仅该时刻，不证明持续独占 |
| block配置 | argv128，metrics32 | 两份直接读数；含义UNRESOLVED |

8个Worker Host PID：944079、944651、944993、945281、946202、946538、946914、947631。角色/TP-EP标题在用户进程表中可见；新采集器会重新发现PID，不用这组历史值指定运行进程。

## 对本轮实验的影响

1. 服务今天重启过，昨日Prefix累计781/256不得作为今日before，也不能跨epoch求增量。
2. `/proc/944079/io`明确Permission denied；保留进程RSS，并采集磁盘窗口I/O，不能用设备I/O冒充Worker I/O。
3. `VmSize=9704073880 kB`是虚拟地址空间，不是物理DDR；不能与Host容量相减或当权重驻留。
4. npu-smi设备DDR容量0不是Host内存容量0。Host使用Linux meminfo。
5. `Cached`、`Shmem`等不能简单相加后声称归于模型；共享页和文件缓存有口径交叠。
6. image digest在这份预检中未打印。新演练会与既有历史digest核对；不一致就阻塞POST，不自动build。
7. 尚无块设备前/中/后采样，也无本轮请求完成证据。预检不是一次推理验收。

## 明确未知

| 状态 | 内容 |
|---|---|
| UNRESOLVED | argv128/metrics32的真实分组语义；运行进程实际加载模块来源 |
| NOT_OBSERVED | Worker物理I/O、具体状态allocation、请求→Block→Layer/Rank映射、实际HBM↔DDR/SSD迁移 |
| INFERRED | 主机/设备资源变化可用于描述实验窗口，不能直接归属特定权重或KV |
| VERIFIED | 上表原始快照读数，限定用户提供的时间与启动实例 |

无需先把完整Hook写好才能采集本轮L0；但L0成功也不表示每个模型状态已经白盒化。下一步操作见[服务器手册](SERVER_RUNBOOK.md)。
