from __future__ import annotations

import unicodedata

import pytest

from scripts import sug_ruby
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


def test_pinned_sudachi_dictionary_finds_adjacent_kanji_word_boundaries():
    sug_ruby._sudachi_segmenter.cache_clear()

    assert sug_ruby._sudachi_kanji_link_positions("今年来年 一番好き") == frozenset(
        {0, 2, 5}
    )


def test_whole_sentence_fill_projects_sudachi_words_without_cross_word_links(
    monkeypatch,
):
    text = "今年来年一番好き"
    readings = ("こ", "とし", "らい", "ねん", "いち", "ばん", "す", None)
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[
            Character(char=char, check_count=1, timestamps=[1_000 + index * 100])
            for index, char in enumerate(text)
        ],
    )
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )

    class SentenceService:
        def apply_to_sentence(self, analyzed, **_kwargs):
            for character, reading in zip(
                analyzed.characters, readings, strict=True
            ):
                if reading:
                    character.ruby = Ruby(parts=[RubyPart(text=reading)])

    monkeypatch.setattr(
        sug_ruby,
        "_sudachi_kanji_link_positions",
        lambda value: frozenset({0, 2, 4}) if value == text else None,
    )

    fill_missing_project_ruby(project, SentenceService())

    assert [character.linked_to_next for character in sentence.characters] == [
        True,
        False,
        True,
        False,
        True,
        False,
        False,
        False,
    ]
    assert [
        (span.surface, span.reading) for span in iter_sug_ruby_spans(project)
    ] == [
        ("今年", "ことし"),
        ("来年", "らいねん"),
        ("一番", "いちばん"),
        ("好", "す"),
    ]


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("(", ")"), ("（", "）"), ("「", "」"), ("『", "』"), ("《", "》")],
)
def test_whole_sentence_fill_breaks_ruby_groups_at_paired_punctuation(opening, closing):
    text = f"今日{opening}きょう{closing}晴{opening}は{closing}れる明日"
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[
            Character(char=char, check_count=1, timestamps=[1_000 + index * 100])
            for index, char in enumerate(text)
        ],
    )
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )
    before_timing = timing_fingerprint(project)

    class SentenceService:
        def apply_to_sentence(self, analyzed, **kwargs):
            assert analyzed.text == text
            boundary_indices = [
                index
                for index, char in enumerate(analyzed.text)
                if char in {opening, closing}
            ]
            for index in boundary_indices:
                analyzed.characters[index].linked_to_next = True
                analyzed.characters[index - 1].linked_to_next = True

            analyzed.characters[0].ruby = Ruby(parts=[RubyPart(text="きょう")])
            analyzed.characters[0].linked_to_next = True

            hare = text.index("晴")
            analyzed.characters[hare].ruby = Ruby(parts=[RubyPart(text="は")])
            analyzed.characters[hare].linked_to_next = True

            tomorrow = text.index("明日")
            analyzed.characters[tomorrow].ruby = Ruby(parts=[RubyPart(text="あ")])
            analyzed.characters[tomorrow].linked_to_next = True
            analyzed.characters[tomorrow + 1].ruby = Ruby(parts=[RubyPart(text="した")])

    records = fill_missing_project_ruby(project, SentenceService())

    assert sentence.text == text
    assert [(span.surface, span.reading) for span in iter_sug_ruby_spans(project)] == [
        ("今日", "きょう"),
        ("晴", "は"),
        ("明日", "あした"),
    ]
    assert sentence.characters[0].linked_to_next is True
    assert sentence.characters[text.index("晴")].linked_to_next is False
    assert [record["surface"] for record in records] == ["今日", "晴", "明日"]
    assert timing_fingerprint(project) == before_timing


@pytest.mark.parametrize(
    ("separator", "category"),
    [
        ("―", "Pd"),
        ("・", "Po"),
        ("、", "Po"),
        ("。", "Po"),
        ("〜", "Pd"),
        ("～", "Sm"),
        ("♪", "So"),
    ],
)
def test_whole_sentence_fill_breaks_ruby_groups_at_punctuation_and_symbols(
    separator, category
):
    text = f"晴{separator}れる明日"
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[Character(char=char, timestamps=[1_000]) for char in text],
    )
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )

    class SentenceService:
        def apply_to_sentence(self, analyzed, **kwargs):
            analyzed.characters[0].ruby = Ruby(parts=[RubyPart(text="は")])
            analyzed.characters[0].linked_to_next = True
            analyzed.characters[1].linked_to_next = True
            tomorrow = text.index("明日")
            analyzed.characters[tomorrow].ruby = Ruby(parts=[RubyPart(text="あ")])
            analyzed.characters[tomorrow].linked_to_next = True
            analyzed.characters[tomorrow + 1].ruby = Ruby(parts=[RubyPart(text="した")])

    records = fill_missing_project_ruby(project, SentenceService())

    assert unicodedata.category(separator) == category
    assert [(span.surface, span.reading) for span in iter_sug_ruby_spans(project)] == [
        ("晴", "は"),
        ("明日", "あした"),
    ]
    assert sentence.characters[0].linked_to_next is False
    assert [record["surface"] for record in records] == ["晴", "明日"]


