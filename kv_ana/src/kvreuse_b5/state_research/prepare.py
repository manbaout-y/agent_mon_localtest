from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from . import SCHEMA_VERSION
from .alignment import LocalFastTokenizerProvider
from .agentmon import correlate_events
from .io import load_records, merge_sidecar, write_csv, write_json, write_jsonl
from .lifemem import prepare_records
from .replay import replay_manifest, representative_cases
from .state_inventory import StateInstance, account_states, capture_requirements, state_dicts


def prepare_dataset(records: list[dict], output: str | Path, *, dataset_id: str,
                    order: str = "constructed", tokenizer=None, events=None, states=None,
                    source_paths=None, cases_per_class: int = 3, max_edit_units: int = 100_000) -> dict:
    if not records:
        raise ValueError("empty LifeMem input")
    root = Path(output)
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"output must be an empty directory: {root}")
    data = prepare_records(records, dataset_id=dataset_id, order=order, tokenizer=tokenizer,
                           max_edit_units=max_edit_units)
    joined = correlate_events(data["requests"], events or [])
    inventory = account_states(states or [])
    inventory["availability"] = "PROVIDED" if states is not None else "NOT_PROVIDED"
    requests_by_id = {r["request_id"]: r for r in data["requests"]}
    for state in states or []:
        if state.request_id not in requests_by_id:
            raise ValueError("state inventory request_id is absent from the prepared dataset")
        request = requests_by_id[state.request_id]
        if request["model_family"] is not None and request["model_family"] != state.model_family:
            raise ValueError(f"{state.state_id}: state model_family conflicts with its prepared request")
        if request["context_version"] != state.context_version:
            raise ValueError(f"{state.state_id}: state context_version conflicts with its prepared request")
    cases = representative_cases(data["transitions"], per_class=cases_per_class)
    counts = dict(Counter(t["classification"] for t in data["transitions"]))
    token_sources = dict(Counter(r["tokenization_source"] or "missing" for r in data["requests"]))
    ready_pairs = sum(t["eligible_for_state_alignment_review"] for t in data["transitions"])
    quality = {
        "schema_version": SCHEMA_VERSION, "validation": "PASS",
        "dataset_id": dataset_id, "source_paths": source_paths or {},
        "request_count": len(data["requests"]), "chunk_count": len(data["chunks"]),
        "adjacent_memory_transition_count": len(data["transitions"]),
        "context_change_count": len(data["context_changes"]), "case_count": len(cases),
        "classification_counts": counts, "tokenization_sources": token_sources,
        "full_prompt_count": sum(r["text_scope"] == "full_prompt" for r in data["requests"]),
        "token_aligned_pairs_for_state_review": ready_pairs,
        "ordering": order, "issues": data["issues"],
        "correlation": {k: v for k, v in joined.items() if k != "rows"},
        "agentmon_event_source": "PROVIDED" if events is not None else "NOT_PROVIDED",
        "state_inventory": inventory,
        "research_readiness": {
            "context_character_analysis": "AVAILABLE",
            "token_state_alignment": "REVIEWABLE" if ready_pairs else "MISSING_OR_SYNTHETIC_INPUTS",
            "resource_optimization": "REQUIRES_STATE_ACCESS_AND_MEASURED_RECOVERY_COST",
            "joint_state_reuse": "REQUIRES_RUNTIME_DEPENDENCIES_AND_SHARED_BINDINGS",
            "repair_functional_validation": "REQUIRES_STATES_AND_RUNTIME_EXECUTION",
        },
        "semantics": ["validation PASS means structurally valid preparation, not experiment success",
                      "no latency, cache-hit or functional-safety claims",
                      "adjacent observed/constructed occurrences are not all-pairs opportunities",
                      "no historical split or recipe math changes"],
    }
    root.mkdir(parents=True, exist_ok=True)
    for key in ("requests", "chunks", "payloads", "transitions", "context_changes"):
        write_jsonl(root / f"{key}.jsonl", data[key])
    write_jsonl(root / "agentmon_joins.jsonl", joined["rows"])
    write_jsonl(root / "state_inventory.jsonl", state_dicts(states or []))
    write_json(root / "capture_requirements.json", capture_requirements())
    write_json(root / "replay_manifest.json", replay_manifest(data["requests"]))
    write_json(root / "representative_cases.json", cases)
    write_json(root / "quality_report.json", quality)
    write_csv(root / "transition_summary.csv", data["transitions"], [
        "transition_id", "classification", "memory_key", "old_request_id", "new_request_id",
        "content_same", "memory_prefix_same", "position_same", "full_text_prefix_same",
        "tokens_same", "token_evidence", "intervening_session_requests", "elapsed_ns", "order_basis",
        "eligible_for_state_alignment_review",
    ])
    report = ["# LifeMem 状态研究准备结果", "", f"- schema: `{SCHEMA_VERSION}`",
              f"- requests: {len(data['requests'])}; chunks: {len(data['chunks'])}",
              f"- 相邻 memory 变化对: {len(data['transitions'])}; 代表案例: {len(cases)}",
              f"- 请求顺序: {order}; 可供 token/state 对齐复核的变化对: {ready_pairs}",
              f"- AgentMon 匹配事件: {joined['matched']}; ambiguous: {joined['ambiguous']}; conflicts: {joined['conflicts']}",
              "", "## 变化类型", ""]
    report += [f"- {kind}: {count}" for kind, count in sorted(counts.items())]
    report += ["", "## 缺失与限制", ""]
    report += [f"- {code}: {count}" for code, count in sorted(Counter(i["code"] for i in data["issues"]).items())]
    report += ["- runtime 状态访问、恢复成本、质量与性能尚未由本工具采集。",
               "- 原始文本/token 只放在 payloads.jsonl；其余文件保存坐标、身份与统计。",
               "- replay_manifest 仅供下阶段执行器读取；本次没有发送模型请求。",
               "- observed 时间只代表声明的请求时间；缺失服务时间时不能推算并发或等待。",
               "- memory subject 匹配属于启发式身份；文本 equal 区间不代表 KV 相等。", ""]
    (root / "report.md").write_text("\n".join(report), encoding="utf-8")
    manifest = {"schema_version": SCHEMA_VERSION, "framework_version": "2.0.1",
                "dataset_id": dataset_id, "sources": source_paths or {},
                "files": sorted(p.name for p in root.iterdir()) + ["manifest.json"],
                "tensor_capture": False, "runtime_execution": False}
    write_json(root / "manifest.json", manifest)
    return quality


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare LifeMem state-research inputs without models or torch")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--sidecar", help="optional per-record research metadata JSON/JSONL")
    parser.add_argument("--tokenizer-dir", help="optional local fast tokenizer; no downloads or model weights")
    parser.add_argument("--tokenizer-id", help="explicit tokenizer/model revision identity for local retokenization")
    parser.add_argument("--add-special-tokens", action="store_true", help="local tokenizer only; default false")
    parser.add_argument("--agentmon-events", help="optional AgentMon events JSONL; exact ID joins only")
    parser.add_argument("--state-snapshot", help="optional JSON {snapshot_id, states}; one simultaneous snapshot")
    parser.add_argument("--order", choices=("constructed", "observed"), default="constructed")
    parser.add_argument("--cases-per-class", type=int, default=3)
    parser.add_argument("--max-edit-units", type=int, default=100_000)
    args = parser.parse_args(argv)
    if args.max_edit_units < 1:
        parser.error("--max-edit-units must be positive")
    if bool(args.tokenizer_dir) != bool(args.tokenizer_id):
        parser.error("--tokenizer-dir and --tokenizer-id must be supplied together")
    if args.add_special_tokens and not args.tokenizer_dir:
        parser.error("--add-special-tokens requires --tokenizer-dir")
    tokenizer = LocalFastTokenizerProvider(args.tokenizer_dir, args.tokenizer_id,
                 add_special_tokens=args.add_special_tokens) if args.tokenizer_dir else None
    records = load_records(args.input)
    if args.sidecar:
        records = merge_sidecar(records, load_records(args.sidecar))
    states, snapshot_id = None, None
    if args.state_snapshot:
        snapshot = json.loads(Path(args.state_snapshot).read_text(encoding="utf-8"))
        snapshot_id = snapshot["snapshot_id"]
        if not snapshot_id:
            raise ValueError("state snapshot_id must be explicit")
        states = [StateInstance(**s) for s in snapshot["states"]]
    quality = prepare_dataset(
        records, args.output, dataset_id=args.dataset_id, order=args.order, tokenizer=tokenizer,
        events=load_records(args.agentmon_events) if args.agentmon_events else None,
        states=states, cases_per_class=args.cases_per_class, max_edit_units=args.max_edit_units,
        source_paths={"input": args.input, "sidecar": args.sidecar,
                      "agentmon_events": args.agentmon_events, "state_snapshot": args.state_snapshot,
                      "snapshot_id": snapshot_id, "tokenizer_dir": args.tokenizer_dir,
                      "tokenizer_id": args.tokenizer_id, "add_special_tokens": args.add_special_tokens},
    )
    print(f"SCHEMA_VERSION={SCHEMA_VERSION}")
    print(f"PREPARATION=PASS REQUESTS={quality['request_count']} TRANSITIONS={quality['adjacent_memory_transition_count']}")
    print(f"ORDER_BASIS={args.order} TOKEN_REVIEWABLE_PAIRS={quality['token_aligned_pairs_for_state_review']}")
    print(f"OUTPUT={Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
