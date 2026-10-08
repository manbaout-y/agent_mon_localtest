from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable

from agentmon.batch.io import batch_context, render_template


SOURCE_EXCLUDES = {
    ".DS_Store",
    ".AppleDouble",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__MACOSX",
    "__pycache__",
}
SOURCE_SUFFIX_EXCLUDES = {".pyc", ".pyo"}
SOURCE_ENV_KEYS = {
    "AGENTMON_HOME",
    "AGENTMON_REPO",
    "AGENTMON_SOURCE_ROOT",
}


class WorkspacePolicyError(ValueError):
    pass


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.expanduser().resolve(strict=False)
    right_resolved = right.expanduser().resolve(strict=False)
    return is_relative_to(left_resolved, right_resolved) or is_relative_to(right_resolved, left_resolved)


def looks_like_agentmon_source_root(path: Path) -> bool:
    resolved = path.expanduser().resolve(strict=False)
    return (resolved / "src" / "agentmon" / "__init__.py").is_file()


def protected_source_roots(
    args: argparse.Namespace,
    env: dict[str, str],
    *,
    default_root: Path,
) -> list[Path]:
    candidates: list[Path] = [default_root]
    candidates.extend(Path(value) for value in (getattr(args, "protected_source_root", None) or []))
    for key in SOURCE_ENV_KEYS:
        value = env.get(key)
        if value:
            candidate = Path(value)
            if looks_like_agentmon_source_root(candidate):
                candidates.append(candidate)

    roots: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _absolute_rendered_path(value: str, *, label: str, task_id: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise WorkspacePolicyError(
            f"task {task_id} {label} must render to an absolute path, got {value!r}"
        )
    return path.resolve(strict=False)


def _validate_workspace_contract(
    *,
    task_id: str,
    batch_root: Path,
    workspace: Path,
    cwd: Path,
    policy: str,
    protected_roots: Iterable[Path],
) -> dict[str, Any]:
    batch_root = batch_root.resolve(strict=False)
    workspace = workspace.resolve(strict=False)
    cwd = cwd.resolve(strict=False)
    workspace_root = (batch_root / "workspaces").resolve(strict=False)

    if workspace == batch_root:
        raise WorkspacePolicyError(f"task {task_id} workspace cannot equal the batch root: {workspace}")
    if not is_relative_to(cwd, workspace):
        raise WorkspacePolicyError(
            f"task {task_id} cwd must stay inside its workspace after symlink resolution: "
            f"cwd={cwd} workspace={workspace}"
        )
    if policy == "isolated" and not is_relative_to(workspace, workspace_root):
        raise WorkspacePolicyError(
            f"task {task_id} workspace escapes the isolated workspace root: "
            f"workspace={workspace} allowed_root={workspace_root}; use --workspace-policy external "
            "only for an intentionally pre-materialized repository"
        )

    overlaps: list[str] = []
    for protected in protected_roots:
        protected_resolved = protected.resolve(strict=False)
        if paths_overlap(workspace, protected_resolved) or paths_overlap(cwd, protected_resolved):
            overlaps.append(str(protected_resolved))
    if overlaps:
        raise WorkspacePolicyError(
            f"task {task_id} workspace/cwd overlaps protected AgentMon source: {', '.join(overlaps)}"
        )

    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "status": "PASS",
        "policy": policy,
        "batch_root": str(batch_root),
        "workspace_root": str(workspace_root),
        "workspace": str(workspace),
        "cwd": str(cwd),
        "protected_source_roots": [str(path.resolve(strict=False)) for path in protected_roots],
        "checks": {
            "absolute_paths": "PASS",
            "cwd_within_workspace": "PASS",
            "workspace_policy_boundary": "PASS",
            "protected_source_overlap": "PASS",
            "symlink_resolution": "PASS",
        },
    }


def render_workspace_contract(
    args: argparse.Namespace,
    task: dict[str, str],
    batch_root: Path,
    protected_roots: Iterable[Path],
) -> dict[str, Any]:
    context = batch_context(task, batch_root)
    workspace = _absolute_rendered_path(
        render_template(args.workspace_template, context),
        label="workspace",
        task_id=task["task_id"],
    )
    cwd = _absolute_rendered_path(
        render_template(args.cwd_template, {**context, "workspace": str(workspace)}),
        label="cwd",
        task_id=task["task_id"],
    )
    return _validate_workspace_contract(
        task_id=task["task_id"],
        batch_root=batch_root,
        workspace=workspace,
        cwd=cwd,
        policy=getattr(args, "workspace_policy", "isolated"),
        protected_roots=protected_roots,
    )


