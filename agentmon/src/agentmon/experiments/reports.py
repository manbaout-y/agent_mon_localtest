from __future__ import annotations

from pathlib import Path
from typing import Any

from agentmon.optimizer.harness.schemas import FORBIDDEN_CONCLUSIONS


def build_optimizer_replay_report(
    evidence_outputs: dict[str, Any],
    replay_outputs: dict[str, Any],
    output_dir: str | Path,
) -> str:
    manifest = evidence_outputs["experiment_manifest"]
    quality = evidence_outputs["data_quality"]
    bottlenecks = evidence_outputs["bottleneck_attribution"]
    summary = replay_outputs["strategy_summary"]
    audit = replay_outputs["risk_audit"]
    lines = [
        "# AgentMon Optimizer Experiment Replay Report",
        "",
        f"- Batch root: `{manifest['batch_root']}`",
        f"- Output dir: `{Path(output_dir).expanduser().resolve()}`",
        f"- SOP phase: `{manifest.get('analysis_sop_phase', '04_evidence')}`",
        f"- Data quality: `{quality.get('overall_status')}`",
        f"- Snapshots: `{summary.get('snapshot_count')}`",
        f"- Decisions: `{summary.get('decision_count')}`",
        "",
        "## Evidence Summary",
        "",
        f"- Result rows: `{manifest['input_summary']['result_rows']}`",
        f"- Step rows: `{manifest['input_summary']['step_rows']}`",
        f"- Run event files: `{manifest['input_summary']['run_event_files']}`",
        f"- Dominant observed component: `{bottlenecks.get('dominant_observed_component')}`",
        "",
        "## Strategy Summary",
        "",
    ]
    for row in summary.get("strategies", []):
        lines.extend(
            [
                f"### `{row['strategy']}`",
                "",
                f"- Decisions: `{row['decision_count']}`",
                f"- Pressure counts: `{row['pressure_counts']}`",
                f"- would_defer: `{row['would_defer_count']}`",
                f"- would_limit: `{row['would_limit_count']}`",
                f"- would_relax: `{row['would_relax_count']}`",
                f"- Risk flags: `{row['risk_flag_counts']}`",
                "",
            ]
        )
    lines.extend(["## Risk Audit", ""])
    for row in audit.get("strategy_audits", []):
        lines.extend(
            [
                f"### `{row['strategy']}`",
                "",
                f"- Oscillation status: `{row['oscillation_status']}`",
                f"- Pressure transitions: `{row['pressure_transition_count']}`",
                f"- Max consecutive would_defer snapshots: `{row['max_consecutive_would_defer_snapshots']}`",
                f"- Starvation risk status: `{row['starvation_risk_status']}`",
                f"- Over-limit status: `{row['over_limit_status']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Forbidden Conclusions",
            "",
            *[f"- {item}" for item in FORBIDDEN_CONCLUSIONS],
            "",
            "## Output Files",
            "",
            "- `experiment_manifest.json`",
            "- `evidence_graph.json`",
            "- `critical_path.csv`",
            "- `bottleneck_attribution.json`",
            "- `workload_normalized_metrics.csv`",
            "- `data_quality.json`",
            "- `optimizer_decision_timeline.csv`",
            "- `optimizer_decisions.jsonl`",
            "- `optimizer_strategy_summary.json`",
            "- `optimizer_risk_audit.json`",
            "- `prediction_variable_validation.json`",
            "- `prediction_variable_validation.csv`",
            "- `mechanism_comparison.json`",
            "- `ablation_table.csv`",
            "- `contribution_decomposition.csv`",
            "- `conflict_audit.json`",
            "- `evaluation_report.md`",
        ]
    )
    return "\n".join(lines) + "\n"


def write_optimizer_replay_report(
    evidence_outputs: dict[str, Any],
    replay_outputs: dict[str, Any],
    output_dir: str | Path,
) -> Path:
    path = Path(output_dir).expanduser().resolve() / "optimizer_replay_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_optimizer_replay_report(evidence_outputs, replay_outputs, output_dir),
        encoding="utf-8",
    )
    return path