@pytest.mark.parametrize(
    ("boundary", "expected_category"),
    [(" ", None), ("・", "Po"), ("♪", "So")],
)
def test_whole_sentence_fill_rejects_ruby_assigned_to_boundary_character(
    boundary, expected_category
):
    text = f"前{boundary}後"
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[
            Character(
                char=char,
                check_count=0 if char.isspace() else 1,
                timestamps=[] if char.isspace() else [1_000],
            )
            for char in text
        ],
    )
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )
    before_links = [character.linked_to_next for character in sentence.characters]
    before_timing = timing_fingerprint(project)

    class SentenceService:
        def apply_to_sentence(self, analyzed, **kwargs):
            analyzed.characters[0].linked_to_next = True
            analyzed.characters[1].ruby = Ruby(parts=[RubyPart(text="きょうかい")])
            analyzed.characters[1].linked_to_next = True

    records = fill_missing_project_ruby(project, SentenceService())

    if expected_category is None:
        assert boundary.isspace()
    else:
        assert unicodedata.category(boundary) == expected_category
    assert records == []
    assert sentence.characters[1].ruby is None
    assert [character.linked_to_next for character in sentence.characters] == before_links
    assert timing_fingerprint(project) == before_timing


@pytest.mark.parametrize(
    ("suffix", "link_positions", "expected_surface", "expected_link"),
    [
        ("る", frozenset(), "語", False),
        ("ー", frozenset(), "語", False),
        ("ｰ", frozenset(), "語", False),
        ("々", frozenset({0}), "語々", True),
    ],
)
def test_successful_sudachi_replaces_helper_links_at_non_kanji_boundaries(
    monkeypatch,
    suffix,
    link_positions,
    expected_surface,
    expected_link,
):
    monkeypatch.setattr(
        sug_ruby,
        "_sudachi_kanji_link_positions",
        lambda _text: link_positions,
    )
    text = f"語{suffix}"
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[Character(char=char, timestamps=[1_000]) for char in text],
    )
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )

    class SentenceService:
        def apply_to_sentence(self, analyzed, **kwargs):
            analyzed.characters[0].ruby = Ruby(parts=[RubyPart(text="ご")])
            analyzed.characters[0].linked_to_next = True

    records = fill_missing_project_ruby(project, SentenceService())

    assert [(span.surface, span.reading) for span in iter_sug_ruby_spans(project)] == [
        (expected_surface, "ご")
    ]
    assert sentence.characters[0].linked_to_next is expected_link
    assert [record["surface"] for record in records] == [expected_surface]


def test_whole_sentence_fill_preserves_existing_human_ruby_and_links():
    existing = Ruby(parts=[RubyPart(text="きょう")])
    text = "今日（きょう）"
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[Character(char=char, timestamps=[1_000]) for char in text],
    )
    sentence.characters[0].ruby = existing
    for index in (0, 1, 2, 5):
        sentence.characters[index].linked_to_next = True
    before_links = [character.linked_to_next for character in sentence.characters]
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )
    before_timing = timing_fingerprint(project)

    class SentenceService:
        def apply_to_sentence(self, analyzed, **kwargs):
            analyzed.characters[0].ruby = Ruby(parts=[RubyPart(text="machine")])
            for character in analyzed.characters:
                character.linked_to_next = True

    assert fill_missing_project_ruby(project, SentenceService()) == []
    assert sentence.characters[0].ruby is existing
    assert sentence.characters[0].ruby.text == "きょう"
    assert all(character.ruby is None for character in sentence.characters[1:])
    assert [character.linked_to_next for character in sentence.characters] == before_links
    assert timing_fingerprint(project) == before_timing


def test_whole_sentence_fill_restores_repeated_source_spaces():
    sentence = Sentence(
        id="sentence-1",
        singer_id="singer-1",
        characters=[
            Character(
                char=char,
                check_count=0 if char.isspace() else 1,
                timestamps=[] if char.isspace() else [1_000 + index * 100],
            )
            for index, char in enumerate("今日  明日")
        ],
    )
    project = Project(
        id="project-1",
        sentences=[sentence],
        metadata=ProjectMetadata(language="ja"),
    )
    before_timing = timing_fingerprint(project)

    class CollapsingSentenceService:
        def apply_to_sentence(self, analyzed, **kwargs):
            assert kwargs["keep_existing_timetags"] is True
            analyzed.characters.pop(3)
            assert analyzed.text == "今日 明日"
            start = analyzed.text.index("明")
            analyzed.characters[start].ruby = Ruby(parts=[RubyPart(text="あ")])
            analyzed.characters[start].linked_to_next = True
            analyzed.characters[start + 1].ruby = Ruby(parts=[RubyPart(text="した")])

    records = fill_missing_project_ruby(project, CollapsingSentenceService())

    assert sentence.text == "今日  明日"
    assert sentence.characters[4].ruby.text == "あ"
    assert sentence.characters[4].linked_to_next is True
    assert sentence.characters[5].ruby.text == "した"
    assert [record["surface"] for record in records] == ["明日"]
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
