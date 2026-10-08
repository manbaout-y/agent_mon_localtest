# AgentMon × KVReuse 完整源码说明与使用指南

版本：联合源码快照 2026-09-23。本文是当前代码的导航与操作手册，不是服务器验收报告。

## 1. 项目目标、版本与边界

两个项目保持独立，通过事件、request sidecar 和显式 runtime binding 交换证据：

| 项目/层 | 职责 | 当前边界 |
|---|---|---|
| AgentMon | Agent 任务运行、model/tool span、进程/设备观测、batch、报告与离线分析 | 是否有真实 runtime 证据取决于实际部署及 adapter 版本 |
| KVReuse frozen framework 2.0.1 | StatePair 算法评估、train/test 隔离、artifact、指标与 checkpoint | representation-level 结果，不证明生成安全 |
| KVReuse Observability 1.0.1 | 分层动作、probe/correction/replacement 归因、严格成本口径 | algorithmic work 不是 latency |
| State research offline | 状态实例/分配、生命周期回放、依赖与恢复方案、请求关联 | 离线方案与模拟，不是已部署的在线缓存管理器 |
| V4.1 joint client v1 | mini-SWE → LiteLLM → HTTP 参数与 P1 关联验证 | 不实现 V4.1 runtime state capture/P2 |

本包包含当前一方源码、测试、配置、文档和运行依赖的 paper specs；不包含 Python 环境、模型、真实 PT、论文 PDF、下载的第三方完整仓库、旧发布包和实验结果。它不是离线依赖安装包。

DeepSeek-V4 历史适配器与 V4.1 客户端适配是两件事：V4.1 HTTP 成功不能证明 V4 的 C4/C128 schema、hook 或 intervention 可直接复用。

## 2. 总体关系

```text
mini-SWE task/config
  → AgentMon model/tool instrumentation
  → LiteLLM → OpenAI-compatible HTTP → 已有模型服务
       │ 最终请求参数、run/model-call/attempt ID
       └→ request_evidence.jsonl
                   │ 显式 engine binding（尚需真实 runtime 提供）
                   ↓
             状态 capture / logical view / allocation
                   ↓
历史 PT + strict mapping → Real Adapter → StatePair
                   ↓
        historical regression / methods / paper components
                   ↓
        metrics + observability + forensic + capsule

生命周期/依赖证据 → state_research 离线回放/恢复规划
```

AgentMon 生产 core 不依赖 kv_ana；联合验证脚本才选择性加载两边。时间相近不等于请求相同，request 匹配也不等于物理内存所有权或状态有效性已证明。

## 3. 源码包结构

```text
agentmon_kvreuse_complete_20260923/
├── README.md                         本指南副本
├── SOURCE_TREE.txt                   实际完整文件清单
├── BUILD_MANIFEST.json               来源、包含/排除范围、文件大小
├── agentmon/
│   ├── src/agentmon/                 监控、运行与分析代码
│   ├── scripts/                     batch、验收、分析和 V4.1 client smoke
│   ├── examples/                    TOML/YAML/registry 示例
│   ├── tests/                       unit/integration
│   ├── docs/                        专题说明
│   ├── offline_ana/                 LifeMem 文本分析代码
│   ├── artifacts/patches/           历史补丁，仅供审计，勿自动应用
│   ├── agentmon_implementation_spec/历史设计材料，非当前部署指令
│   └── pyproject.toml
└── kv_ana/
    ├── src/kvreuse_b5/
    ├── scripts/ tests/ configs/ docs/
    ├── manifests/ references/       48-pair split 与历史 anchors
    ├── runtime_intervention/        历史 V4 C4 layer2 实验仪器
    ├── runtime_references/          固定版本三文件 reference
    ├── literature_fetch_pack_v3/literature/paper_specs/
    │   └── batch1/ batch2/ unified20/及 evidence matrices
    ├── REAL_OFFLINE_PIPELINE.md
    └── pyproject.toml
```

完整清单以 SOURCE_TREE.txt 为准。旧文档中的旧 bundle 名、服务器绝对路径和 P2 启动示例是历史上下文，不应直接用于 V4.1。

