"""Three human-readable reports from the same C1 data, including failed/dry runs."""
from __future__ import annotations

import json
from pathlib import Path


def display(value):
    return "未观测（NOT_OBSERVED）" if value is None else str(value)


def write_reports(output: Path, result, identity, rows, commands):
    def save(name, value):
        (output / name).write_text(value, encoding="utf-8")

    request = result["request"]
    table = ["| NPU | 请求前MB | 请求中采样峰值MB | 请求后MB |", "|---|---:|---:|---:|"]
    table += [f"| {r['device_id']} | {display(r['before_mb_reported'])} | {display(r['during_sample_peak_mb_reported'])} | {display(r['after_mb_reported'])} |" for r in result["hbm"]]
    q = result["metric_deltas"]["prefix_cache_queries_total"]["delta"]
    h = result["metric_deltas"]["prefix_cache_hits_total"]["delta"]
    gauge_table = ["| 服务指标 | 请求前 | 请求中采样峰值 | 请求后 |", "|---|---:|---:|---:|"]
    for name, label in (('kv_cache_usage_perc', 'KV使用率（原始比例值）'),
                        ('num_requests_running', '运行中请求数'), ('num_requests_waiting', '等待请求数')):
        values = result['kv_gauges'][name]
        gauge_table.append(f"| {label} | {display(values['before'])} | {display(values['during_sample_peak'])} | {display(values['after'])} |")
    worker_table = ["| Worker Host PID | 请求前RSS字节 | 请求后RSS字节 | 进程I/O读取/写入增量字节 |", "|---|---:|---:|---|"]
    for item in result['processes']:
        if item['role'] == 'worker':
            worker_table.append(f"| {item['pid']} | {display(item['before'].get('rss_bytes'))} | {display(item['after'].get('rss_bytes'))} | {display(item['io_deltas']['read_bytes']['delta'])} / {display(item['io_deltas']['write_bytes']['delta'])} |")
    beginner = ["# 一次DSV41请求：这一轮看到了什么？", "",
                f"本轮状态：**{result['status']}**。这是{'真实HTTP尝试' if request.get('post_attempts') else '零请求演练或预检阻塞'}，不是历史成功结果。",
                f"运行目录：`{output.name}`；HTTP POST尝试数：{request.get('post_attempts', 0)}；请求状态：{request.get('status', 'NOT_OBSERVED')}。", "",
                f"metadata版本：vLLM {display(identity.get('source_metadata', {}).get('vllm'))}，Ascend {display(identity.get('source_metadata', {}).get('vllm-ascend'))}；实际加载模块来源尚未闭环。",
                "## 请求做了什么", "",
                "本入口设计为固定测试提示、thinking=false，并从服务指标、NPU工具和Linux文件采样。实际完成情况以上述状态和下表为准。没有注入Hook或修改模型服务。",
                f"API响应ID：`{display(request.get('api_request_id'))}`；输入/输出Tokens：{display(request.get('prompt_tokens'))} / {display(request.get('completion_tokens'))}。",
                f"HTTP窗口UTC：{display(request.get('started_utc'))} → {display(request.get('ended_utc'))}；请求前/中/后三段采样的真实时刻与覆盖见技术报告。",
                f"客户端总耗时：{display(request.get('e2e_seconds'))}秒；第一条非空正文事件延迟：{display(request.get('content_ttft_seconds'))}秒。", "",
                "## HBM、DDR、SSD分别是什么", "",
                "HBM是NPU的高速设备内存；这里测的是整卡占用，不能直接拆成权重和KV。Host DDR是CPU使用的主机内存；进程RSS表示该进程驻留内存，共享页可能重复出现。SSD保存模型文件，I/O计数表示选定设备在观察窗口内的读写，不是逐权重搬运记录。", "",
                "## 每卡HBM", "", *table, "",
                "单位原样保留npu-smi的MB标记，没有擅自换算GiB。峰值是采样峰值，两个采样之间可能漏掉变化。", "",
                "## 主机、SSD和KV", "",
                "| 指标 | 本次结果 | 范围 |", "|---|---|---|",
                f"| Host MemAvailable前/后字节 | {display((result['host']['before'] or {}).get('MemAvailable'))} / {display((result['host']['after'] or {}).get('MemAvailable'))} | 整台主机，不是offload量 |",
                f"| 磁盘读取/写入增量字节 | {display(result['disk']['read_bytes']['delta'])} / {display(result['disk']['write_bytes']['delta'])} | {result['disk']['device']}设备窗口 |",
                f"| Prefix查询/命中增量 | {display(q)} / {display(h)} | Engine时间窗口，非逐请求物理字节 |",
                f"| 缓存计数比值 | {display(result['metric_deltas']['prefix_ratio']['value'])} | INFERRED，分母为查询增量 |", "",
                *gauge_table, "",
                "例如KV比例0.01表示该服务指标报告约1%使用率，不能用它直接计算某条请求的HBM字节。全部读数为0，也不能证明生成时没有使用KV：服务统计刷新和外部采样都可能漏掉短暂变化。查询/命中增量为0时，复用比值未观测，不补成0%。", "",
                *worker_table, "",
                "RSS包含共享页，不能简单相加当作独占DDR。进程I/O或PSS被权限拒绝时保留原因并记录NOT_OBSERVED，不用零表示失败。", "",
                "## 目前还不知道什么", "",
                "没有Request→Block→Allocation证据，因此不知道具体Layer/Rank/Block的物理字节，也不能证明发生HBM↔DDR/SSD迁移。HBM占用不变可能与预分配池有关，但本轮没有对象证据，不能作为已验证原因。",
                f"缺少完整前/中/后覆盖的数据源：{result['missing_full_phase_coverage']}；旁路流量指标：{result['other_traffic_indicator']}，操作者独占声明仍不等于数学证明。", "",
                "## 下一步", "",
                "先核对本轮采样缺口；需要深入时，在0.27.1实际执行进程建立请求身份关联，再观察Coordinator/BlockPool对象，最后连接Worker的Layer/Rank/buffer。当前不自动部署Hook。",
                "详情：[技术报告](02_TECHNICAL_REPORT.md)，原始小型索引：[原始观测](03_RAW_OBSERVATIONS.txt)。", ""]
    save("01_BEGINNER_SUMMARY.md", "\n".join(beginner))
    technical = ["# C1技术报告", "", f"验收：{result['status']}；当前来源为本次输出目录。", "",
                 "## 环境与进程", "", "```json", json.dumps(identity, ensure_ascii=False, indent=2), "```", "",
                 "## 客户端请求", "", "```json", json.dumps(request, ensure_ascii=False, indent=2), "```", "",
                 "## 资源及差分", "", *table, "", "```json",
                 json.dumps({k: v for k, v in result.items() if k not in ('request', 'hbm')}, ensure_ascii=False, indent=2), "```", "",
                 "## 方法与公式", "",
                 "每次采样记录UTC、主机monotonic_ns起止与实际duration；严格during样本的完整采集区间必须位于HTTP起止之间。overlap不冒充during。各来源独立线程、不并发堆积同一采样命令。",
                 "采样间隔不等于服务内部统计刷新周期；若短请求未被KV gauge捕获，保留原始0及覆盖时间，不能反推没有KV。请求API ID是响应对象ID，尚非Engine/Block身份绑定。",
                 "差分采用最后一条pre与最后一条post读数，包含post观察期以等待清理/指标刷新；这是窗口增量，不是精确请求边界。started_ns在持久记录尝试前取值，ended_ns在连接收尾后取值，属于客户端边界而非服务端执行边界。",
                 "Counter delta=同series后值-前值；降值、created变化、容器epoch变化时不计算。有限采样不能排除所有reset。窗口可能包含其他流量，不标REQUEST_EXACT。",
                 "磁盘字节=sector delta×512；只选一个映射设备，不相加分区和整盘。进程I/O对PID+starttime相同才计算。Host数值以LinuxkB×1024转换，NPU保留原reported MB。",
                 "RSS/PSS和设备HBM都不是特定权重/KV physical allocation。VmSize不是物理驻留量。PSS仅前后读取，禁止高频页表扫描；PermissionError按NOT_OBSERVED。", "",
                 "## 状态归属与Hook建议", "",
                 "Dense/MoE权重、Engram、KV的具体物理归属与迁移事件均NOT_OBSERVED；可见的HBM/Host/I/O为设备、主机、进程或块设备窗口数据。实际argv与cache_config_info分别记录；历史128/32差异仍UNRESOLVED，不估算KV物理字节。",
                 "runtime_loaded_modules=UNRESOLVED；另起docker exec的metadata/磁盘HEAD不证明已运行EngineCore模块。没有启用vllm_018 Hook。后续0.27.1适配先完成API/IPC/Engine/Scheduler身份，再读实际Block引用与allocation，独立批准后部署。", "",
                 "## 原始证据与异常", "",
                 "参见samples/*.jsonl、raw/*、commands.jsonl、runtime_identity.json、metric_deltas.json、residency_evidence.json、response_summary.json和03_RAW_OBSERVATIONS.txt。",
                 "状态含义：VERIFIED=直接读数或可复核差分；INFERRED=公式/窗口解释；NOT_OBSERVED=缺权限/字段/样本/事件；UNRESOLVED=语义或身份不能闭环。客户端timeout不证明server终止；报告保留终态未知。", ""]
    save("02_TECHNICAL_REPORT.md", "\n".join(technical))
    raw = ["C1 raw observation index (not a tensor/model dump)", f"RUN={output.name}",
           "No values below are copied from historical preflight or synthetic tests.", "COMMANDS:"]
    raw += [json.dumps(c, ensure_ascii=False) for c in commands]
    raw += ["OBSERVATIONS:"] + [json.dumps(r, ensure_ascii=False) for r in rows]
    raw += ["RESPONSE:", json.dumps(request, ensure_ascii=False), ""]
    save("03_RAW_OBSERVATIONS.txt", "\n".join(raw))
    save("single_request_report.json", json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    save("metric_deltas.json", json.dumps(result["metric_deltas"], ensure_ascii=False, indent=2)+"\n")
    residency = [{"state_type": obj, "physical_bytes": None, "request_binding": "NOT_OBSERVED",
                  "memory_tier": "UNRESOLVED", "offload_event": "NOT_OBSERVED",
                  "status": "NOT_OBSERVED", "reason": "resource_samples_do_not_identify_object_allocations"}
                 for obj in ("DENSE_WEIGHT", "MOE_WEIGHT", "ENGRAM", "KV_CACHE", "OTHER")]
    save("residency_evidence.json", json.dumps(residency, ensure_ascii=False, indent=2)+"\n")
    save("capsule.txt", f"STATUS={result['status']}\nPOST_ATTEMPTS={request.get('post_attempts',0)}\nHOOK_INSTALLED=false\nSERVICE_MODIFIED=false\nOFFLOAD=NOT_OBSERVED\n")
