#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentmon.pipeline.identity import (  # noqa: E402 - script must bootstrap its local source tree.
    EXCLUDED_DIR_NAMES,
    EXCLUDED_FILE_NAMES,
    EXCLUDED_SUFFIXES,
    SOURCE_MANIFEST_RELATIVE_PATH,
    build_source_manifest,
    is_excluded,
)


DEFAULT_INCLUDE_PATHS = (
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
def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    prefix = args.prefix or default_archive_prefix(output)
    include_paths = tuple(args.include or DEFAULT_INCLUDE_PATHS)
    summary = build_source_package(repo, output, prefix=prefix, include_paths=include_paths)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a clean AgentMon source tarball.")
    parser.add_argument("--repo", default=str(ROOT), help="AgentMon source tree root")
    parser.add_argument("--output", required=True, help="output .tar.gz path")
    parser.add_argument("--prefix", help="top-level archive directory name")
    parser.add_argument(
        "--include",
        action="append",
        help="repo-relative file or directory to include; may be repeated",
    )
    return parser


def build_source_package(
    repo: Path,
    output: Path,
    *,
    prefix: str,
    include_paths: Sequence[str] = DEFAULT_INCLUDE_PATHS,
) -> dict[str, Any]:
    started = time.monotonic()
    repo = repo.expanduser().resolve()
    output = output.expanduser().resolve()
    files = list(iter_package_files(repo, include_paths))
    source_manifest = build_source_manifest(repo, include_paths=include_paths)
    source_manifest_bytes = (json.dumps(source_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["COPYFILE_DISABLE"] = "1"
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                add_directory_entry(tar, prefix)
                seen_dirs: set[str] = {prefix}
                for file_path in files:
                    rel = file_path.relative_to(repo)
                    archive_name = f"{prefix}/{rel.as_posix()}"
                    for parent in parent_archive_dirs(archive_name):
                        if parent not in seen_dirs:
                            add_directory_entry(tar, parent)
                            seen_dirs.add(parent)
                    add_file_entry(tar, file_path, archive_name)
                manifest_name = f"{prefix}/{SOURCE_MANIFEST_RELATIVE_PATH.as_posix()}"
                for parent in parent_archive_dirs(manifest_name):
                    if parent not in seen_dirs:
                        add_directory_entry(tar, parent)
                        seen_dirs.add(parent)
                add_bytes_entry(tar, manifest_name, source_manifest_bytes)

    sha256 = sha256_file(output)
    sha_path = output.with_name(output.name + ".sha256")
    sha_path.write_text(f"{sha256}  {output.name}\n", encoding="utf-8")
    members = archive_members(output)
    forbidden = forbidden_archive_members(members)
    summary_path = output.with_name(output.name + ".summary.json")
    build_manifest_path = output.with_name(output.name + ".BUILD_MANIFEST.json")
    build_manifest = build_manifest_payload(
        repo=repo,
        output=output,
        sha256=sha256,
        prefix=prefix,
        file_count=len(files),
        member_count=len(members),
        forbidden_members=forbidden,
        duration_seconds=time.monotonic() - started,
        source_tree_sha256=source_manifest["source_tree_sha256"],
    )
    build_manifest_path.write_text(
        json.dumps(build_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "status": "PASS" if not forbidden else "FAIL",
        "schema_version": "1.0",
        "repo": str(repo),
        "output": str(output),
        "sha256": sha256,
        "sha256_path": str(sha_path),
        "prefix": prefix,
        "file_count": len(files),
        "source_tree_sha256": source_manifest["source_tree_sha256"],
        "source_manifest_member": f"{prefix}/{SOURCE_MANIFEST_RELATIVE_PATH.as_posix()}",
        "member_count": len(members),
        "forbidden_members": forbidden,
        "summary_path": str(summary_path),
        "build_manifest_path": str(build_manifest_path),
        "excluded_rules": {
            "dir_names": sorted(EXCLUDED_DIR_NAMES),
            "file_names": sorted(EXCLUDED_FILE_NAMES),
            "suffixes": list(EXCLUDED_SUFFIXES),
            "appledouble_prefix": "._",
        },
        "duration_seconds": time.monotonic() - started,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def build_manifest_payload(
    *,
    repo: Path,
    output: Path,
    sha256: str,
    prefix: str,
    file_count: int,
    member_count: int,
    forbidden_members: Sequence[str],
    duration_seconds: float,
    source_tree_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "manifest_type": "agentmon.source_package.build",
        "created_wall_time_ns": time.time_ns(),
        "repo": str(repo),
        "package": str(output),
        "package_name": output.name,
        "package_sha256": sha256,
        "source_tree_sha256": source_tree_sha256,
        "archive_prefix": prefix,
        "file_count": file_count,
        "member_count": member_count,
        "forbidden_member_count": len(forbidden_members),
        "forbidden_members": list(forbidden_members),
        "python": {
            "executable": sys.executable,
            "version": sys.version,
            "version_info": list(sys.version_info[:5]),
        },
        "platform": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "system": platform.system(),
        },
        "git": git_snapshot(repo),
        "duration_seconds": duration_seconds,
    }


def git_snapshot(repo: Path) -> dict[str, Any]:
    root = run_git(repo, "rev-parse", "--show-toplevel")
    if root["returncode"] != 0:
        return {
            "available": False,
            "reason": "not_a_git_repository_or_git_unavailable",
            "stderr": root.get("stderr", "")[-4000:],
        }
    commit = run_git(repo, "rev-parse", "HEAD")
    status = run_git(repo, "status", "--short")
    return {
        "available": True,
        "root": root.get("stdout", "").strip(),
        "commit": commit.get("stdout", "").strip() if commit["returncode"] == 0 else None,
        "dirty": bool(status.get("stdout", "").strip()) if status["returncode"] == 0 else None,
        "status_short": status.get("stdout", "").splitlines() if status["returncode"] == 0 else [],
    }


def run_git(repo: Path, *args: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - build manifest should preserve git probe failures as data.
        return {"returncode": -1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def iter_package_files(repo: Path, include_paths: Sequence[str]) -> Iterable[Path]:
    seen: set[Path] = set()
    for raw in include_paths:
        path = (repo / raw).resolve()
        try:
            path.relative_to(repo)
        except ValueError:
            raise ValueError(f"include path escapes repo: {raw}") from None
        if not path.exists() or is_excluded(path, repo):
            continue
        if path.is_file():
            if path not in seen:
                seen.add(path)
                yield path
            continue
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not child.is_symlink() and not is_excluded(child, repo) and child not in seen:
                    seen.add(child)
                    yield child


def default_archive_prefix(output: Path) -> str:
    name = output.name
    if name.endswith(".tar.gz"):
        name = name[:-7]
    if name.endswith("-src"):
        name = name[:-4]
    return name


def parent_archive_dirs(archive_name: str) -> list[str]:
    parts = archive_name.split("/")[:-1]
    result: list[str] = []
    for index in range(1, len(parts) + 1):
        result.append("/".join(parts[:index]))
    return result


def add_directory_entry(tar: tarfile.TarFile, archive_name: str) -> None:
    info = tarfile.TarInfo(archive_name.rstrip("/") + "/")
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    normalize_tarinfo(info)
    tar.addfile(info)


def add_file_entry(tar: tarfile.TarFile, file_path: Path, archive_name: str) -> None:
    info = tar.gettarinfo(str(file_path), arcname=archive_name)
    info.mode = 0o755 if is_executable(file_path) else 0o644
    normalize_tarinfo(info)
    with file_path.open("rb") as stream:
        tar.addfile(info, stream)


def add_bytes_entry(tar: tarfile.TarFile, archive_name: str, content: bytes) -> None:
    import io

    info = tarfile.TarInfo(archive_name)
    info.size = len(content)
    info.mode = 0o644
    normalize_tarinfo(info)
    tar.addfile(info, io.BytesIO(content))


def normalize_tarinfo(info: tarfile.TarInfo) -> None:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}


def is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_members(path: Path) -> list[str]:
    with tarfile.open(path, mode="r:gz") as tar:
        return tar.getnames()


def forbidden_archive_members(members: Sequence[str]) -> list[str]:
    return [
        member
        for member in members
        if member_is_forbidden(member)
    ]


def member_is_forbidden(member: str) -> bool:
    parts = Path(member).parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts):
        return True
    name = parts[-1] if parts else member
    if name in EXCLUDED_FILE_NAMES or name.startswith("._"):
        return True
    if name == "Icon\r":
        return True
    return any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


if __name__ == "__main__":
    raise SystemExit(main())
