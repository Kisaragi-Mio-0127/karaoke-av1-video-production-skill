from __future__ import annotations

import hashlib
import io
import json
import math
import statistics
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import render_vinyl_karaoke as renderer

_ASS_STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding"
)


def test_formal_mp4_audio_args_are_aac_lc_320k():
    args = renderer._audio_codec_args("aac")

    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-profile:a") + 1] == "aac_low"
    assert args[args.index("-b:a") + 1] == "320k"
    assert "copy" not in args


def _write_gate_ass(
    path: Path,
    *,
    layout: str = "wide-bottom-en",
    main_event_sizes: tuple[int, int] = (54, 96),
    letter_spacing: str = r"\fsp1.6",
    inline_scale_x: str = "",
    secondary: bool = False,
    secondary_highlight_bgr: str | None = None,
) -> Path:
    """Write a small ASS fixture covering the renderer's publication contract."""

    is_wide = layout.startswith("wide")
    main_size = 96 if layout == "wide-bottom-en" else 108 if is_wide else 52
    ruby_size = 51 if is_wide else 24
    main_outline = 6 if is_wide else 4
    main_y = 600 if is_wide else 790
    cue_y = main_y - 16
    highlight_bgr = "849EE1"
    secondary_highlight_bgr = secondary_highlight_bgr or highlight_bgr
    styles = [
        f"Style: Glow,HarmonyOS Sans SC,{main_size},&H50{highlight_bgr},"
        f"&H70FFFFFF,&H90FFFFFF,&HFF000000,1,0,0,0,100,100,0,0,1,"
        f"{main_outline},0,7,0,0,0,1",
        f"Style: Main,HarmonyOS Sans SC,{main_size},&H00{highlight_bgr},"
        f"&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,"
        f"{main_outline},0,7,0,0,0,1",
        f"Style: RubyGlow,HarmonyOS Sans SC,{ruby_size},&H70F3F3F3,"
        f"&H70F3F3F3,&HA0FFFFFF,&HFF000000,1,0,0,0,100,100,0,0,1,"
        f"{2 if not is_wide else 3},0,8,0,0,0,1",
        f"Style: Ruby,HarmonyOS Sans SC,{ruby_size},&H00F3F3F3,"
        f"&H00F3F3F3,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,"
        f"{2 if not is_wide else 3},0,8,0,0,0,1",
        "Style: CueDim,HarmonyOS Sans SC,39,&H68FFFFFF,&H68FFFFFF,"
        "&H80000000,&HFF000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1",
        f"Style: CueHot,HarmonyOS Sans SC,39,&H00{highlight_bgr},"
        f"&H00{highlight_bgr},&H50000000,&HFF000000,1,0,0,0,100,100,0,0,1,"
        "4,0,5,0,0,0,1",
    ]
    if secondary:
        styles.extend(
            [
                f"Style: SecondaryGlow,HarmonyOS Sans SC,60,"
                f"&H50{secondary_highlight_bgr},"
                "&H50FFFFFF,&HA0FFFFFF,&HFF000000,1,0,0,0,100,100,0,0,1,"
                "3,0,8,0,0,0,1",
                f"Style: Secondary,HarmonyOS Sans SC,60,"
                f"&H00{secondary_highlight_bgr},"
                "&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,"
                "3,0,8,0,0,0,1",
            ]
        )

    glow_override = (
        f"{{\\an8\\pos(600,{main_y})\\fs{main_event_sizes[0]}"
        f"{letter_spacing}{inline_scale_x}}}"
    )
    main_override = (
        f"{{\\an8\\pos(900,{main_y})\\fs{main_event_sizes[1]}"
        f"{letter_spacing}{inline_scale_x}}}"
    )
    events = [
        f"Dialogue: 1,0:00:00.00,0:00:01.00,Glow,,0,0,0,,{glow_override}A",
        f"Dialogue: 2,0:00:00.00,0:00:01.00,Main,,0,0,0,,{main_override}A",
        f"Dialogue: 5,0:00:00.00,0:00:01.00,CueDim,,0,0,0,,"
        f"{{\\an5\\pos(800,{cue_y})}}A",
        f"Dialogue: 6,0:00:00.00,0:00:01.00,CueHot,,0,0,0,,"
        f"{{\\an5\\pos(850,{cue_y})}}A",
    ]
    if secondary:
        secondary_override = r"{\an8\pos(900,72)\fs51}A"
        events.extend(
            [
                f"Dialogue: 7,0:00:00.00,0:00:01.00,SecondaryGlow,,0,0,0,,"
                f"{secondary_override}",
                f"Dialogue: 8,0:00:00.00,0:00:01.00,Secondary,,0,0,0,,"
                f"{secondary_override}",
            ]
        )
    path.write_text(
        "[Script Info]\n"
        "; Layout: "
        f"{layout}\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "[V4+ Styles]\n"
        f"{_ASS_STYLE_FORMAT}\n"
        + "\n".join(styles)
        + "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        + "\n".join(events)
        + "\n",
        encoding="utf-8",
    )
    return path


