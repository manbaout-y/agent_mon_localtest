#!/usr/bin/env python3
"""Read-only source/metadata collector, runnable via container Python stdin.

No vLLM import, model request, hook, process change or service restart.
Candidate files and lexical calls are evidence for review, not proven call edges.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import importlib.metadata as md
import json
from pathlib import Path
import re
import subprocess

FILES = (
    "v1/metrics/loggers.py", "v1/metrics/stats.py", "v1/core/sched/scheduler.py",
    "v1/core/kv_cache_manager.py", "v1/core/block_pool.py", "v1/core/kv_cache_utils.py",
    "v1/core/kv_cache_coordinator.py", "v1/core/single_type_kv_cache_manager.py",
    "v1/kv_cache_interface.py", "config/cache.py", "config/vllm.py", "engine/arg_utils.py",
    "v1/engine/core.py", "v1/engine/core_client.py", "v1/engine/__init__.py",
    "v1/engine/async_llm.py", "v1/engine/input_processor.py", "v1/request.py",
    "entrypoints/openai/chat_completion/serving.py", "entrypoints/openai/serving_engine.py",
)
WORDS = re.compile(r"prefix_cache|cache_config_info|kv_cache_usage|num_gpu_blocks|kv_cache_size_tokens|"
                   r"kv_cache_max_concurrency|block_size|BlockPool|KVCacheManager|SchedulerStats|"
                   r"get_computed_blocks|allocate_slots|get_new_blocks|free_blocks|cache_full_blocks|"
                   r"evict|ref_cnt|reset_prefix_cache|request_id|trace_headers")


def read_file(path, relative):
    row = {"relative_path": relative, "path": str(path), "status": "MISSING"}
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return dict(row, status="TOO_LARGE")
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, UnicodeError, SyntaxError) as exc:
        return dict(row, status="MISSING" if isinstance(exc, FileNotFoundError) else "SOURCE_ERROR", error_type=type(exc).__name__)
    definitions, calls = [], []

    def walk(node, scope=""):
        for child in ast.iter_child_nodes(node):
            owner = scope
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = scope + "." + child.name if scope else child.name
                definitions.append({"name": owner, "line": child.lineno, "end_line": child.end_lineno,
                                    "signature": ast.unparse(child.args) if hasattr(child, "args") else None})
            if isinstance(child, ast.Call):
                callee = ast.unparse(child.func)
                if WORDS.search(callee):
                    calls.append({"owner": scope, "line": child.lineno, "callee_expression": callee,
                                  "resolution": "LEXICAL_CALL_NOT_RUNTIME_DISPATCH"})
            walk(child, owner)

    walk(tree)
    return dict(row, status="SOURCE_READ", source_text=text, definitions=definitions, lexical_calls=calls,
                relevant_lines=[{"line": n, "text": line} for n, line in enumerate(text.splitlines(), 1) if WORDS.search(line)])


def collect(root, ascend_root=None):
    versions = {}
    for name in ("vllm", "vllm-ascend"):
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            versions[name] = "UNKNOWN"
    files = [read_file(root / f, f) for f in FILES]
    # Find additional config/backend assignment points without assuming candidate paths exist.
    references = []
    for label, folder in (("vllm", root), ("vllm_ascend", ascend_root)):
        if folder is None:
            continue
        for path in sorted(folder.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            matches = [{"line": n, "text": s} for n, s in enumerate(lines, 1) if WORDS.search(s)]
            if matches:
                references.append({"package": label, "path": str(path), "matches": matches})
    git = {}
    for label, folder in (("vllm", root), ("vllm_ascend", ascend_root)):
        if folder is None:
            continue
        try:
            result = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
            git[label] = result.stdout.strip() if result.returncode == 0 else "UNKNOWN"
        except (OSError, subprocess.TimeoutExpired):
            git[label] = "UNKNOWN"
    return {"schema_version": "agentmon-kv-source-snapshot-v1", "metadata_versions": versions,
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_root": str(root), "ascend_root": str(ascend_root) if ascend_root else "NOT_PROVIDED",
            "git_heads": git, "files": files, "references": references,
            "runtime_dispatch": "UNVERIFIED", "loaded_module_paths": "NOT_CAPTURED",
            "model_executed": False, "hook_installed": False}


def render(snapshot, output):
    """Inventory source evidence; never turn lexical expressions into resolved edges."""
    output.mkdir(parents=True, exist_ok=False)
    lines = ["# KV source inventory", "", "Static source evidence only. Runtime dispatch, counter semantics and loaded module paths require review.",
             "", "| Candidate file | Status |", "|---|---|"]
    for file in snapshot["files"]:
        lines.append(f"| {file['relative_path']} | {file['status']} |")
        if file["status"] != "SOURCE_READ":
            continue
        lines += ["", "```text"]
        for item in file["definitions"]:
            lines.append(f"{file['path']}:{item['line']}-{item['end_line']} {item['name']}")
        for item in file["lexical_calls"]:
            lines.append(f"{file['path']}:{item['line']} {item['owner']} -> {item['callee_expression']} [UNRESOLVED dispatch]")
        lines += ["```", ""]
    (output / "source_inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "source_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", type=Path, default=Path("/vllm-workspace/vllm/vllm"))
    p.add_argument("--ascend-root", type=Path)
    p.add_argument("--render-snapshot", type=Path)
    p.add_argument("--output-dir", type=Path, help="must not exist; required with --render-snapshot")
    args = p.parse_args(argv)
    if args.render_snapshot:
        if not args.output_dir:
            p.error("--render-snapshot requires --output-dir")
        render(json.loads(args.render_snapshot.read_text(encoding="utf-8")), args.output_dir)
        return 0
    snapshot = collect(args.source_root, args.ascend_root)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0 if all(f["status"] == "SOURCE_READ" for f in snapshot["files"][:5]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
