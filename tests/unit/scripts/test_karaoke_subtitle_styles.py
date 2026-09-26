"""Timing, colours and render-entry coverage for jumping subtitles."""

import re
from types import SimpleNamespace

import pytest

from scripts import karaoke_full_auto, karaoke_workflow
from scripts import render_karaoke_track as renderer
from scripts import run_karaoke_japanese_mms_workflow as japanese_mms
from scripts.karaoke_common.subtitle_styles import jump_glyph_events
from scripts.render_vinyl_karaoke import validate_ass_for_render
from scripts.run_karaoke_japanese_workflow import make_parser
from strange_uta_game.backend.domain import Ruby, RubyPart, Sentence


@pytest.fixture(autouse=True)
def _synthetic_font(monkeypatch):
    """Keep timing tests independent of fonts installed in a target checkout."""
    monkeypatch.setattr(
        renderer.ImageFont,
        "truetype",
        lambda _path, size: SimpleNamespace(getlength=lambda text: len(text) * size * 0.6),
    )
    monkeypatch.setattr(renderer, "verify_font", lambda *args, **kwargs: {"status": "pass"})


def _jump(**overrides):
    options = {
        "x": 100,
        "y": 660,
        "onset_ms": 1_000,
        "release_ms": 1_500,
        "event_start_ms": 800,
        "event_end_ms": 1_800,
        "font_size": 108,
        "outline_px": 6,
        "glow_blur": 12,
        "color_ass": "&H00563412",
        "glow_style": "Glow",
        "main_style": "Main",
        "glow_layer": 1,
        "main_layer": 2,
    }
    options.update(overrides)
    return jump_glyph_events("歌", **options)


def _time_ms(value):
    hours, minutes, seconds = value.split(":")
    return round((int(hours) * 3_600 + int(minutes) * 60 + float(seconds)) * 1_000)


def test_jump_has_contiguous_white_rise_fall_and_highlighted_rest():
    events = [line.split(",", 9) for line in _jump() if ",Main," in line]
    assert [(_time_ms(row[1]), _time_ms(row[2])) for row in events] == [
        (800, 1_000),
        (1_000, 1_120),
        (1_120, 1_320),
        (1_320, 1_800),
    ]
    assert r"\k20\k0" in events[0][9]
    assert r"\move(100,660,100,642,0,120)" in events[1][9]
    assert r"\move(100,642,100,660,0,200)" in events[2][9]
    assert r"\pos(100,660)" in events[3][9]
    assert all(r"\k0" in row[9] for row in events[1:])
    assert all(r"\1c&H00563412\2c&H00FFFFFF" in row[9] for row in events)
    assert all(r"\fad(0,0)" in row[9] for row in events[1:3])


@pytest.mark.parametrize(
    "onset,release,start,end",
    [
        (1_009, 1_019, 800, 1_800),
        (1_000, 1_500, 1_050, 1_240),
        (-100, 100, 0, 300),
        (2_000, 2_500, 0, 800),
    ],
)
def test_jump_short_notes_offsets_and_clipped_windows_have_no_gaps(
    onset, release, start, end
):
    rows = [
        line.split(",", 9)
        for line in _jump(
            onset_ms=onset,
            release_ms=release,
            event_start_ms=start,
            event_end_ms=end,
        )
        if ",Main," in line
    ]
    spans = [(_time_ms(row[1]), _time_ms(row[2])) for row in rows]
    assert spans[0][0] == start // 10 * 10
    assert spans[-1][1] == end // 10 * 10
    assert all(left < right for left, right in spans)
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))


@pytest.mark.parametrize(
    "language,text", [("ja", "春風")]
)
def test_jump_ass_keeps_source_timing_singer_colors_and_passes_render_gate(
    tmp_path, language, text
):
    sentence = Sentence.from_text(text, "singer")
    for index, character in enumerate(sentence.characters):
        character.add_timestamp(1_000 + index * 400)
    sentence.characters[-1].set_sentence_end_ts(4_000)
    if language == "ja":
        sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="はる")]))
        sentence.characters[1].set_ruby(Ruby(parts=[RubyPart(text="かぜ")]))
    project = SimpleNamespace(
        sentences=[sentence],
        singers=[
            SimpleNamespace(id="singer", color="#123456", is_default=True, group="")
        ],
        metadata=SimpleNamespace(language=language),
    )
    original_timing = [list(ch.global_timestamps) for ch in sentence.characters]
    ass_path = tmp_path / f"jump-{language}.ass"
    report = renderer.build_karaoke_ass(
        project,
        ass_path,
        font_file=renderer.SHARED_FONT_FILE,
        release_overrides={0: 4_000},
        layout=renderer.WIDE_LAYOUT,
        subtitle_style="jump",
        offset_ms=100,
    )
    ass = ass_path.read_text(encoding="utf-8")
    assert report["subtitle_style"] == "jump"
    assert r"\move(" in ass
    assert [list(ch.global_timestamps) for ch in sentence.characters] == original_timing
    gate = validate_ass_for_render(ass_path, renderer.FONT_FAMILY)
    assert gate["ok"], gate["errors"]
    ruby = [line for line in ass.splitlines() if line.startswith("Dialogue: 4,")]
    if language == "ja":
        assert len(ruby) == 1  # Adjacent kanji readings form one canonical span.
        # Source timing is shifted 100 ms; the display starts 200 ms before onset.
        assert r"\1c&H00563412\2c&H00FFFFFF\k20\kf300" in ruby[0]
        assert ",607)" in ruby[0]  # 18 px of travel reserved under the reading.
    else:
        assert ruby == []