def _annulus_sector_means(image, inner: float, outer: float, sectors: int = 24):
    center_x = image.width // 2
    center_y = image.height // 2
    sums = [0.0] * sectors
    counts = [0] * sectors
    pixels = image.convert("RGB")
    for y in range(image.height):
        delta_y = y - center_y
        for x in range(image.width):
            delta_x = x - center_x
            radius = math.hypot(delta_x, delta_y)
            if inner <= radius <= outer:
                angle = math.atan2(delta_y, delta_x) + math.pi
                sector = int(angle / (2 * math.pi) * sectors) % sectors
                red, green, blue = pixels.getpixel((x, y))
                sums[sector] += (red + green + blue) / 3
                counts[sector] += 1
    return [total / count for total, count in zip(sums, counts, strict=True)]


def test_parser_exposes_partial_manifest_gate():
    args = renderer.make_parser().parse_args(
        ["--manifest", "manifest.json", "--allow-partial-manifest"]
    )

    assert args.allow_partial_manifest is True


def test_background_has_no_compact_vinyl_backplate():
    cover = renderer.Image.new("RGB", renderer.CANVAS_SIZE, (180, 120, 80))

    background = renderer._draw_background(cover)

    # With a flat source, the global overlays vary only by y. Equal pixels on
    # the same scanline prove there is no local panel in the former box.
    assert background.getpixel((1200, 500)) == background.getpixel((800, 500))


def test_vinyl_motion_defaults_to_rotate_and_static_filter_has_no_rotate():
    args = renderer.make_parser().parse_args(["--manifest", "manifest.json"])

    assert args.vinyl_motion == "rotate"
    assert "rotate=" not in renderer._vinyl_filter(
        vinyl_motion="static", rotation_period=8.0
    )
    rotating = renderer._vinyl_filter(
        vinyl_motion="rotate", rotation_period=11.5
    )
    assert "rotate=2*PI*t/11.500000" in rotating


def test_artwork_metadata_binds_current_vinyl_style_and_hashes(
    tmp_path: Path,
    monkeypatch,
):
    cover = renderer.Image.new("RGB", (64, 64), (180, 120, 80))
    cover_bytes = io.BytesIO()
    cover.save(cover_bytes, format="PNG")
    monkeypatch.setattr(
        renderer,
        "embedded_cover",
        lambda _audio: (
            cover_bytes.getvalue(),
            {"present": True, "source": "test", "mime": "image/png"},
        ),
    )
    monkeypatch.setattr(
        renderer,
        "inspect_font_dir",
        lambda _fonts: {"family": "test", "regular": {}, "bold": {}, "files": []},
    )
    monkeypatch.setattr(
        renderer,
        "_draw_background",
        lambda _cover: renderer.Image.new("RGBA", renderer.CANVAS_SIZE, (0, 0, 0, 255)),
    )
    monkeypatch.setattr(renderer, "_draw_envelope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        renderer,
        "_draw_vinyl",
        lambda _cover: renderer.Image.new("RGBA", (32, 32), (10, 10, 10, 255)),
    )
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"audio")
    fonts = tmp_path / "fonts"
    fonts.mkdir()

    metadata = renderer.build_artwork(
        audio,
        tmp_path / "artwork",
        "Title",
        "Artist",
        "",
        fonts,
        allow_network=False,
    )
    saved = json.loads(
        (tmp_path / "artwork" / "artwork.json").read_text(encoding="utf-8")
    )
    vinyl = tmp_path / "artwork" / "vinyl.png"

    assert metadata == saved
    assert saved["vinyl_style_version"] == renderer.VINYL_STYLE_VERSION
    assert saved["vinyl_generator_sha256"] == hashlib.sha256(
        Path(renderer.__file__).read_bytes()
    ).hexdigest()
    assert saved["render_vinyl_karaoke_sha256"] == saved["vinyl_generator_sha256"]
    assert saved["vinyl_sha256"] == hashlib.sha256(vinyl.read_bytes()).hexdigest()
    assert saved["vinyl_backplate"] is None
    assert saved["vinyl_backplate_present"] is False
    assert saved["vinyl_backplate_preserved"] is False
    assert saved["vinyl_motion_contract"]["default"] == "rotate"