## 4. AgentMon 模块说明

以下路径相对 `agentmon/src/agentmon/`。

| 模块 | 主要职责/阅读入口 |
|---|---|
| cli.py、config | doctor/run/runtime/analysis 等入口与配置校验 |
| adapters/agent_frameworks/mini_swe | agent/model/environment spans；profiled_model.py 为新 profile opt-in header 透传 |
| adapters/inference/vllm_018 | 特定版本 P2 hook；不是通用 V4.1 hook |
| contracts、storage | 事件/运行数据契约、JSONL/SQLite 等存储 |
| runtime、control、scheduler、batch | 任务生命周期、进程 backend、队列与批量调度 |
| sampling | host/process/Ascend 等资源采样；与模型请求 timeout 分离 |
| analysis、reporting、step_sequence | 已有数据分析、质量诊断、步骤关联、报告导出 |
| optimizer、experiments | 实验策略、资格门和对比编排，不等于已验证的性能收益 |
| context_compression | 可选上下文准备/摘要；会改变实际请求，应单独记录 context version |

优先阅读 `agentmon/README.md`、`docs/agent_runtime_architecture_design_zh.md`、`docs/p1_mini_swe_adapter.md`。batch/analysis 的完整参数分别以对应脚本 `--help` 和 `docs/agent_runtime_full_usage_zh.md`、`docs/analysis_sop.md` 为准。

## 5. KVReuse 模块说明

路径相对 `kv_ana/src/kvreuse_b5/`。

| 模块 | 作用 |
|---|---|
| data | StatePair、dataset 校验、synthetic fixtures |
| adapters | QwenRawKVAdapter、DeepSeekPrefixC4Adapter；CPU 读取与 schema 校验 |
| methods | shared/lowrank/probe/selective/detector/anchor/position/composite 与能力声明 |
| execution | runner、配置、artifact sharing、checkpoint/resume |
| metrics | SSE、zero residual 状态与参数隔离汇总 |
| regression | 固定 B1–B4 口径与历史 reference 比较 |
| pipeline | real offline 主流程、capability audit、tournament、provider plan、capsule |
| recipes | PaperRecipe contract、20 declarative specs、代表性 orchestration |
| providers | 明确的 signal/state provider contract；缺能力不能隐式生成 proxy |
| observability | trace schema、summary/detail、cost、forensic |
| state_research | LifeMem、alignment、inventory、lifecycle、policy replay、recovery、compare、request bridge |
| cli | synthetic runner 与 recipe 查询；真实数据使用 scripts 专用入口 |

`state_research/request_bridge.py` 从最终 HTTP 请求生成不含 prompt 的 sidecar；`bind_state_captures` 需要显式的 service-run/engine-request 绑定。没有绑定保持 BLOCKED，不使用 nearest timestamp。`snapshot_archive.py` 的 artifact 是离线材料，不等于在线显存仍然驻留。

## 6. 核心数据与数值口径

- `StatePair.a` 是 reused/old，`b` 是 exact/target，统一 `[T,F]`；`Delta=B-A`。
- state identity 含 model/condition/state/pair/layer；同 model+pair 的 condition/state/layer 不能跨 train/test。
- baseline SSE=`||B-A||²`，corrected SSE=`||B-A_hat||²`，explained=`1-corrected/baseline`。负 explained 合法。
- pooled 必须先累加 SSE 再除，不是 pair explained 的均值。aggregation 保留 method_config_hash 和 rank/budget/probe 等维度。
- zero baseline 保留 NaN/N/A；若 correction 破坏零残差，保留真实 SSE 并报告 ZERO_BASELINE_DEGRADED。
- FrozenSubspace basis 仅 train fit；artifact 多 rank 共用。test projection coefficient 用完整 B 时仍是 oracle。FrozenSubspace-dependent stage 只能 standalone 或 composite stage0。
- B4 历史预算为 round；frozen framework 预算为 ceil。不得为统一外观改掉两者定义。
- Generic anchor 不等于 KVCOMM；RoPE primitive 不等于 CacheSlide；synthetic provider 跑通不自动提升为论文复现。
- F0 exact/F1 algorithmic/F2 mechanism/F3 interface fidelity 与实际运行证据分开。