def build_workspace_contracts(
    args: argparse.Namespace,
    tasks: list[dict[str, str]],
    batch_root: Path,
    protected_roots: Iterable[Path],
) -> dict[str, dict[str, Any]]:
    contracts: dict[str, dict[str, Any]] = {}
    workspace_owners: dict[str, str] = {}
    task_ids: set[str] = set()
    for task in tasks:
        task_id = task["task_id"]
        if task_id in task_ids:
            raise WorkspacePolicyError(f"duplicate task id after sanitization: {task_id}")
        task_ids.add(task_id)
        contract = render_workspace_contract(args, task, batch_root, protected_roots)
        workspace = str(contract["workspace"])
        previous = workspace_owners.get(workspace)
        if previous is not None:
            raise WorkspacePolicyError(
                f"tasks {previous} and {task_id} resolve to the same workspace: {workspace}"
            )
        workspace_owners[workspace] = task_id
        contracts[task_id] = contract
    return contracts


def revalidate_workspace_contract(
    contract: dict[str, Any],
    *,
    protected_roots: Iterable[Path],
) -> dict[str, Any]:
    return _validate_workspace_contract(
        task_id=str(contract["task_id"]),
        batch_root=Path(str(contract["batch_root"])),
        workspace=Path(str(contract["workspace"])),
        cwd=Path(str(contract["cwd"])),
        policy=str(contract["policy"]),
        protected_roots=protected_roots,
    )


def write_workspace_contracts(batch_root: Path, contracts: dict[str, dict[str, Any]]) -> Path:
    root = batch_root / "workspace-contracts"
    root.mkdir(parents=True, exist_ok=True)
    for task_id, contract in contracts.items():
        path = root / f"{task_id}.json"
        path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)
        contract["contract_path"] = str(path)
    index = batch_root / "workspace-contracts.json"
    payload = {
        "schema_version": "1.0",
        "status": "PASS",
        "task_count": len(contracts),
        "contracts": [contracts[key] for key in sorted(contracts)],
    }
    index.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    index.chmod(0o600)
    return index


def _iter_source_files(root: Path, relative_roots: Iterable[str]) -> Iterable[tuple[str, Path]]:
    for relative_root in relative_roots:
        base = root / relative_root
        if base.is_file():
            yield base.relative_to(root).as_posix(), base
            continue
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in SOURCE_EXCLUDES or part.startswith("._") for part in relative.parts):
                continue
            if path.suffix in SOURCE_SUFFIX_EXCLUDES:
                continue
            yield relative.as_posix(), path


def capture_source_manifest(root: Path, *, relative_roots: Iterable[str] = ("src", "scripts")) -> dict[str, Any]:
    resolved_root = root.expanduser().resolve(strict=False)
    files: dict[str, dict[str, Any]] = {}
    aggregate = sha256()
    total_bytes = 0
    for relative, path in _iter_source_files(resolved_root, relative_roots):
        digest = sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        file_digest = digest.hexdigest()
        files[relative] = {"sha256": file_digest, "size_bytes": size}
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(bytes.fromhex(file_digest))
        total_bytes += size
    return {
        "schema_version": "1.0",
        "root": str(resolved_root),
        "relative_roots": list(relative_roots),
        "status": "CAPTURED",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "aggregate_sha256": aggregate.hexdigest(),
        "files": files,
    }


