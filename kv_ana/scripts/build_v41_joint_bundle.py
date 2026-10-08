#!/usr/bin/env python3
"""Package independent source trees; no dependencies, weights or raw data."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agentmon-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    kvroot = Path(__file__).resolve().parents[1]
    files = {}
    selections = {
        "agentmon": (args.agentmon_root, ["src", "examples", "scripts", "tests", "pyproject.toml",
                                         "README.md",
                                         "docs/DEEPSEEK_V41_JOINT_CLIENT.md"]),
        "kv_ana": (kvroot, ["src", "tests/test_request_state_bridge.py",
                             "scripts/validate_v41_joint_client.py", "scripts/state_research_offline_smoke.py",
                             "docs/AGENTMON_V41_JOINT_ACCEPTANCE.md", "docs/V41_JOINT_VALIDATION.md"]),
    }
    for label, (root, items) in selections.items():
        for item in items:
            path = root / item
            if not path.exists():
                raise FileNotFoundError(path)
            for f in sorted(path.rglob("*")) if path.is_dir() else [path]:
                if not f.is_file() or f.is_symlink() or any(x in f.parts for x in ("__pycache__", ".pytest_cache")):
                    continue
                if f.name == ".DS_Store" or f.suffix == ".pyc":
                    continue
                files[f"{label}/{f.relative_to(root).as_posix()}"] = f
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        p.error("bundle already exists; choose a new output path")
    prefix = "agentmon_kvreuse_v41_joint_v1"
    manifest = {"version": "v41-joint-client-v1", "files": sorted(files),
                "runtime_capture": "NOT_IMPLEMENTED", "p2_v41": "NOT_VALIDATED",
                "dependencies_vendored": False, "source_roots": {k: str(v[0]) for k, v in selections.items()}}
    with tarfile.open(args.output, "w:gz") as archive:
        for name, file in files.items():
            archive.add(file, arcname=f"{prefix}/{name}", recursive=False)
        content = (json.dumps(manifest, indent=2) + "\n").encode()
        info = tarfile.TarInfo(f"{prefix}/MANIFEST.json"); info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    with tarfile.open(args.output) as archive:
        for member in archive.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts or not member.isfile():
                raise ValueError("unexpected bundle member")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_name(args.output.name + ".sha256").write_text(f"{digest}  {args.output.name}\n")
    print(f"BUNDLE={args.output.resolve()}\nBYTES={args.output.stat().st_size}\nSHA256={digest}")


if __name__ == "__main__":
    raise SystemExit(main())
