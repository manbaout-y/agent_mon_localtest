from __future__ import annotations

import json
from typing import Any

from agentmon.config import AgentMonConfig
from agentmon.paths import write_private_text
from agentmon.run_controller import RunController
from agentmon.runtime.task_state import TaskStatePolicy, build_task_state_from_events

from ..types import (
    BackendCapabilities,
    BackendStatus,
    PreparedRun,
    RunHandle,
    RunSpec,
    RuntimeContext,
)


class ProcessBackend:
    name = "process"

    def __init__(self, cfg: AgentMonConfig) -> None:
        self.cfg = cfg

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name=self.name,
            start=True,
            poll=True,
            cancel=False,
            pause=False,
            resume=False,
            checkpoint="none",
            semantic_events=(),
            tool_pid_attribution=False,
            model_request_id_injection=False,
        )

    def prepare(self, spec: RunSpec, context: RuntimeContext) -> PreparedRun:
        if not spec.command:
            raise ValueError("process backend requires RunSpec.command")
        payload = {
            "argv": list(spec.command),
            "cwd": spec.cwd,
            "trajectory": spec.trajectory,
            "write_final_report": spec.write_final_report,
        }
        return PreparedRun(spec=spec, context=context, backend_payload=payload)

    def start(self, prepared: PreparedRun) -> RunHandle:
        spec = prepared.spec
        label = spec.labels.get("label") or spec.task_id
        controller = RunController(
            self.cfg,
            list(spec.command),
            label=label,
            cwd=spec.cwd,
            trajectory=spec.trajectory,
            write_final_report=spec.write_final_report,
        )
        exit_code = controller.run()
        self._annotate_manifest(controller, spec, exit_code)
        state = "succeeded" if exit_code == 0 else "failed"
        return RunHandle(
            run_id=controller.run_id,
            run_dir=controller.run_dir,
            backend=self.name,
            state=state,
        )

    def poll(self, handle: RunHandle) -> BackendStatus:
        manifest_path = handle.run_dir / "manifest.json"
        if not manifest_path.is_file():
            return BackendStatus(state=handle.state, message="manifest not found")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return BackendStatus(state="unknown", message=f"invalid manifest: {exc}")
        status = manifest.get("status")
        if status == "success":
            state = "succeeded"
        elif status == "failed":
            state = "failed"
        elif status == "cancelled":
            state = "cancelled"
        elif status == "running":
            state = "running"
        else:
            state = "unknown"
        exit_code = manifest.get("exit_code")
        return BackendStatus(
            state=state,
            exit_code=exit_code if isinstance(exit_code, int) else None,
            message=str(status or ""),
        )

    def cancel(self, handle: RunHandle, reason: str) -> None:
        raise NotImplementedError("process backend cancel is not available for synchronous runs")

    def _annotate_manifest(self, controller: RunController, spec: RunSpec, exit_code: int) -> None:
        manifest_path = controller.run_dir / "manifest.json"
        try:
            manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        task_state = self._build_task_state(controller, spec)
        manifest.update(
            {
                "runtime_schema_version": "1.0",
                "runtime_backend": self.name,
                "runtime_task_id": spec.task_id,
                "runtime_state": "succeeded" if exit_code == 0 else "failed",
                "runtime_spec": spec.to_dict(),
                "runtime_task_state": {
                    "path_state": task_state.get("control", {}).get("state"),
                    "progress_score": task_state.get("progress", {}).get("score"),
                    "phase": task_state.get("phase"),
                    "context_active_tokens": task_state.get("context", {}).get("active_tokens"),
                    "prediction": task_state.get("prediction"),
                    "data_quality": task_state.get("data_quality", {}),
                    "limitations": task_state.get("limitations", []),
                },
            }
        )
        write_private_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def _build_task_state(self, controller: RunController, spec: RunSpec) -> dict[str, Any]:
        events = read_jsonl_events(controller.run_dir / "events.jsonl")
        prediction = spec.input.get("workload_prediction") if isinstance(spec.input, dict) else None
        task_state = build_task_state_from_events(
            events,
            task_id=spec.task_id,
            run_id=controller.run_id,
            resource_budget=spec.resource_budget,
            policy=TaskStatePolicy.from_config(self.cfg),
            initial_prediction=prediction if isinstance(prediction, dict) else None,
        ).to_dict()
        write_private_text(
            controller.run_dir / "runtime_task_state.json",
            json.dumps(task_state, ensure_ascii=False, indent=2, sort_keys=True),
        )
        return task_state


def read_jsonl_events(path: Any) -> list[dict[str, Any]]:
    try:
        rows = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows
    except OSError:
        return []