def test_jump_ruby_uses_span_release_overrides_and_matching_singer_color():
    sentence = Sentence.from_text("春風", "singer")
    for index, ch in enumerate(sentence.characters):
        ch.add_timestamp(1_000 + index * 500)
    sentence.characters[0].linked_to_next = True
    sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="はるかぜ")]))
    events = renderer.ruby_events(
        sentence,
        event_start_ms=800,
        event_end_ms=2_500,
        lane=renderer.WIDE_LAYOUT.lanes[0],
        font_file=renderer.SHARED_FONT_FILE,
        main_font_size=108,
        tokens=renderer.canonical_ruby_tokens(sentence),
        subtitle_style="jump",
        release_ms=2_400,
        offset_ms=100,
        onset_overrides={0: 1_100},
        release_overrides={1: 2_000},
        character_colors=["#FF8040", "#FF8040"],
    )
    assert all(r"\1c&H004080FF\2c&H00FFFFFF\k40\kf90" in event for event in events)


def test_sweep_keeps_existing_stationary_ruby():
    sentence = Sentence.from_text("歌", "singer")
    sentence.characters[0].add_timestamp(1_000)
    sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="うた")]))
    events = renderer.ruby_events(
        sentence,
        event_start_ms=800,
        event_end_ms=2_000,
        lane=renderer.WIDE_LAYOUT.lanes[0],
        font_file=renderer.SHARED_FONT_FILE,
        main_font_size=108,
        tokens=renderer.canonical_ruby_tokens(sentence),
    )
    assert all(",625)" in event and r"\kf" not in event for event in events)


def test_workflow_passes_jump_style_to_renderer(tmp_path):
    args = make_parser().parse_args(
        [
            "--sug",
            "song.sug",
            "--audio",
            "song.flac",
            "--composition",
            "cover.png",
            "--output-dir",
            str(tmp_path),
            "--title",
            "Title",
            "--artist",
            "Artist",
            "--subtitle-style",
            "jump",
            "--output-mode",
            "subtitle-overlay",
        ]
    )
    config = karaoke_workflow.config_from_args(
        args,
        language="ja",
        layout="wide",
        pronunciation_validation="optional",
    )
    command = karaoke_workflow.build_ass_command(
        config,
        generated_vinyl=None,
        ass_path=tmp_path / "out.ass",
        report_path=tmp_path / "report.json",
        output_path=tmp_path / "out.mov",
        duration=3,
    )
    assert command[command.index("--subtitle-style") + 1] == "jump"


@pytest.mark.parametrize(
    "parser_factory",
    [
        karaoke_full_auto.build_parser,
        japanese_mms.make_parser,
    ],
)
def test_full_auto_and_staged_entries_accept_jump(parser_factory):
    parser = parser_factory()
    args, _ = parser.parse_known_args(
        [
            "--manifest",
            "album.json",
            "--song-id",
            "song",
            "--source",
            "canonical",
            "--output-dir",
            "out",
            "--subtitle-style",
            "jump",
            "--language",
            "zh",
        ]
    )
    assert args.subtitle_style == "jump"


def test_motion_endpoints_are_checked_by_render_gate(tmp_path):
    # Use a real generated file so this specifically tests the move endpoint.
    sentence = Sentence.from_text("歌", "singer")
    sentence.characters[0].add_timestamp(1_000)
    project = SimpleNamespace(
        sentences=[sentence],
        singers=[
            SimpleNamespace(id="singer", color="#123456", is_default=True, group="")
        ],
        metadata=SimpleNamespace(language="ja"),
    )
    output = tmp_path / "bad-motion.ass"
    renderer.build_karaoke_ass(
        project,
        output,
        font_file=renderer.SHARED_FONT_FILE,
        release_overrides={0: 2_000},
        layout=renderer.WIDE_LAYOUT,
        subtitle_style="jump",
    )
    ass = output.read_text(encoding="utf-8")
    ass = re.sub(r"(\\move\([^,]+,[^,]+,[^,]+,)642", r"\g<1>100", ass, count=1)
    output.write_text(ass, encoding="utf-8")
    gate = validate_ass_for_render(output, renderer.FONT_FAMILY)
    assert not gate["ok"]
    assert any(
        "explicit_positions_outside_layout_bounds" in error for error in gate["errors"]
    )
