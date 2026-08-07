from __future__ import annotations

from scripts.sug_ruby import (
    RUBY_REVIEW_SCHEMA,
    apply_review_patches,
    candidate_ruby_tokens,
    fill_missing_project_ruby,
    is_pure_katakana,
    iter_sug_ruby_spans,
    span_hash,
    sug_hash,
    timing_fingerprint,
    validate_review_sidecar,
    validate_sug_ruby,
)
from strange_uta_game.backend.domain import (
    Character,
    Project,
    ProjectMetadata,
    Ruby,
    RubyPart,
    Sentence,
)


def _raw_document(
    text: str,
    readings: dict[int, str],
    *,
    links: dict[int, bool] | None = None,
    language: str = "ja",
) -> dict:
    links = links or {}
    characters = []
    for index, char in enumerate(text):
        character = {
            "char": char,
            "check_count": 0 if char.isspace() else 1,
            "timestamps": [] if char.isspace() else [1_000 + index * 100],
            "linked_to_next": bool(links.get(index, False)),
        }
        if index in readings:
            character["ruby"] = {
                "parts": [{"text": readings[index], "offset_ms": 0}]
            }
        characters.append(character)
    return {
        "id": "ruby-fixture",
        "metadata": {"language": language},
        "sentences": [{"id": "sentence-1", "characters": characters}],
    }


def _review_sidecar(
    document: dict,
    *,
    review_status: str = "human-reviewed",
    source: str = "human-review",
    confidence: float | None = 1.0,
    surface: str | None = None,
    after_hash: str | None = None,
) -> dict:
    span = iter_sug_ruby_spans(document)[0]
    record = {
        "sentence_id": span.sentence_id,
        "start": span.start,
        "end": span.end,
        "surface": surface if surface is not None else span.surface,
        "source": source,
        "review_status": review_status,
        "confidence": confidence,
        "after_hash": (
            after_hash
            if after_hash is not None
            else span_hash(document, 0, span.start, span.end)
        ),
    }
    return {
        "schema": RUBY_REVIEW_SCHEMA,
        "sug_hash_after": sug_hash(document),
        "records": [record],
    }


def test_fill_missing_does_not_overwrite_existing_canonical_ruby():
    existing = Ruby(parts=[RubyPart(text="\u3042\u3081")])
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[
            Character(char="\u96e8", ruby=existing, check_count=1, timestamps=[1_000]),
            Character(char="\u597d", check_count=1, timestamps=[1_100]),
        ],
    )
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )
    before_timing = timing_fingerprint(project)

    class Helper:
        def ruby(self, char: str, language: str):
            assert language == "ja"
            return Ruby(parts=[RubyPart(text="\u3059")]) if char == "\u597d" else None

    records = fill_missing_project_ruby(project, Helper())

    assert len(records) == 1
    assert sentence.characters[0].ruby is existing
    assert sentence.characters[0].ruby.text == "\u3042\u3081"
    assert sentence.characters[1].ruby.text == "\u3059"
    assert timing_fingerprint(project) == before_timing
    assert sentence.characters[0].check_count == sentence.characters[1].check_count == 1


def test_pure_katakana_never_receives_generated_ruby():
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[Character(char=char, timestamps=[1_000]) for char in "ヵー・ㇰ"],
    )
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )

    class Helper:
        def ruby(self, _char: str, language: str):
            assert language == "ja"
            return Ruby(parts=[RubyPart(text="ふりがな")])

    assert is_pure_katakana("ヵー・ㇰ") is True
    assert fill_missing_project_ruby(project, Helper()) == []
    assert all(character.ruby is None for character in sentence.characters)


def test_whole_sentence_fill_preserves_words_existing_ruby_and_timing():
    existing = Ruby(parts=[RubyPart(text="あめ")])
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[
            Character(char=char, check_count=1, timestamps=[1_000 + index * 100])
            for index, char in enumerate("今日雨カナ")
        ],
    )
    sentence.characters[2].ruby = existing
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )
    before_timing = timing_fingerprint(project)

    class SentenceService:
        def apply_to_sentence(self, analyzed, **kwargs):
            assert analyzed.text == "今日雨カナ"
            assert kwargs == {
                "keep_existing_timetags": True,
                "only_noruby": True,
                "apply_user_dict": True,
            }
            analyzed.characters[0].ruby = Ruby(parts=[RubyPart(text="きょう")])
            analyzed.characters[0].linked_to_next = True
            analyzed.characters[2].ruby = Ruby(parts=[RubyPart(text="う")])
            analyzed.characters[3].ruby = Ruby(parts=[RubyPart(text="か")])
            analyzed.characters[3].linked_to_next = True

    records = fill_missing_project_ruby(project, SentenceService())

    assert [(record["start"], record["end"]) for record in records] == [(0, 2)]
    assert records[0]["source"] == "project-auto-check"
    assert records[0]["evidence"] == [
        "whole-sentence-tokenizer",
        "project-dictionary",
    ]
    assert sentence.characters[0].ruby.text == "きょう"
    assert sentence.characters[0].linked_to_next is True
    assert sentence.characters[1].ruby is None
    assert sentence.characters[1].linked_to_next is False
    assert sentence.characters[2].ruby is existing
    assert sentence.characters[2].ruby.text == "あめ"
    assert all(character.ruby is None for character in sentence.characters[3:])
    assert timing_fingerprint(project) == before_timing


