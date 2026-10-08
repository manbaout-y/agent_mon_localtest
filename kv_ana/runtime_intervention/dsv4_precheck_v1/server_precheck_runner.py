#!/usr/bin/env python3
"""Single-request DeepSeek-V4 state-intervention precheck runner.

The server process must be restarted with the mode for each intervention case.
Use ``prepare`` before starting E1/E2/E3, then ``run`` after the endpoint is
ready.  E0 runs against the unpatched baseline and needs no preparation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


BASELINE_SHA256 = "99903d816a1967634f9684dd098088fdeb4711c64324739a3a0b8a7b2a4df9f2"
PATCH_MARKER = "# [DSV4_STATE_INTERVENTION_V1] BEGIN"
DEFAULT_TARGET = Path(
    "/data/node0_disk1/liguowei/AK-Infer-Lab/.conda/envs/"
    "ak-infer-lab-vllm-ascend0.22.1rc1/lib/python3.11/site-packages/"
    "vllm_ascend/attention/dsa_v1.py"
)
CASE_MODE = {"E1": "identity", "E2": "zero_selected", "E3": "external"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_rows(value: str) -> list[int]:
    rows = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not rows or len(set(rows)) != len(rows) or min(rows) < 0:
        raise ValueError("--rows must be a non-empty comma-separated list of distinct nonnegative integers")
    return rows


def load_artifact(path: Path) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - server-only path
        raise RuntimeError("preparing an intervention spec from .pt requires server torch") from exc
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if not isinstance(payload, dict) or "compressed_kv" not in payload:
        raise RuntimeError("artifact must be a dict containing compressed_kv")
    return payload


def prepare(args: argparse.Namespace) -> int:
    if args.case not in CASE_MODE:
        raise RuntimeError("prepare supports E1, E2, or E3")
    if not args.artifact or not args.artifact.is_file():
        raise RuntimeError("--artifact must identify the same-input C4/layer-2 capture")
    payload = load_artifact(args.artifact)
    tensor = payload["compressed_kv"]
    shape = [int(item) for item in tensor.shape]
    if len(shape) != 2:
        raise RuntimeError(f"artifact compressed_kv must be 2-D, got {shape}")
    if int(payload.get("layer", -1)) != 2 or int(payload.get("compress_ratio", -1)) != 4:
        raise RuntimeError("v1 requires an artifact captured at layer=2, compress_ratio=4")
    rows = parse_rows(args.rows)
    if max(rows) >= shape[0]:
        raise RuntimeError(f"requested row is outside artifact shape {shape}")

    tag = args.request_tag or f"DSV4_PRECHECK_{args.case}"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.tag_file.parent.mkdir(parents=True, exist_ok=True)
    args.tag_file.write_text(tag + "\n", encoding="utf-8")
    spec = {
        "request_tag": tag,
        "experiment_class": "CAUSAL_PRECHECK",
        "mode": CASE_MODE[args.case],
        "target_layer": 2,
        "compress_ratio": 4,
        "source_artifact": str(args.artifact.resolve()) if args.case == "E3" else None,
        "source_rows": rows if args.case == "E3" else [],
        "target_rows": rows,
        "expected_tensor_shape": shape,
        "expected_slot_mapping": (
            payload["slot_mapping"].detach().cpu().tolist() if "slot_mapping" in payload else None
        ),
        "metadata": {"case": args.case, "same_input_replay": args.case == "E3"},
    }
    args.spec_file.parent.mkdir(parents=True, exist_ok=True)
    args.spec_file.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CASE={args.case}")
    print("SPEC_PREPARED=PASS")
    print(f"DSV4_STATE_INTERVENTION={CASE_MODE[args.case]}")
    print(f"DSV4_STATE_INTERVENTION_SPEC={args.spec_file}")
    print(f"DSV4_STATE_INTERVENTION_TAG_FILE={args.tag_file}")
    print(f"DSV4_STATE_INTERVENTION_TRACE={args.trace}")
    return 0


def request_payload(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    if args.api_style == "chat":
        return {
            "model": args.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": args.max_tokens,
            "temperature": 0,
            "seed": 0,
            "logprobs": True,
        }
    return {
        "model": args.model,
        "prompt": prompt,
        "max_tokens": args.max_tokens,
        "temperature": 0,
        "seed": 0,
        "logprobs": 5,
    }


def send_request(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    body = json.dumps(request_payload(args, prompt)).encode("utf-8")
    request = urllib.request.Request(
        args.endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=args.timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"server returned error: {payload['error']}")
    choice = payload["choices"][0]
    text = choice.get("text")
    if text is None:
        text = choice.get("message", {}).get("content", "")
    logprobs = choice.get("logprobs")
    first_token = None
    if isinstance(logprobs, dict):
        content = logprobs.get("content")
        if isinstance(content, list) and content:
            first_token = content[0].get("token")
        elif isinstance(logprobs.get("tokens"), list) and logprobs["tokens"]:
            first_token = logprobs["tokens"][0]
    token_ids = choice.get("token_ids")
    return {
        "output_text": text,
        "output_token_ids": token_ids,
        "first_token": first_token,
        "logprobs": logprobs,
        "finish_reason": choice.get("finish_reason"),
    }


def trace_records(path: Path, start_offset: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(start_offset)
        return [json.loads(line) for line in handle.read().decode("utf-8").splitlines() if line.strip()]


def canonical_response(value: dict[str, Any]) -> tuple[Any, ...]:
    return (value["output_text"], value.get("output_token_ids"), value.get("first_token"))


def read_baseline(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"E0 baseline result does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))["response"]


def run(args: argparse.Namespace) -> int:
    prompt = args.prompt_file.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.output_dir / f"{args.case}.json"

    if args.case == "E0":
        target_sha = sha256(args.target)
        sha_match = target_sha == BASELINE_SHA256
        args.tag_file.parent.mkdir(parents=True, exist_ok=True)
        args.tag_file.write_text((args.request_tag or "DSV4_PRECHECK_E0") + "\n", encoding="utf-8")
        first = send_request(args, prompt)
        second = send_request(args, prompt)
        repeatable = canonical_response(first) == canonical_response(second)
        result = {
            "case": "E0",
            "prompt_sha256": prompt_hash,
            "response": first,
            "repeat_response": second,
            "server_baseline_sha_match": sha_match,
            "baseline_repeatable": repeatable,
        }
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"SERVER_BASELINE_SHA_MATCH={'PASS' if sha_match else 'FAIL'}")
        print(f"BASELINE_REPEATABLE={'PASS' if repeatable else 'FAIL'}")
        return 0 if sha_match and repeatable else 1

    if args.case not in CASE_MODE:
        raise RuntimeError("run supports E0, E1, E2, or E3")
    target_source = args.target.read_text(encoding="utf-8")
    patch_applied = target_source.count(PATCH_MARKER) == 1
    baseline = read_baseline(args.baseline_result)
    trace_offset = args.trace.stat().st_size if args.trace.exists() else 0
    response = send_request(args, prompt)
    records = trace_records(args.trace, trace_offset)
    matching = [
        record
        for record in records
        if record.get("mode") == CASE_MODE[args.case]
        and record.get("layer") == 2
        and record.get("compress_ratio") == 4
    ]
    applied = [record for record in matching if record.get("applied")]
    errors = [record for record in matching if record.get("error")]
    if len(applied) != 1 or errors:
        raise RuntimeError(
            f"expected exactly one applied layer-2 trace and no errors; applied={len(applied)}, errors={len(errors)}"
        )
    event = applied[0]
    result = {
        "case": args.case,
        "prompt_sha256": prompt_hash,
        "response": response,
        "trace_event": event,
        "patch_applied": patch_applied,
        "output_match": canonical_response(response) == canonical_response(baseline),
        "logprob_match": (
            response.get("logprobs") == baseline.get("logprobs")
            if response.get("logprobs") is not None and baseline.get("logprobs") is not None
            else None
        ),
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PATCH_APPLIED={'PASS' if patch_applied else 'FAIL'}")
    if args.case == "E1":
        delta_zero = abs(float(event["delta_norm"])) <= args.identity_tolerance
        print("IDENTITY_INTERVENTION_APPLIED=PASS")
        print(f"IDENTITY_STATE_DELTA_ZERO={'PASS' if delta_zero else 'FAIL'}")
        print(f"IDENTITY_OUTPUT_MATCH={'PASS' if result['output_match'] else 'FAIL'}")
        return 0 if patch_applied and delta_zero and result["output_match"] else 1
    if args.case == "E2":
        delta_nonzero = float(event["delta_norm"]) > 0
        print("ZERO_SELECTED_APPLIED=PASS")
        print(f"ZERO_SELECTED_STATE_DELTA_NONZERO={'PASS' if delta_nonzero else 'FAIL'}")
        return 0 if patch_applied and delta_nonzero else 1

    shape_match = event.get("tensor_shape") is not None and not event.get("error")
    slot_match = event.get("slot_mapping_match") is True
    print("EXTERNAL_REPLAY_APPLIED=PASS")
    print(f"EXTERNAL_REPLAY_SHAPE_MATCH={'PASS' if shape_match else 'FAIL'}")
    print(f"EXTERNAL_REPLAY_SLOT_MAPPING_MATCH={'PASS' if slot_match else 'FAIL'}")
    print(f"EXTERNAL_REPLAY_OUTPUT_MATCH={'PASS' if result['output_match'] else 'FAIL'}")
    if result["logprob_match"] is not None:
        print(f"EXTERNAL_REPLAY_LOGPROB_MATCH={'PASS' if result['logprob_match'] else 'FAIL'}")
    return 0 if patch_applied and shape_match and slot_match and result["output_match"] else 1


def summarize(args: argparse.Namespace) -> int:
    required = [args.output_dir / f"E{index}.json" for index in range(4)]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"cannot summarize; missing case results: {missing}")
    results = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in required}
    e0, e1, e2, e3 = (results[f"E{index}"] for index in range(4))
    checks = {
        "SERVER_BASELINE_SHA_MATCH": e0["server_baseline_sha_match"],
        "PATCH_APPLIED": all(results[name].get("patch_applied", True) for name in ("E1", "E2", "E3")),
        "BASELINE_REPEATABLE": e0["baseline_repeatable"],
        "IDENTITY_INTERVENTION_APPLIED": e1["trace_event"]["applied"],
        "IDENTITY_STATE_DELTA_ZERO": abs(float(e1["trace_event"]["delta_norm"])) <= args.identity_tolerance,
        "IDENTITY_OUTPUT_MATCH": e1["output_match"],
        "ZERO_SELECTED_APPLIED": e2["trace_event"]["applied"],
        "ZERO_SELECTED_STATE_DELTA_NONZERO": float(e2["trace_event"]["delta_norm"]) > 0,
        "EXTERNAL_REPLAY_APPLIED": e3["trace_event"]["applied"],
        "EXTERNAL_REPLAY_SHAPE_MATCH": not e3["trace_event"].get("error"),
        "EXTERNAL_REPLAY_SLOT_MAPPING_MATCH": e3["trace_event"].get("slot_mapping_match") is True,
        "EXTERNAL_REPLAY_OUTPUT_MATCH": e3["output_match"],
    }
    for name, passed in checks.items():
        print(f"{name}={'PASS' if passed else 'FAIL'}")
    if e3.get("logprob_match") is not None:
        print(f"EXTERNAL_REPLAY_LOGPROB_MATCH={'PASS' if e3['logprob_match'] else 'FAIL'}")
    return 0 if all(checks.values()) else 1


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("action", choices=("prepare", "run", "summarize"))
    value.add_argument("--case", choices=("E0", "E1", "E2", "E3"), default="E0")
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--artifact", type=Path)
    value.add_argument("--rows", default="1,2,3,4")
    value.add_argument("--request-tag")
    value.add_argument("--spec-file", type=Path)
    value.add_argument("--tag-file", type=Path)
    value.add_argument("--trace", type=Path)
    value.add_argument("--prompt-file", type=Path)
    value.add_argument("--baseline-result", type=Path)
    value.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    value.add_argument("--endpoint", default="http://127.0.0.1:8000/v1/chat/completions")
    value.add_argument("--api-style", choices=("chat", "completion"), default="chat")
    value.add_argument("--model", default="/data/node0_disk1/Public/DeepSeek-V4-Flash-w8a8-mtp")
    value.add_argument("--max-tokens", type=int, default=32)
    value.add_argument("--timeout", type=float, default=300.0)
    value.add_argument("--identity-tolerance", type=float, default=0.0)
    return value


def main() -> int:
    args = parser().parse_args()
    args.spec_file = args.spec_file or args.output_dir / "active_spec.json"
    args.tag_file = args.tag_file or args.output_dir / "active_tag.txt"
    args.trace = args.trace or args.output_dir / "intervention_trace.jsonl"
    args.baseline_result = args.baseline_result or args.output_dir / "E0.json"
    try:
        if args.action == "prepare":
            return prepare(args)
        if args.action == "summarize":
            return summarize(args)
        if args.prompt_file is None or not args.prompt_file.is_file():
            raise RuntimeError("run requires --prompt-file")
        return run(args)
    except Exception as exc:
        print(f"PRECHECK_RUNNER=FAIL", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
