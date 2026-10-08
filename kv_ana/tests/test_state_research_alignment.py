from copy import deepcopy

import pytest

from kvreuse_b5.state_research.alignment import Tokenization, align_span, edit_map, locate
from kvreuse_b5.state_research.fixtures import SyntheticCharacterTokenizer, synthetic_records
from kvreuse_b5.state_research.lifemem import prepare_records


def prepare(records, **kwargs):
    return prepare_records(records, dataset_id="test", **kwargs)


def test_no_tokenizer_fallback_and_no_fabricated_full_prompt():
    records = synthetic_records()
    for r in records:
        del r["research"]
    data = prepare(records)
    assert all(r["token_count"] is None and r["text_scope"] == "formatted_context" for r in data["requests"])
    assert all(t["tokens_same"] is None for t in data["transitions"])
    assert all(t["elapsed_ns"] is None for t in data["transitions"])


def test_original_inputs_untouched_and_payload_keeps_query():
    records = synthetic_records()
    before = deepcopy(records)
    data = prepare(records)
    assert records == before
    assert data["payloads"][0]["query"] == records[0]["query"]
    assert data["payloads"][0]["text"] == records[0]["research"]["full_prompt"]


def test_explicit_provider_is_supported_and_synthetic_cannot_be_real_ready():
    records = synthetic_records()
    for r in records:
        del r["research"]["tokenization"]
    data = prepare(records, tokenizer=SyntheticCharacterTokenizer())
    assert all(r["token_count"] > 0 for r in data["requests"])
    assert not any(t["eligible_for_state_alignment_review"] for t in data["transitions"])


def test_unicode_offsets_and_special_tokens_are_character_based():
    tokens = Tokenization((9, 1, 2, 9), ((0, 0), (0, 1), (1, 2), (0, 0)), "fixture", "provided", "full_prompt")
    tokens.validate("中α", "full_prompt")
    assert align_span(tokens, (0, 2)) == {"status": "EXACT_BOUNDARIES", "token_span": [1, 3]}


@pytest.mark.parametrize("offsets", [((0, 4),), ((1, 0),), ((-1, 1),)])
def test_bad_offsets_rejected(offsets):
    with pytest.raises(ValueError):
        Tokenization((1,), offsets, "fixture", "provided", "full_prompt").validate("abc", "full_prompt")


def test_reversed_token_offsets_rejected():
    with pytest.raises(ValueError, match="ordered"):
        Tokenization((1, 2), ((1, 3), (0, 2)), "fixture", "provided", "full_prompt").validate("abc", "full_prompt")


def test_unicode_byte_fallback_duplicate_offsets_keep_all_tokens():
    tokens = Tokenization((1, 2), ((0, 1), (0, 1)), "fixture", "provided", "full_prompt")
    tokens.validate("中", "full_prompt")
    assert align_span(tokens, (0, 1)) == {"status": "EXACT_BOUNDARIES", "token_span": [0, 2]}


def test_scope_mismatch_rejected():
    with pytest.raises(ValueError, match="text_scope"):
        Tokenization((1,), ((0, 1),), "fixture", "provided", "formatted_context").validate("a", "full_prompt")


def test_chunk_inside_token_is_not_exact():
    tokens = Tokenization((1,), ((0, 6),), "fixture", "provided", "full_prompt")
    assert align_span(tokens, (2, 5))["status"] == "BOUNDARY_CROSSING"


def test_duplicate_body_is_ambiguous_unless_explicit_or_unique_wrapper():
    assert locate("abc abc", "abc") == (None, "AMBIGUOUS", 2)
    assert locate("abc abc", "abc", [4, 7])[0] == (4, 7)
    assert locate("abc\nmemory: abc", "abc", subject="memory")[0] == (12, 15)
    with pytest.raises(ValueError, match="exact original body"):
        locate("abc", "abc", [1, 3])


def test_no_silent_newline_normalization():
    assert locate("a\nb", "a\r\nb")[1] == "MISSING"


