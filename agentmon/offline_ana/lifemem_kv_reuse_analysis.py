#!/usr/bin/env python3
"""Offline exact-content reuse analysis for LifeMem retrieval results.

This script intentionally has no AgentMon or LifeMem runtime dependencies.  It
uses ``results[]`` as the retrieved-memory inventory and ``formatted_context``
as the source of truth for which chunk bodies actually entered the context.
All positions are zero-based Python character offsets with an exclusive end.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


# =============================================================================
# 直接运行配置：通常只需要修改下面两个路径，然后在 IDE 中运行本文件。
# INPUT_PATH 必须指向合法的 JSON/JSONL 文件；OUTPUT_PATH 是输出目录。
# LIMIT = 1 表示只分析第一条 QA；确认无误后改成 None 可分析全部记录。
# =============================================================================
USE_COMMAND_LINE_ARGUMENTS = False
INPUT_PATH = "/path/to/your/lifemem_results.json"
OUTPUT_PATH = str(Path(__file__).resolve().parent / "analysis_out")
LIMIT: int | None = 1
VERBOSE = True
MAX_PAIR_DETAIL_ROWS = 20_000


PERIOD_SUMMARY_HEADER_RE = re.compile(
    r"^\[周期摘要[ \t]+(period_\d{4}-m\d{2})\][ \t]*$",
    re.MULTILINE,
)
CONTEXT_SEPARATOR_RE = re.compile(r"^[ \t]*---[ \t]*$", re.MULTILINE)
PREVIEW_CHARS = 160

RECORD_FIELDS = (
    "record_index",
    "question_id",
    "conversation_id",
    "query",
    "is_multi_round",
    "round1_count",
    "round2_count",
    "final_count",
    "result_count",
    "valid_result_count",
    "formatted_context_chars",
    "unique_retrieved_chunks",
    "located_retrieved_chunks",
    "duplicate_within_context_count",
    "warning_count",
)

CHUNK_FIELDS = (
    "record_index",
    "question_id",
    "conversation_id",
    "result_rank",
    "chunk_type",
    "subject",
    "logical_memory_key",
    "logical_memory_identity_basis",
    "score",
    "content_hash",
    "content_chars",
    "content_preview",
    "located_in_context",
    "context_occurrence_count",
    "context_char_start",
    "context_char_end",
    "canonical_context_source",
    "all_context_positions",
    "context_occurrence_sources",
    "preceding_chunk_count",
    "preceding_chunk_hashes",
    "classifications",
)

REUSE_GROUP_FIELDS = (
    "content_hash",
    "chunk_type",
    "occurrence_count",
    "located_occurrence_count",
    "question_count",
    "conversation_count",
    "subjects",
    "chunk_types",
    "result_ranks",
    "scores",
    "context_positions",
    "preceding_chunk_sequences",
    "different_position",
    "different_preceding_memory_context",
    "cross_context_non_prefix_candidate",
    "potential_reusable_chars",
    "min_context_position",
    "max_context_position",
    "position_span_chars",
    "classifications",
)

INTRA_CONTEXT_FIELDS = (
    "record_index",
    "question_id",
    "conversation_id",
    "content_hash",
    "chunk_type",
    "subject",
    "occurrence_count",
    "positions",
    "occurrence_sources",
    "preceding_chunk_sequences",
    "different_position",
    "different_preceding_memory_context",
    "same_body_different_wrapper",
    "redundant_context_candidate",
    "potential_redundant_chars",
    "classifications",
)

CONTEXT_VARIATION_FIELDS = (
    "logical_memory_key",
    "identity_basis",
    "conversation_id",
    "subject",
    "chunk_type",
    "context_occurrence_count",
    "unlocated_occurrence_count",
    "distinct_question_count",
    "distinct_content_versions",
    "distinct_prefix_sequences",
    "distinct_positions",
    "comparable_context_pair_count",
    "content_changed_pairs",
    "content_changed_pair_ratio",
    "same_content_prefix_or_position_changed_pairs",
    "same_content_prefix_or_position_changed_pair_ratio",
    "same_content_prefix_changed_only_pairs",
    "same_content_position_changed_only_pairs",
    "same_content_prefix_and_position_changed_pairs",
    "all_same_pairs",
    "all_same_pair_ratio",
)

CONTEXT_PAIR_FIELDS = (
    "logical_memory_key",
    "identity_basis",
    "conversation_id",
    "subject",
    "classification",
    "record_index_a",
    "question_id_a",
    "result_rank_a",
    "content_hash_a",
    "content_preview_a",
    "context_position_a",
    "preceding_chunk_hashes_a",
    "record_index_b",
    "question_id_b",
    "result_rank_b",
    "content_hash_b",
    "content_preview_b",
    "context_position_b",
    "preceding_chunk_hashes_b",
    "content_same",
    "prefix_same",
    "position_same",
)

OUTPUT_FILES = {
    "records": "records.csv",
    "chunks": "chunks.csv",
    "reuse_groups": "reuse_groups.csv",
    "intra_context_duplicates": "intra_context_duplicates.csv",
    "context_variation_groups": "context_variation_groups.csv",
    "context_pair_comparisons": "context_pair_comparisons.csv",
    "context_variation_report": "context_variation_report.md",
    "summary": "summary.json",
}


def normalize_content(text: str) -> str:
    """Apply the deliberately conservative normalization used for hashing."""

    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def content_hash(text: str) -> str:
    """Return SHA-256 for already-normalized content."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_chunk_type(subject: Any) -> str:
    """Classify only the summary types that are reliable in the input schema."""

    if not isinstance(subject, str) or not subject.strip():
        return "other"
    value = subject.strip()
    if value.startswith("[月摘要]"):
        return "monthly_summary"
    if value.startswith("[周摘要]"):
        return "weekly_summary"
    return "episode_or_event"


def _normalize_with_boundaries(text: str) -> tuple[str, list[int]]:
    """Normalize newlines and retain normalized-boundary to raw-index mapping."""

    characters: list[str] = []
    boundaries = [0]
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            characters.append("\n")
            index += 2
        elif char == "\r":
            characters.append("\n")
            index += 1
        else:
            characters.append(char)
            index += 1
        boundaries.append(index)
    return "".join(characters), boundaries


