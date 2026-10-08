from __future__ import annotations

import os
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from .config import AgentMonConfig


def output_root(cfg: AgentMonConfig) -> Path:
    return Path(cfg.general.output_root).expanduser()


def find_run_dir(cfg: AgentMonConfig, run_id: str) -> Path:
    run_dir = output_root(cfg) / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"run not found: {run_id}")
    return run_dir


def make_private_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    try:
        target.chmod(0o700)
    except PermissionError:
        pass
    return target


def write_private_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        chmod_private_file(target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def open_private_text(path: str | Path, *, encoding: str = "utf-8", newline: str | None = None) -> TextIO:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    return os.fdopen(fd, "w", encoding=encoding, newline=newline)


def chmod_private_file(path: str | Path) -> None:
    try:
        Path(path).chmod(0o600)
    except (FileNotFoundError, PermissionError):
        pass