def compare_source_manifests(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_files = before.get("files") if isinstance(before.get("files"), dict) else {}
    after_files = after.get("files") if isinstance(after.get("files"), dict) else {}
    before_names = set(before_files)
    after_names = set(after_files)
    modified = sorted(
        name
        for name in before_names & after_names
        if before_files[name].get("sha256") != after_files[name].get("sha256")
    )
    added = sorted(after_names - before_names)
    removed = sorted(before_names - after_names)
    changed = bool(modified or added or removed)
    return {
        "schema_version": "1.0",
        "status": "CHANGED" if changed else "PASS",
        "before_sha256": before.get("aggregate_sha256"),
        "after_sha256": after.get("aggregate_sha256"),
        "modified_files": modified,
        "added_files": added,
        "removed_files": removed,
        "forbidden_conclusions": (
            []
            if not changed
            else ["Do not compare or publish this batch as a reproducible experiment."]
        ),
    }


def _snapshot_ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in SOURCE_EXCLUDES or name.startswith("._") or Path(name).suffix in SOURCE_SUFFIX_EXCLUDES
    }


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def prepare_runtime_source_snapshot(source_root: Path, batch_root: Path, *, identity: str) -> dict[str, Any]:
    source_src = source_root / "src"
    if not (source_src / "agentmon" / "__init__.py").is_file():
        raise RuntimeError(f"AgentMon source snapshot unavailable: {source_src}")
    source_manifest = capture_source_manifest(source_root, relative_roots=("src",))
    snapshot_root = batch_root / "runtime-source-snapshots" / identity
    snapshot_src = snapshot_root / "src"
    if snapshot_root.exists():
        raise RuntimeError(f"runtime source snapshot already exists: {snapshot_root}")
    snapshot_root.mkdir(parents=True, mode=0o700)
    shutil.copytree(source_src, snapshot_src, ignore=_snapshot_ignore)
    manifest = capture_source_manifest(snapshot_root, relative_roots=("src",))
    copy_verification = compare_source_manifests(source_manifest, manifest)
    if copy_verification["status"] != "PASS":
        raise RuntimeError(
            f"AgentMon runtime source changed while creating the snapshot: {copy_verification}"
        )
    _make_tree_read_only(snapshot_root)
    return {
        "schema_version": "1.0",
        "status": "READY",
        "source_root": str(source_root.resolve(strict=False)),
        "snapshot_root": str(snapshot_root.resolve(strict=False)),
        "pythonpath": str(snapshot_src.resolve(strict=False)),
        "manifest": manifest,
        "copy_verification": copy_verification,
        "read_only": True,
    }


def _path_exposes_protected_source(value: str, protected_roots: Iterable[Path]) -> bool:
    if not value or not Path(value).expanduser().is_absolute():
        return False
    candidate = Path(value).expanduser().resolve(strict=False)
    return any(paths_overlap(candidate, root) for root in protected_roots)


def build_isolated_runtime_env(
    env: dict[str, str],
    *,
    snapshot_pythonpath: Path,
    protected_roots: Iterable[Path],
) -> tuple[dict[str, str], dict[str, Any]]:
    isolated = dict(env)
    removed_env_keys: list[str] = []
    for key in SOURCE_ENV_KEYS:
        value = isolated.get(key, "")
        if _path_exposes_protected_source(value, protected_roots):
            isolated.pop(key, None)
            removed_env_keys.append(key)

    kept_pythonpath: list[str] = []
    removed_pythonpath: list[str] = []
    for entry in isolated.get("PYTHONPATH", "").split(os.pathsep):
        if not entry:
            continue
        if _path_exposes_protected_source(entry, protected_roots):
            removed_pythonpath.append(entry)
        else:
            kept_pythonpath.append(entry)
    isolated["PYTHONPATH"] = os.pathsep.join([str(snapshot_pythonpath), *kept_pythonpath])
    isolated["PYTHONDONTWRITEBYTECODE"] = "1"
    return isolated, {
        "schema_version": "1.0",
        "status": "PASS",
        "snapshot_pythonpath": str(snapshot_pythonpath),
        "removed_environment_keys": sorted(removed_env_keys),
        "removed_pythonpath_entries": removed_pythonpath,
    }


def verify_runtime_snapshot(python: str, env: dict[str, str], *, expected_pythonpath: Path) -> dict[str, Any]:
    marker = "__AGENTMON_RUNTIME_SNAPSHOT__="
    code = (
        "import json, agentmon, agentmon.cli; "
        f"print({marker!r} + json.dumps({{'file': agentmon.__file__}}))"
    )
    result = subprocess.run(
        [python, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    payload: dict[str, Any] = {}
    if result.returncode == 0:
        for line in reversed(result.stdout.splitlines()):
            if not line.startswith(marker):
                continue
            try:
                payload = json.loads(line[len(marker) :])
            except json.JSONDecodeError:
                payload = {}
            break
    imported_file = Path(str(payload.get("file") or "")).resolve(strict=False) if payload.get("file") else None
    passed = imported_file is not None and is_relative_to(imported_file, expected_pythonpath.resolve(strict=False))
    report = {
        "schema_version": "1.0",
        "status": "PASS" if passed else "FAIL",
        "python": python,
        "expected_pythonpath": str(expected_pythonpath),
        "imported_agentmon_file": str(imported_file) if imported_file else None,
        "returncode": result.returncode,
        "stderr": result.stderr[-4000:],
    }
    if not passed:
        raise RuntimeError(f"isolated AgentMon runtime snapshot import failed: {report}")
    return report
