#!/usr/bin/env python3
"""Bundle both current source trees, documentation and offline evidence specs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile

PREFIX = "agentmon_kvreuse_complete_20260923"
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv",
                  "outputs", "reports", "dist", "smoke_output_qa177"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pt", ".pth", ".safetensors", ".pdf", ".gz", ".zip"}


def collect(root, selections):
    files = {}
    for item in selections:
        path = root / item
        if not path.exists():
            raise FileNotFoundError(path)
        for file in sorted(path.rglob("*")) if path.is_dir() else [path]:
            relative = file.relative_to(root)
            if (not file.is_file() or file.is_symlink()
                    or any(part in EXCLUDED_PARTS for part in relative.parts)
                    or file.name == ".DS_Store" or file.suffix in EXCLUDED_SUFFIXES):
                continue
            files[relative.as_posix()] = file
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agentmon-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output exists; choose a new filename")
    kvroot = Path(__file__).resolve().parents[1]
    roots = {"kv_ana": kvroot, "agentmon": args.agentmon_root.resolve()}
    selections = {
        "kv_ana": ["src", "scripts", "tests", "configs", "manifests", "references", "docs",
                   "runtime_intervention", "runtime_references", "pyproject.toml", "README.md",
                   "REAL_OFFLINE_PIPELINE.md", "literature_fetch_pack_v3/literature/paper_specs"],
        "agentmon": ["src", "scripts", "tests", "examples", "docs", "offline_ana",
                     "agentmon_implementation_spec", "artifacts/README.md", "artifacts/patches",
                     "pyproject.toml", "README.md"],
    }
    files = {f"{label}/{name}": path for label, root in roots.items()
             for name, path in collect(root, selections[label]).items()}
    generated = {
        "README.md": (kvroot / "docs/AGENTMON_KVREUSE_COMPLETE_GUIDE_ZH.md").read_bytes(),
        "SOURCE_TREE.txt": ("\n".join(sorted(files)) + "\n").encode(),
    }
    manifest = {
        "snapshot": PREFIX, "created_at": datetime.now(timezone.utc).isoformat(),
        "source_roots": {k: str(v) for k, v in roots.items()},
        "source_kind": "current working trees, including uncommitted files; not a Git release",
        "versions": {"kvreuse_framework": "2.0.1", "observability": "1.0.1",
                     "v41_joint_client": "v1", "agentmon_package": "0.1.0"},
        "selections": selections,
        "excluded": ["downloaded PDFs/full third-party repos", "raw tensors/models",
                     "environments/dependency wheels", "outputs/reports", "old package snapshots",
                     "Git metadata/caches/symlinks"],
        "cautions": ["V4 historical patches are NOT V4.1 patches",
                     "Client wire validation is NOT runtime capture or real model validation"],
        "files": [{"path": name, "bytes": path.stat().st_size} for name, path in sorted(files.items())],
        "generated_files": sorted([*generated, "BUILD_MANIFEST.json"]),
    }
    generated["BUILD_MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w:gz") as archive:
        for name, file in sorted(files.items()):
            info = archive.gettarinfo(str(file), arcname=f"{PREFIX}/{name}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.pax_headers = {}
            with file.open("rb") as source:
                archive.addfile(info, source)
        for name, content in generated.items():
            info = tarfile.TarInfo(f"{PREFIX}/{name}")
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    with tarfile.open(args.output) as archive:
        members = archive.getmembers()
        for member in members:
            if member.name.startswith("/") or ".." in Path(member.name).parts or not member.isfile():
                raise ValueError(f"unsafe archive member: {member.name}")
        if len({m.name for m in members}) != len(members):
            raise ValueError("duplicate archive paths")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_name(args.output.name + ".sha256").write_text(f"{digest}  {args.output.name}\n")
    print(json.dumps({"bundle": str(args.output.resolve()), "bytes": args.output.stat().st_size,
                      "files": len(members), "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
