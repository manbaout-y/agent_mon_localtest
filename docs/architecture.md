# 架构

AgentMon负责执行与观测，KVReuse负责状态与离线研究。统一入口/测试/交付，不合并Python包。

```text
mini-SWE → AgentMon instrumentation → LiteLLM → HTTP
                             ↓ request evidence
          显式service/engine binding（需真实runtime）
                             ↓
capture/artifact → adapter → StatePair → evaluator/observability
lifecycle/dependency → offline replay/recovery planning
```

只有联合脚本按需加载双方；生产core不互相import。保留内部相对路径，避免paper_spec_path和fixtures失效。
顶层脚本从自身位置推导子项目路径，不依赖旧目录。

StatePair A=old、B=target，不用B residual冒充deployable query signal。
保留sample/base-pair split、aggregation维度、zero-baseline degradation与预算语义。
physical allocation、logical view、artifact bytes、work、latency分别记录。
request join不等于ownership/capture完整性/functional safety。
