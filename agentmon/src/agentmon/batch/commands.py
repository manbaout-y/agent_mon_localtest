from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
from typing import Any, Sequence

from agentmon.adapters.agent_frameworks.mini_swe.adapter import (
    configure_p1_command as configure_mini_swe_p1_command,
)
from agentmon.batch.io import make_private_dir, render_template

RUN_ID_RE = re.compile(r"agentmon run_id=([^ ]+)")
REPORT_RE = re.compile(r"report=([^ \n]+)")

def task_command(args: argparse.Namespace, task: dict[str, str], context: dict[str, str]) -> str:
    if args.command_template:
        return render_template(args.command_template, {**context, **task})
    if args.command_column and task.get(args.command_column):
        return render_template(task[args.command_column], {**context, **task})
    raise ValueError(f"task {task['task_id']} has no command")


def configure_p1_command(command: str, mode: str) -> tuple[str, str]:
    return configure_mini_swe_p1_command(command, mode)


def run_judge(
    args: argparse.Namespace,
    context: dict[str, Any],
    env: dict[str, str],
    log_dir: Path,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    command = render_template(args.judge_command_template, context)
    stdout = log_dir / "judge.stdout.log"
    stderr = log_dir / "judge.stderr.log"
    result = run_logged(
        ["bash", "-lc", command],
        env=env,
        stdout_path=stdout,
        stderr_path=stderr,
    )
    return result, stdout, stderr


def append_error(current: str, message: str) -> str:
    return f"{current}; {message}" if current else message


def agentmon_command(args: argparse.Namespace, command: str, *extra: str) -> list[str]:
    argv = [args.agentmon_python, "-m", "agentmon", command]
    if args.agentmon_config:
        argv.extend(["--config", args.agentmon_config])
    if args.output_root:
        argv.extend(["--output-root", args.output_root])
    argv.extend(extra)
    return argv


def run_logged(
    argv: Sequence[str],
    *,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> subprocess.CompletedProcess[str]:
    make_private_dir(stdout_path.parent)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        return subprocess.run(
            list(argv),
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=False,
        )


def parse_run_id(stderr_text: str) -> str | None:
    matches = RUN_ID_RE.findall(stderr_text)
    return matches[-1] if matches else None


def parse_run_dir(stderr_text: str, output_root: str | None, run_id: str | None) -> Path | None:
    report_matches = REPORT_RE.findall(stderr_text)
    if report_matches:
        return Path(report_matches[-1]).expanduser().parent
    if output_root and run_id:
        return Path(output_root).expanduser() / run_id
    return None