物理 allocation bytes、logical view bytes、artifact bytes、work units 和实测时间分别计数。alias 不应重复算物理容量。work fraction 或 cosine 不能解释成延迟/安全复用阈值。

## 7. 环境与解压

解压到新目录，不覆盖现有安装。可先用 `.sha256` 校验包；macOS 用 `shasum -a 256`，Linux 用 `sha256sum`。

```bash
tar -xzf agentmon_kvreuse_complete_20260923.tar.gz
cd agentmon_kvreuse_complete_20260923
export BUNDLE_ROOT="$PWD"
export AGENTMON_REPO="$BUNDLE_ROOT/agentmon"
export KVREUSE_REPO="$BUNDLE_ROOT/kv_ana"
export PY=python3
```

| 用途 | 依赖 |
|---|---|
| kv synthetic 与多数离线分析 | Python ≥3.9、numpy；YAML 配置需 PyYAML；测试需 pytest |
| AgentMon 基础 | Python ≥3.10；完整分析可需 pandas/pyarrow/zstandard，见 pyproject extras |
| 两项目联合验证 | 建议 Python 3.11，numpy、PyYAML、pytest |
| V4.1 精确 client 验收 | mini-swe-agent 2.4.3、litellm 1.90.1、openai 2.44.0 |
| 真实 PT 离线读取 | 现有 CPU 可用 torch；不需加载模型权重、不需 GPU/NPU |
| mini-SWE TUI/dataset 全流程 | 安装其相应可选依赖；本次 model/client 验证不覆盖这些入口 |

推荐直接 PYTHONPATH 运行，避免离线 pip build 触发下载。两个 pyproject 可用于已有依赖环境的正规安装，但本包不提供 wheelhouse。服务器和 Mac 的解释器路径可不同。

## 8. 第一轮：纯离线验收

输出目录应是新的/空的，避免与已有 checkpoint 混用。

```bash
cd "$KVREUSE_REPO"
PYTHONPATH=src "$PY" -m pytest tests -q
PYTHONPATH=src "$PY" -m kvreuse_b5.cli.smoke \
  --config configs/synthetic_smoke.yaml --output /tmp/kv-full-guide-smoke
"$PY" scripts/state_research_offline_smoke.py --output /tmp/state-full-guide-smoke
PYTHONPATH=src "$PY" -m kvreuse_b5.cli.recipes list

cd "$AGENTMON_REPO"
PYTHONPATH=src "$PY" -m pytest tests -q
PYTHONPATH=src "$PY" -m agentmon --help

"$PY" -m compileall -q "$KVREUSE_REPO/src" "$KVREUSE_REPO/scripts" \
  "$AGENTMON_REPO/src" "$AGENTMON_REPO/scripts"
```

部分 AgentMon integration fixtures 绑定本机 loopback socket，在限制网络 sandbox 下应给予本地 fixture 权限；不应为此修改算法。Mac bytecode cache 无权限时设置 `PYTHONPYCACHEPREFIX=/tmp/kv-python-cache`。

旧 `kvreuse_b5.cli.run` 只支持 `--synthetic`，其旧提示不代表真实 adapters 不存在；真实数据请使用下一节 scripts。

## 9. 真实状态：CPU adapter 与 pipeline

只在已有数据的机器上运行。以下变量由操作者填入实际路径，不向 Mac 回传 PT/CSV：

