from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from .alignment import Tokenization, TokenizerProvider, align_span, edit_map, locate


def _identity(*parts) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _digest(text: str) -> str:
    # Content identity only; no file/dataset fingerprint subsystem.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare_records(records: list[dict], *, dataset_id: str,
                    order: str = "constructed", tokenizer: TokenizerProvider | None = None,
                    max_edit_units: int = 100_000) -> dict:
    if not dataset_id or order not in {"constructed", "observed"}:
        raise ValueError("dataset_id and constructed/observed order are required")
    requests, chunks, payloads, issues = [], [], [], []
    request_ids = set()
    context_versions, memory_versions = defaultdict(dict), defaultdict(dict)

    def issue(request_id, code, result_rank=None):
        issues.append({"request_id": request_id, "result_rank": result_rank, "code": code})

    indexed = list(enumerate(records))
    if order == "observed":
        domains = {r.get("research", {}).get("clock_domain") for r in records}
        if len(domains) != 1 or None in domains or "" in domains:
            raise ValueError("observed order requires one explicit common clock_domain")
        if any(type(r.get("research", {}).get("request_time_ns")) is not int
               or r["research"]["request_time_ns"] < 0 for r in records):
            raise ValueError("observed order requires integer request_time_ns for every record")
        indexed.sort(key=lambda item: (item[1]["research"]["request_time_ns"], item[0]))

    session_indices = defaultdict(int)
    for ordinal, (record_index, record) in enumerate(indexed):
        extra = record.get("research", {})
        explicit_id = extra.get("request_id")
        request_id = str(explicit_id if explicit_id is not None and explicit_id != ""
                         else f"{dataset_id}:record:{record_index}")
        if request_id in request_ids:
            raise ValueError(f"duplicate request_id: {request_id}")
        request_ids.add(request_id)
        conversation = record.get("conversation_id")
        missing_conversation = conversation is None or (isinstance(conversation, str) and not conversation.strip())
        # Namespaces separate real conversation IDs from isolated missing-session
        # requests, and avoid cross-dataset memory identities when outputs are joined.
        session = (_identity(dataset_id, "unlinked_request", request_id) if missing_conversation
                   else _identity(dataset_id, "conversation", str(conversation)))
        if missing_conversation:
            issue(request_id, "CONVERSATION_MISSING_NO_CROSS_REQUEST_LINK")
        context = record.get("retrieval_metadata", {}).get("formatted_context")
        if not isinstance(context, str):
            raise ValueError(f"{request_id}: retrieval_metadata.formatted_context is required")
        if not isinstance(record.get("results"), list):
            raise ValueError(f"{request_id}: results must be a list")
        full_prompt = extra.get("full_prompt")
        if full_prompt is not None and not isinstance(full_prompt, str):
            raise ValueError(f"{request_id}: full_prompt must be a string")
        scope = "full_prompt" if full_prompt is not None else "formatted_context"
        text = full_prompt if full_prompt is not None else context
        if full_prompt is None:
            issue(request_id, "FULL_PROMPT_MISSING")
            context_span, context_status = (0, len(context)), "CONTEXT_ONLY"
        else:
            context_span, context_status, _ = locate(
                text, context, extra.get("context_span_in_prompt"))
            if context_span is None:
                issue(request_id, "CONTEXT_IN_PROMPT_" + context_status)
        token_data = extra.get("tokenization")
        tokens = Tokenization.from_dict(token_data) if token_data is not None else None
        if tokens is None and tokenizer is not None:
            tokens = tokenizer.tokenize(text, text_scope=scope)
        if tokens is not None:
            tokens.validate(text, scope)
        else:
            issue(request_id, "TOKENIZATION_MISSING")
        digest = _digest(text)
        versions = context_versions[session]
        versions.setdefault(digest, len(versions) + 1)
        identities = extra.get("agentmon", {})
        timing = extra.get("request_time_ns")
        req = {
            "request_id": request_id, "record_index": record_index, "order_index": ordinal,
            "session_order_index": session_indices[session], "session_id": session,
            "conversation_id": conversation,
            "question_id": record.get("question_id"), "order_basis": order,
            "request_time_ns": timing, "clock_domain": extra.get("clock_domain"),
            "model_family": extra.get("model_family"), "condition": extra.get("condition"),
            "context_version": versions[digest], "text_scope": scope, "text_sha256": digest,
            "text_chars": len(text), "context_in_prompt_status": context_status,
            "context_span_in_text": context_span,
            "token_count": len(tokens.input_ids) if tokens else None,
            "tokenizer_id": tokens.tokenizer_id if tokens else None,
            "tokenization_source": tokens.source if tokens else None,
            "agentmon": identities,
        }
        session_indices[session] += 1
        requests.append(req)
        payloads.append({"request_id": request_id, "text_scope": scope, "text": text,
                         "query": record.get("query"), "formatted_context": context,
                         "retrieved_contents": [r.get("content") for r in record["results"]],
                         "input_ids": list(tokens.input_ids) if tokens else None,
                         "offsets": list(tokens.offsets) if tokens else None})
        req_chunks = []
        for rank, result in enumerate(record["results"]):
            body = result.get("content")
            if not isinstance(body, str) or not body:
                issue(request_id, "EMPTY_OR_INVALID_CHUNK", rank)
                continue
            meta = result.get("metadata") or {}
            subject, memory_id = meta.get("subject"), meta.get("memory_id")
            if memory_id is not None:
                key, basis = _identity(session, "memory_id", str(memory_id)), "EXPLICIT_MEMORY_ID"
            elif isinstance(subject, str) and subject.strip():
                key, basis = _identity(session, "subject", subject.strip()), "HEURISTIC_SUBJECT"
            else:
                key, basis = _identity(session, "body", _digest(body)), "BODY_ONLY_NO_VERSION_LINK"
                issue(request_id, "MEMORY_ID_MISSING_BODY_ONLY", rank)
            versions = memory_versions[key]
            body_digest = _digest(body)
            versions.setdefault(body_digest, len(versions) + 1)
            local_span, location, count = locate(
                context, body, extra.get("chunk_spans", {}).get(str(rank)), subject)
            if local_span is None:
                issue(request_id, "CHUNK_" + location, rank)
            span = ([context_span[0] + local_span[0], context_span[0] + local_span[1]]
                    if context_span is not None and local_span is not None else None)
            alignment = align_span(tokens, span)
            row = {"request_id": request_id, "session_id": session, "result_rank": rank,
                   "chunk_id": f"{request_id}:chunk:{rank}", "memory_key": key,
                   "memory_identity_basis": basis, "memory_version": versions[body_digest],
                   "subject": subject, "content_sha256": body_digest, "content_chars": len(body),
                   "context_char_span": local_span, "text_char_span": span,
                   "location_status": location, "occurrence_count": count,
                   "token_alignment": alignment, "text_scope": scope,
                   "preceding_memory_versions": None}
            if alignment["status"] == "BOUNDARY_CROSSING":
                issue(request_id, "TOKEN_BOUNDARY_CROSSING", rank)
            elif alignment["status"] == "NO_TOKEN_OVERLAP":
                issue(request_id, "NO_TOKEN_OVERLAP", rank)
            req_chunks.append(row)
        located = sorted((c for c in req_chunks if c["context_char_span"] is not None),
                         key=lambda c: (c["context_char_span"][0], c["result_rank"]))
        for c in located:
            c["preceding_memory_versions"] = [
                [p["memory_key"], p["content_sha256"]] for p in located
                if p["context_char_span"][0] < c["context_char_span"][0]]
        chunks.extend(req_chunks)

    transitions = _transitions(requests, chunks, payloads, issues, max_edit_units)
    context_changes = _context_changes(requests, payloads, max_edit_units)
    return {"requests": requests, "chunks": chunks, "payloads": payloads,
            "transitions": transitions, "context_changes": context_changes, "issues": issues}