def find_all_occurrences(haystack: str, content: str) -> list[tuple[int, int]]:
    """Find all non-overlapping normalized-content matches in raw context.

    Returned offsets refer to the original ``haystack`` rather than to a
    newline-normalized copy.
    """

    needle = normalize_content(content)
    if not needle:
        return []
    normalized_haystack, boundaries = _normalize_with_boundaries(haystack)
    positions: list[tuple[int, int]] = []
    search_from = 0
    while True:
        start = normalized_haystack.find(needle, search_from)
        if start < 0:
            break
        end = start + len(needle)
        positions.append((boundaries[start], boundaries[end]))
        search_from = end
    return positions


def extract_period_summaries(formatted_context: str) -> list[dict[str, Any]]:
    """Extract only explicitly wrapped period-summary bodies.

    The extractor deliberately stops at the next standalone ``---`` line and
    does not attempt to parse the remainder of the formatted context.
    """

    summaries: list[dict[str, Any]] = []
    for header in PERIOD_SUMMARY_HEADER_RE.finditer(formatted_context):
        body_start = header.end()
        if formatted_context.startswith("\r\n", body_start):
            body_start += 2
        elif body_start < len(formatted_context) and formatted_context[body_start] in "\r\n":
            body_start += 1

        separator = CONTEXT_SEPARATOR_RE.search(formatted_context, body_start)
        raw_end = separator.start() if separator else len(formatted_context)
        raw_body = formatted_context[body_start:raw_end]
        body = normalize_content(raw_body)
        if body:
            leading = len(raw_body) - len(raw_body.lstrip())
            trailing = len(raw_body) - len(raw_body.rstrip())
            actual_start = body_start + leading
            actual_end = raw_end - trailing if trailing else raw_end
            summaries.append(
                {
                    "period_id": header.group(1),
                    "header_start": header.start(),
                    "header_end": header.end(),
                    "body_start": actual_start,
                    "body_end": actual_end,
                    "normalized_body": body,
                    "body_hash": content_hash(body),
                    "terminator_found": separator is not None,
                }
            )
    return summaries