```bash
export Q8_ROOT=/path/to/qwen8/raw_kv
export Q35_ROOT=/path/to/qwen35/raw_kv
export DS_ROOT=/path/to/deepseek/packed
export DS_MAPPING=/path/to/deepseek_prefix_c4_strict_mapping.csv
export OUT=/path/to/new-output
cd "$KVREUSE_REPO"

"$PY" scripts/real_adapter_smoke.py --adapter qwen --root "$Q8_ROOT" \
  --model-family Qwen3-8B --pair-id 0 --condition POSITION_ONLY
"$PY" scripts/real_adapter_smoke.py --adapter qwen --root "$Q35_ROOT" \
  --model-family Qwen3.5-27B --pair-id 146 --condition POSITION_ONLY
"$PY" scripts/real_adapter_smoke.py --adapter deepseek-c4 \
  --root "$DS_ROOT" --mapping "$DS_MAPPING" --pair-id 0

"$PY" scripts/run_real_offline_smoke.py --q8-root "$Q8_ROOT" \
  --q35-root "$Q35_ROOT" --deepseek-root "$DS_ROOT" \
  --deepseek-mapping "$DS_MAPPING" --output "$OUT/smoke"

"$PY" scripts/run_real_offline_pipeline.py --q8-root "$Q8_ROOT" \
  --q35-root "$Q35_ROOT" --deepseek-root "$DS_ROOT" \
  --deepseek-mapping "$DS_MAPPING" --output "$OUT/full" --resume \
  --capsule-max-bytes 45000
```

Qwen flatten 必须 raw[0] → permute(T,H,D) → `[T,H*D]`。Q8 layer=slot_index；Q35 layer=真实 3,7,…63。positions 是 X-relative，不是绝对 RoPE；position_offset_a/b 单独保留。

DeepSeek 当前仅 V4 PREFIX C4，row_a/row_b 分别从冻结 mapping 取，x_rel_anchor_a/b 必须一致；不推导 support、不补行。actual_seq_lengths_query 是 `(0,prompt_tokens)` 一类长度结构，不是字面 `[2]`；start_pos 当前 frozen dataset 值 `[0]`，不是 `[1]`。

历史用户服务器控制点：Q8 pair0 POSITION 为 36 层 K/V `[114,1024]`；Q35 pair146 POSITION 为 16 层 `[68,1024]`、K/V SSE=0；DS pair0 为 21 层 `[22,512]`、位置4…88。它们不是代码硬编码结果，本次打包未在服务器重跑。

主流程依次 precheck、regression、severe gate、20-paper audit、可执行 components/B5、汇总、provider plan、capsule。缺 provider 跳过对应方法，不自动用 B-A 冒充 query/deployable signal。`--max-pairs` smoke 不是完整历史回归 PASS；完整 B1–B4 scope/tolerance 见 REAL_OFFLINE_PIPELINE.md。

输出包括 historical_regression.csv、paper_capability_audit.csv、paper_offline_tournament.csv、b5_mechanism_tournament.csv、offline_tournament.csv、provider plan、real_offline_pipeline_capsule.txt。完整结果留服务器，capsule ≤45000 bytes 供文本粘贴。FATAL 不可继续论文结论；NUMERIC_WARN、scope difference 应读 notes；METHOD_BLOCKED 不是全流程失败。

## 10. Observability 与 forensic

```bash
cd "$KVREUSE_REPO"
KVREUSE_TRACE=detail "$PY" scripts/real_observability_smoke.py \
  --q8-root "$Q8_ROOT" --pair-id 221 --condition PREFIX_ONLY \
  --output "$OUT/forensic-q8-221"
```

`KVREUSE_TRACE=off|summary|detail` 默认 summary；`KVREUSE_TRACE_DIR` 可指定目录。summary 按 pair/method/layer/state/action 汇总，只保留 count；positions、selected/retired sets 在压缩 detail。输出 trace_summary.jsonl、trace_detail.jsonl.gz、cost_accounting.csv、forensic_summary.csv、run_manifest.json、trace_schema.json。

selected position fraction 分母是 total_positions；layer-position work 分母是 total_layer_position_units。重复 recompute 累加与 unique(layer,position) 分开。selector 全零/全相等记录 degenerate，不改变 stable top-k。Agent correction 后与 probe replacement 后分别记录 SSE。probe-only/shift-only 是 FORENSIC_ABLATION，不进入正式论文 tournament；CacheSlide LTE 是 canonical forensic，GTE 仅 sensitivity。顶层 correction_norm 表示实际施加 correction，不是隐藏在 metadata 的估计量。

详见 docs/KVREUSE_OBSERVABILITY_V1.md。trace 不存完整 KV tensor，不估算 runtime latency。

## 11. LifeMem 与异构状态离线研究

首先执行第8节 synthetic suite；其生成的 `inputs/config.json` 可作为真实材料模板：

