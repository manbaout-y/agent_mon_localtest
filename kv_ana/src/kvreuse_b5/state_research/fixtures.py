"""Explicitly synthetic, model-free fixtures; never a fallback tokenizer for real inputs."""
from __future__ import annotations

from dataclasses import asdict

from .alignment import Tokenization
from .state_inventory import StateInstance


class SyntheticCharacterTokenizer:
    def tokenize(self, text: str, *, text_scope: str) -> Tokenization:
        return Tokenization(tuple(ord(c) for c in text), tuple((i, i + 1) for i in range(len(text))),
                            "synthetic-character-v1", "synthetic", text_scope)


def synthetic_records() -> list[dict]:
    layouts = [
        [("intro", "AA"), ("memory", "共同记忆 α"), ("tail", "BB")],
        [("intro", "CC"), ("memory", "共同记忆 α"), ("tail", "BB")],
        [("memory", "共同记忆 α"), ("intro", "CC"), ("tail", "BB")],
        [("memory", "共同记忆 β"), ("intro", "CC"), ("tail", "BB")],
        [("memory", "共同记忆 β"), ("intro", "CC"), ("tail", "BB")],
    ]
    records = []
    for i, layout in enumerate(layouts):
        context = "\n---\n".join(f"{name}: {body}" for name, body in layout)
        prompt = ("SYSTEM changed\n" if i == 4 else "SYSTEM\n") + context + f"\nQUERY {i}"
        tokens = SyntheticCharacterTokenizer().tokenize(prompt, text_scope="full_prompt")
        records.append({
            "question_id": f"q{i}", "conversation_id": "synthetic-session", "query": f"QUERY {i}",
            "results": [{"content": body, "metadata": {"subject": name, "memory_id": name}}
                        for name, body in layout],
            "retrieval_metadata": {"formatted_context": context},
            "research": {"request_id": f"request-{i}", "full_prompt": prompt,
                         "request_time_ns": 1_000 + i * 20, "clock_domain": "synthetic-clock",
                         "model_family": "synthetic", "tokenization": asdict(tokens),
                         "agentmon": {"agent_run_id": "synthetic-agent", "model_request_id": f"model-{i}"}},
        })
    return records


def synthetic_events() -> list[dict]:
    events = []
    for i in range(5):
        events.extend([
            {"event_id": f"consume-{i}", "event_type": "vllm.kv.block_event", "source": "vllm_p2",
             "quality": "synthetic", "payload": {"vllm_request_id": f"engine-{i}"}},
            {"event_id": f"received-{i}", "event_type": "vllm.request.received", "source": "vllm_p2",
             "quality": "synthetic", "payload": {"agentmon_run_id": "synthetic-agent",
             "agentmon_model_request_id": f"model-{i}", "vllm_request_id": f"engine-{i}"}},
        ])
    return events


def synthetic_states() -> list[StateInstance]:
    return [StateInstance(
        state_id=f"state-{i}", request_id="request-0", model_family="synthetic", state_kind="shared_main",
        context_version=1, producer_layer=2, consumer_layers=(4, 6), allocation_id="allocation-0",
        device=f"synthetic_device:{0 if i < 2 else 1}", tier="synthetic_hbm",
        allocation_bytes=200, logical_bytes=128, dtype="synthetic", shape=(8, 16), source="synthetic",
    ) for i in range(3)]
