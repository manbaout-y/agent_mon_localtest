from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Sequence


def command_hash(argv: Sequence[str], *, salt: str | bytes | None = None) -> str:
    if salt is None:
        salt = os.environ.get("AGENTMON_COMMAND_HASH_SALT", "")
    if isinstance(salt, str):
        salt_bytes = salt.encode("utf-8", errors="surrogateescape")
    else:
        salt_bytes = salt
    h = hashlib.sha256()
    if salt_bytes:
        h.update(salt_bytes)
        h.update(b"\0")
    for item in argv:
        h.update(item.encode("utf-8", errors="surrogateescape"))
        h.update(b"\0")
    return h.hexdigest()


def read_command_hash_salt(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def redacted_argv(argv: Sequence[str], *, store_full_command: bool) -> list[str]:
    if store_full_command:
        return list(argv)
    if not argv:
        return []
    exe = os.path.basename(argv[0]) or argv[0]
    if len(argv) == 1:
        return [exe]
    return [exe, f"<{len(argv) - 1} args redacted>"]


def exception_fingerprint(exc: BaseException) -> dict[str, object]:
    message = str(exc)
    payload: dict[str, object] = {
        "error_type": type(exc).__name__,
        "error_category": type(exc).__module__,
        "error_message_length": len(message.encode("utf-8", errors="replace")),
        "error_message_hash": hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest(),
    }
    for attr in ("errno", "returncode"):
        value = getattr(exc, attr, None)
        if value is not None:
            payload[f"error_{attr}"] = value
    return payload
