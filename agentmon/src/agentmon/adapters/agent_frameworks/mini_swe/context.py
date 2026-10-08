from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass
class AgentMonContext:
    run_id: str
    run_dir: Path | None
    events_path: Path | None
    agent_id: str | None = None
    clock_anchor: dict[str, int | str] | None = None
    step_id: str | None = None
    current_span_id: str | None = None
    root_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.root_span_id is None:
            self.root_span_id = f"run:{self.run_id}"

    @classmethod
    def from_env(cls) -> "AgentMonContext":
        run_id = os.environ.get("AGENTMON_RUN_ID", "unattached")
        run_dir_raw = os.environ.get("AGENTMON_RUN_DIR")
        events_raw = os.environ.get("AGENTMON_EVENTS_PATH")
        root_span_id = os.environ.get("AGENTMON_ROOT_SPAN_ID")
        agent_id = os.environ.get("AGENTMON_AGENT_ID") or None
        run_dir = Path(run_dir_raw) if run_dir_raw else None
        events_path = Path(events_raw) if events_raw else (run_dir / "events.jsonl" if run_dir else None)
        return cls(
            run_id=run_id,
            run_dir=run_dir,
            events_path=events_path,
            agent_id=agent_id,
            root_span_id=root_span_id,
        )

    def child(
        self,
        *,
        step_id: str | None = None,
        current_span_id: str | None = None,
    ) -> "AgentMonContext":
        return AgentMonContext(
            run_id=self.run_id,
            run_dir=self.run_dir,
            events_path=self.events_path,
            agent_id=self.agent_id,
            clock_anchor=self.clock_anchor,
            step_id=step_id if step_id is not None else self.step_id,
            current_span_id=current_span_id if current_span_id is not None else self.current_span_id,
            root_span_id=self.root_span_id,
        )
