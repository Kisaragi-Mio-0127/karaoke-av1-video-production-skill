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
