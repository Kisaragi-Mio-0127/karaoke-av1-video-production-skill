"""Synthetic regression tests for language-neutral Japanese line fitting."""

from pathlib import Path

from scripts import render_karaoke_track as renderer
from scripts.sug_ruby import is_pure_katakana
from strange_uta_game.backend.domain import Sentence


def _sentence(text: str) -> Sentence:
    sentence = Sentence.from_text(text, "singer")
    for index, character in enumerate(sentence.characters):
        character.add_timestamp(index * 100)
    return sentence


def test_japanese_line_that_fits_is_not_split_by_character_count(monkeypatch):
    sentence = _sentence("来週予定カレンダー共同編集確認事項")
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
