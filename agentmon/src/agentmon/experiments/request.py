from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Sequence

from agentmon.experiments.mechanisms import mechanism_definition, mechanism_registry


SCHEMA_VERSION = "1.0"
UNKNOWN = "UNKNOWN"
CONTRACT_FILENAME = "experiment-contract.json"


def load_experiment_request(path: str | Path) -> dict[str, Any]:
    request_path = Path(path).expanduser()
    suffix = request_path.suffix.lower()
    text = request_path.read_text(encoding="utf-8")
    if suffix == ".json":
        value = json.loads(text)
    elif suffix in {".toml", ".tml"}:
        value = tomllib.loads(text)
    elif suffix in {".yaml", ".yml"}:
        value = load_yaml_request(text)
    else:
        raise ValueError(f"unsupported experiment request format: {suffix or '<none>'}")
    if not isinstance(value, dict):
        raise ValueError("experiment request must be an object")
    return value


def load_yaml_request(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("YAML experiment requests require PyYAML; use JSON or TOML in this environment") from exc
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("experiment request must be an object")
    return value


def generate_experiment_contract(request: dict[str, Any]) -> dict[str, Any]:
    experiment = as_dict(request.get("experiment"))
    source = as_dict(request.get("source"))
    workload = as_dict(request.get("workload"))
    pilot = as_dict(request.get("pilot"))
    formal = as_dict(request.get("formal"))
    analysis = deepcopy(as_dict(request.get("analysis")))
    evidence = as_dict(request.get("evidence"))
    mechanisms = requested_mechanisms(request)
    definitions = {name: mechanism_definition(name) for name in mechanisms}
    known_definitions = {name: definition for name, definition in definitions.items() if definition}
    unknown_mechanisms = [name for name, definition in definitions.items() if not definition]

    experiment_id = str(experiment.get("id") or request.get("experiment_id") or slugify(experiment.get("name")) or "agentmon-experiment")
    allowed_differences = unique_strings(
        request.get("allowed_variant_differences"),
        *(definition.get("allowed_config_differences", ()) for definition in known_definitions.values()),
    )
    execution_path = str(
        request.get("execution_path")
        or as_dict(request.get("execution")).get("path")
        or source.get("execution_path")
        or infer_execution_path(known_definitions)
        or UNKNOWN
    )

    required_config = merge_required_config(known_definitions.values())
    baseline_config = deepcopy(as_dict(request.get("baseline_config")))
    candidate_config = deepcopy(as_dict(request.get("candidate_config")))
    variants_override = as_dict(request.get("variants"))
    baseline_config = deep_merge(baseline_config, as_dict(as_dict(variants_override.get("A")).get("config")))
    candidate_config = deep_merge(candidate_config, as_dict(as_dict(variants_override.get("B")).get("config")))
    apply_required_config(baseline_config, required_config, candidate=False)
    apply_required_config(candidate_config, required_config, candidate=True)
    apply_common_mode(baseline_config, candidate_config, request, allowed_differences)

    activation_paths = unique_strings(
        evidence.get("activation_paths"),
        source.get("activation_evidence_paths"),
        request.get("activation_evidence_paths"),
    )
    contract: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "source": keep_non_empty(
            {
                "repo": source.get("repo"),
                "request_name": experiment.get("name"),
            }
        ),
        "baseline_variant": "A",
        "allowed_variant_differences": allowed_differences,
        "execution_path_requirements": {
            name: list(definition.get("allowed_execution_paths", ()))
            for name, definition in known_definitions.items()
        },
        "variants": {
            "A": {
                "role": "baseline",
                "config": baseline_config,
            },
            "B": {
                "role": "candidate",
                "mechanisms": mechanisms,
                "execution_path": execution_path,
                "activation_evidence_paths": activation_paths,
                "config": candidate_config,
            },
        },
        "pilot": build_pilot_section(pilot),
        "formal": build_formal_section(formal),
        "analysis": build_analysis_section(analysis, workload, pilot, formal),
        "commands": deepcopy(as_dict(request.get("commands"))),
        "formal_gates": deep_merge(
            {
                "require_activation": True,
                "require_pilot": bool(pilot),
            },
            as_dict(request.get("formal_gates")),
        ),
        "generator": {
            "result_type": "agentmon.experiment_request.generated_contract",
            "mechanism_registry": sorted(mechanism_registry()),
            "unknown_mechanisms": unknown_mechanisms,
            "limitations": [
                "The request generator expands intent into a contract; it does not prove mechanism activation.",
                "Missing batch roots or evidence paths remain unresolved until smoke or pilot artifacts exist.",
                "Mechanism semantics come from registry profiles and may be overridden by an explicit contract.",
            ],
        },
    }
    if activation_paths:
        contract["activation_evidence_paths"] = activation_paths
    candidate_override = as_dict(variants_override.get("B"))
    denominator = candidate_override.get("activation_coverage_denominator", evidence.get("coverage_denominator"))
    if denominator is not None:
        contract["variants"]["B"]["activation_coverage_denominator"] = denominator
    if candidate_override.get("expected_activation_count") is not None:
        contract["variants"]["B"]["expected_activation_count"] = candidate_override["expected_activation_count"]
    return prune_empty_contract(contract)