```bash
cd "$KVREUSE_REPO"
"$PY" scripts/prepare_state_research.py --help
"$PY" scripts/run_state_research_offline.py --help
"$PY" scripts/run_state_research_offline.py \
  --config /tmp/state-full-guide-smoke/inputs/config.json \
  --output /tmp/state-full-guide-replay
```

LifeMem preparation 支持真实 tokenizer/显式 sidecar；文本相同不保证 server chat-template token IDs 相同。constructed request order 与 observed agent workload 不能混称。加入真实 agentmon-events、state-snapshot 前先检查身份、时间域、内容版本和来源。

离线套件输出 state timeline、policy summary、dependency impact、recovery plans、state comparison、AgentMon correlation 和 capsule。`selected_path=repair` 与 `execution_status=NOT_EXECUTED` 可以同时成立：这是有证据/成本约束的方案，不是已执行修复。missing validity evidence 不得改成 direct reuse PASS。

相关说明：docs/LIFEMEM_STATE_RESEARCH_V1.md、STATE_RESEARCH_OFFLINE_PHASES_2_3.md、STATE_RESEARCH_AUDIT_V1_0_1.md。

## 12. V4.1 客户端：调用链与使用

```text
local_deepseek_v41.yaml
 → mini-SWE get_model
 → ProfiledLitellmModel（opt-in 合并 profile headers）
 → 原 InstrumentedLitellmModel（run/model-call headers 与 span）
 → mini-SWE LitellmModel._query（config.model_kwargs | call kwargs）
 → litellm.completion
 → OpenAI-compatible HTTP JSON
```

`extra_body.chat_template_kwargs.thinking=false` 经真实 serializer 成为 HTTP 顶层 chat_template_kwargs；不是仅检查 YAML。配置仅影响独立 V4.1 profile，不修改旧 Qwen 或给所有模型关闭 thinking。

Registry 中 legacy max_tokens=4096、max_output_tokens=4096 是保守 client output policy；max_input_tokens=262144 是 supplied serving window ceiling，不保证 prompt+output 均可占满。输入、模板和输出合计仍需符合服务窗口，registry 不会扩容服务器。

model timeout600 与 metrics/models/doctor probe timeout2 分开；retry 可放大总耗时。第一次 live smoke 限制重试，C=1。target config 的 prefix_caching=unknown 不宣称客户端开启了服务端能力；bf16 compute dtype 不等于 W4 权重或 cache dtype。

先做完全离线的真实 serializer 验证（socket 被阻断、HTTP response 是 synthetic）：

```bash
"$PY" "$KVREUSE_REPO/scripts/validate_v41_joint_client.py" \
  --agentmon-root "$AGENTMON_REPO" --output /tmp/v41-wire-check
```

仅在已有 `127.0.0.1:8800/v1` 服务的服务器，操作者再显式执行：

```bash
"$PY" "$AGENTMON_REPO/scripts/deepseek_v41_client_smoke.py" \
  --live --kvreuse-root "$KVREUSE_REPO" --output ./v41-client-smoke
```

脚本不启动服务/采样器/NPU，只请求固定 `printf AGENTMON_V41_SMOKE_OK`，严格验证工具命令后执行，提交 observation，再验证第二次响应。不同命令视为 smoke FAIL，不执行任意生成 shell。输出 client_smoke.json、events.jsonl、request_evidence.jsonl。

该 PASS 只证明小型 model/tool client closure，不是完整 SWE task completion、P0/P2 性能验收或 state capture。summary/retry 路径及 Qwen 不变性另由离线 validator 覆盖。

## 13. AgentMon 正式任务与 batch

已有服务验证通过后再使用完整 CLI。先查参数，不直接执行旧服务器启动脚本：

```bash
PYTHONPATH="$AGENTMON_REPO/src" "$PY" -m agentmon --help
"$PY" "$AGENTMON_REPO/scripts/agentmon_batch_runtime.py" --help
"$PY" "$AGENTMON_REPO/scripts/run_agentmon_analysis_from_path.py" --help
```

