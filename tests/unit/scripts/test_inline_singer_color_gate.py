"""Regression coverage for per-event singer colours in the ASS render gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "integration" / "strangeutagame" / "scripts"


def _load_renderer():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "public_render_vinyl_karaoke", SCRIPTS / "render_vinyl_karaoke.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def _write_inline_singer_ass(path: Path, *, cue_inline: bool) -> Path:
    hot = "849EE1"
    cue_color = f"\\1c&H00{hot}&\\2c&H00{hot}&" if cue_inline else ""
    path.write_text(
        "[Script Info]\n"
        "; Layout: wide-bottom\n"
        "PlayResX: 1920\nPlayResY: 1080\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Glow,HarmonyOS Sans SC,108,&H50{hot},&H70FFFFFF,&H90FFFFFF,"
        "&HFF000000,1,0,0,0,100,100,0,0,1,6,0,7,0,0,0,1\n"
        f"Style: Main,HarmonyOS Sans SC,108,&H00{hot},&H00FFFFFF,&H00000000,"
        "&H64000000,1,0,0,0,100,100,0,0,1,6,0,7,0,0,0,1\n"
        "Style: RubyGlow,HarmonyOS Sans SC,51,&H70F3F3F3,&H70F3F3F3,"
        "&HA0FFFFFF,&HFF000000,1,0,0,0,100,100,0,0,1,3,0,8,0,0,0,1\n"
        "Style: Ruby,HarmonyOS Sans SC,51,&H00F3F3F3,&H00F3F3F3,"
        "&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,3,0,8,0,0,0,1\n"
        "Style: CueDim,HarmonyOS Sans SC,39,&H68FFFFFF,&H68FFFFFF,"
        "&H80000000,&HFF000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1\n"
        f"Style: CueHot,HarmonyOS Sans SC,39,&H00{hot},&H00{hot},&H50000000,"
        "&HFF000000,1,0,0,0,100,100,0,0,1,4,0,5,0,0,0,1\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        f"Dialogue: 1,0:00:00.00,0:00:01.00,Glow,,0,0,0,,"
        f"{{\\an8\\pos(600,660)\\1c&H50{hot}&\\fs108\\fsp1.6\\k0\\kf100}}A\n"
        f"Dialogue: 2,0:00:00.00,0:00:01.00,Main,,0,0,0,,"
        f"{{\\an8\\pos(900,660)\\1c&H00{hot}&\\fs108\\fsp1.6\\k0\\kf100}}A\n"
        "Dialogue: 5,0:00:00.00,0:00:01.00,CueDim,,0,0,0,,"
        "{\\an5\\pos(800,609)}A\n"
        "Dialogue: 6,0:00:00.00,0:00:01.00,CueHot,,0,0,0,,"
        f"{{\\an5\\pos(850,609){cue_color}}}A\n",
        encoding="utf-8",
    )
    return path


def test_cue_hot_participates_in_inline_singer_colour_completeness(tmp_path: Path):
    renderer = _load_renderer()
    valid = renderer.validate_ass_for_render(
        _write_inline_singer_ass(tmp_path / "cue-inline.ass", cue_inline=True),
        "HarmonyOS Sans SC",
    )
    assert valid["ok"], valid["errors"]
    assert valid["gate"]["inline_singer_event_colors"] is True

    invalid = renderer.validate_ass_for_render(
        _write_inline_singer_ass(tmp_path / "cue-style-only.ass", cue_inline=False),
        "HarmonyOS Sans SC",
    )
    assert invalid["ok"] is False
    assert invalid["gate"]["inline_singer_event_colors"] is False
    assert any(
        record["style"] == "CueHot"
        for record in invalid["singer_event_colors"]
        if record["bgr"] is None
    )


def test_event_color_gate_rejects_wrong_paired_or_inactive_colors(tmp_path: Path):
    renderer = _load_renderer()
    valid_path = _write_inline_singer_ass(
        tmp_path / "event-colors.ass", cue_inline=True
    )
    valid_text = valid_path.read_text(encoding="utf-8")

    mutations = [
        (
            r"\1c&H50849EE1&\fs108",
            r"\1c&H5000FF00&\fs108",
            "paired_primary_colors_differ",
        ),
        (
            r"\1c&H00849EE1&\2c&H00849EE1&",
            r"\1c&H00849EE1&\2c&H0000FF00&",
            "cue_hot_primary_secondary_colors_differ",
        ),
        (
            "Style: Main,HarmonyOS Sans SC,108,&H00849EE1,&H00FFFFFF,",
            "Style: Main,HarmonyOS Sans SC,108,&H00849EE1,&H0000FF00,",
            "unhighlighted_color_must_be_white",
        ),
    ]
    for index, (before, after, reason) in enumerate(mutations):
        assert before in valid_text
        candidate = tmp_path / f"wrong-event-color-{index}.ass"
        candidate.write_text(valid_text.replace(before, after, 1), encoding="utf-8")

        invalid = renderer.validate_ass_for_render(
            candidate, "HarmonyOS Sans SC"
        )

        assert invalid["ok"] is False
        assert invalid["gate"]["inline_singer_event_colors"] is False
        assert any(reason in error for error in invalid["errors"])