def test_existing_pure_katakana_ruby_is_ignored_without_mutating_source():
    document = _raw_document("カ・ナ", {0: "か", 2: "な"})
    before = sug_hash(document)

    assert iter_sug_ruby_spans(document) == []
    assert validate_sug_ruby(document) == []
    assert sug_hash(document) == before
    assert document["sentences"][0]["characters"][0]["ruby"]["parts"][0]["text"] == "か"


def test_agent_patch_preserves_timestamps_check_count_and_release_point():
    document = _raw_document("\u597d", {0: "\u304b"})
    character = document["sentences"][0]["characters"][0]
    before = {
        "timestamps": list(character["timestamps"]),
        "check_count": character["check_count"],
        "sentence_end_ts": character.get("sentence_end_ts"),
    }
    result = apply_review_patches(
        document,
        [
            {
                "sentence_id": "sentence-1",
                "start": 0,
                "end": 1,
                "surface": "\u597d",
                "reading": "\u3059",
                "review_status": "ai-approved",
                "confidence": 0.99,
                "source": "agent",
            }
        ],
        sidecar={
            "records": [
                {
                    "sentence_id": "sentence-1",
                    "start": 0,
                    "end": 1,
                    "source": "dictionary",
                }
            ]
        },
    )

    assert result["unresolved"] == []
    assert result["timing_unchanged"] is True
    assert character["timestamps"] == before["timestamps"]
    assert character["check_count"] == before["check_count"]
    assert character.get("sentence_end_ts") == before["sentence_end_ts"]
    assert character["ruby"]["parts"][0]["text"] == "\u3059"


def test_low_confidence_and_conflict_are_fail_closed():
    document = _raw_document("\u597d", {0: "\u304b"})
    before = sug_hash(document)
    patches = [
        {
            "sentence_id": "sentence-1",
            "start": 0,
            "end": 1,
            "reading": "\u3059",
            "review_status": "low-confidence",
            "confidence": 0.20,
        }
    ]

    result = apply_review_patches(document, patches)

    assert result["changes"] == []
    assert result["unresolved"][0]["reason"] == "low-confidence"
    assert sug_hash(document) == before


def test_validate_review_sidecar_accepts_human_approval_states():
    document = _raw_document("好", {0: "す"})

    for review_status in ("human-reviewed", "human-locked"):
        assert (
            validate_review_sidecar(
                document,
                _review_sidecar(document, review_status=review_status),
            )
            == []
        )


def test_validate_review_sidecar_fails_closed_for_missing_or_stale_sidecar():
    document = _raw_document("好", {0: "す"})

    assert validate_review_sidecar(document, None)

    missing_hash = _review_sidecar(document)
    missing_hash.pop("sug_hash_after")
    assert validate_review_sidecar(document, missing_hash)

    stale = _review_sidecar(document)
    stale["sug_hash_after"] = "stale-sug-hash"
    assert validate_review_sidecar(document, stale)

    wrong_schema = _review_sidecar(document)
    wrong_schema["schema"] = "strange-utagame-ruby-review/v0"
    assert validate_review_sidecar(document, wrong_schema)


def test_validate_review_sidecar_requires_latest_exact_span_and_hash():
    document = _raw_document("好", {0: "す"})

    wrong_surface = _review_sidecar(document, surface="別")
    errors = validate_review_sidecar(document, wrong_surface)
    assert any("surface mismatch" in error for error in errors)

    wrong_hash = _review_sidecar(document, after_hash="old-span-hash")
    errors = validate_review_sidecar(document, wrong_hash)
    assert any("after_hash mismatch" in error for error in errors)

    latest_blocked = _review_sidecar(document)
    latest_blocked["records"].append(
        {**latest_blocked["records"][0], "review_status": "unresolved"}
    )
    errors = validate_review_sidecar(document, latest_blocked)
    assert any("blocked" in error for error in errors)


