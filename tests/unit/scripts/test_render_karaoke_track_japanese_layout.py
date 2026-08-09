"""Synthetic regression tests for language-neutral Japanese line fitting."""

from pathlib import Path

import pytest

from scripts import render_karaoke_track as renderer
from scripts.sug_ruby import is_pure_katakana, iter_sug_ruby_spans
from strange_uta_game.backend.domain import Ruby, RubyPart, Sentence


def _sentence(text: str) -> Sentence:
    sentence = Sentence.from_text(text, "singer")
    for index, character in enumerate(sentence.characters):
        character.add_timestamp(index * 100)
    return sentence


def _set_single_character_ruby(sentence: Sentence, index: int, reading: str) -> None:
    sentence.characters[index].set_ruby(Ruby(parts=[RubyPart(text=reading)]))


def _machine_ruby_sidecar(sentence: Sentence) -> dict[str, object]:
    return {
        "records": [
            {
                "sentence_id": span.sentence_id,
                "start": span.start,
                "end": span.end,
                "source": "project-auto-check",
                "review_status": "machine-fill",
            }
            for span in iter_sug_ruby_spans(sentence)
        ]
    }


def test_legacy_per_kanji_ruby_keeps_compound_on_one_display_line():
    sentence = Sentence.from_text("帰り道長い線路沿いを歩いて確認事項追加", "singer")
    sen = sentence.text.index("線")
    ro = sentence.text.index("路")
    for index, character in enumerate(sentence.characters):
        character.add_timestamp(index * 100 + (3_000 if index >= ro else 0))
    _set_single_character_ruby(sentence, sen, "せん")
    _set_single_character_ruby(sentence, ro, "ろ")

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=8,
        language="ja",
    )
    boundaries = {
        (left.text[-1], right.text[0])
        for left, right in zip(phrases, phrases[1:], strict=False)
    }

    assert ("線", "路") not in boundaries
    assert "".join(phrase.text for phrase in phrases) == sentence.text


def test_legacy_per_kanji_ruby_projects_as_one_word_level_ruby_span():
    sentence = _sentence("線路")
    _set_single_character_ruby(sentence, 0, "せん")
    _set_single_character_ruby(sentence, 1, "ろ")

    tokens = renderer._canonical_tokens_for_phrase(sentence, sentence)

    assert [(token.text, token.reading, token.start, token.end) for token in tokens] == [
        ("線路", "せんろ", 0, 2),
    ]


@pytest.mark.parametrize(
    ("text", "readings", "expected"),
    [
        ("今年来年", ("こと", "し", "らい", "ねん"), ("今", "年", "来", "年")),
        ("一番好き", ("いち", "ばん", "す", None), ("一", "番", "好")),
    ],
)
def test_machine_generated_adjacent_kanji_ruby_remains_separate(
    text: str,
    readings: tuple[str | None, ...],
    expected: tuple[str, ...],
):
    sentence = _sentence(text)
    for index, reading in enumerate(readings):
        if reading is not None:
            _set_single_character_ruby(sentence, index, reading)

    tokens = renderer._canonical_tokens_for_phrase(
        sentence,
        sentence,
        sidecar=_machine_ruby_sidecar(sentence),
    )

    assert tuple(token.text for token in tokens) == expected
    assert all(token.source == "project-auto-check" for token in tokens)
    assert all(token.review_status == "machine-fill" for token in tokens)


@pytest.mark.parametrize("reading", ["^", "^pause^"])
def test_placeholder_ruby_never_projects_as_a_word_level_span(reading: str):
    sentence = _sentence("線路")
    _set_single_character_ruby(sentence, 0, reading)
    _set_single_character_ruby(sentence, 1, reading)

    assert renderer._canonical_tokens_for_phrase(sentence, sentence) == []


def test_cjk_extension_h_is_recognized_as_kanji():
    assert renderer._is_kanji_character(chr(0x31350)) is True


def test_display_override_rejects_a_legacy_per_kanji_ruby_split():
    sentence = _sentence("前半を保持する線路の後半も保持する")
    line = sentence.text
    sen = line.index("線")
    ro = line.index("路")
    _set_single_character_ruby(sentence, sen, "せん")
    _set_single_character_ruby(sentence, ro, "ろ")

    with pytest.raises(ValueError, match="protected Japanese display unit"):
        renderer.split_sentence_for_display(
            sentence,
            max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
            language="ja",
            display_phrase_overrides={line: (line[:ro], line[ro:])},
        )