def test_adjacent_pairs_not_all_pairs_and_version_change():
    data = prepare(synthetic_records())
    target = [t for t in data["transitions"] if '"memory"' in t["memory_key"]]
    assert len(target) == 4  # five occurrences, not ten combinations
    assert [t["classification"] for t in target][:3] == [
        "SAME_CONTENT_PREFIX_CHANGED", "SAME_CONTENT_PREFIX_AND_POSITION_CHANGED", "CONTENT_CHANGED"]
    edited = target[2]
    assert any(b["operation"] == "replace" for b in edited["character_edit_map"]["blocks"])


def test_memory_context_same_does_not_hide_changed_system_prompt():
    records = synthetic_records()
    # Make the changed system header equal length, leaving memory coordinates equal.
    records[4]["research"]["full_prompt"] = records[4]["research"]["full_prompt"].replace("SYSTEM changed", "OTHER!")
    records[4]["research"].pop("tokenization")
    data = prepare(records)
    row = next(t for t in data["transitions"] if '"memory"' in t["memory_key"] and t["new_request_id"] == "request-4")
    assert row["classification"] == "ALL_SAME_MEMORY_CONTEXT"
    assert row["full_text_prefix_same"] is False


def test_same_body_can_have_different_token_ids():
    records = synthetic_records()[:2]
    for r in records:
        r["research"]["tokenization"]["source"] = "provided"
        r["research"]["tokenization"]["input_ids"] = list(r["research"]["tokenization"]["input_ids"])
    index = records[1]["research"]["full_prompt"].index("共同记忆")
    records[1]["research"]["tokenization"]["input_ids"][index] += 100
    row = next(t for t in prepare(records)["transitions"] if '"memory"' in t["memory_key"])
    assert row["content_same"] is True
    assert row["tokens_same"] is False
    assert row["eligible_for_state_alignment_review"] is True


def test_different_tokenizer_ids_not_compared():
    records = synthetic_records()[:2]
    records[1]["research"]["tokenization"]["tokenizer_id"] = "different-vocab"
    assert all(t["tokens_same"] is None for t in prepare(records)["transitions"])


def test_missing_session_does_not_cross_link():
    records = synthetic_records()[:2]
    for r in records:
        del r["conversation_id"]
    assert prepare(records)["transitions"] == []


def test_same_memory_id_in_different_sessions_does_not_cross_link():
    records = synthetic_records()[:2]
    records[1]["conversation_id"] = "different-session"
    assert prepare(records)["transitions"] == []


def test_missing_subject_uses_body_identity_without_claiming_version_changes():
    records = synthetic_records()
    for r in records:
        r["results"][0]["metadata"] = {}
    data = prepare(records)
    assert any(c["memory_identity_basis"] == "BODY_ONLY_NO_VERSION_LINK" for c in data["chunks"])


def test_repeated_memory_in_request_does_not_arbitrarily_select_one():
    records = synthetic_records()[:3]
    records[1]["results"].append(deepcopy(records[1]["results"][1]))
    data = prepare(records)
    assert not [t for t in data["transitions"] if '"memory"' in t["memory_key"]]
    assert any(i["code"] == "REPEATED_MEMORY_IN_REQUEST" for i in data["issues"])


def test_context_duplicate_in_full_prompt_not_arbitrarily_selected():
    records = synthetic_records()[:1]
    context = records[0]["retrieval_metadata"]["formatted_context"]
    records[0]["research"] = {"full_prompt": context + context}
    data = prepare(records)
    assert all(c["text_char_span"] is None for c in data["chunks"])
    records[0]["research"]["context_span_in_prompt"] = [len(context), len(context) * 2]
    assert all(c["text_char_span"] is not None for c in prepare(records)["chunks"])


def test_edit_maps_cover_both_sequences_and_reconstruct_new():
    old, new = "abcXYZtail", "abcNEWtail!"
    blocks = edit_map(old, new)["blocks"]
    assert "".join(new[slice(*b["new_span"])] for b in blocks) == new
    assert "".join(old[slice(*b["old_span"])] for b in blocks) == old
    assert all(old[slice(*b["old_span"])] == new[slice(*b["new_span"])] for b in blocks if b["operation"] == "equal")
    assert edit_map(old, new, max_units=3)["status"] == "SIZE_LIMIT"