def _looks_like_record(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    identity_keys = {"question_id", "query", "conversation_id", "retrieval_metadata"}
    if identity_keys.intersection(value):
        return True
    results = value.get("results")
    if not isinstance(results, list):
        return False
    if not results:
        return True
    return all(
        isinstance(item, dict)
        and bool({"content", "score", "metadata"}.intersection(item))
        and not _looks_like_record(item)
        for item in results
    )


def _validate_record_list(value: Any, *, source: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{source} must contain a JSON list")
    invalid = [index for index, item in enumerate(value) if not isinstance(item, dict)]
    if invalid:
        preview = ", ".join(str(index) for index in invalid[:5])
        raise ValueError(f"{source} contains non-object record(s) at index: {preview}")
    return value


def records_from_json_value(value: Any) -> tuple[list[dict[str, Any]], str]:
    """Resolve a parsed JSON value into QA records without guessing broadly."""

    if isinstance(value, list):
        return _validate_record_list(value, source="top-level JSON array"), "json_array"
    if not isinstance(value, dict):
        raise ValueError("top-level JSON must be an object or array")
    if _looks_like_record(value):
        return [value], "json_object"

    preferred_keys = ("records", "data", "items", "questions", "qa_records", "results")
    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    for key in preferred_keys:
        items = value.get(key)
        if not isinstance(items, list):
            continue
        if not items or all(_looks_like_record(item) for item in items):
            candidates.append((key, _validate_record_list(items, source=f"field {key!r}")))

    if not candidates:
        for key, items in value.items():
            if isinstance(items, list) and items and all(_looks_like_record(item) for item in items):
                candidates.append((key, _validate_record_list(items, source=f"field {key!r}")))

    if len(candidates) == 1:
        key, records = candidates[0]
        return records, f"json_object_field:{key}"
    if len(candidates) > 1:
        keys = ", ".join(key for key, _ in candidates)
        raise ValueError(f"multiple object fields look like QA record lists: {keys}")
    raise ValueError("JSON object is neither a QA record nor a supported record-list container")


def parse_records_text(text: str, *, source: str = "input") -> tuple[list[dict[str, Any]], str]:
    """Parse valid JSON array/object text or one-object-per-line JSONL."""

    if not text.strip():
        raise ValueError(f"input is empty: {source}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as json_error:
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as line_error:
                raise ValueError(
                    f"input is neither valid JSON nor JSONL; line {line_number}: {line_error.msg}"
                ) from json_error
            if not isinstance(item, dict):
                raise ValueError(f"JSONL line {line_number} must be an object")
            records.append(item)
        if not records:
            raise ValueError("JSONL input contains no object records")
        return records, "jsonl"
    return records_from_json_value(value)


def load_records(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Load valid JSON array/object input or one-object-per-line JSONL."""

    if not path.is_file():
        raise FileNotFoundError(f"input file does not exist: {path}")
    return parse_records_text(path.read_text(encoding="utf-8-sig"), source=str(path))


def _warning(
    warnings: list[dict[str, Any]],
    *,
    record_index: int,
    question_id: Any,
    code: str,
    message: str,
) -> None:
    warnings.append(
        {
            "record_index": record_index,
            "question_id": question_id if isinstance(question_id, str) else None,
            "code": code,
            "message": message,
        }
    )


def _optional_scalar(
    value: Any,
    *,
    field: str,
    warnings: list[dict[str, Any]],
    record_index: int,
    question_id: Any,
) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    _warning(
        warnings,
        record_index=record_index,
        question_id=question_id,
        code="INVALID_SCALAR_FIELD",
        message=f"{field} must be a scalar or null; wrote null",
    )
    return None


def _is_retrieved_wrapper(formatted_context: str, start: int, subject: Any) -> bool:
    if not isinstance(subject, str) or not subject:
        return False
    lookback = formatted_context[max(0, start - len(subject) - 8) : start]
    return any(
        lookback.endswith(subject + suffix)
        for suffix in (": ", ":\n", ":\r\n", "：", "： ")
    )


def _occurrence_details(
    formatted_context: str,
    content: str,
    subject: Any,
    period_summaries: list[dict[str, Any]],
    digest: str,
) -> list[dict[str, Any]]:
    period_spans = {
        (summary["body_start"], summary["body_end"])
        for summary in period_summaries
        if summary["body_hash"] == digest
    }
    details: list[dict[str, Any]] = []
    for start, end in find_all_occurrences(formatted_context, content):
        if (start, end) in period_spans:
            source = "period_summary"
        elif _is_retrieved_wrapper(formatted_context, start, subject):
            source = "retrieved_result"
        else:
            source = "unclassified_match"
        details.append({"start": start, "end": end, "source": source})
    return details


def _choose_canonical_occurrence(
    occurrences: list[dict[str, Any]], used_retrieved_spans: set[tuple[int, int]]
) -> dict[str, Any] | None:
    for occurrence in occurrences:
        span = (occurrence["start"], occurrence["end"])
        if occurrence["source"] == "retrieved_result" and span not in used_retrieved_spans:
            used_retrieved_spans.add(span)
            return occurrence
    for preferred_source in ("retrieved_result", "unclassified_match", "period_summary"):
        for occurrence in occurrences:
            if occurrence["source"] == preferred_source:
                return occurrence
    return None


def _preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _unique_in_order(values: Iterable[Any]) -> list[Any]:
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        identity = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if identity not in seen:
            seen.add(identity)
            unique.append(value)
    return unique


def _logical_memory_identity(
    conversation_id: Any,
    subject: Any,
    digest: str,
) -> tuple[str, str]:
    """Build a conservative key for comparing versions of one logical memory.

    A subject is meaningful only within the same conversation when a
    conversation id is available.  Without a subject, exact content hash is the
    only safe fallback, so content-change comparisons are impossible for that
    fallback group.
    """

    conversation = str(conversation_id) if conversation_id is not None else "<missing>"
    if isinstance(subject, str) and subject.strip():
        normalized_subject = normalize_content(subject)
        basis = "conversation_id+subject" if conversation_id is not None else "subject"
        identity = f"{basis}\0{conversation}\0{normalized_subject}"
    else:
        basis = "conversation_id+content_hash_fallback" if conversation_id is not None else "content_hash_fallback"
        identity = f"{basis}\0{conversation}\0{digest}"
    return content_hash(identity), basis


def _pair_classification(first: dict[str, Any], second: dict[str, Any]) -> tuple[str, bool, bool, bool]:
    content_same = first["content_hash"] == second["content_hash"]
    prefix_same = first["preceding_chunk_hashes"] == second["preceding_chunk_hashes"]
    position_same = first["context_char_start"] == second["context_char_start"]
    if not content_same:
        classification = "CONTENT_CHANGED"
    elif prefix_same and position_same:
        classification = "ALL_SAME"
    elif not prefix_same and not position_same:
        classification = "SAME_CONTENT_PREFIX_AND_POSITION_CHANGED"
    elif not prefix_same:
        classification = "SAME_CONTENT_PREFIX_CHANGED"
    else:
        classification = "SAME_CONTENT_POSITION_CHANGED"
    return classification, content_same, prefix_same, position_same


def compare_logical_memory_contexts(
    chunks: Sequence[dict[str, Any]],
    *,
    max_pair_detail_rows: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Compare corresponding logical memories across different QA contexts.

    The mutually exclusive unit is a pair of located chunk occurrences from
    two different QA records.  Aggregates are complete even when pair-detail
    CSV output is capped.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        grouped[chunk["logical_memory_key"]].append(chunk)

    group_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    overall = Counter()
    detail_limit = max(0, max_pair_detail_rows)

    for logical_key, members in sorted(grouped.items()):
        located = sorted(
            (member for member in members if member["context_char_start"] is not None),
            key=lambda item: (item["record_index"], item["result_rank"]),
        )
        counts = Counter()
        for first_index, first in enumerate(located):
            for second in located[first_index + 1 :]:
                if first["_record_key"] == second["_record_key"]:
                    continue
                classification, content_same, prefix_same, position_same = _pair_classification(first, second)
                counts[classification] += 1
                overall[classification] += 1
                if len(pair_rows) < detail_limit:
                    pair_rows.append(
                        {
                            "logical_memory_key": logical_key,
                            "identity_basis": first["logical_memory_identity_basis"],
                            "conversation_id": first["conversation_id"],
                            "subject": first["subject"],
                            "classification": classification,
                            "record_index_a": first["record_index"],
                            "question_id_a": first["question_id"],
                            "result_rank_a": first["result_rank"],
                            "content_hash_a": first["content_hash"],
                            "content_preview_a": first["content_preview"],
                            "context_position_a": first["context_char_start"],
                            "preceding_chunk_hashes_a": first["preceding_chunk_hashes"],
                            "record_index_b": second["record_index"],
                            "question_id_b": second["question_id"],
                            "result_rank_b": second["result_rank"],
                            "content_hash_b": second["content_hash"],
                            "content_preview_b": second["content_preview"],
                            "context_position_b": second["context_char_start"],
                            "preceding_chunk_hashes_b": second["preceding_chunk_hashes"],
                            "content_same": content_same,
                            "prefix_same": prefix_same,
                            "position_same": position_same,
                        }
                    )

        pair_count = sum(counts.values())
        if pair_count == 0:
            continue
        same_content_changed = (
            counts["SAME_CONTENT_PREFIX_CHANGED"]
            + counts["SAME_CONTENT_POSITION_CHANGED"]
            + counts["SAME_CONTENT_PREFIX_AND_POSITION_CHANGED"]
        )
        representative = members[0]
        chunk_types = sorted({member["chunk_type"] for member in members})
        group_rows.append(
            {
                "logical_memory_key": logical_key,
                "identity_basis": representative["logical_memory_identity_basis"],
                "conversation_id": representative["conversation_id"],
                "subject": representative["subject"],
                "chunk_type": chunk_types[0] if len(chunk_types) == 1 else "mixed",
                "context_occurrence_count": len(located),
                "unlocated_occurrence_count": len(members) - len(located),
                "distinct_question_count": len({member["_record_key"] for member in located}),
                "distinct_content_versions": len({member["content_hash"] for member in located}),
                "distinct_prefix_sequences": len(
                    {tuple(member["preceding_chunk_hashes"]) for member in located}
                ),
                "distinct_positions": len({member["context_char_start"] for member in located}),
                "comparable_context_pair_count": pair_count,
                "content_changed_pairs": counts["CONTENT_CHANGED"],
                "content_changed_pair_ratio": _ratio(counts["CONTENT_CHANGED"], pair_count),
                "same_content_prefix_or_position_changed_pairs": same_content_changed,
                "same_content_prefix_or_position_changed_pair_ratio": _ratio(
                    same_content_changed, pair_count
                ),
                "same_content_prefix_changed_only_pairs": counts["SAME_CONTENT_PREFIX_CHANGED"],
                "same_content_position_changed_only_pairs": counts["SAME_CONTENT_POSITION_CHANGED"],
                "same_content_prefix_and_position_changed_pairs": counts[
                    "SAME_CONTENT_PREFIX_AND_POSITION_CHANGED"
                ],
                "all_same_pairs": counts["ALL_SAME"],
                "all_same_pair_ratio": _ratio(counts["ALL_SAME"], pair_count),
            }
        )

    total_pairs = sum(overall.values())
    same_content_changed_total = (
        overall["SAME_CONTENT_PREFIX_CHANGED"]
        + overall["SAME_CONTENT_POSITION_CHANGED"]
        + overall["SAME_CONTENT_PREFIX_AND_POSITION_CHANGED"]
    )
    summary = {
        "comparison_unit": "cross-record pairs of the same logical memory key",
        "logical_memory_identity": "conversation_id + normalized metadata.subject; content_hash fallback when subject is missing",
        "logical_memory_groups_observed": len(grouped),
        "comparable_logical_memory_groups": len(group_rows),
        "comparable_context_pairs": total_pairs,
        "content_changed_pairs": overall["CONTENT_CHANGED"],
        "content_changed_pair_ratio": _ratio(overall["CONTENT_CHANGED"], total_pairs),
        "same_content_prefix_or_position_changed_pairs": same_content_changed_total,
        "same_content_prefix_or_position_changed_pair_ratio": _ratio(
            same_content_changed_total, total_pairs
        ),
        "same_content_prefix_changed_only_pairs": overall["SAME_CONTENT_PREFIX_CHANGED"],
        "same_content_position_changed_only_pairs": overall["SAME_CONTENT_POSITION_CHANGED"],
        "same_content_prefix_and_position_changed_pairs": overall[
            "SAME_CONTENT_PREFIX_AND_POSITION_CHANGED"
        ],
        "all_same_pairs": overall["ALL_SAME"],
        "all_same_pair_ratio": _ratio(overall["ALL_SAME"], total_pairs),
        "pair_detail_rows_written": len(pair_rows),
        "pair_detail_rows_truncated": total_pairs > len(pair_rows),
        "pair_detail_row_limit": detail_limit,
        "subjectless_occurrences_using_hash_fallback": sum(
            "content_hash_fallback" in member["logical_memory_identity_basis"] for member in chunks
        ),
    }
    return group_rows, pair_rows, summary


def _public_chunk_row(chunk: dict[str, Any]) -> dict[str, Any]:
    return {field: chunk.get(field) for field in CHUNK_FIELDS}


def analyze_records(
    records: Sequence[dict[str, Any]],
    *,
    input_path: str | None = None,
    input_format: str | None = None,
    limit: int | None = None,
    max_pair_detail_rows: int = MAX_PAIR_DETAIL_ROWS,
) -> dict[str, Any]:
    """Analyze parsed QA records and return all output-table payloads."""

    selected = list(records[:limit] if limit is not None else records)
    warnings: list[dict[str, Any]] = []
    record_rows: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    intra_context_rows: list[dict[str, Any]] = []
    records_skipped = 0

    for record_index, record in enumerate(selected):
        warning_start = len(warnings)
        raw_question_id = record.get("question_id")
        question_id = _optional_scalar(
            raw_question_id,
            field="question_id",
            warnings=warnings,
            record_index=record_index,
            question_id=raw_question_id,
        )
        record_key = f"record:{record_index}"
        results = record.get("results")
        if not isinstance(results, list):
            _warning(
                warnings,
                record_index=record_index,
                question_id=question_id,
                code="MISSING_OR_INVALID_RESULTS",
                message="results is missing or is not a list; skipped record",
            )
            records_skipped += 1
            continue

        conversation_id = _optional_scalar(
            record.get("conversation_id"),
            field="conversation_id",
            warnings=warnings,
            record_index=record_index,
            question_id=question_id,
        )
        query = _optional_scalar(
            record.get("query"),
            field="query",
            warnings=warnings,
            record_index=record_index,
            question_id=question_id,
        )
        retrieval_metadata = record.get("retrieval_metadata")
        if retrieval_metadata is None:
            retrieval_metadata = {}
        elif not isinstance(retrieval_metadata, dict):
            _warning(
                warnings,
                record_index=record_index,
                question_id=question_id,
                code="INVALID_RETRIEVAL_METADATA",
                message="retrieval_metadata is not an object; treated as missing",
            )
            retrieval_metadata = {}

        formatted_context_value = retrieval_metadata.get("formatted_context")
        if isinstance(formatted_context_value, str):
            formatted_context: str | None = formatted_context_value
        else:
            formatted_context = None
            _warning(
                warnings,
                record_index=record_index,
                question_id=question_id,
                code="MISSING_FORMATTED_CONTEXT",
                message="formatted_context is missing or is not a string; chunk positions are null",
            )

        period_summaries = extract_period_summaries(formatted_context) if formatted_context is not None else []
        for summary in period_summaries:
            if not summary["terminator_found"]:
                _warning(
                    warnings,
                    record_index=record_index,
                    question_id=question_id,
                    code="PERIOD_SUMMARY_TERMINATOR_MISSING",
                    message=f"{summary['period_id']} has no standalone --- terminator; extracted to context end",
                )

        record_chunks: list[dict[str, Any]] = []
        for result_rank, result in enumerate(results):
            if not isinstance(result, dict):
                _warning(
                    warnings,
                    record_index=record_index,
                    question_id=question_id,
                    code="INVALID_RESULT_ITEM",
                    message=f"results[{result_rank}] is not an object; skipped chunk",
                )
                continue
            raw_content = result.get("content")
            if not isinstance(raw_content, str):
                _warning(
                    warnings,
                    record_index=record_index,
                    question_id=question_id,
                    code="INVALID_CHUNK_CONTENT",
                    message=f"results[{result_rank}].content is not a string; skipped chunk",
                )
                continue
            normalized = normalize_content(raw_content)
            if not normalized:
                _warning(
                    warnings,
                    record_index=record_index,
                    question_id=question_id,
                    code="EMPTY_CHUNK_CONTENT",
                    message=f"results[{result_rank}].content is empty after normalization; skipped chunk",
                )
                continue

            metadata = result.get("metadata")
            if metadata is None:
                metadata = {}
            elif not isinstance(metadata, dict):
                _warning(
                    warnings,
                    record_index=record_index,
                    question_id=question_id,
                    code="INVALID_CHUNK_METADATA",
                    message=f"results[{result_rank}].metadata is not an object; treated as missing",
                )
                metadata = {}
            subject = metadata.get("subject")
            if subject is not None and not isinstance(subject, str):
                _warning(
                    warnings,
                    record_index=record_index,
                    question_id=question_id,
                    code="INVALID_SUBJECT",
                    message=f"results[{result_rank}].metadata.subject is not a string; wrote null",
                )
                subject = None
            score = result.get("score")
            if score is not None and (not isinstance(score, (int, float)) or isinstance(score, bool)):
                _warning(
                    warnings,
                    record_index=record_index,
                    question_id=question_id,
                    code="INVALID_SCORE",
                    message=f"results[{result_rank}].score is not numeric; wrote null",
                )
                score = None

            digest = content_hash(normalized)
            logical_memory_key, logical_memory_identity_basis = _logical_memory_identity(
                conversation_id,
                subject,
                digest,
            )
            occurrences = (
                _occurrence_details(formatted_context, normalized, subject, period_summaries, digest)
                if formatted_context is not None
                else []
            )
            chunk = {
                "_record_key": record_key,
                "_occurrences": occurrences,
                "_all_preceding_sequences": [],
                "record_index": record_index,
                "question_id": question_id,
                "conversation_id": conversation_id,
                "result_rank": result_rank,
                "chunk_type": classify_chunk_type(subject),
                "subject": subject,
                "logical_memory_key": logical_memory_key,
                "logical_memory_identity_basis": logical_memory_identity_basis,
                "score": score,
                "content_hash": digest,
                "content_chars": len(normalized),
                "content_preview": _preview(normalized),
                "located_in_context": bool(occurrences),
                "context_occurrence_count": len(occurrences),
                "context_char_start": None,
                "context_char_end": None,
                "canonical_context_source": None,
                "all_context_positions": [[item["start"], item["end"]] for item in occurrences],
                "context_occurrence_sources": occurrences,
                "preceding_chunk_count": 0,
                "preceding_chunk_hashes": [],
                "classifications": [],
            }
            record_chunks.append(chunk)

        used_retrieved_spans: set[tuple[int, int]] = set()
        for chunk in sorted(record_chunks, key=lambda item: item["result_rank"]):
            canonical = _choose_canonical_occurrence(chunk["_occurrences"], used_retrieved_spans)
            if canonical is not None:
                chunk["context_char_start"] = canonical["start"]
                chunk["context_char_end"] = canonical["end"]
                chunk["canonical_context_source"] = canonical["source"]
                if canonical["source"] != "retrieved_result":
                    _warning(
                        warnings,
                        record_index=record_index,
                        question_id=question_id,
                        code="RETRIEVED_WRAPPER_NOT_CONFIRMED",
                        message=(
                            f"results[{chunk['result_rank']}] body was found, but no unassigned "
                            "subject wrapper was confirmed; used fallback position"
                        ),
                    )

        ordered_chunks = sorted(
            (chunk for chunk in record_chunks if chunk["context_char_start"] is not None),
            key=lambda item: (item["context_char_start"], item["result_rank"]),
        )
        preceding: list[str] = []
        for chunk in ordered_chunks:
            chunk["preceding_chunk_hashes"] = list(preceding)
            chunk["preceding_chunk_count"] = len(preceding)
            preceding.append(chunk["content_hash"])

        for chunk in record_chunks:
            sequences: list[list[str]] = []
            for occurrence in chunk["_occurrences"]:
                sequence = [
                    other["content_hash"]
                    for other in ordered_chunks
                    if other["context_char_start"] < occurrence["start"]
                ]
                sequences.append(sequence)
            chunk["_all_preceding_sequences"] = sequences

        chunks_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk in record_chunks:
            chunks_by_hash[chunk["content_hash"]].append(chunk)

        record_duplicate_count = 0
        for digest, same_hash_chunks in chunks_by_hash.items():
            span_sources: dict[tuple[int, int], set[str]] = defaultdict(set)
            for chunk in same_hash_chunks:
                for occurrence in chunk["_occurrences"]:
                    span_sources[(occurrence["start"], occurrence["end"])].add(occurrence["source"])
            if len(span_sources) <= 1:
                continue

            record_duplicate_count += 1
            sorted_spans = sorted(span_sources)
            occurrence_sources = [
                {
                    "start": start,
                    "end": end,
                    "sources": sorted(span_sources[(start, end)]),
                }
                for start, end in sorted_spans
            ]
            sequences = [
                [
                    other["content_hash"]
                    for other in ordered_chunks
                    if other["context_char_start"] < start
                ]
                for start, _ in sorted_spans
            ]
            unique_sequences = _unique_in_order(sequences)
            has_period = any("period_summary" in sources for sources in span_sources.values())
            has_retrieved = any("retrieved_result" in sources for sources in span_sources.values())
            same_wrapper_body = has_period and has_retrieved and any(
                summary["body_hash"] == digest for summary in period_summaries
            )
            classifications = [
                "INTRA_CONTEXT_DUPLICATE",
                "POSITION_VARIANT",
                "DIFFERENT_POSITION",
                "REDUNDANT_CONTEXT_CANDIDATE",
            ]
            if same_wrapper_body:
                classifications.insert(0, "SAME_BODY_DIFFERENT_WRAPPER")
            if len(unique_sequences) > 1:
                classifications.append("NON_PREFIX_REUSE_CANDIDATE")

            representative = same_hash_chunks[0]
            intra_context_rows.append(
                {
                    "_record_key": record_key,
                    "_content_chars": representative["content_chars"],
                    "record_index": record_index,
                    "question_id": question_id,
                    "conversation_id": conversation_id,
                    "content_hash": digest,
                    "chunk_type": representative["chunk_type"],
                    "subject": representative["subject"],
                    "occurrence_count": len(sorted_spans),
                    "positions": [[start, end] for start, end in sorted_spans],
                    "occurrence_sources": occurrence_sources,
                    "preceding_chunk_sequences": unique_sequences,
                    "different_position": True,
                    "different_preceding_memory_context": len(unique_sequences) > 1,
                    "same_body_different_wrapper": same_wrapper_body,
                    "redundant_context_candidate": True,
                    "potential_redundant_chars": representative["content_chars"] * (len(sorted_spans) - 1),
                    "classifications": classifications,
                }
            )
            for chunk in same_hash_chunks:
                chunk["classifications"] = _unique_in_order(chunk["classifications"] + classifications)

        located_count = sum(chunk["context_char_start"] is not None for chunk in record_chunks)
        for chunk in record_chunks:
            if chunk["context_occurrence_count"] == 0:
                chunk["classifications"].append("NOT_FOUND_IN_FORMATTED_CONTEXT")
        chunks.extend(record_chunks)

        record_rows.append(
            {
                "record_index": record_index,
                "question_id": question_id,
                "conversation_id": conversation_id,
                "query": query,
                "is_multi_round": retrieval_metadata.get("is_multi_round"),
                "round1_count": retrieval_metadata.get("round1_count"),
                "round2_count": retrieval_metadata.get("round2_count"),
                "final_count": retrieval_metadata.get("final_count"),
                "result_count": len(results),
                "valid_result_count": len(record_chunks),
                "formatted_context_chars": len(formatted_context) if formatted_context is not None else None,
                "unique_retrieved_chunks": len(chunks_by_hash),
                "located_retrieved_chunks": located_count,
                "duplicate_within_context_count": record_duplicate_count,
                "warning_count": len(warnings) - warning_start,
            }
        )

    chunks_by_hash_global: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        chunks_by_hash_global[chunk["content_hash"]].append(chunk)

    reuse_group_rows: list[dict[str, Any]] = []
    repeated_groups: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for digest, members in sorted(chunks_by_hash_global.items()):
        if len(members) < 2:
            continue
        located = [member for member in members if member["context_char_start"] is not None]
        starts = [member["context_char_start"] for member in located]
        preceding_sequences = _unique_in_order(
            [member["preceding_chunk_hashes"] for member in located]
        )
        record_keys = {member["_record_key"] for member in members}
        located_record_keys = {member["_record_key"] for member in located}
        different_position = len(set(starts)) > 1
        different_preceding = len(preceding_sequences) > 1
        cross_context_candidate = len(located_record_keys) >= 2 and different_preceding
        classifications: list[str] = []
        if different_position:
            classifications.extend(("POSITION_VARIANT", "DIFFERENT_POSITION"))
        if cross_context_candidate:
            classifications.extend(
                ("NON_PREFIX_REUSE_CANDIDATE", "CROSS_CONTEXT_NON_PREFIX_REUSE_CANDIDATE")
            )
            for member in members:
                member["classifications"] = _unique_in_order(member["classifications"] + classifications)

        chunk_types = sorted({member["chunk_type"] for member in members})
        candidate_chars = members[0]["content_chars"] * max(0, len(located) - 1)
        row = {
            "content_hash": digest,
            "chunk_type": chunk_types[0] if len(chunk_types) == 1 else "mixed",
            "occurrence_count": len(members),
            "located_occurrence_count": len(located),
            "question_count": len(record_keys),
            "conversation_count": len(
                {member["conversation_id"] for member in members if member["conversation_id"] is not None}
            ),
            "subjects": _unique_in_order(member["subject"] for member in members),
            "chunk_types": chunk_types,
            "result_ranks": [member["result_rank"] for member in members],
            "scores": [member["score"] for member in members],
            "context_positions": starts,
            "preceding_chunk_sequences": preceding_sequences,
            "different_position": different_position,
            "different_preceding_memory_context": different_preceding,
            "cross_context_non_prefix_candidate": cross_context_candidate,
            "potential_reusable_chars": candidate_chars,
            "min_context_position": min(starts) if starts else None,
            "max_context_position": max(starts) if starts else None,
            "position_span_chars": max(starts) - min(starts) if starts else None,
            "classifications": classifications,
        }
        reuse_group_rows.append(row)
        repeated_groups.append((members, row))

    context_variation_rows, context_pair_rows, context_variation_summary = compare_logical_memory_contexts(
        chunks,
        max_pair_detail_rows=max_pair_detail_rows,
    )

    retrieved_content_chars = sum(chunk["content_chars"] for chunk in chunks)
    located_chunks = [chunk for chunk in chunks if chunk["context_char_start"] is not None]
    located_retrieved_content_chars = sum(chunk["content_chars"] for chunk in located_chunks)
    repeated_content_chars = sum(
        member["content_chars"] for members, _ in repeated_groups for member in members
    )
    cross_question_repeated_occurrences = sum(
        len(members)
        for members, _ in repeated_groups
        if len({member["_record_key"] for member in members}) >= 2
    )
    candidate_groups = [
        (members, row)
        for members, row in repeated_groups
        if row["cross_context_non_prefix_candidate"]
    ]
    candidate_located_chunks = [
        member
        for members, _ in candidate_groups
        for member in members
        if member["context_char_start"] is not None
    ]
    non_prefix_candidate_chars = sum(chunk["content_chars"] for chunk in candidate_located_chunks)
    potential_reusable_chars = sum(row["potential_reusable_chars"] for _, row in repeated_groups)
    non_prefix_potential_reusable_chars = sum(row["potential_reusable_chars"] for _, row in candidate_groups)
    redundant_context_chars = sum(row["potential_redundant_chars"] for row in intra_context_rows)

    chunk_types = sorted({chunk["chunk_type"] for chunk in chunks})
    by_chunk_type: dict[str, Any] = {}
    for chunk_type in chunk_types:
        type_chunks = [chunk for chunk in chunks if chunk["chunk_type"] == chunk_type]
        repeated_hashes = {
            row["content_hash"] for row in reuse_group_rows if row["chunk_type"] == chunk_type
        }
        candidate_hashes = {
            row["content_hash"]
            for row in reuse_group_rows
            if row["chunk_type"] == chunk_type and row["cross_context_non_prefix_candidate"]
        }
        by_chunk_type[chunk_type] = {
            "retrieved_chunk_occurrences": len(type_chunks),
            "unique_retrieved_chunks": len({chunk["content_hash"] for chunk in type_chunks}),
            "repeated_chunk_occurrences": sum(
                chunk["content_hash"] in repeated_hashes for chunk in type_chunks
            ),
            "non_prefix_candidate_occurrences": sum(
                chunk["content_hash"] in candidate_hashes and chunk["context_char_start"] is not None
                for chunk in type_chunks
            ),
            "retrieved_content_chars": sum(chunk["content_chars"] for chunk in type_chunks),
            "potential_reusable_chars": sum(
                row["potential_reusable_chars"]
                for row in reuse_group_rows
                if row["chunk_type"] == chunk_type
            ),
        }

    summary = {
        "schema_version": "1.0",
        "analysis_type": "lifemem.non_prefix_kv_reuse_opportunity",
        "input": {
            "path": input_path,
            "format": input_format,
            "records_loaded": len(records),
            "record_limit": limit,
        },
        "records_analyzed": len(record_rows),
        "records_skipped": records_skipped,
        "retrieved_chunk_occurrences": len(chunks),
        "located_retrieved_chunk_occurrences": len(located_chunks),
        "unique_retrieved_chunks": len(chunks_by_hash_global),
        "repeated_chunk_occurrences": sum(len(members) for members, _ in repeated_groups),
        "cross_question_repeated_occurrences": cross_question_repeated_occurrences,
        "non_prefix_candidate_occurrences": len(candidate_located_chunks),
        "non_prefix_candidate_groups": len(candidate_groups),
        "cross_context_non_prefix_candidate_occurrences": len(candidate_located_chunks),
        "cross_context_non_prefix_candidate_groups": len(candidate_groups),
        "intra_context_duplicate_groups": len(intra_context_rows),
        "same_body_different_wrapper_groups": sum(
            bool(row["same_body_different_wrapper"]) for row in intra_context_rows
        ),
        "redundant_context_candidate_groups": sum(
            bool(row["redundant_context_candidate"]) for row in intra_context_rows
        ),
        "retrieved_content_chars": retrieved_content_chars,
        "located_retrieved_content_chars": located_retrieved_content_chars,
        "repeated_content_chars": repeated_content_chars,
        "non_prefix_candidate_chars": non_prefix_candidate_chars,
        "potential_reusable_chars": potential_reusable_chars,
        "non_prefix_potential_reusable_chars": non_prefix_potential_reusable_chars,
        "redundant_context_chars": redundant_context_chars,
        "repeated_char_ratio": _ratio(repeated_content_chars, retrieved_content_chars),
        "non_prefix_candidate_char_ratio": _ratio(non_prefix_candidate_chars, located_retrieved_content_chars),
        "potential_reusable_char_ratio": _ratio(potential_reusable_chars, located_retrieved_content_chars),
        "non_prefix_potential_reusable_char_ratio": _ratio(
            non_prefix_potential_reusable_chars, located_retrieved_content_chars
        ),
        "context_variation": context_variation_summary,
        "by_chunk_type": by_chunk_type,
        "warning_count": len(warnings),
        "warnings": warnings,
        "metric_notes": {
            "positions": "zero-based Python character offsets; end is exclusive",
            "retrieved_inventory": "counts rows from results[] after invalid chunks are skipped",
            "opportunity_denominator": "located_retrieved_content_chars",
            "potential_reusable_chars": "for each repeated retrieved hash, located occurrence chars after one baseline occurrence",
            "redundant_context_chars": "duplicate body chars inside one formatted_context after one baseline occurrence",
            "non_prefix_candidate_metrics": "cross-context candidates only; intra-context umbrella classifications remain in duplicate rows",
            "token_claim": "all size metrics are characters, not tokens",
        },
    }

    return {
        "records": record_rows,
        "chunks": [_public_chunk_row(chunk) for chunk in chunks],
        "reuse_groups": reuse_group_rows,
        "intra_context_duplicates": [
            {field: row.get(field) for field in INTRA_CONTEXT_FIELDS} for row in intra_context_rows
        ],
        "context_variation_groups": context_variation_rows,
        "context_pair_comparisons": context_pair_rows,
        "summary": summary,
    }


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _csv_value(value: Any) -> Any:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fields})


def _markdown_cell(value: Any, *, limit: int = 80) -> str:
    if value is None:
        return "null"
    text = " ".join(str(value).split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_context_variation_report(analysis: dict[str, Any]) -> str:
    variation = analysis["summary"]["context_variation"]
    total = variation["comparable_context_pairs"]

    def percentage(value: float) -> str:
        return f"{value * 100:.2f}%"

    lines = [
        "# LifeMem 上下文变化直观报告",
        "",
        "比较单位：同一 logical memory 在两个不同 QA record 中的一个上下文对。",
        "logical memory 默认由 `conversation_id + metadata.subject` 对应；subject 缺失时退化为 content hash。",
        "prefix 仅指 `preceding_memory_chunks`，不是完整模型 prompt prefix。",
        "",
        "## 总览",
        "",
        f"- 可比较 logical memory 组：{variation['comparable_logical_memory_groups']}",
        f"- 可比较上下文对：{total}",
        f"- 内容变化：{variation['content_changed_pairs']}（{percentage(variation['content_changed_pair_ratio'])}）",
        "- 内容相同但 prefix 或位置变化："
        f"{variation['same_content_prefix_or_position_changed_pairs']}"
        f"（{percentage(variation['same_content_prefix_or_position_changed_pair_ratio'])}）",
        f"- 内容、prefix、位置都相同：{variation['all_same_pairs']}"
        f"（{percentage(variation['all_same_pair_ratio'])}）",
        "",
        "内容相同但发生变化的上下文对进一步分为：",
        "",
        f"- 只变 prefix：{variation['same_content_prefix_changed_only_pairs']}",
        f"- 只变位置：{variation['same_content_position_changed_only_pairs']}",
        f"- prefix 和位置都变：{variation['same_content_prefix_and_position_changed_pairs']}",
        "",
        "## 每个 logical memory",
        "",
    ]
    groups = analysis["context_variation_groups"]
    if not groups:
        lines.append("当前输入中没有跨 QA 的同一 logical memory，无法形成上下文对。")
    else:
        lines.extend(
            [
                "| conversation | subject | contexts | pairs | 内容变化 | 内容相同但 prefix/位置变化 | 三者都相同 |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in sorted(
            groups,
            key=lambda item: (
                str(item.get("conversation_id") or ""),
                str(item.get("subject") or ""),
            ),
        ):
            lines.append(
                "| "
                + " | ".join(
                    (
                        _markdown_cell(row["conversation_id"]),
                        _markdown_cell(row["subject"]),
                        str(row["context_occurrence_count"]),
                        str(row["comparable_context_pair_count"]),
                        str(row["content_changed_pairs"]),
                        str(row["same_content_prefix_or_position_changed_pairs"]),
                        str(row["all_same_pairs"]),
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## 明细说明",
            "",
            "`context_pair_comparisons.csv` 给出每一对上下文的 question id、content hash、位置、preceding chunk hashes 和互斥分类。",
            f"本次写入 {variation['pair_detail_rows_written']} 条 pair 明细；"
            f"上限为 {variation['pair_detail_row_limit']}；"
            f"是否截断：{'是' if variation['pair_detail_rows_truncated'] else '否'}。",
            "聚合计数始终覆盖全部 pair，即使明细 CSV 因上限而截断。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(output_dir: Path, analysis: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {key: output_dir / name for key, name in OUTPUT_FILES.items()}
    write_csv(paths["records"], analysis["records"], RECORD_FIELDS)
    write_csv(paths["chunks"], analysis["chunks"], CHUNK_FIELDS)
    write_csv(paths["reuse_groups"], analysis["reuse_groups"], REUSE_GROUP_FIELDS)
    write_csv(
        paths["intra_context_duplicates"],
        analysis["intra_context_duplicates"],
        INTRA_CONTEXT_FIELDS,
    )
    write_csv(
        paths["context_variation_groups"],
        analysis["context_variation_groups"],
        CONTEXT_VARIATION_FIELDS,
    )
    write_csv(
        paths["context_pair_comparisons"],
        analysis["context_pair_comparisons"],
        CONTEXT_PAIR_FIELDS,
    )
    paths["context_variation_report"].write_text(
        render_context_variation_report(analysis),
        encoding="utf-8",
    )
    paths["summary"].write_text(
        json.dumps(analysis["summary"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return paths


def print_terminal_summary(analysis: dict[str, Any], *, verbose: bool = False) -> None:
    summary = analysis["summary"]
    print("===== LifeMem KV Reuse Opportunity Analysis =====")
    print()
    print(f"records analyzed: {summary['records_analyzed']}")
    print(f"retrieved chunks: {summary['retrieved_chunk_occurrences']}")
    print(f"unique retrieved chunks: {summary['unique_retrieved_chunks']}")
    print()
    print(
        "chunks located in formatted_context: "
        f"{summary['located_retrieved_chunk_occurrences']}/{summary['retrieved_chunk_occurrences']}"
    )
    print()
    print(f"cross-context non-prefix groups: {summary['cross_context_non_prefix_candidate_groups']}")
    print(f"intra-context duplicate groups: {summary['intra_context_duplicate_groups']}")
    print(f"same-body-different-wrapper groups: {summary['same_body_different_wrapper_groups']}")
    variation = summary["context_variation"]
    print()
    print("logical-memory context pair comparison:")
    print(f"  comparable context pairs: {variation['comparable_context_pairs']}")
    print(f"  content changed: {variation['content_changed_pairs']}")
    print(
        "  same content, prefix or position changed: "
        f"{variation['same_content_prefix_or_position_changed_pairs']}"
    )
    print(f"  content, prefix and position all same: {variation['all_same_pairs']}")

    duplicates = analysis["intra_context_duplicates"]
    if duplicates:
        example = duplicates[0]
        matching_chunk = next(
            (chunk for chunk in analysis["chunks"] if chunk["content_hash"] == example["content_hash"]),
            None,
        )
        print()
        print("Example:")
        print(f"  type: {example['chunk_type']}")
        print(f"  subject: {example['subject']}")
        if matching_chunk is not None:
            print(f"  retrieved rank: {matching_chunk['result_rank']}")
        for index, occurrence in enumerate(example["occurrence_sources"], start=1):
            sources = ",".join(occurrence["sources"])
            print()
            print(f"  occurrence {index}:")
            print(f"    source: {sources}")
            print(f"    position: [{occurrence['start']}, {occurrence['end']})")
        print()
        print("  classification:")
        for classification in example["classifications"]:
            print(f"    {classification}")

    if verbose and summary["warnings"]:
        print(file=sys.stderr)
        for warning in summary["warnings"]:
            print(
                f"warning[{warning['code']}] record={warning['record_index']} "
                f"question_id={warning['question_id']!r}: {warning['message']}",
                file=sys.stderr,
            )


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze exact non-prefix memory-chunk reuse in existing LifeMem JSON/JSONL results"
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="LifeMem result JSON/JSONL file, or - for stdin",
    )
    parser.add_argument("--output", required=True, type=Path, help="output directory")
    parser.add_argument("--limit", type=positive_int, help="analyze at most the first N input records")
    parser.add_argument(
        "--max-pair-details",
        type=positive_int,
        default=MAX_PAIR_DETAIL_ROWS,
        help="maximum context-pair detail rows written to CSV (aggregates are never truncated)",
    )
    parser.add_argument("--verbose", action="store_true", help="print structured warnings to stderr")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output.expanduser().resolve()
    try:
        if str(args.input) == "-":
            input_path_text = "<stdin>"
            records, input_format = parse_records_text(sys.stdin.read(), source=input_path_text)
        else:
            input_path = args.input.expanduser().resolve()
            input_path_text = str(input_path)
            for output_name in OUTPUT_FILES.values():
                if input_path == (output_dir / output_name).resolve():
                    raise ValueError(f"input path would be overwritten by output file: {output_name}")
            records, input_format = load_records(input_path)
        analysis = analyze_records(
            records,
            input_path=input_path_text,
            input_format=input_format,
            limit=args.limit,
            max_pair_detail_rows=args.max_pair_details,
        )
        write_outputs(output_dir, analysis)
        print_terminal_summary(analysis, verbose=args.verbose)
    except (OSError, ValueError, TypeError) as exc:
        print(f"LifeMem KV reuse analysis error: {exc}", file=sys.stderr)
        return 2
    return 0


def build_direct_run_arguments() -> list[str]:
    """Translate the editable configuration block into normal CLI arguments."""

    if not str(INPUT_PATH).strip() or not str(OUTPUT_PATH).strip():
        raise ValueError("set INPUT_PATH and OUTPUT_PATH in the direct-run configuration block")
    arguments = [
        "--input",
        str(INPUT_PATH),
        "--output",
        str(OUTPUT_PATH),
        "--max-pair-details",
        str(MAX_PAIR_DETAIL_ROWS),
    ]
    if LIMIT is not None:
        if LIMIT <= 0:
            raise ValueError("LIMIT must be a positive integer or None")
        arguments.extend(("--limit", str(LIMIT)))
    if VERBOSE:
        arguments.append("--verbose")
    return arguments


if __name__ == "__main__":
    if USE_COMMAND_LINE_ARGUMENTS or len(sys.argv) > 1:
        raise SystemExit(main())
    try:
        direct_arguments = build_direct_run_arguments()
    except ValueError as exc:
        print(f"LifeMem KV reuse analysis configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(direct_arguments))