def test_vinyl_has_no_rotating_directional_seam():
    cover = renderer.Image.new("RGB", (320, 320), (180, 120, 80))

    vinyl = renderer._draw_vinyl(cover)
    sector_means = _annulus_sector_means(vinyl, 350, 405)

    assert max(sector_means) - min(sector_means) < 2.0
    assert statistics.pstdev(sector_means) < 1.0


def test_vinyl_disc_is_opaque_and_surrounding_canvas_is_transparent():
    cover = renderer.Image.new("RGB", (320, 320), (180, 120, 80))

    vinyl = renderer._draw_vinyl(cover)
    alpha = vinyl.getchannel("A")
    center = renderer.VINYL_SIZE // 2
    disc_radius = renderer.VINYL_SIZE // 2 - 16

    for y in range(vinyl.height):
        for x in range(vinyl.width):
            radius = math.hypot(x - center, y - center)
            if radius <= disc_radius - 1:
                assert alpha.getpixel((x, y)) == 255
            elif radius >= disc_radius + 2:
                assert alpha.getpixel((x, y)) == 0


def test_real_lyrics_libass_probe_uses_supplied_ass(
    tmp_path: Path,
    monkeypatch,
):
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    ass = tmp_path / "lyrics.ass"
    ass.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "[V4+ Styles]\n"
        "Style: Default,HarmonyOS Sans SC,58\n"
        "[Events]\n"
        "Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,示例文本\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_capture(_executable, args):
        command = [str(value) for value in args]
        calls.append(command)
        return SimpleNamespace(
            command=["ffmpeg", *command],
            returncode=0,
            stdout="",
            stderr="fontselect: (HarmonyOS Sans SC, 58, 0) -> HarmonyOS Sans SC\n",
        )

    monkeypatch.setattr(renderer, "run_capture", fake_capture)

    result = renderer.probe_libass_font(
        tmp_path / "ffmpeg.exe",
        fonts_dir,
        "HarmonyOS Sans SC",
        ass_path=ass,
    )

    assert result["ok"] is True
    assert result["probe_kind"] == "real_lyrics"
    assert result["ass_path"] == str(ass.resolve())
    assert calls
    command = calls[0]
    expected_ass = ass.resolve().as_posix().replace(":", r"\:")
    assert expected_ass in " ".join(command)
    assert "font probe" not in ass.read_text(encoding="utf-8")
    assert command[command.index("-ss") + 1] == "2.000"


