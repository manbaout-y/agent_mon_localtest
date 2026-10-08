from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import os
import tempfile

from kvreuse_b5 import __version__


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, allow_nan=True, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CheckpointStore:
    def __init__(self, root: str | Path, config_hash: str, dataset_hash: str):
        self.root = Path(root)
        self.config_hash = config_hash
        self.dataset_hash = dataset_hash
        self.root.mkdir(parents=True, exist_ok=True)
        manifest = self.root / "manifest.json"
        if manifest.exists():
            existing = json.loads(manifest.read_text(encoding="utf-8"))
            if existing.get("config_hash") != config_hash:
                raise ValueError("checkpoint config hash mismatch; refusing silent reuse")
            if existing.get("dataset_hash") != dataset_hash:
                raise ValueError("checkpoint dataset fingerprint mismatch; refusing silent reuse")
        else:
            atomic_write_json(
                manifest,
                {
                    "version": __version__,
                    "config_hash": config_hash,
                    "dataset_hash": dataset_hash,
                    "schema_version": 2,
                },
            )

    def _path(self, state_identity: tuple[object, ...], method_key: str) -> Path:
        token = json.dumps([state_identity, method_key, self.config_hash], sort_keys=True, default=str).encode("utf-8")
        return self.root / "pairs" / f"{sha256(token).hexdigest()}.json"

    def load(self, state_identity: tuple[object, ...], method_key: str) -> dict[str, Any] | None:
        path = self._path(state_identity, method_key)
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("config_hash") != self.config_hash:
            raise ValueError(f"checkpoint {path} has mismatched config hash")
        if record.get("dataset_hash") != self.dataset_hash:
            raise ValueError(f"checkpoint {path} has mismatched dataset fingerprint")
        if record.get("state_identity") != list(state_identity):
            raise ValueError(f"checkpoint {path} state_identity round-trip mismatch")
        if record.get("checkpoint_key") != method_key:
            raise ValueError(f"checkpoint {path} method variant/config round-trip mismatch")
        return record

    def save(self, state_identity: tuple[object, ...], method_key: str, record: dict[str, Any]) -> None:
        if record.get("config_hash") != self.config_hash:
            raise ValueError("cannot save checkpoint with a different config hash")
        if record.get("dataset_hash") != self.dataset_hash:
            raise ValueError("cannot save checkpoint with a different dataset fingerprint")
        if record.get("state_identity") != list(state_identity):
            raise ValueError("cannot save checkpoint under a different state_identity")
        if record.get("checkpoint_key") != method_key:
            raise ValueError("cannot save checkpoint under a different method variant/config hash")
        atomic_write_json(self._path(state_identity, method_key), record)
