from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable


SOURCE_MANIFEST_RELATIVE_PATH = Path(".agentmon") / "source-manifest.json"
DEFAULT_MANAGED_PATHS = (
    "pyproject.toml",
    "README.md",
    "src",
    "scripts",
    "docs",
    "examples",
    "tests",
    "agentmon_implementation_spec",
    "artifacts/README.md",
)
EXCLUDED_DIR_NAMES = frozenset({".git", ".hg", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__MACOSX", "__pycache__"})
EXCLUDED_FILE_NAMES = frozenset({".DS_Store", ".LSOverride"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo")


def build_source_manifest(repo: str | Path, *, include_paths: Iterable[str] = DEFAULT_MANAGED_PATHS) -> dict[str, Any]:
    root = Path(repo).expanduser().resolve()
    files = []
    for path in iter_managed_files(root, include_paths):
        relative = path.relative_to(root).as_posix()
        files.append({"path": relative, "size": path.stat().st_size, "sha256": file_sha256(path)})
    files.sort(key=lambda item: str(item["path"]))
    canonical = json.dumps(files, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": "1.0",
        "manifest_type": "agentmon.source_tree",
        "managed_paths": list(include_paths),
        "excluded_rules": {
            "dir_names": sorted(EXCLUDED_DIR_NAMES),
            "file_names": sorted(EXCLUDED_FILE_NAMES),
            "suffixes": list(EXCLUDED_SUFFIXES),
            "appledouble_prefix": "._",
        },
        "file_count": len(files),
        "files": files,
        "source_tree_sha256": sha256(canonical).hexdigest(),
    }


def iter_managed_files(root: Path, include_paths: Iterable[str] = DEFAULT_MANAGED_PATHS) -> Iterable[Path]:
    seen: set[Path] = set()
    for raw in include_paths:
        path = (root / raw).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError(f"managed path escapes source root: {raw}") from None
        if not path.exists() or is_excluded(path, root):
            continue
        if path.is_file():
            if path not in seen:
                seen.add(path)
                yield path
            continue
        for child in sorted(path.rglob("*")):
            if child.is_file() and not child.is_symlink() and not is_excluded(child, root) and child not in seen:
                seen.add(child)
                yield child


def is_excluded(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
        return True
    name = path.name
    if name in EXCLUDED_FILE_NAMES or name.startswith("._") or name == "Icon\r":
        return True
    return any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def load_source_manifest(root: str | Path) -> dict[str, Any] | None:
    path = Path(root).expanduser().resolve() / SOURCE_MANIFEST_RELATIVE_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) and value.get("manifest_type") == "agentmon.source_tree" else None


def verify_source_tree(root: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    source_root = Path(root).expanduser().resolve()
    expected = {str(item.get("path")): item for item in manifest.get("files", []) if isinstance(item, dict) and item.get("path")}
    actual = {path.relative_to(source_root).as_posix(): path for path in iter_managed_files(source_root, manifest.get("managed_paths") or DEFAULT_MANAGED_PATHS)}
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    modified = sorted(
        relative
        for relative in sorted(set(expected) & set(actual))
        if expected[relative].get("sha256") != file_sha256(actual[relative])
    )
    rebuilt = build_source_manifest(source_root, include_paths=manifest.get("managed_paths") or DEFAULT_MANAGED_PATHS)
    valid_manifest = rebuilt.get("source_tree_sha256") == manifest.get("source_tree_sha256")
    return {
        "status": "PASS" if valid_manifest and not missing and not unexpected and not modified else "FAIL",
        "source_tree_sha256": manifest.get("source_tree_sha256"),
        "actual_source_tree_sha256": rebuilt.get("source_tree_sha256"),
        "missing_files": missing,
        "unexpected_files": unexpected,
        "modified_files": modified,
        "checked_file_count": len(actual),
    }


def detect_source_identity(source_root: str | Path) -> dict[str, Any]:
    root = Path(source_root).expanduser().resolve()
    manifest = load_source_manifest(root)
    if manifest is not None:
        verification = verify_source_tree(root, manifest)
        return {
            "source_identity_mode": "packaged_verified" if verification["status"] == "PASS" else "packaged_invalid",
            "source_tree_sha256": manifest.get("source_tree_sha256"),
            "verification": verification,
        }
    built = build_source_manifest(root)
    return {
        "source_identity_mode": "working_tree_unsealed",
        "source_tree_sha256": built["source_tree_sha256"],
        "verification": {"status": "NOT_VERIFIED", "reason": "package_source_manifest_missing"},
    }


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