def test_ass_gate_accepts_english_wide_dynamic_typography_and_parses_color(
    tmp_path: Path,
):
    ass_path = _write_gate_ass(tmp_path / "english-wide.ass", letter_spacing="")

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"], gate["errors"]
    assert gate["layout"] == "wide-bottom-en"
    assert {record["value"] for record in gate["inline_font_sizes"]} == {54.0, 96.0}
    assert gate["letter_spacing"]["positive"] is False
    assert gate["letter_spacing"]["non_negative"] is True
    assert gate["letter_spacing"]["values"] == []
    assert gate["natural_advance"]["ok"] is True
    assert gate["highlight_color"] == "#E19E84"
    assert gate["highlight_color_ass"] == "&H00849EE1"
    assert gate["highlight_colors"] == {
        "Main": "#E19E84",
        "Glow": "#E19E84",
        "CueHot": "#E19E84",
    }
    assert gate["secondary"]["highlight_color_required"] is False
    assert gate["gate"]["secondary_highlight_color_consistency"] is True


def test_ass_gate_accepts_chinese_wide_dynamic_font_bounds(tmp_path: Path):
    ass_path = _write_gate_ass(
        tmp_path / "chinese-wide.ass",
        layout="wide-bottom-zh",
        main_event_sizes=(75, 108),
        letter_spacing="",
    )

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"], gate["errors"]
    assert gate["layout"] == "wide-bottom-zh"
    assert {record["value"] for record in gate["inline_font_sizes"]} == {75.0, 108.0}
    assert gate["gate"]["inline_font_size_profile"] is True


def test_ass_gate_rejects_english_negative_spacing_and_compressed_advance(
    tmp_path: Path,
):
    ass_path = _write_gate_ass(
        tmp_path / "invalid-english-wide.ass",
        letter_spacing=r"\fsp-1",
        inline_scale_x=r"\fscx78",
    )

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"] is False
    assert gate["gate"]["letter_spacing"] is False
    assert gate["gate"]["natural_advance"] is False
    assert any("letter_spacing_negative" in error for error in gate["errors"])
    assert any("natural_advance" in error for error in gate["errors"])


def test_ass_gate_rejects_mismatched_highlight_colors(tmp_path: Path):
    ass_path = _write_gate_ass(tmp_path / "mismatched-color.ass")
    ass_path.write_text(
        ass_path.read_text(encoding="utf-8").replace(
            "&H50849EE1", "&H500000FF", 1
        ),
        encoding="utf-8",
    )

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"] is False
    assert gate["gate"]["highlight_color_consistency"] is False
    assert any("highlight_colors_not_consistent" in error for error in gate["errors"])


def test_ass_gate_requires_dynamic_english_font_size_bounds(tmp_path: Path):
    ass_path = _write_gate_ass(
        tmp_path / "too-small-english-wide.ass",
        main_event_sizes=(53, 96),
    )

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"] is False
    assert gate["gate"]["inline_font_size_profile"] is False
    assert any("inline_font_size_outside_layout_role_range" in error for error in gate["errors"])


def test_ass_gate_validates_secondary_pair_top_safe_area_and_isolation(tmp_path: Path):
    ass_path = _write_gate_ass(tmp_path / "secondary.ass", secondary=True)

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"], gate["errors"]
    assert gate["secondary"]["style_pair"] is True
    assert gate["secondary"]["font_sizes"] == [51.0]
    assert gate["secondary"]["positions"] == [(900.0, 72.0), (900.0, 72.0)]
    assert gate["secondary"]["excluded_from_main_lane_phase"] is True
    assert gate["secondary"]["excluded_from_main_cue_pairing"] is True
    assert gate["secondary"]["excluded_from_ruby"] is True
    assert gate["secondary"]["highlight_color_required"] is True
    assert gate["secondary"]["highlight_colors"] == {
        "Secondary": "#E19E84",
        "SecondaryGlow": "#E19E84",
    }
    assert gate["secondary"]["highlight_color_consistency"] is True
    assert gate["gate"]["secondary_styles"] is True
    assert gate["gate"]["secondary_highlight_color_consistency"] is True

    invalid = ass_path.read_text(encoding="utf-8").replace(
        r"\pos(900,72)", r"\pos(900,161)"
    )
    ass_path.write_text(invalid, encoding="utf-8")
    invalid_gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert invalid_gate["ok"] is False
    assert invalid_gate["gate"]["secondary_styles"] is False
    assert any("secondary_positions_outside_top_safe_area" in error for error in invalid_gate["errors"])


