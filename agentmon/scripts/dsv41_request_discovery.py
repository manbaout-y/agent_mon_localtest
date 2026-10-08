#!/usr/bin/env python3
"""Read-only v0.27.1 source discovery; usable via container Python stdin.

Default: standard-library AST scan, no vLLM import. --runtime-introspection
explicitly imports existing modules, but creates no engine, hook, or model.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stderr, redirect_stdout
import importlib
import importlib.metadata as md
import inspect
from io import StringIO
import json
import os
from pathlib import Path
import re
import sys

TARGET_RELEASE = "0.27.1"
TARGETS = {
    "entrypoints/openai/chat_completion/serving.py": ("OpenAIServingChat", ("create_chat_completion",)),
    "entrypoints/openai/serving_engine.py": ("OpenAIServing", ("_base_request_id",)),
    "v1/engine/async_llm.py": ("AsyncLLM", ("generate", "add_request", "abort")),
    "v1/engine/input_processor.py": ("InputProcessor", ("process_inputs", "assign_request_id")),
    "v1/engine/core_client.py": (None, ("add_request", "add_request_async")),
    "v1/engine/core.py": ("EngineCore", ("add_request",)),
    "v1/core/sched/scheduler.py": ("Scheduler", ("add_request", "schedule", "update_from_output")),
    "v1/core/kv_cache_manager.py": ("KVCacheManager", ("get_computed_blocks", "allocate_slots", "free")),
    "v1/request.py": ("Request", ("__init__",)),
}
ID_WORDS = ("request_id", "external_req_id", "trace_headers", "X-AgentMon", "x-agentmon", "prompt_token_ids")


def release(value):
    match = re.match(r"^(\d+\.\d+\.\d+)(?:\+[^\s]+)?$", str(value or ""))
    return match.group(1) if match else None


def compatible_runtime(module_version, metadata_version, loaded_file, source_root):
    return (release(module_version) == TARGET_RELEASE and release(metadata_version) == TARGET_RELEASE
            and Path(loaded_file).resolve().parent == Path(source_root).resolve())


def scan_source(root: Path):
    objects, files = [], []
    for relative, (target_class, names) in TARGETS.items():
        path = root / relative
        row = {"relative_path": relative, "path": str(path), "status": "MISSING", "identity_lines": []}
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, UnicodeError, SyntaxError) as exc:
            row["error_type"] = type(exc).__name__
            files.append(row)
            continue
        row["status"] = "SOURCE_READ"
        row["identity_lines"] = [{"line": n, "source": line.strip()[:500]} for n, line in enumerate(text.splitlines(), 1)
                                 if any(word in line for word in ID_WORDS)]
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        for cls in classes:
            if target_class and cls.name != target_class:
                continue
            for node in cls.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                    objects.append({"module": "vllm." + relative[:-3].replace("/", "."),
                                    "class": cls.name, "method": node.name, "file": str(path), "line": node.lineno,
                                    "static_signature": "(" + ast.unparse(node.args) + ")",
                                    "signature_source": "AST_NOT_LOADED_RUNTIME", "async": isinstance(node, ast.AsyncFunctionDef)})
        files.append(row)
    return files, objects


def collect(root: Path, *, runtime_introspection=False):
    files, objects = scan_source(root)
    distributions = {}
    for name in ("vllm", "vllm-ascend"):
        try:
            distributions[name] = md.version(name)
        except md.PackageNotFoundError:
            distributions[name] = None
    snapshot = {"schema_version": "agentmon-dsv41-discovery-v1", "python": sys.executable,
                "source_root": str(root.resolve()), "distributions": distributions,
                "source_files": files, "objects": objects, "runtime_introspection": "NOT_RUN",
                "compatibility": "UNVERIFIED_NO_ADAPTER_INSTALLED", "model_executed": False, "hook_installed": False}
    if not runtime_introspection:
        return snapshot
    # Fail closed for inspection eligibility; this never installs an adapter.
    runtime_rows = []
    snapshot["runtime_introspection"] = "ATTEMPTED"
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        try:
            vllm = importlib.import_module("vllm")
            ascend = importlib.import_module("vllm_ascend")
            loaded = {"vllm_version": getattr(vllm, "__version__", None), "vllm_file": getattr(vllm, "__file__", None),
                      "ascend_file": getattr(ascend, "__file__", None), "ascend_version": getattr(ascend, "__version__", None)}
            snapshot["loaded_runtime"] = loaded
            matches = bool(loaded["vllm_file"] and compatible_runtime(
                loaded["vllm_version"], distributions["vllm"], loaded["vllm_file"], root))
            snapshot["compatibility"] = "VERSION_PATH_MATCH_NO_ADAPTER_INSTALLED" if matches else "BLOCKED_VERSION_OR_PATH"
            if not matches:
                return snapshot
            for item in objects:
                row = {k: item[k] for k in ("module", "class", "method")}
                try:
                    module = importlib.import_module(item["module"])
                    obj = getattr(getattr(module, item["class"]), item["method"])
                    row.update(status="INSPECTED", file=inspect.getsourcefile(obj), line=inspect.getsourcelines(obj)[1],
                               signature=str(inspect.signature(obj)))
                    if Path(row["file"]).resolve() != Path(item["file"]).resolve():
                        row["status"] = "BLOCKED_PATH_MISMATCH"
                except Exception as exc:
                    row.update(status="INSPECTION_ERROR", error_type=type(exc).__name__)
                runtime_rows.append(row)
        except Exception as exc:
            snapshot["compatibility"] = "BLOCKED_IMPORT"
            snapshot["import_error_type"] = type(exc).__name__
    snapshot["runtime_objects"] = runtime_rows
    # Missing static targets also block runtime completeness; successful imports are not a hook validation.
    required = {("OpenAIServingChat", "create_chat_completion"), ("AsyncLLM", "add_request"),
                ("EngineCore", "add_request"), ("Scheduler", "add_request"), ("InputProcessor", "process_inputs")}
    inspected = {(r["class"], r["method"]) for r in runtime_rows if r["status"] == "INSPECTED"}
    ipc_observed = any(r["status"] == "INSPECTED" and r["module"] == "vllm.v1.engine.core_client" for r in runtime_rows)
    snapshot["runtime_introspection"] = "SIGNATURES_OBSERVED_REVIEW_REQUIRED" if required <= inspected and ipc_observed else "PARTIAL_OR_BLOCKED"
    return snapshot


def render(snapshot, output: Path):
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be new/empty")
    output.mkdir(parents=True, exist_ok=True)
    text = ["# REQUEST_CALL_CHAIN — target evidence", "", "This is source/signature evidence; ID propagation requires review and live events.",
            f"Python: `{snapshot['python']}`", f"Source root: `{snapshot['source_root']}`",
            f"Runtime introspection: `{snapshot['runtime_introspection']}`", f"Compatibility: `{snapshot['compatibility']}`", "",
            "## Actual discovered methods", "", "| Class.method | Source | AST signature |", "|---|---|---|"]
    for o in snapshot["objects"]:
        text.append(f"| {o['class']}.{o['method']} | {o['file']}:{o['line']} | `{o['static_signature'].replace('|', '/')} ` |")
    text += ["", "## Loaded runtime signatures", "", "```json", json.dumps(snapshot.get("runtime_objects", []), indent=2), "```",
             "", "## Identity source lines (actual source, not request data)"]
    for f in snapshot["source_files"]:
        text += ["", f"### {f['relative_path']} — {f['status']}", "", "```text"]
        text += [f"{r['line']}: {r['source']}" for r in f["identity_lines"]]
        text.append("```")
    text += ["", "## Required review (not automatically PASS)", "",
             "- API response ID creation and HTTP header extraction.",
             "- AsyncLLM/InputProcessor external/internal ID rewrite, fan-out and retry.",
             "- CoreClient serialization/IPC and context fields preserved into EngineCore.",
             "- EngineCore request object passed to Scheduler and native scheduler ID.",
             "- Separate API/EngineCore process hook installation and cancellation paths.",
             "", "REAL_ACCEPTANCE_PENDING; no hook deployment was performed."]
    for name, content in (("REQUEST_CALL_CHAIN.md", "\n".join(text) + "\n"),
                          ("v41-request-discovery.txt", json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")):
        path = output / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
    output.chmod(0o700)


def main(argv=None):
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", type=Path, default=Path("/vllm-workspace/vllm/vllm"))
    p.add_argument("--runtime-introspection", action="store_true")
    p.add_argument("--render-snapshot", type=Path, help="render previously collected JSON on host, without importing vLLM")
    p.add_argument("--output-dir", type=Path, help="required with --render-snapshot; new/empty directory")
    args = p.parse_args(argv)
    if args.render_snapshot:
        if not args.output_dir:
            p.error("--render-snapshot needs --output-dir")
        render(json.loads(args.render_snapshot.read_text(encoding="utf-8")), args.output_dir)
        return 0
    snapshot = collect(args.source_root, runtime_introspection=args.runtime_introspection)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0 if (not args.runtime_introspection and any(f["status"] == "SOURCE_READ" for f in snapshot["source_files"])) or snapshot["runtime_introspection"] == "SIGNATURES_OBSERVED_REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
