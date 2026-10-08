#!/usr/bin/env python3
"""Portable CPU tools bundle; includes one read-only runtime source used by evidence tests."""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import tarfile
from datetime import datetime, timezone


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/kvreuse_state_research_offline_v1.0.1.tar.gz")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = [p for p in (root / "src/kvreuse_b5").rglob("*")
             if p.is_file() and p.suffix in {".py", ".yaml", ".json"} and "__pycache__" not in p.parts]
    files += [root / "pyproject.toml", root / "README.md", root / "configs/state_research_offline.example.json",
              root / "configs/synthetic_smoke.yaml"]
    files += [root / "scripts" / name for name in (
        "prepare_state_research.py", "state_research_smoke.py", "run_state_research_offline.py",
        "state_research_offline_smoke.py", "build_state_research_bundle.py")]
    files += [root / "docs" / name for name in (
        "HETEROGENEOUS_STATE_BATCH1_PLAN.md", "LIFEMEM_STATE_RESEARCH_V1.md",
        "STATE_RESEARCH_OFFLINE_PHASES_2_3.md", "STATE_RESEARCH_OFFLINE_VALIDATION.md",
        "STATE_RESEARCH_AUDIT_V1_0_1.md")]
    files += sorted((root / "tests").glob("test_state_research*.py"))
    # This source-evidence test must remain runnable after extraction. Never import/patch this reference.
    files += [root / "runtime_references/dsv4_runtime_official_reference_v0221/official/vllm-ascend/vllm_ascend/attention/dsa_v1.py"]
    prefix = "kvreuse_state_research_offline_v1.0.1"
    manifest = "\n".join(["state-research-offline-v1.0.1", f"created_utc={datetime.now(timezone.utc).isoformat()}",
                          "CPU offline tools. Real acceptance PENDING. No model/runtime execution.",
                          "Dependencies: Python>=3.9, NumPy; pytest only for tests.",
                          "Included files:"] + sorted(str(p.relative_to(root)) for p in files)) + "\n"
    with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(files):
            archive.add(path, arcname=f"{prefix}/{path.relative_to(root)}", recursive=False)
        raw = manifest.encode("utf-8")
        info = tarfile.TarInfo(f"{prefix}/BUNDLE_MANIFEST.txt")
        info.size = len(raw)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(raw))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_name(destination.name + ".sha256").write_text(f"{digest}  {destination.name}\n")
    print(f"BUNDLE={destination}\nBYTES={destination.stat().st_size}\nSHA256={digest}")


if __name__ == "__main__":
    main()