def test_ass_gate_accepts_nonwhite_secondary_singer_color(tmp_path: Path):
    ass_path = _write_gate_ass(
        tmp_path / "secondary-album-accent.ass",
        secondary=True,
        secondary_highlight_bgr="00FF00",
    )

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"], gate["errors"]
    assert gate["secondary"]["highlight_color_consistency"] is True
    assert gate["secondary"]["matches_main_highlight"] is False
    assert gate["gate"]["secondary_highlight_color_consistency"] is True


def test_ass_gate_accepts_per_event_singer_colors_and_requires_complete_inline_mode(
    tmp_path: Path,
):
    ass_path = _write_gate_ass(tmp_path / "per-event-colors.ass", secondary=True)
    text = ass_path.read_text(encoding="utf-8")
    replacements = {
        r"\fs54": r"\1c&H00849EE1&\fs54\k0\kf100",
        r"\fs96": r"\1c&H00849EE1&\fs96\k0\kf100",
        r"\fs51}A": r"\1c&H0000FF00&\fs51\k0\kf100}A",
        r"\an5\pos(850,584)}A": (
            r"\an5\pos(850,584)\1c&H00849EE1&\2c&H00849EE1&}A"
        ),
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    text += (
        "Dialogue: 2,0:00:03.00,0:00:04.00,Main,,0,0,0,,"
        r"{\an8\pos(960,870)\fs96\fad(80,120)}OUTRO"
        "\n"
    )
    ass_path.write_text(text, encoding="utf-8")

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"], gate["errors"]
    assert gate["secondary"]["inline_singer_color_mode"] is True
    assert gate["gate"]["inline_singer_event_colors"] is True
    assert "#00FF00" in {
        record["rgb"] for record in gate["secondary"]["event_highlight_colors"]
    }

    ass_path.write_text(
        text.replace(
            r"\1c&H00849EE1&\fs96\k0\kf100",
            r"\fs96\k0\kf100",
        ),
        encoding="utf-8",
    )
    invalid = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")
    assert invalid["ok"] is False
    assert invalid["gate"]["inline_singer_event_colors"] is False
    assert any(
        "singer_events_missing_readable_inline_primary_color" in error
        for error in invalid["errors"]
    )


def test_ass_gate_requires_inline_singer_color_for_karaoke_cue_hot(tmp_path: Path):
    ass_path = _write_gate_ass(tmp_path / "cue-hot-inline-color.ass", secondary=True)
    text = ass_path.read_text(encoding="utf-8")
    text = text.replace(
        r"\fs54",
        r"\1c&H00849EE1&\fs54\k0\kf100",
    ).replace(
        r"\fs96",
        r"\1c&H00849EE1&\fs96\k0\kf100",
    ).replace(
        r"\fs51}A",
        r"\1c&H0000FF00&\fs51\k0\kf100}A",
    ).replace(
        r"\an5\pos(850,584)}A",
        r"\an5\pos(850,584)\k0\kf100}A",
    )
    ass_path.write_text(text, encoding="utf-8")

    invalid = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert invalid["ok"] is False
    assert invalid["gate"]["inline_singer_event_colors"] is False
    assert any("CueHot" in error for error in invalid["errors"])

    ass_path.write_text(
        text.replace(
            r"\an5\pos(850,584)\k0\kf100}A",
            r"\an5\pos(850,584)\1c&H00849EE1&\2c&H00849EE1&\k0\kf100}A",
        ),
        encoding="utf-8",
    )
    valid = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")
    assert valid["ok"], valid["errors"]


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        (
            r"\1c&H00849EE1&\fs54\k0\kf100",
            r"\1c&H0000FF00&\fs54\k0\kf100",
            "paired_primary_colors_differ",
        ),
        (
            r"\1c&H0000FF00&\fs51\k0\kf100",
            r"\1c&H00FF0000&\fs51\k0\kf100",
            "paired_primary_colors_differ",
        ),
        (
            r"\1c&H00849EE1&\2c&H00849EE1&\k0\kf100",
            r"\1c&H00849EE1&\2c&H0000FF00&\k0\kf100",
            "cue_hot_primary_secondary_colors_differ",
        ),
        (
            "Style: Main,HarmonyOS Sans SC,96,&H00849EE1,&H00FFFFFF,",
            "Style: Main,HarmonyOS Sans SC,96,&H00849EE1,&H0000FF00,",
            "unhighlighted_color_must_be_white",
        ),
    ],
)
def test_ass_gate_rejects_per_event_color_mismatch_or_nonwhite_inactive_text(
    tmp_path: Path,
    before: str,
    after: str,
    reason: str,
):
    ass_path = _write_gate_ass(tmp_path / f"wrong-{reason}.ass", secondary=True)
    text = ass_path.read_text(encoding="utf-8")
    text = text.replace(
        r"\fs54",
        r"\1c&H00849EE1&\fs54\k0\kf100",
    ).replace(
        r"\fs96",
        r"\1c&H00849EE1&\fs96\k0\kf100",
    ).replace(
        r"\fs51}A",
        r"\1c&H0000FF00&\fs51\k0\kf100}A",
    ).replace(
        r"\an5\pos(850,584)}A",
        r"\an5\pos(850,584)\1c&H00849EE1&\2c&H00849EE1&\k0\kf100}A",
    )
    assert before in text
    ass_path.write_text(text.replace(before, after, 1), encoding="utf-8")

    invalid = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert invalid["ok"] is False
    assert invalid["gate"]["inline_singer_event_colors"] is False
    assert any(reason in error for error in invalid["errors"])