def write_generated_contract(contract: dict[str, Any], path: str | Path) -> Path:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def requested_mechanisms(request: dict[str, Any]) -> list[str]:
    experiment = as_dict(request.get("experiment"))
    raw_values = [
        request.get("mechanism"),
        request.get("mechanisms"),
        experiment.get("mechanism"),
        experiment.get("mechanisms"),
    ]
    names: list[str] = []
    for raw in raw_values:
        for item in as_list_or_scalar(raw):
            name = mechanism_name(item)
            if name and name not in names:
                names.append(name)
    return names


def mechanism_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or "").strip()
    return str(value or "").strip()


def infer_execution_path(definitions: dict[str, dict[str, Any]]) -> str | None:
    if not definitions:
        return None
    allowed_lists = [
        [str(item) for item in definition.get("allowed_execution_paths", ())]
        for definition in definitions.values()
    ]
    common = set(allowed_lists[0])
    for allowed in allowed_lists[1:]:
        common &= set(allowed)
    if not common:
        return None
    for candidate in allowed_lists[0]:
        if candidate in common:
            return candidate
    return None


def merge_required_config(definitions: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for definition in definitions:
        for path, value in as_dict(definition.get("required_config")).items():
            merged[str(path)] = value
    return merged


def apply_required_config(config: dict[str, Any], required_config: dict[str, Any], *, candidate: bool) -> None:
    for path, value in sorted(required_config.items()):
        if candidate:
            set_dotted_path(config, path, value)
        elif isinstance(value, bool):
            set_dotted_path(config, path, not value)
        else:
            set_dotted_path(config, path, None)


def apply_common_mode(
    baseline_config: dict[str, Any],
    candidate_config: dict[str, Any],
    request: dict[str, Any],
    allowed_differences: list[str],
) -> None:
    experiment = as_dict(request.get("experiment"))
    mode = request.get("mode") or experiment.get("mode")
    if not mode and not path_allowed("control.mode", allowed_differences):
        return
    selected_mode = str(mode or "advise")
    if selected_mode == "advice":
        selected_mode = "advise"
    if selected_mode not in {"advise", "enforce"}:
        raise ValueError("experiment mode must be advise or enforce")
    set_dotted_path(baseline_config, "control.mode", selected_mode)
    set_dotted_path(candidate_config, "control.mode", selected_mode)


def build_pilot_section(pilot: dict[str, Any]) -> dict[str, Any]:
    if not pilot:
        return {}
    task_count = int_or_none(pilot.get("tasks") or pilot.get("min_task_count"))
    success_count = int_or_none(pilot.get("min_success_count"))
    if success_count is None and as_bool(pilot.get("require_all_success", True)):
        success_count = task_count
    return keep_non_empty(
        {
            "batch_root": pilot.get("batch_root"),
            "planned_task_count": task_count,
            "min_task_count": task_count,
            "min_success_count": success_count,
        }
    )


def build_formal_section(formal: dict[str, Any]) -> dict[str, Any]:
    if not formal:
        return {}
    return keep_non_empty(
        {
            "planned_task_count": int_or_none(formal.get("tasks") or formal.get("task_count")),
            "concurrency": as_list_or_scalar(formal.get("concurrency")),
            "batch_root": formal.get("batch_root"),
        }
    )


def build_analysis_section(
    analysis: dict[str, Any],
    workload: dict[str, Any],
    pilot: dict[str, Any],
    formal: dict[str, Any],
) -> dict[str, Any]:
    if workload.get("tasks") and "task_file" not in analysis:
        analysis["task_file"] = workload.get("tasks")
    if "batch_root" not in analysis:
        analysis["batch_root"] = formal.get("batch_root") or pilot.get("batch_root")
    if "analysis_preset" not in analysis:
        analysis["analysis_preset"] = "standard"
    return keep_non_empty(analysis)


def set_dotted_path(config: dict[str, Any], path: str, value: Any) -> None:
    current = config
    parts = str(path).split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def prune_empty_contract(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: pruned
            for key, item in value.items()
            if (pruned := prune_empty_contract(item)) not in ({}, [], None)
        }
    if isinstance(value, list):
        return [item for item in (prune_empty_contract(item) for item in value) if item not in ({}, [], None)]
    return value


def keep_non_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not empty_value(item)}


def unique_strings(*values: Any) -> list[str]:
    rows: list[str] = []
    for raw in values:
        for item in as_list_or_scalar(raw):
            text = str(item).strip()
            if text and text not in rows:
                rows.append(text)
    return rows


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list_or_scalar(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None or value == "":
        return []
    return [value]


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def path_allowed(path: str, allowed: list[str]) -> bool:
    for rule in allowed:
        if rule == path:
            return True
        if rule.endswith(".*") and path.startswith(rule[:-1]):
            return True
    return False


def empty_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {} or value == ()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate an AgentMon experiment contract from a small request file")
    parser.add_argument("--request", required=True, help="experiment request JSON/TOML/YAML")
    parser.add_argument("--output", help="contract JSON path; defaults to <output-dir>/experiment-contract.json")
    parser.add_argument("--output-dir", help="directory used when --output is omitted")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        contract = generate_experiment_contract(load_experiment_request(args.request))
        output = Path(args.output).expanduser() if args.output else Path(args.output_dir or ".").expanduser() / CONTRACT_FILENAME
        path = write_generated_contract(contract, output)
    except Exception as exc:  # noqa: BLE001 - CLI should return a clear generator failure.
        print(f"agentmon experiment init error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "PLAN_READY", "contract": str(path)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