`agentmon run --cwd "$TARGET_REPO" -- <mini命令>` 在被修复项目中运行。V4.1 使用独立 `examples/config.deepseek_v41.toml` 和 `examples/models/local_deepseek_v41.yaml`；不能让旧命令中的 `--model-class InstrumentedLitellmModel` 覆盖新 profile 的 ProfiledLitellmModel。P1 agent/environment 类仍用原 instrumented 类。trajectory 使用 `{run_id}` 防覆盖。

配置中涉及工作目录、AgentMon home、环境变量和 registry 路径须按解压位置填写。先 concurrency=1，不将服务 max_num_seqs=1 当成高并发能力。旧 Qwen profile 保留不变，用户可选择它跑历史任务。

正式 batch 完整操作见 AgentMon README、docs/batch_p2_step_sequence_usage.md、docs/agent_runtime_full_usage_zh.md；其中 P2 示例仅适用其明确的 runtime 版本。V4.1 当前 P2 保持关闭；不自动套用 vLLM 0.18 hooks、旧 checkpoint path 或旧服务启动参数。

## 14. 证据、隐私与失败排查

| 现象 | 应检查/解释 |
|---|---|
| ModuleNotFoundError | PYTHONPATH 指向包中 src；确认解释器，不把环境一起复制 |
| recipe source missing | 必须保留包内 paper_specs 原相对路径；PDF/官方 repo 重新审查材料需另备 |
| V4.1 content null/reasoning耗尽 | 检查最终 HTTP thinking=false，而非只看配置 |
| mini-SWE response invalid | 当前 model class 需要合法 tool action；普通文本不是该路径成功证明 |
| request sidecar matched但无state | 仍需 runtime binding、producer/consumer、token/state mapping 和真实 captures |
| zero explained/负explained | 零残差应 NaN，负值合法；不要为“好看”截断 |
| high work fraction | additive 可重复计算；不能推 latency；先分清 position 与 layer-position |
| historical mismatch | 检查scope、split、B4 round、strict mapping，再核对数值容差 |
| synthetic PASS | 仅本地逻辑/合同通过；不能写成服务器/真实模型 PASS |

默认事件不存完整 prompt/KV；需要研究原文/张量时独立 opt-in artifact 并控制权限。文献 inventory 中原机器路径只作为来源记录，不保证新机器存在。报告/manifest 的来源路径属于可识别本地信息，向第三方发包前可按需要审查。

## 15. 历史 runtime 工具与尚未完成事项

`runtime_intervention/dsv4_precheck_v1` 是独立、默认 off、SHA guard 的 V4 C4 layer2 causal precheck 仪器；apply/restore 会修改目标 runtime，仅可在明确授权、匹配历史 SHA 的环境使用。本指南不要求执行它，不适用于 V4.1。参考目录不是完整 vLLM 发行版。

尚需真实服务器验收/开发：V4.1 input token IDs、runtime revision/schema、API attempt→engine mapping、所有 state types/allocations/views、rank/device时间域、capture coverage/overhead、partial-forward/query/attention providers、functional logits/generation、安全复用及性能收益。

LifeMem 与历史 A/B 可支持上下文敏感性、representation比较和离线策略研究；不能独立回答真实 Agent 并发生命周期、完整 KV 保存或线上收益。

## 16. 维护与重新打包

修改入口按职责放在 AgentMon instrumentation/runtime 或 kv_ana adapter/provider/analysis；不要为新论文重构冻结 evaluator。新增 provider 明确来源、oracle/full-target使用、cost和缺能力状态。数值算法、method hash、split、strict mapping 都需回归。

```bash
cd "$KVREUSE_REPO"
"$PY" scripts/build_complete_source_bundle.py \
  --agentmon-root "$AGENTMON_REPO" \
  --output dist/agentmon_kvreuse_complete_20260923.tar.gz
```

已有输出不覆盖，请新建输出名。builder 不安装依赖、不访问服务、不修改两项目算法；按正向目录清单打包，拒绝 symlink，过滤 cache/output/raw tensor/环境/旧归档，并生成 manifest、文件清单与 tar SHA256 sidecar。验收结果另见 docs/COMPLETE_SOURCE_BUNDLE_VALIDATION.md。