def test_ass_gate_requires_secondary_style_default_60_but_allows_inline_shrink(
    tmp_path: Path,
):
    ass_path = _write_gate_ass(tmp_path / "secondary-60.ass", secondary=True)
    assert renderer.validate_ass_for_render(
        ass_path, "HarmonyOS Sans SC"
    )["ok"] is True

    ass_path.write_text(
        ass_path.read_text(encoding="utf-8").replace(
            "Style: Secondary,HarmonyOS Sans SC,60,",
            "Style: Secondary,HarmonyOS Sans SC,51,",
        ),
        encoding="utf-8",
    )
    invalid = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")
    assert invalid["ok"] is False
    assert any("font_size_outside_layout_role_range" in error for error in invalid["errors"])


def test_ass_gate_rejects_white_secondary_hot_color(
    tmp_path: Path,
):
    ass_path = _write_gate_ass(
        tmp_path / "secondary-white.ass",
        secondary=True,
        secondary_highlight_bgr="FFFFFF",
    )

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"] is False
    assert gate["secondary"]["highlight_color_required"] is True
    assert gate["secondary"]["highlight_color_consistency"] is False
    assert gate["gate"]["secondary_highlight_color_consistency"] is False
    assert any(
        "secondary_hot_highlight_must_not_be_white" in error
        for error in gate["errors"]
    )


def test_ass_gate_rejects_unpaired_secondary_styles(tmp_path: Path):
    ass_path = _write_gate_ass(tmp_path / "unpaired-secondary.ass", secondary=True)
    ass_path.write_text(
        "\n".join(
            line
            for line in ass_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("Style: SecondaryGlow,")
        )
        + "\n",
        encoding="utf-8",
    )

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"] is False
    assert gate["gate"]["secondary_styles"] is False
    assert any("secondary_styles_must_be_paired" in error for error in gate["errors"])


@pytest.mark.parametrize(
    ("layout", "main_size", "ruby_size"),
    [("standard-v7", 52, 24), ("wide-bottom", 108, 51)],
)
def test_ass_gate_keeps_japanese_and_legacy_wide_profiles(
    tmp_path: Path,
    layout: str,
    main_size: int,
    ruby_size: int,
):
    ass_path = _write_gate_ass(
        tmp_path / f"{layout}.ass",
        layout=layout,
        main_event_sizes=(main_size, main_size),
    )

    gate = renderer.validate_ass_for_render(ass_path, "HarmonyOS Sans SC")

    assert gate["ok"], gate["errors"]
    assert gate["font_sizes"] == sorted({float(main_size), float(ruby_size), 39.0})