@pytest.mark.parametrize(
    ("runs", "max_chars"),
    [
        (("短句", "前前前線路後後後後後"), 8),
        (("前前前前前線路後後後", "短句"), 8),
    ],
)
def test_short_phrase_rebalancing_never_moves_a_boundary_inside_legacy_ruby(
    runs: tuple[str, str],
    max_chars: int,
):
    sentences = [_sentence(text) for text in runs]
    joined = [character for sentence in sentences for character in sentence.characters]
    sen = next(index for index, character in enumerate(joined) if character.char == "線")
    ro = next(index for index, character in enumerate(joined) if character.char == "路")
    joined_sentence = Sentence(singer_id="singer", characters=joined)
    _set_single_character_ruby(joined_sentence, sen, "せん")
    _set_single_character_ruby(joined_sentence, ro, "ろ")

    result = renderer._join_short_display_runs(
        [list(sentence.characters) for sentence in sentences],
        max_chars=max_chars,
        eligible_ruby_character_ids=(
            renderer._legacy_reviewed_single_ruby_character_ids(joined_sentence)
        ),
    )
    boundaries = {
        (left[-1].char, right[0].char)
        for left, right in zip(result, result[1:], strict=False)
    }

    assert ("線", "路") not in boundaries


@pytest.mark.parametrize("reading", ["^", "^pause^"])
def test_ruby_placeholders_do_not_protect_a_display_boundary(reading: str):
    sentence = _sentence("線路")
    _set_single_character_ruby(sentence, 0, reading)
    _set_single_character_ruby(sentence, 1, reading)

    assert renderer._is_protected_display_boundary(sentence.characters, 1) is False


def test_noneligible_valid_ruby_does_not_protect_a_display_boundary():
    sentence = _sentence("線路")
    _set_single_character_ruby(sentence, 0, "せん")
    _set_single_character_ruby(sentence, 1, "ろ")

    assert (
        renderer._is_protected_display_boundary(
            sentence.characters,
            1,
            eligible_ruby_character_ids=frozenset(),
        )
        is False
    )


def test_display_override_allows_a_source_whitespace_between_ruby_kanji():
    sentence = _sentence("前半を保持する線 路の後半も保持する")
    visible_text = sentence.text.replace(" ", "")
    sen = sentence.text.index("線")
    ro = sentence.text.index("路")
    _set_single_character_ruby(sentence, sen, "せん")
    _set_single_character_ruby(sentence, ro, "ろ")
    visible_ro = visible_text.index("路")

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        display_phrase_overrides={
            visible_text: (visible_text[:visible_ro], visible_text[visible_ro:]),
        },
    )

    assert [phrase.text for phrase in phrases] == [
        "前半を保持する線",
        "路の後半も保持する",
    ]


def test_compact_japanese_line_that_fits_keeps_soft_character_overrun(monkeypatch):
    sentence = _sentence("来週予定カレンダー共同編集")
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == [sentence.text]


def test_long_japanese_line_that_fits_still_splits_at_semantic_boundary(monkeypatch):
    sentence = _sentence("聞いたってきっと朝にはいつもいないんだろう")
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == [
        "聞いたってきっと朝には",
        "いつもいないんだろう",
    ]
    assert "".join(phrase.text for phrase in phrases) == sentence.text


def test_long_japanese_line_prefers_internal_spaces_even_when_width_fits(monkeypatch):
    sentence = _sentence("それでも もう一度 この手を 僕は伸ばしてみたよ")
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == [
        "それでももう一度",
        "この手を僕は伸ばしてみたよ",
    ]
    assert "".join(phrase.text for phrase in phrases) == sentence.text.replace(" ", "")


def test_compact_japanese_line_keeps_internal_space_as_semantic_gap(monkeypatch):
    sentence = _sentence("海が見たいって 君は言うよ")
    source_timestamps = {
        id(character): tuple(character.global_timestamps)
        for character in sentence.characters
        if not character.char.isspace()
    }
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == ["海が見たいって君は言うよ"]
    assert all(
        not character.char.isspace()
        for phrase in phrases
        for character in phrase.characters
    )
    assert renderer._semantic_gap_after_indices(
        sentence,
        phrases[0],
        language="ja",
    ) == frozenset({6})
    assert all(
        tuple(character.global_timestamps) == source_timestamps[id(character)]
        for character in phrases[0].characters
    )


def test_thirteen_visible_characters_prefer_source_space_before_width_fast_path(
    monkeypatch,
):
    sentence = _sentence("春風を待ってる 君と歩きたい")
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == [
        "春風を待ってる",
        "君と歩きたい",
    ]


def test_mid_sentence_continuation_never_starts_the_following_line(monkeypatch):
    sentence = _sentence("忘れないでいて けど 明日は歩き出せるから")
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == [
        "忘れないでいてけど",
        "明日は歩き出せるから",
    ]
    assert all(not phrase.text.startswith("けど") for phrase in phrases[1:])


def test_whitespace_split_never_moves_a_particle_across_the_source_boundary(monkeypatch):
    sentence = _sentence("冷たい雨が 降り出す前に 帰る場所を")
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == [
        "冷たい雨が降り出す前に",
        "帰る場所を",
    ]


def test_short_continuation_block_stays_with_the_preceding_phrase(monkeypatch):
    sentence = _sentence("この手で握りしめた かけらがひとつこぼれ落ちたよ けど")
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == [
        "この手で握りしめた",
        "かけらがひとつこぼれ落ちたよけど",
    ]


