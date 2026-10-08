from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol


@dataclass(frozen=True)
class Tokenization:
    """Offsets are Python character spans in the exact supplied text, not bytes."""

    input_ids: tuple[int, ...]
    offsets: tuple[tuple[int, int], ...]
    tokenizer_id: str
    source: str
    text_scope: str

    def validate(self, text: str, text_scope: str) -> None:
        if not self.tokenizer_id or self.source not in {"runtime", "provided", "local_tokenizer", "synthetic"}:
            raise ValueError("tokenizer_id and explicit tokenization source are required")
        if self.text_scope != text_scope:
            raise ValueError("tokenization text_scope does not match the text being analyzed")
        if len(self.input_ids) != len(self.offsets):
            raise ValueError("input_ids and offsets must have the same length")
        previous_start, previous_end = 0, 0
        for token, (start, end) in zip(self.input_ids, self.offsets):
            if type(token) is not int or token < 0 or type(start) is not int or type(end) is not int:
                raise ValueError("token IDs and character offsets must be nonnegative integers")
            if (start, end) == (0, 0):  # Explicit tokenizer special token.
                continue
            # Byte-fallback tokenizers may assign several tokens to the same Unicode character.
            if not 0 <= start < end <= len(text) or start < previous_start or end < previous_end:
                raise ValueError("token offsets must be ordered character spans")
            previous_start, previous_end = start, end

    @classmethod
    def from_dict(cls, value: dict) -> "Tokenization":
        return cls(tuple(value["input_ids"]), tuple(tuple(x) for x in value["offsets"]),
                   value["tokenizer_id"], value["source"], value["text_scope"])


class TokenizerProvider(Protocol):
    def tokenize(self, text: str, *, text_scope: str) -> Tokenization:
        """Caller supplies a local tokenizer; no model loading or network fallback."""
        ...


class LocalFastTokenizerProvider:
    """Explicit local-only tokenizer loading; never loads model weights or remote code."""

    def __init__(self, directory: str, tokenizer_id: str, *, add_special_tokens: bool = False):
        from pathlib import Path
        if not Path(directory).is_dir() or not tokenizer_id:
            raise ValueError("an existing local tokenizer directory and tokenizer_id are required")
        from transformers import AutoTokenizer  # Optional dependency, loaded only by explicit request.
        self.tokenizer = AutoTokenizer.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False, use_fast=True)
        if not self.tokenizer.is_fast:
            raise ValueError("a fast tokenizer with character offsets is required")
        self.add_special_tokens = add_special_tokens
        self.identity = f"{tokenizer_id}|add_special_tokens={int(add_special_tokens)}"

    def tokenize(self, text: str, *, text_scope: str) -> Tokenization:
        encoded = self.tokenizer(text, add_special_tokens=self.add_special_tokens,
                                 return_offsets_mapping=True, truncation=False)
        return Tokenization(tuple(encoded["input_ids"]), tuple(tuple(x) for x in encoded["offset_mapping"]),
                            self.identity, "local_tokenizer", text_scope)


def occurrences(text: str, body: str) -> list[tuple[int, int]]:
    if not body:
        return []
    spans, start = [], 0
    while True:
        index = text.find(body, start)
        if index < 0:
            return spans
        spans.append((index, index + len(body)))
        start = index + 1


def locate(text: str, body: str, explicit=None, subject: str | None = None) -> tuple:
    if explicit is not None:
        if (len(explicit) != 2 or any(type(x) is not int for x in explicit)
                or not 0 <= explicit[0] < explicit[1] <= len(text)
                or text[explicit[0]:explicit[1]] != body):
            raise ValueError("explicit span must identify the exact original body")
        return tuple(explicit), "EXPLICIT", 1
    candidates = occurrences(text, body)
    if len(candidates) == 1:
        return candidates[0], "UNIQUE_EXACT", 1
    if len(candidates) > 1 and subject:
        wrapper = subject + ": "
        wrapped = occurrences(text, wrapper + body)
        if len(wrapped) == 1:
            return (wrapped[0][0] + len(wrapper), wrapped[0][1]), "SUBJECT_WRAPPER", len(candidates)
    return None, "AMBIGUOUS" if candidates else "MISSING", len(candidates)


def align_span(tokens: Tokenization | None, span) -> dict:
    if tokens is None:
        return {"status": "TOKENIZATION_MISSING", "token_span": None}
    if span is None:
        return {"status": "CHAR_SPAN_UNRESOLVED", "token_span": None}
    start, end = span
    hits = [i for i, (a, b) in enumerate(tokens.offsets) if b > start and a < end and b > a]
    if not hits:
        return {"status": "NO_TOKEN_OVERLAP", "token_span": None}
    first, last = hits[0], hits[-1]
    exact = tokens.offsets[first][0] == start and tokens.offsets[last][1] == end
    contiguous = hits == list(range(first, last + 1))
    return {"status": "EXACT_BOUNDARIES" if exact and contiguous else "BOUNDARY_CROSSING",
            "token_span": [first, last + 1]}


def edit_map(old, new, *, max_units: int = 100_000) -> dict:
    """Textual alignment, including repeated-text tie choices; never a KV mapping."""
    if len(old) + len(new) > max_units:
        return {"status": "SIZE_LIMIT", "blocks": []}
    return {"status": "TEXTUAL_ALIGNMENT", "blocks": [
        {"operation": tag, "old_span": [a, b], "new_span": [c, d]}
        for tag, a, b, c, d in SequenceMatcher(None, old, new, autojunk=True).get_opcodes()
    ]}