def test_validate_review_sidecar_rejects_machine_and_low_confidence_records():
    document = _raw_document("好", {0: "す"})
    cases = [
        {"review_status": "machine-fill"},
        {"review_status": "blocked"},
        {"review_status": "low-confidence"},
        {"source": "machine-fill"},
        {"source": " machine-fill "},
        {"review_status": "ai-approved", "confidence": 0.20},
        {"review_status": "ai-approved", "confidence": True},
        {"review_status": "ai-approved", "confidence": False},
    ]

    for overrides in cases:
        sidecar = _review_sidecar(document, **overrides)
        assert validate_review_sidecar(document, sidecar)


def test_validate_review_sidecar_requires_source_and_confidence():
    document = _raw_document("\u597d", {0: "\u3059"})
    for source, confidence in (
        (None, 1.0),
        ("", 1.0),
        ("   ", 1.0),
        ("human-review", None),
    ):
        sidecar = _review_sidecar(document, confidence=confidence)
        if source is None:
            sidecar["records"][0].pop("source")
        else:
            sidecar["records"][0]["source"] = source
        assert validate_review_sidecar(document, sidecar)


def test_apply_review_patches_requires_explicit_source_and_numeric_confidence():
    missing_source_result = apply_review_patches(
        _raw_document("\u597d", {}),
        [
            {
                "sentence_id": "sentence-1",
                "start": 0,
                "end": 1,
                "surface": "\u597d",
                "reading": "\u3059",
                "review_status": "ai-approved",
                "confidence": 0.99,
            }
        ],
    )
    assert missing_source_result["unresolved"][0]["reason"] == "invalid-review-source"

    cases = [
        ({"source": None}, "invalid-review-source"),
        ({"source": " machine-fill "}, "invalid-review-source"),
        ({"confidence": True}, "invalid-confidence"),
        ({"confidence": False}, "invalid-confidence"),
    ]
    for overrides, expected_reason in cases:
        document = _raw_document("\u597d", {})
        patch = {
            "sentence_id": "sentence-1",
            "start": 0,
            "end": 1,
            "surface": "\u597d",
            "reading": "\u3059",
            "review_status": "ai-approved",
            "confidence": 0.99,
            "source": "agent-review",
            **overrides,
        }
        result = apply_review_patches(document, [patch])
        assert result["changes"] == []
        assert result["unresolved"][0]["reason"] == expected_reason


def test_validate_review_sidecar_allows_historical_machine_fill_provenance():
    document = _raw_document("好", {0: "す"})
    sidecar = _review_sidecar(
        document,
        review_status="ai-approved",
        source="agent-review",
        confidence=0.99,
    )
    current_record = sidecar["records"][0]
    sidecar["records"].insert(
        0,
        {
            **current_record,
            "review_status": "machine-fill",
            "source": "machine-fill",
        },
    )

    assert validate_review_sidecar(document, sidecar) == []


def test_synthetic_reviewed_japanese_examples_are_canonical_spans():
    cases = [
        ("甲かな", {0: "こう"}, {}, "甲", "こう"),
        ("乙丙", {0: "おつ", 1: "へい"}, {0: True}, "乙丙", "おつへい"),
    ]
    for text, readings, links, surface, reading in cases:
        document = _raw_document(text, readings, links=links)
        spans = iter_sug_ruby_spans(document)
        assert [(span.surface, span.reading) for span in spans] == [(surface, reading)]
        assert validate_sug_ruby(document) == []


def test_zh_and_en_documents_cannot_carry_canonical_ruby():
    for language in ("zh", "en"):
        document = _raw_document("\u597d", {0: "\u3059"}, language=language)
        assert validate_sug_ruby(document) == [
            f"ruby is disabled for language {language!r}"
        ]
        result = apply_review_patches(document, [])
        assert result["changes"] == []
        assert result["unresolved"][0]["reason"] == "ruby-disabled-language"


def test_candidate_is_generic_and_rejects_non_japanese_or_katakana():
    tokens = candidate_ruby_tokens("猫")
    assert tokens
    assert all(token.text == "猫" and token.reading for token in tokens)
    assert candidate_ruby_tokens("123") == []
    assert candidate_ruby_tokens("ヵー・ㇰ") == []
    assert candidate_ruby_tokens("好", language="zh") == []
    assert candidate_ruby_tokens("hello", language="en") == []
