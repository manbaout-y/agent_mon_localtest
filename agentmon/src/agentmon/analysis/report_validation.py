from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
import re
from typing import Any

from agentmon.analysis.io import float_or_none, read_json, write_json, write_markdown, write_plain_csv, write_table, write_table_manifest


NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?(?:\d+\.\d+|\d+)(?:%|)(?![A-Za-z0-9_])")
CAUSAL_WORDS = ("caused", "because of", "root cause is", "导致", "因果", "根因是", "造成")
SCHEDULED_AS_GENERATED = ("scheduled token", "scheduled tokens", "调度 token", "调度tokens")
GENERATED_TOKEN_WORDS = ("generated token", "generation token", "生成 token", "生成tokens")


def verify_model_report(
    *,
    model_pack: str | Path,
    report_path: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    pack = Path(model_pack)
    report = Path(report_path)
    out = Path(output_dir).expanduser() if output_dir else report.parent
    text = report.read_text(encoding="utf-8", errors="replace")
    known_numbers = collect_pack_numbers(pack)
    forbidden_rows = find_forbidden_claims(pack, text)
    numeric_rows = find_numeric_mismatches(text, known_numbers)
    unsupported_rows = find_unsupported_claims(pack, text)
    superseded_rows = find_superseded_claims(pack, text)
    semantic_rows = find_semantic_mismatches(pack, text)
    blocking_semantics = [row for row in semantic_rows + unsupported_rows if row.get("status") == "FAIL"]
    status = "FAIL" if forbidden_rows or superseded_rows or blocking_semantics else "PARTIAL" if numeric_rows or unsupported_rows or semantic_rows else "PASS"
    result = {
        "schema_version": "1.0",
        "status": status,
        "model_pack": str(pack),
        "report": str(report),
        "forbidden_claim_count": len(forbidden_rows),
        "numeric_mismatch_count": len(numeric_rows),
        "unsupported_claim_count": len(unsupported_rows),
        "semantic_mismatch_count": len(semantic_rows),
        "superseded_claim_count": len(superseded_rows),
    }
    write_json(out / "report_validation.json", result)
    write_markdown(out / "report_validation.md", validation_markdown(result))
    artifacts = {
        "unsupported_claims": write_table(out, "unsupported_claims", unsupported_rows, output_format="csv"),
        "numeric_mismatches": write_table(out, "numeric_mismatches", numeric_rows, output_format="csv"),
        "forbidden_claims": write_table(out, "forbidden_claims", forbidden_rows, output_format="csv"),
        "semantic_mismatches": write_table(out, "semantic_mismatches", semantic_rows, output_format="csv"),
        "superseded_claims": write_table(out, "superseded_claims", superseded_rows, output_format="csv"),
    }
    for name, rows in (
        ("unsupported_claims", unsupported_rows),
        ("numeric_mismatches", numeric_rows),
        ("forbidden_claims", forbidden_rows),
        ("semantic_mismatches", semantic_rows),
        ("superseded_claims", superseded_rows),
    ):
        write_plain_csv(out / f"{name}.csv", rows)
    write_table_manifest(out / "report_validation_manifest.json", artifacts)
    return result


def collect_pack_numbers(pack: Path) -> set[str]:
    numbers: set[str] = set()
    for path in pack.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".json":
            collect_numbers_from_obj(read_json(path, default={}), numbers)
        elif path.name.endswith(".csv.gz"):
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    collect_numbers_from_obj(row, numbers)
        elif path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    collect_numbers_from_obj(row, numbers)
    return numbers


def collect_numbers_from_obj(value: Any, numbers: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            collect_numbers_from_obj(item, numbers)
    elif isinstance(value, list):
        for item in value:
            collect_numbers_from_obj(item, numbers)
    else:
        parsed = float_or_none(value)
        if parsed is not None:
            numbers.add(normalize_number(parsed))


def find_forbidden_claims(pack: Path, text: str) -> list[dict[str, Any]]:
    forbidden = read_json(pack / "forbidden_conclusions.json", default=[])
    lowered = text.lower()
    rows: list[dict[str, Any]] = []
    for item in forbidden if isinstance(forbidden, list) else []:
        statement = item.get("statement") if isinstance(item, dict) else str(item)
        if not statement:
            continue
        needle = first_distinct_phrase(str(statement))
        if needle and needle.lower() in lowered:
            rows.append({"status": "FAIL", "claim": statement, "match": needle, "reason": "forbidden_conclusion"})
    return rows


def find_numeric_mismatches(text: str, known_numbers: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in NUMBER_RE.finditer(text):
        raw = match.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        normalized = normalize_number(raw.rstrip("%"))
        if normalized not in known_numbers and raw not in {"0", "1", "2", "3", "4", "5", "10"}:
            rows.append(
                {
                    "status": "PARTIAL",
                    "number": raw,
                    "normalized": normalized,
                    "reason": "number_not_found_in_model_pack_tables_or_json",
                }
            )
    return rows


def find_unsupported_claims(pack: Path, text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    ready = read_json(pack / "analysis_ready.json", default={})
    module_status = ready.get("module_status", {}) if isinstance(ready.get("module_status"), dict) else {}
    rows: list[dict[str, Any]] = []
    if any(word in lowered for word in CAUSAL_WORDS):
        rows.append({"status": "PARTIAL", "claim_type": "causal_language", "reason": "causal wording requires controlled experiment evidence"})
    if any(a in lowered for a in SCHEDULED_AS_GENERATED) and any(b in lowered for b in GENERATED_TOKEN_WORDS):
        rows.append({"status": "FAIL", "claim_type": "scheduled_tokens_as_generated_tokens", "reason": "scheduled tokens and generated tokens must remain distinct"})
    if module_status.get("npu_mapping") != "PASS" and ("physical npu" in lowered or "物理npu" in lowered or "硬件瓶颈" in lowered):
        rows.append({"status": "FAIL", "claim_type": "physical_npu_mapping", "reason": "NPU mapping did not pass evidence gate"})
    if module_status.get("pipeline_reconciliation") != "PASS" and ("precise postprocessing" in lowered or "精确归因后处理" in lowered):
        rows.append({"status": "FAIL", "claim_type": "pipeline_precise_attribution", "reason": "pipeline reconciliation did not pass"})
    return rows


def find_superseded_claims(pack: Path, text: str) -> list[dict[str, Any]]:
    registry = read_json(pack / "claim_registry.json", default={})
    claims = registry.get("claims", []) if isinstance(registry, dict) else []
    lowered = text.lower()
    rows: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict) or claim.get("status") != "SUPERSEDED":
            continue
        statement = str(claim.get("statement") or "")
        phrase = first_distinct_phrase(statement)
        if phrase and phrase.lower() in lowered:
            rows.append(
                {
                    "status": "FAIL",
                    "claim_id": claim.get("claim_id"),
                    "statement": statement,
                    "superseded_by_claim_id": claim.get("superseded_by_claim_id"),
                    "reason": "superseded_claim_used",
                }
            )
    return rows


def find_semantic_mismatches(pack: Path, text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    rows: list[dict[str, Any]] = []
    checks = (
        (("engine residence", "single token", "单token计算", "单 token 计算"), "engine_residence_as_single_token_time"),
        (("configured concurrency equals", "配置并发等于实际", "配置并发就是实际"), "configured_as_actual_concurrency"),
        (("task failure is observability failure", "任务失败就是可观测性失败"), "task_failure_as_observability_failure"),
        (("scheduled tokens are generated", "scheduled token就是generated", "调度token就是生成token"), "scheduled_as_generated_tokens"),
        (("common-task throughput equals all-task", "common task throughput is all task", "共同任务吞吐等于全部任务"), "common_task_as_all_task_throughput"),
        (("whole-workload mean equals steady-state", "全窗口均值等于稳态", "pre-drain等于steady-state"), "time_window_conflation"),
        (("request count equals task count", "请求数等于任务数"), "aggregation_level_conflation"),
    )
    for phrases, reason in checks:
        if any(phrase in lowered for phrase in phrases):
            rows.append({"status": "FAIL", "reason": reason, "matched_phrases": [phrase for phrase in phrases if phrase in lowered]})
    registry = read_json(pack / "claim_registry.json", default={})
    claims = registry.get("claims", []) if isinstance(registry, dict) else []
    for claim in claims:
        if not isinstance(claim, dict) or claim.get("allowed_in_final_report") not in {False, "false", "False", 0, "0"}:
            continue
        statement = str(claim.get("statement") or "")
        phrase = first_distinct_phrase(statement)
        if phrase and phrase.lower() in lowered:
            rows.append({"status": "FAIL", "reason": "claim_not_allowed_in_final_report", "claim_id": claim.get("claim_id"), "match": phrase})
    ready = read_json(pack / "analysis_ready.json", default={})
    if ready.get("overall_status") != "PASS" and any(phrase in lowered for phrase in ("analysis status: pass", "analysis passed", "分析状态为pass", "分析通过")):
        rows.append({"status": "FAIL", "reason": "partial_or_failed_analysis_reported_as_pass", "actual_status": ready.get("overall_status")})
    known_concurrency = collect_named_values(pack, "configured_concurrency")
    for match in re.finditer(r"(?:configured[ _-]?concurrency|配置并发|并发档位)\s*(?:=|:|为)?\s*(\d+)", lowered):
        value = normalize_number(match.group(1))
        if known_concurrency and value not in known_concurrency:
            rows.append({"status": "FAIL", "reason": "configured_concurrency_not_in_model_pack", "value": value, "known_values": sorted(known_concurrency)})
    rows.extend(find_metric_unit_mismatches(pack, text))
    return rows


def find_metric_unit_mismatches(pack: Path, text: str) -> list[dict[str, Any]]:
    dictionary = pack / "metric_dictionary.md"
    if not dictionary.is_file():
        return []
    metric_units: dict[str, str] = {}
    for line in dictionary.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\|\s*`([^`]+)`\s*\|[^|]*\|\s*([^|]+?)\s*\|", line)
        if match:
            metric_units[match.group(1).strip()] = match.group(2).strip()
    distinctive_units = {unit for unit in metric_units.values() if unit not in {"s", "id", "status", "count"}}
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        for metric, expected_unit in metric_units.items():
            if metric.lower() not in lowered:
                continue
            observed = sorted(unit for unit in distinctive_units if unit.lower() in lowered and unit != expected_unit)
            if observed and expected_unit.lower() not in lowered:
                rows.append(
                    {
                        "status": "FAIL",
                        "reason": "metric_unit_mismatch",
                        "metric": metric,
                        "expected_unit": expected_unit,
                        "observed_units": observed,
                        "report_line": line_number,
                    }
                )
    return rows


def collect_named_values(pack: Path, field: str) -> set[str]:
    values: set[str] = set()
    for path in pack.rglob("*"):
        if not path.is_file():
            continue
        rows: list[dict[str, Any]] = []
        if path.suffix == ".json":
            payload = read_json(path, default={})
            collect_named_values_from_object(payload, field, values)
            continue
        if path.name.endswith(".csv.gz"):
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                rows = [dict(row) for row in csv.DictReader(handle)]
        elif path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = [dict(row) for row in csv.DictReader(handle)]
        for row in rows:
            if row.get(field) not in {None, ""}:
                values.add(normalize_number(row[field]))
    return values


def collect_named_values_from_object(value: Any, field: str, output: set[str]) -> None:
    if isinstance(value, dict):
        if value.get(field) not in {None, ""}:
            output.add(normalize_number(value[field]))
        for item in value.values():
            collect_named_values_from_object(item, field, output)
    elif isinstance(value, list):
        for item in value:
            collect_named_values_from_object(item, field, output)


def normalize_number(value: Any) -> str:
    parsed = float_or_none(value)
    if parsed is None:
        return str(value)
    if abs(parsed - round(parsed)) < 1e-9:
        return str(int(round(parsed)))
    return f"{parsed:.6g}"


def first_distinct_phrase(statement: str) -> str | None:
    text = statement.strip()
    for prefix in ("Do not ", "do not ", "不得", "禁止"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text:
        return None
    for sep in (".", " until ", " from ", "。"):
        if sep in text:
            part = text.split(sep, 1)[0].strip()
            if len(part) >= 12:
                return part
    return text if len(text) <= 120 else text[:120]


def validation_markdown(result: dict[str, Any]) -> str:
    return f"""# Report Validation

Status: `{result['status']}`

- Forbidden claims: {result['forbidden_claim_count']}
- Numeric mismatches: {result['numeric_mismatch_count']}
- Unsupported claims: {result['unsupported_claim_count']}
- Semantic mismatches: {result.get('semantic_mismatch_count', 0)}
- Superseded claims: {result.get('superseded_claim_count', 0)}
"""
