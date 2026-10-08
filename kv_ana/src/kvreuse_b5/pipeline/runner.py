from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from kvreuse_b5.pipeline.b5 import build_b5_rows, validate_target_usage
from kvreuse_b5.pipeline.capability_audit import audit_paper_capabilities
from kvreuse_b5.pipeline.capsule import render_capsule, write_bounded_capsule
from kvreuse_b5.pipeline.io import atomic_json, read_csv, write_csv
from kvreuse_b5.pipeline.paper_offline import run_real_paper_components
from kvreuse_b5.pipeline.provider_plan import build_provider_plan, write_provider_plan
from kvreuse_b5.observability import ObservabilityWriter, TraceCollector
from kvreuse_b5.pipeline.real_data import RealStateSource
from kvreuse_b5.regression.historical_suite import HistoricalRegressionSuite
from kvreuse_b5.regression.manifest import load_lifemem_manifest
from kvreuse_b5.recipes import default_recipe_registry


STAGES = ("PRECHECK", "REGRESSION", "PAPER_AUDIT", "PAPER_OFFLINE", "B5", "AGGREGATE", "PROVIDER_PLAN", "CAPSULE")


class FatalPipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class PipelinePaths:
    project_root: Path
    output_root: Path
    config: Path
    manifest: Path
    references: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_config(path: Path) -> dict[str, Any]:
    # The checked-in YAML is deliberately JSON-compatible, avoiding a PyYAML dependency.
    return json.loads(path.read_text(encoding="utf-8"))


def _energy(pairs: Sequence[Any]) -> float:
    return float(sum(np.sum(pair.residual.astype(np.float64) ** 2) for pair in pairs))


