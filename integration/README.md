# 联合验收

顶层validate_workspace.py调用既有实现，不复制业务逻辑。
wire validator仍在kv_ana/scripts/validate_v41_joint_client.py，显式--agentmon-root指向同工作区。
AgentMon提供run/model-call/attempt证据，KVReuse消费sidecar与显式runtime binding。
缺engine/capture证据保持BLOCKED；不执行live smoke/P2/runtime patch。
