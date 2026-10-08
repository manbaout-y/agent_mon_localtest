from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from kvreuse_b5.pipeline.io import atomic_json


SIGNALS: dict[str, dict[str, str]] = {
    "raw_reuse_state": {"granularity": "pair x layer x token/state-row", "source": "already saved"},
    "exact_fresh_recompute_state": {"granularity": "pair x layer x selected token", "source": "model partial/full forward"},
    "partial_forward_state": {"granularity": "layer x selected token with current corrected surroundings", "source": "runtime provider"},
    "full_exact_probe": {"granularity": "layer x paper-selected probe token x K/V", "source": "exact fresh state"},
    "approximate_context_probe": {"granularity": "layer x probe token under reused/partially corrected context", "source": "runtime provider"},
    "query_signal": {"granularity": "query/chunk/token", "source": "query encoder/model hook"},
    "attention_signal": {"granularity": "layer x head x query x context token", "source": "attention hook"},
    "historical_attention": {"granularity": "profile dataset x layer/head/token", "source": "offline profiling"},
    "detector_score": {"granularity": "detector layer x position", "source": "paper-defined detector"},
    "anchor_metadata_state": {"granularity": "anchor pool x layer x K/V plus eligibility metadata", "source": "online cache runtime"},
    "position_transform_metadata": {"granularity": "model/layer/head plus source/target position", "source": "model config/runtime"},
    "semantic_embedding": {"granularity": "chunk/document/resource", "source": "paper-specified embedding model"},
    "offline_preprocessing_artifacts": {"granularity": "paper-specific cache/profile object", "source": "offline preprocessing job"},
    "training_artifacts": {"granularity": "paper variant x model", "source": "paper-defined training"},
    "model_modification": {"granularity": "model architecture/checkpoint", "source": "paper-defined model change"},
    "cache_runtime_events": {"granularity": "request x cache object/page", "source": "cache runtime instrumentation"},
    "system_runtime_profile": {"granularity": "request/stage/kernel", "source": "serving runtime instrumentation"},
    "logits": {"granularity": "evaluation token x vocabulary", "source": "functional forward"},
    "next_token_distribution": {"granularity": "evaluation point x vocabulary", "source": "functional forward"},
    "exact_next_token": {"granularity": "evaluation point", "source": "reference generation"},
    "generation_outputs": {"granularity": "sample x decoding trace", "source": "generation runtime"},
    "runtime_timing_repair_cost": {"granularity": "stage/kernel/request", "source": "runtime instrumentation"},
}


CAPABILITY_TO_SIGNALS = {
    "PARTIAL_FORWARD": ("exact_fresh_recompute_state", "partial_forward_state"),
    "QUERY_SIGNAL": ("query_signal",),
    "ATTENTION_SIGNAL": ("attention_signal",),
    "DETECTOR_SCORE": ("detector_score",),
    "ONLINE_ANCHOR_STATE": ("anchor_metadata_state",),
    "POSITION_TRANSFORM": ("position_transform_metadata",),
    "SEMANTIC_EMBEDDING": ("semantic_embedding",),
    "OFFLINE_PREPROCESSING": ("offline_preprocessing_artifacts",),
    "TRAINING": ("training_artifacts",),
    "MODEL_MODIFICATION": ("model_modification",),
    "CACHE_RUNTIME": ("cache_runtime_events", "runtime_timing_repair_cost"),
    "SYSTEM_RUNTIME": ("system_runtime_profile", "runtime_timing_repair_cost"),
}


def build_provider_plan(audit_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    papers: dict[str, set[str]] = defaultdict(set)
    for row in audit_rows:
        paper = f"{row['paper_id']} {row['paper_name']}"
        raw = "|".join((
            str(row.get("execution_requirements", "")),
            str(row.get("required_providers", "")),
            str(row.get("missing_providers", "")),
        ))
        requirements = {item for item in raw.split("|") if item}
        for requirement in requirements:
            for signal in CAPABILITY_TO_SIGNALS.get(requirement, (requirement.lower(),)):
                if signal in SIGNALS:
                    papers[signal].add(paper)
        if row.get("paper_id") == "14":
            papers["full_exact_probe"].add(paper)
            papers["approximate_context_probe"].add(paper)
        if row.get("paper_id") == "12":
            papers["anchor_metadata_state"].add(paper)
    # Functional validation is cross-cutting and intentionally deferred.
    for name in ("logits", "next_token_distribution", "exact_next_token", "generation_outputs"):
        papers[name].add("cross-paper functional tournament")
    return {
        "schema_version": "runtime-provider-plan-v1",
        "collection_goal": "one NPU activation should collect reusable provider signals without claiming CPU functional validation",
        "providers": [
            {"signal": name, **definition, "papers": sorted(papers.get(name, set())), "required_next_npu": bool(papers.get(name))}
            for name, definition in SIGNALS.items()
        ],
    }


def write_provider_plan(output_dir: str | Path, plan: Mapping[str, Any]) -> None:
    root = Path(output_dir)
    atomic_json(root / "runtime_provider_requirements.json", plan)
    lines = [
        "# Runtime provider plan",
        "",
        "This is a collection contract for a future NPU run; it does not implement runtime providers.",
        "",
        "| Signal | Required next NPU | Granularity | Papers |",
        "|---|---:|---|---|",
    ]
    for item in plan["providers"]:
        papers = "; ".join(item["papers"]) or "none"
        lines.append(f"| {item['signal']} | {'yes' if item['required_next_npu'] else 'no'} | {item['granularity']} | {papers} |")
    (root / "runtime_provider_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