def _transitions(requests, chunks, payloads, issues, max_edit_units):
    reqs = {r["request_id"]: r for r in requests}
    texts = {p["request_id"]: p for p in payloads}
    grouped = defaultdict(lambda: defaultdict(list))
    for c in chunks:
        grouped[c["memory_key"]][c["request_id"]].append(c)
    output = []
    for key, request_chunks in grouped.items():
        ordered = sorted(request_chunks, key=lambda rid: reqs[rid]["order_index"])
        # Keep ambiguous occurrences in the sequence: never jump over them and invent adjacency.
        for old_id, new_id in zip(ordered, ordered[1:]):
            a, b = request_chunks[old_id], request_chunks[new_id]
            if len(a) != 1 or len(b) != 1:
                issues.append({"request_id": new_id, "code": "REPEATED_MEMORY_IN_REQUEST",
                               "memory_key": key})
                continue
            a, b = a[0], b[0]
            ra, rb, pa, pb = reqs[old_id], reqs[new_id], texts[old_id], texts[new_id]
            sa, sb = a["text_char_span"], b["text_char_span"]
            same_scope = ra["text_scope"] == rb["text_scope"]
            located = sa is not None and sb is not None and same_scope
            content_same = a["content_sha256"] == b["content_sha256"]
            prefix_same = (a["preceding_memory_versions"] == b["preceding_memory_versions"]
                           if located else None)
            position_same = sa[0] == sb[0] if located else None
            if not located:
                classification = "UNRESOLVED_ALIGNMENT"
            elif not content_same:
                classification = "CONTENT_CHANGED"
            elif prefix_same and position_same:
                classification = "ALL_SAME_MEMORY_CONTEXT"
            elif not prefix_same and not position_same:
                classification = "SAME_CONTENT_PREFIX_AND_POSITION_CHANGED"
            elif not prefix_same:
                classification = "SAME_CONTENT_PREFIX_CHANGED"
            else:
                classification = "SAME_CONTENT_POSITION_CHANGED"
            ta, tb = a["token_alignment"], b["token_alignment"]
            token_comparable = (located and ta["status"] == tb["status"] == "EXACT_BOUNDARIES"
                                and ra["tokenizer_id"] == rb["tokenizer_id"]
                                and ra["tokenization_source"] == rb["tokenization_source"])
            token_map = {"status": "UNAVAILABLE", "blocks": []}
            if token_comparable:
                ia = pa["input_ids"][slice(*ta["token_span"])]
                ib = pb["input_ids"][slice(*tb["token_span"])]
                token_map = edit_map(ia, ib, max_units=max_edit_units)
                tokens_same = ia == ib
            else:
                tokens_same = None
            elapsed = (rb["request_time_ns"] - ra["request_time_ns"]
                       if ra["order_basis"] == "observed" else None)
            output.append({
                "transition_id": f"transition:{len(output)}", "memory_key": key,
                "old_request_id": old_id, "new_request_id": new_id,
                "old_chunk_id": a["chunk_id"], "new_chunk_id": b["chunk_id"],
                "classification": classification, "memory_identity_basis": a["memory_identity_basis"],
                "content_same": content_same, "memory_prefix_same": prefix_same,
                "position_same": position_same, "text_scope": ra["text_scope"] if same_scope else None,
                "full_text_prefix_same": pa["text"][:sa[0]] == pb["text"][:sb[0]] if located else None,
                "old_char_span": sa, "new_char_span": sb,
                "old_token_span": ta["token_span"], "new_token_span": tb["token_span"],
                "tokens_same": tokens_same,
                "token_evidence": ra["tokenization_source"] if token_comparable else None,
                "character_edit_map": edit_map(pa["text"][slice(*sa)], pb["text"][slice(*sb)],
                                               max_units=max_edit_units) if located else {"status": "UNAVAILABLE", "blocks": []},
                "token_edit_map": token_map,
                "intervening_session_requests": rb["session_order_index"] - ra["session_order_index"] - 1,
                "elapsed_ns": elapsed, "order_basis": ra["order_basis"],
                "timestamp_tied": elapsed == 0 if elapsed is not None else None,
                "mapping_semantics": "TEXTUAL_ONLY_NOT_STATE_MAPPING",
                "eligible_for_state_alignment_review": bool(token_comparable and ra["text_scope"] == "full_prompt"
                                                            and ra["tokenization_source"] != "synthetic"),
            })
    return output


def _context_changes(requests, payloads, max_edit_units):
    payload = {p["request_id"]: p for p in payloads}
    previous, changes = {}, []
    for r in requests:
        old = previous.get(r["session_id"])
        previous[r["session_id"]] = r
        if old is None:
            continue
        a, b = payload[old["request_id"]], payload[r["request_id"]]
        same_scope = old["text_scope"] == r["text_scope"]
        changes.append({"old_request_id": old["request_id"], "new_request_id": r["request_id"],
                        "session_id": r["session_id"], "order_basis": r["order_basis"],
                        "text_scope": r["text_scope"] if same_scope else None,
                        "character_edit_map": edit_map(a["text"], b["text"], max_units=max_edit_units)
                        if same_scope else {"status": "SCOPE_MISMATCH", "blocks": []},
                        "mapping_semantics": "TEXTUAL_ONLY_NOT_STATE_MAPPING"})
    return changes
