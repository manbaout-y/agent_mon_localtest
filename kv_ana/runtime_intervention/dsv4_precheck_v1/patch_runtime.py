#!/usr/bin/env python3
"""Install or restore the guarded DeepSeek-V4 intervention hook."""

from __future__ import annotations

import argparse
import hashlib
import py_compile
import shutil
import sys
from pathlib import Path


BASELINE_SHA256 = "99903d816a1967634f9684dd098088fdeb4711c64324739a3a0b8a7b2a4df9f2"
DUMP_ANCHOR = "# [DSV4_KV_DUMP] LifeMem compressed-KV experiment."
CONSUMER_GUARD = "            if compressed_kv.numel() == 0:"
PATCH_BEGIN = "# [DSV4_STATE_INTERVENTION_V1] BEGIN"
DEFAULT_TARGET = Path(
    "/data/node0_disk1/liguowei/AK-Infer-Lab/.conda/envs/"
    "ak-infer-lab-vllm-ascend0.22.1rc1/lib/python3.11/site-packages/"
    "vllm_ascend/attention/dsa_v1.py"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paths(target: Path) -> tuple[Path, Path]:
    return (
        target.with_name(target.name + ".dsv4_precheck_v1.backup"),
        target.with_name("dsv4_state_intervention_v1.py"),
    )


def apply_patch(target: Path, bundle_dir: Path) -> None:
    if not target.is_file():
        raise RuntimeError(f"target file does not exist: {target}")
    actual_sha = sha256(target)
    if actual_sha != BASELINE_SHA256:
        raise RuntimeError(
            f"FAIL CLOSED: target SHA mismatch; expected={BASELINE_SHA256}, actual={actual_sha}"
        )

    source = target.read_text(encoding="utf-8")
    if source.count(DUMP_ANCHOR) != 1:
        raise RuntimeError("FAIL CLOSED: expected exactly one DSV4_KV_DUMP anchor")
    if PATCH_BEGIN in source:
        raise RuntimeError("FAIL CLOSED: intervention marker already exists")
    anchor_offset = source.index(DUMP_ANCHOR)
    guard_offset = source.find(CONSUMER_GUARD, anchor_offset)
    if guard_offset < 0:
        raise RuntimeError("FAIL CLOSED: compressed_kv numel consumer guard not found after capture anchor")
    if source.find(DUMP_ANCHOR, anchor_offset + 1, guard_offset) >= 0:
        raise RuntimeError("FAIL CLOSED: capture anchor is not unique before consumer guard")

    fragment_path = bundle_dir / "patch_block.pyfrag"
    helper_source = bundle_dir / "dsv4_state_intervention_v1.py"
    if not fragment_path.is_file() or not helper_source.is_file():
        raise RuntimeError("bundle is missing patch_block.pyfrag or intervention helper")
    fragment = fragment_path.read_text(encoding="utf-8")
    patched_source = source[:guard_offset] + fragment + source[guard_offset:]

    backup, helper_target = paths(target)
    if backup.exists() and sha256(backup) != BASELINE_SHA256:
        raise RuntimeError(f"FAIL CLOSED: existing backup has unexpected SHA: {backup}")
    if not backup.exists():
        shutil.copy2(target, backup)
    if helper_target.exists() and helper_target.read_bytes() != helper_source.read_bytes():
        raise RuntimeError(f"FAIL CLOSED: refusing to overwrite unrelated helper: {helper_target}")

    temporary = target.with_name(target.name + ".dsv4_precheck_v1.tmp")
    try:
        temporary.write_text(patched_source, encoding="utf-8")
        shutil.copymode(target, temporary)
        shutil.copy2(helper_source, helper_target)
        py_compile.compile(str(temporary), doraise=True)
        py_compile.compile(str(helper_target), doraise=True)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        if sha256(target) != BASELINE_SHA256:
            shutil.copy2(backup, target)
        if helper_target.exists() and helper_target.read_bytes() == helper_source.read_bytes():
            helper_target.unlink()
        raise

    print(f"SERVER_BASELINE_SHA_MATCH=PASS")
    print(f"PATCH_APPLIED=PASS")
    print(f"TARGET={target}")
    print(f"BACKUP={backup}")
    print(f"PATCHED_SHA256={sha256(target)}")


def restore_patch(target: Path, bundle_dir: Path) -> None:
    backup, helper_target = paths(target)
    if sha256(target) == BASELINE_SHA256:
        print("RESTORE=PASS")
        print(f"RESTORED_SHA256={BASELINE_SHA256}")
        return
    if not backup.is_file():
        raise RuntimeError(f"FAIL CLOSED: backup not found: {backup}")
    if sha256(backup) != BASELINE_SHA256:
        raise RuntimeError("FAIL CLOSED: backup SHA does not match the server baseline")
    source = target.read_text(encoding="utf-8")
    if source.count(PATCH_BEGIN) != 1:
        raise RuntimeError("FAIL CLOSED: installed target is not the expected patched file")

    shutil.copy2(backup, target)
    if sha256(target) != BASELINE_SHA256:
        raise RuntimeError("restore copy completed but baseline SHA verification failed")

    helper_source = bundle_dir / "dsv4_state_intervention_v1.py"
    if helper_target.exists():
        if helper_target.read_bytes() != helper_source.read_bytes():
            raise RuntimeError(f"restored dsa_v1.py but preserved unrecognized helper: {helper_target}")
        helper_target.unlink()
    py_compile.compile(str(target), doraise=True)
    print("RESTORE=PASS")
    print(f"RESTORED_SHA256={sha256(target)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("apply", "restore"))
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()
    bundle_dir = Path(__file__).resolve().parent
    try:
        if args.operation == "apply":
            apply_patch(args.target, bundle_dir)
        else:
            restore_patch(args.target, bundle_dir)
    except Exception as exc:
        print(f"{args.operation.upper()}=FAIL", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