def test_whitespace_split_preserves_character_objects_and_timestamps(monkeypatch):
    sentence = _sentence("それでも もう一度 この手を 僕は伸ばしてみたよ")
    source_characters = [
        character for character in sentence.characters if not character.char.isspace()
    ]
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width - 20,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    output_characters = [
        character for phrase in phrases for character in phrase.characters
    ]
    assert output_characters == source_characters
    assert all(
        output is source
        for output, source in zip(output_characters, source_characters, strict=True)
    )
    assert "".join(phrase.text for phrase in phrases) == renderer._normalize_display_text(
        sentence.text
    )


def test_english_whitespace_behavior_is_unchanged():
    text = "This line keeps its source spaces"
    sentence = _sentence(text)

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
        language="en",
    )

    assert phrases == [sentence]
    assert phrases[0].text == text


def test_required_split_never_cuts_a_continuous_katakana_run():
    sentence = _sentence("予定カレンダー共同編集確認事項追加")

    runs = renderer._split_character_run(sentence.characters, max_chars=8)

    assert "".join(character.char for run in runs for character in run) == sentence.text
    cursor = 0
    for run in runs[:-1]:
        cursor += len(run)
        assert not (
            is_pure_katakana(sentence.characters[cursor - 1].char)
            and is_pure_katakana(sentence.characters[cursor].char)
        )


def test_required_split_never_cuts_a_canonical_ruby_span():
    sentence = _sentence("帰り道の長い線路沿いを歩いて確認事項追加")
    start = sentence.text.index("線路")
    sentence.characters[start].linked_to_next = True

    runs = renderer._split_character_run(sentence.characters, max_chars=8)

    cursor = 0
    for run in runs[:-1]:
        cursor += len(run)
        assert cursor != start + 1
        assert not sentence.characters[cursor - 1].linked_to_next


def test_required_split_never_starts_next_phrase_with_particle_when_other_candidates_are_protected(
    monkeypatch,
):
    sentence = _sentence("春夏秋冬東西南北を歩いて帰る場所へ")
    sentence.characters[8].timestamps[0] = 5_000
    monkeypatch.setattr(
        renderer,
        "_is_protected_display_boundary",
        lambda _characters, position, **_kwargs: position not in {6, 8},
    )

    runs = renderer._split_character_run(sentence.characters, max_chars=8)

    rendered_runs = ["".join(character.char for character in run) for run in runs]
    assert rendered_runs[0] == "春夏秋冬東西"
    assert "".join(rendered_runs) == sentence.text
    assert all(
        not run.startswith(renderer._BAD_DISPLAY_BOUNDARY_START_TOKENS)
        for run in rendered_runs[1:]
    )


def test_fifteen_character_split_keeps_particle_before_short_tail_and_preserves_ruby_span():
    sentence = _sentence("春夏秋冬東西南北空を歩いて帰る")
    ruby_start = 5
    _set_single_character_ruby(sentence, ruby_start, "にしみなみきたそら")
    for index in range(ruby_start, 8):
        sentence.characters[index].linked_to_next = True

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=8,
        language="ja",
    )
    rendered_runs = [phrase.text for phrase in phrases]

    assert len(sentence.characters) == 15
    assert rendered_runs == ["春夏秋冬東西南北空を", "歩いて帰る"]
    assert len(phrases[-1].characters) == 5
    assert all(
        not phrase.text.startswith(renderer._BAD_DISPLAY_BOUNDARY_START_TOKENS)
        for phrase in phrases[1:]
    )
    cursor = 0
    for phrase in phrases[:-1]:
        cursor += len(phrase.characters)
        assert sentence.characters[cursor - 1].linked_to_next is False
    assert "".join(rendered_runs) == sentence.text


def test_display_override_never_cuts_a_canonical_word_span():
    sentence = _sentence("明日もずっと信じ続けて歩いていく")
    sentence.characters[7].linked_to_next = True

    with pytest.raises(ValueError, match="protected Japanese display unit"):
        renderer.split_sentence_for_display(
            sentence,
            max_chars=renderer.WIDE_LAYOUT.max_phrase_chars,
            language="ja",
            display_phrase_overrides={
                "明日もずっと信じ続けて歩いていく": (
                    "明日もずっと信じ",
                    "続けて歩いていく",
                )
            },
        )


def test_long_pure_katakana_without_lexical_boundary_stays_intact():
    sentence = _sentence("アイウエオカキクケコサシスセソタチツテト")

    runs = renderer._split_character_run(sentence.characters, max_chars=8)

    assert ["".join(character.char for character in run) for run in runs] == [
        sentence.text
    ]


def test_split_finds_legal_boundary_beyond_preferred_window(monkeypatch):
    katakana = "アイウエオカキクケコサシスセソタチツテト"
    sentence = _sentence(f"{katakana}確認事項追加")
    monkeypatch.setattr(
        renderer,
        "_measured_text_span",
        lambda *args, **kwargs: renderer.WIDE_LAYOUT.slot_width + 1,
    )

    phrases = renderer.split_sentence_for_display(
        sentence,
        max_chars=8,
        language="ja",
        font_file=Path("synthetic-font.ttf"),
        layout=renderer.WIDE_LAYOUT,
    )

    assert [phrase.text for phrase in phrases] == [katakana, "確認事項追加"]