class RealOfflinePipeline:
    def __init__(
        self,
        *,
        project_root: str | Path,
        q8_root: str | Path,
        q35_root: str | Path,
        deepseek_root: str | Path,
        deepseek_mapping: str | Path,
        output_root: str | Path,
        config_path: str | Path = "configs/real_offline_pipeline.yaml",
        max_pairs: int | None = None,
        capsule_max_bytes: int | None = None,
    ):
        root = Path(project_root).resolve()
        config_file = Path(config_path)
        if not config_file.is_absolute():
            config_file = root / config_file
        config = _load_config(config_file)
        self.config = config
        self.paths = PipelinePaths(
            root,
            Path(output_root).resolve(),
            config_file,
            root / config["fixed_manifest"],
            root / config["historical_references"],
        )
        self.raw_paths = {
            "q8_root": Path(q8_root),
            "q35_root": Path(q35_root),
            "deepseek_root": Path(deepseek_root),
            "deepseek_mapping": Path(deepseek_mapping),
        }
        self.source = RealStateSource(q8_root, q35_root, deepseek_root, deepseek_mapping)
        self.max_pairs = max_pairs
        self.capsule_max_bytes = int(capsule_max_bytes or config.get("capsule_max_bytes", 45000))
        self.state_path = self.paths.output_root / "pipeline_state.json"
        self.state: dict[str, Any] = {"schema_version": "real-offline-pipeline-v1", "stages": {}}

    def _load_state(self, resume: bool, force_stage: str | None) -> None:
        self.paths.output_root.mkdir(parents=True, exist_ok=True)
        if resume and self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if force_stage:
            index = STAGES.index(force_stage)
            self.state["stages"] = {
                name: value for name, value in self.state.get("stages", {}).items() if STAGES.index(name) < index
            }
        atomic_json(self.state_path, self.state)

    def _complete(self, stage: str, **details: Any) -> None:
        self.state.setdefault("stages", {})[stage] = {"status": "COMPLETE", "completed_at": _now(), **details}
        atomic_json(self.state_path, self.state)

    def _done(self, stage: str) -> bool:
        return self.state.get("stages", {}).get(stage, {}).get("status") == "COMPLETE"

    def precheck(self) -> dict[str, Any]:
        missing = [f"{name}={path}" for name, path in self.raw_paths.items() if not path.exists()]
        for path in (self.paths.config, self.paths.manifest, self.paths.references):
            if not path.exists():
                missing.append(str(path))
        if missing:
            raise FatalPipelineError("missing required input: " + ", ".join(missing))
        manifest = load_lifemem_manifest(self.paths.manifest)
        registry = default_recipe_registry()
        if len(registry.list_recipes()) != 20:
            raise FatalPipelineError("recipe registry is not 20/20")

        q8 = self.source.load_qwen_kv("Qwen3-8B", "POSITION_ONLY", 0)
        q8_all = q8["K"] + q8["V"]
        if len(q8["K"]) != 36 or len(q8["V"]) != 36:
            raise FatalPipelineError("Q8 pair0 POSITION_ONLY layer count regression")
        if any(pair.a.shape != (114, 1024) for pair in q8_all):
            raise FatalPipelineError("Q8 pair0 POSITION_ONLY shape regression")
        if any((int(pair.positions[0]), int(pair.positions[-1])) != (0, 113) for pair in q8_all):
            raise FatalPipelineError("Q8 pair0 POSITION_ONLY position regression")

        q35 = self.source.load_qwen_kv("Qwen3.5-27B", "POSITION_ONLY", 146)
        q35_all = q35["K"] + q35["V"]
        expected_layers = list(range(3, 64, 4))
        if [pair.layer_id for pair in q35["K"]] != expected_layers or [pair.layer_id for pair in q35["V"]] != expected_layers:
            raise FatalPipelineError("Q35 pair146 actual layer-ID regression")
        nonzero = [(pair.state_kind, pair.layer_id, _energy([pair])) for pair in q35_all if _energy([pair]) != 0.0]
        if nonzero:
            raise FatalPipelineError(f"Q35 pair146 POSITION_ONLY no longer zero residual: {nonzero[:3]}")

        deepseek = self.source.deepseek.load_pair(0)
        if len(deepseek) != 21 or [pair.layer_id for pair in deepseek] != list(range(2, 43, 2)):
            raise FatalPipelineError("DeepSeek pair0 C4 layer regression")
        if any(pair.a.shape != (22, 512) for pair in deepseek):
            raise FatalPipelineError("DeepSeek pair0 strict mapping shape regression")
        if any((int(pair.positions[0]), int(pair.positions[-1])) != (4, 88) for pair in deepseek):
            raise FatalPipelineError("DeepSeek pair0 strict mapping position regression")
        deepseek_layer2_sse = _energy([next(pair for pair in deepseek if pair.layer_id == 2)])
        if abs(deepseek_layer2_sse - 221.64072071361159) > 1e-3:
            raise FatalPipelineError(
                f"DeepSeek pair0 layer2 baseline SSE regression: {deepseek_layer2_sse:.17g}"
            )
        return {
            "manifest_pairs": len(manifest), "manifest_train": sum(x.split == "train" for x in manifest),
            "manifest_test": sum(x.split == "test" for x in manifest), "recipes": 20,
            "q8_pair0": {"layers_per_state": 36, "shape": [114, 1024]},
            "q35_pair146": {"layers_per_state": 16, "zero_residual": True},
            "deepseek_pair0": {"layers": 21, "shape": [22, 512], "positions": [4, 88], "layer2_baseline_sse": deepseek_layer2_sse},
        }

    def run(self, *, resume: bool = False, force_stage: str | None = None) -> dict[str, Any]:
        if force_stage and force_stage not in STAGES:
            raise ValueError(f"force stage must be one of {STAGES}")
        self._load_state(resume, force_stage)
        output = self.paths.output_root
        failures: list[str] = []

        if not self._done("PRECHECK"):
            details = self.precheck()
            atomic_json(output / "precheck.json", details)
            self._complete("PRECHECK", **details)

        regression_dir = output / "regression"
        if not self._done("REGRESSION"):
            randomized = self.config["randomized_svd"]
            suite = HistoricalRegressionSuite(
                self.source, self.paths.manifest, self.paths.references, regression_dir,
                pair_count=int(self.config["pair_count"]), max_pairs=self.max_pairs,
                seed=int(randomized["seed"]), oversample=int(randomized["oversample"]), niter=int(randomized["niter"]),
            )
            result = suite.run()
            if result["severe_failures"]:
                raise FatalPipelineError("; ".join(result["severe_failures"]))
            self._complete("REGRESSION", comparison_rows=len(result["comparisons"]), summary_rows=len(result["summaries"]))

        if not self._done("PAPER_AUDIT"):
            audit = audit_paper_capabilities(self.paths.project_root)
            write_csv(output / "paper_capability_audit.csv", audit)
            self._complete("PAPER_AUDIT", papers=len(audit))

        if not self._done("PAPER_OFFLINE"):
            manifest = load_lifemem_manifest(self.paths.manifest)
            observer = TraceCollector.from_env(output / "observability")
            paper_rows, costs, errors = run_real_paper_components(
                self.source, manifest, max_pairs=self.max_pairs, observability_collector=observer
            )
            write_csv(output / "paper_offline_tournament.csv", paper_rows)
            write_csv(output / "paper_method_costs.csv", costs)
            write_csv(output / "paper_method_failures.csv", errors)
            observability = ObservabilityWriter(observer.trace_dir or (output / "observability")).write(observer)
            self._complete(
                "PAPER_OFFLINE", rows=len(paper_rows), blocked=len(errors),
                trace_level=observability["trace_level"] if "trace_level" in observability else observability["level"],
                summary_event_count=observability["summary_event_count"],
                detail_event_count=observability["detail_event_count"],
            )

        if not self._done("B5"):
            historical = read_csv(regression_dir / "historical_summary.csv")
            b5_rows = build_b5_rows(historical)
            validate_target_usage(b5_rows)
            write_csv(output / "b5_mechanism_tournament.csv", b5_rows)
            self._complete("B5", rows=len(b5_rows))

        if not self._done("AGGREGATE"):
            paper_rows = read_csv(output / "paper_offline_tournament.csv")
            b5_rows = read_csv(output / "b5_mechanism_tournament.csv")
            combined = [*paper_rows, *b5_rows]
            validate_target_usage(combined)
            write_csv(output / "offline_tournament.csv", combined)
            costs = read_csv(output / "paper_method_costs.csv")
            write_csv(output / "method_costs.csv", costs)
            self._complete("AGGREGATE", rows=len(combined), target_leakage_audit="PASS")

        if not self._done("PROVIDER_PLAN"):
            audit = read_csv(output / "paper_capability_audit.csv")
            plan = build_provider_plan(audit)
            write_provider_plan(output, plan)
            self._complete("PROVIDER_PLAN", providers=len(plan["providers"]))

        if not self._done("CAPSULE"):
            regression = read_csv(regression_dir / "historical_regression.csv")
            audit = read_csv(output / "paper_capability_audit.csv")
            tournament = read_csv(output / "offline_tournament.csv")
            plan = json.loads((output / "runtime_provider_requirements.json").read_text(encoding="utf-8"))
            files = [str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()]
            capsule = render_capsule(regression=regression, audit=audit, tournament=tournament, provider_plan=plan, output_files=files, failures=failures)
            size = write_bounded_capsule(output / "real_offline_pipeline_capsule.txt", capsule, self.capsule_max_bytes)
            self._complete("CAPSULE", bytes=size, maximum=self.capsule_max_bytes)
        return self.state
