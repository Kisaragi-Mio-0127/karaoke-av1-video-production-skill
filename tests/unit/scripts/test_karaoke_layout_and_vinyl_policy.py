from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from scripts import render_karaoke_direct_av1_420_album as direct
from scripts import render_karaoke_track as renderer
from scripts import render_vinyl_karaoke as vinyl_renderer
from scripts.karaoke_common.layout import STANDARD_LAYOUT, Lane, SubtitleLayout
from scripts.karaoke_japanese import layout as japanese_layout
from scripts.karaoke_japanese.layout import WIDE_LAYOUT
from strange_uta_game.backend.domain import Sentence


def test_track_renderer_reexports_public_layout_contracts():
    assert renderer.Lane is Lane
    assert renderer.SubtitleLayout is SubtitleLayout
    assert renderer.STANDARD_LAYOUT is STANDARD_LAYOUT
    assert renderer.WIDE_LAYOUT is WIDE_LAYOUT


def test_japanese_layout_does_not_import_local_zh_en_extension():
    japanese_source = Path(japanese_layout.__file__).read_text(encoding="utf-8")
    assert "karaoke_zh_en" not in japanese_source


@pytest.mark.parametrize(
    ("visual_style", "sleeve", "title_block_x"),
    (
        ("vinyl", {"x": 40, "y": 30, "width": 340, "height": 402}, 430),
        ("spectrum", {"x": 40, "y": 30, "width": 460, "height": 522}, 800),
        ("spectrum-line", {"x": 40, "y": 30, "width": 460, "height": 522}, 800),
        (
            "spectrum-mirror",
            {"x": 40, "y": 30, "width": 460, "height": 522},
            800,
        ),
        (
            "spectrum-dots",
            {"x": 40, "y": 30, "width": 460, "height": 522},
            800,
        ),
        (
            "spectrum-waterfall",
            {"x": 40, "y": 30, "width": 460, "height": 522},
            800,
        ),
    ),
)
def test_wide_composition_gate_requires_current_style_specific_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    visual_style: str,
    sleeve: dict[str, int],
    title_block_x: int,
):
    generator = tmp_path / "build_karaoke_wide_artwork.py"
    generator.write_text(
        'WIDE_LAYOUT_VERSION = "wide-layout-v6/top-secondary-clearance"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(direct, "WIDE_ARTWORK_GENERATOR", generator)
    composition = tmp_path / "composition.png"
    composition.write_bytes(b"panel-free-composition")
    metadata_path = composition.with_suffix(".json")
    metadata = {
        "layout_version": "wide-layout-v6/top-secondary-clearance",
        "layout_generator_sha256": direct.sha256_file(generator),
        "composition_sha256": direct.sha256_file(composition),
        "visual_style": visual_style,
        "sleeve": sleeve,
        "title_block_x": title_block_x,
        "title_block_y": {"label": 120, "title": 155, "artist": 220},
        "title_bounds": [title_block_x, 123, title_block_x + 430, 261],
        "secondary_overlay_contract": {
            "anchor_y": 12,
            "font_size_px": 60,
            "safe_bounds": [0, 0, 1920, 96],
            "outline_px": 3,
            "glow_px": 8,
            "reserved_bounds": [0, 0, 1920, 107],
        },
        "secondary_reserved_bounds": [0, 0, 1920, 107],
        "title_secondary_clearance_px": 16,
        "title_secondary_collision": False,
        "bottom_panel": [20, 576, 1900, 1050],
        "right_panel": None,
        "right_panel_visible": False,
        "outer_right_panel": None,
        "outer_right_panel_visible": False,
        "vinyl_backplate": None,
        "vinyl_backplate_present": False,
        "vinyl_backplate_preserved": False,
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    task = SimpleNamespace(profile="wide", composition_path=composition)

    (result,) = direct.validate_current_wide_compositions(
        [task], visual_style=visual_style
    )
    assert result["layout_version"] == (
        "wide-layout-v6/top-secondary-clearance"
    )
    assert result["right_panel_visible"] is False
    assert result["vinyl_backplate"] is None
    assert result["vinyl_backplate_present"] is False
    assert result["vinyl_backplate_preserved"] is False
    assert result["visual_style"] == visual_style
    assert result["title_block_x"] == title_block_x

    other_style = "spectrum" if visual_style == "vinyl" else "vinyl"
    with pytest.raises(direct.DirectAV1420RenderError, match="visual_style"):
        direct.validate_current_wide_compositions(
            [task], visual_style=other_style
        )

    metadata["title_bounds"][1] = 107
    metadata["title_secondary_clearance_px"] = 0
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(
        direct.DirectAV1420RenderError,
        match="title_bounds|title_secondary_no_collision",
    ):
        direct.validate_current_wide_compositions(
            [task], visual_style=visual_style
        )
    metadata["title_bounds"][1] = 123
    metadata["title_secondary_clearance_px"] = 16

    metadata["title_bounds"][1] = 122
    metadata["title_secondary_clearance_px"] = 15
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(
        direct.DirectAV1420RenderError,
        match="title_secondary_no_collision",
    ):
        direct.validate_current_wide_compositions([task], visual_style=visual_style)
    metadata["title_bounds"][1] = 123
    metadata["title_secondary_clearance_px"] = 16

    metadata["right_panel"] = [640, 30, 1900, 970]
    metadata["right_panel_visible"] = True
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(
        direct.DirectAV1420RenderError,
        match="outer_right_panel_removed",
    ):
        direct.validate_current_wide_compositions(
            [task], visual_style=visual_style
        )


def test_wide_composition_gate_rejects_old_or_preserved_vinyl_backplate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    generator = tmp_path / "build_karaoke_wide_artwork.py"
    generator.write_text(
        'WIDE_LAYOUT_VERSION = "wide-layout-v5/no-right-panels"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(direct, "WIDE_ARTWORK_GENERATOR", generator)
    composition = tmp_path / "composition.png"
    composition.write_bytes(b"backplate-absent-composition")
    metadata_path = composition.with_suffix(".json")
    metadata = {
        "layout_version": "wide-layout-v4/no-outer-right-panel",
        "layout_generator_sha256": direct.sha256_file(generator),
        "composition_sha256": direct.sha256_file(composition),
        "visual_style": "vinyl",
        "sleeve": {"x": 40, "y": 30, "width": 340, "height": 402},
        "title_block_x": 430,
        "bottom_panel": [20, 576, 1900, 1050],
        "right_panel": None,
        "right_panel_visible": False,
        "outer_right_panel": None,
        "outer_right_panel_visible": False,
        "vinyl_backplate_preserved": True,
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    task = SimpleNamespace(profile="wide", composition_path=composition)

    with pytest.raises(direct.DirectAV1420RenderError, match="layout_version"):
        direct.validate_current_wide_compositions([task])

    metadata["layout_version"] = "wide-layout-v5/no-right-panels"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(
        direct.DirectAV1420RenderError,
        match="vinyl_backplate_absent",
    ):
        direct.validate_current_wide_compositions([task])


def test_build_karaoke_ass_writes_file_and_returns_report(tmp_path: Path):
    sentence = Sentence.from_text("Hello", "singer")
    for index, character in enumerate(sentence.characters):
        character.add_timestamp(1_000 + index * 100)
    sentence.characters[-1].set_sentence_end_ts(2_000)
    project = SimpleNamespace(
        sentences=[sentence],
        singers=[
            SimpleNamespace(
                id="singer", color="#123456", is_default=True, group=""
            )
        ],
        metadata=SimpleNamespace(language="en"),
        audio_duration_ms=2_500,
    )
    output = tmp_path / "review.ass"
    font_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
    assert font_file.is_file()

    report = renderer.build_karaoke_ass(
        project,
        output,
        font_file=font_file,
        release_overrides={0: 2_000},
    )

    assert output.is_file()
    assert isinstance(report, dict)
    assert report["ass"] == str(output)
    assert report["pronunciation_validation"]["mode"] == "optional"


def _render_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **kwargs,
):
    output = tmp_path / f"{kwargs.get('vinyl_motion', 'default')}.mp4"
    vinyl = tmp_path / "vinyl.png"
    vinyl.write_bytes(b"current-vinyl")
    captured = {}

    def fake_run(command, **_run_kwargs):
        captured["command"] = command
        output.write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(renderer.subprocess, "run", fake_run)
    report = renderer.render_karaoke_video(
        ass_path=tmp_path / "subtitles.ass",
        audio_path=tmp_path / "audio.flac",
        composition_path=tmp_path / "composition.png",
        vinyl_path=vinyl,
        fonts_dir=tmp_path,
        output_path=output,
        start_seconds=0,
        duration_seconds=1,
        **kwargs,
    )
    graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    return report, graph


def test_vinyl_defaults_to_rotate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    report, graph = _render_filter(tmp_path, monkeypatch)
    assert report["vinyl_motion"] == "rotate"
    assert "rotate=2*PI*t/8" in graph
    assert renderer.make_parser().get_default("vinyl_motion") == "rotate"
    assert direct.make_parser().get_default("vinyl_motion") == "rotate"


def test_static_vinyl_is_explicit_and_has_no_rotate_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    report, graph = _render_filter(
        tmp_path,
        monkeypatch,
        vinyl_motion="static",
    )
    assert report["vinyl_motion"] == "static"
    assert "rotate=" not in graph


@pytest.mark.parametrize(
    ("audio_name", "expects_lossless"),
    (("source.mp3", False), ("source.flac", False)),
)
def test_direct_preview_command_requests_lossless_only_for_lossless_sources(
    tmp_path: Path,
    audio_name: str,
    expects_lossless: bool,
):
    task = SimpleNamespace(
        sug_path=tmp_path / "timing.sug",
        track=SimpleNamespace(audio_path=tmp_path / audio_name),
        composition_path=tmp_path / "composition.png",
        vinyl_path=tmp_path / "vinyl.png",
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=12.5,
        profile="standard",
    )
    command = direct.build_track_render_command(
        task,
        temporary_video=tmp_path / "output.mp4",
        temporary_lossless_video=tmp_path / "output.mkv",
        temporary_ass=tmp_path / "output.ass",
        temporary_report=tmp_path / "output.json",
        track_renderer_script=tmp_path / "renderer.py",
        av1_cq=38,
    )

    assert ("--lossless-output" in command) is expects_lossless
    direct.validate_direct_source_command(command)


def test_direct_preview_command_lossless_companion_is_explicit_opt_in(
    tmp_path: Path,
):
    task = SimpleNamespace(
        sug_path=tmp_path / "timing.sug",
        track=SimpleNamespace(audio_path=tmp_path / "source.flac"),
        composition_path=tmp_path / "composition.png",
        vinyl_path=tmp_path / "vinyl.png",
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=12.5,
        profile="standard",
    )
    command = direct.build_track_render_command(
        task,
        temporary_video=tmp_path / "output.mp4",
        temporary_lossless_video=tmp_path / "output.mkv",
        temporary_ass=tmp_path / "output.ass",
        temporary_report=tmp_path / "output.json",
        track_renderer_script=tmp_path / "renderer.py",
        av1_cq=38,
        lossless_companion=True,
    )

    assert command[command.index("--lossless-output") + 1].endswith("output.mkv")
    direct.validate_direct_source_command(command)


def test_direct_preview_command_rejects_mp3_lossless_opt_in(tmp_path: Path):
    task = SimpleNamespace(
        sug_path=tmp_path / "timing.sug",
        track=SimpleNamespace(audio_path=tmp_path / "source.mp3"),
        composition_path=tmp_path / "composition.png",
        vinyl_path=tmp_path / "vinyl.png",
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=12.5,
        profile="standard",
    )
    with pytest.raises(direct.DirectAV1420RenderError, match="lossless source"):
        direct.build_track_render_command(
            task,
            temporary_video=tmp_path / "output.mp4",
            temporary_lossless_video=tmp_path / "output.mkv",
            temporary_ass=tmp_path / "output.ass",
            temporary_report=tmp_path / "output.json",
            track_renderer_script=tmp_path / "renderer.py",
            av1_cq=38,
            lossless_companion=True,
        )


def test_report_only_mp3_does_not_probe_or_require_mkv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video = tmp_path / "output.mp4"
    ass = tmp_path / "output.ass"
    report_path = tmp_path / "report.json"
    audio = tmp_path / "source.mp3"
    for path, payload in ((video, b"mp4"), (ass, b"ass"), (audio, b"mp3")):
        path.write_bytes(payload)
    task = SimpleNamespace(
        profile="standard",
        track=SimpleNamespace(
            song_id="1",
            title="Song",
            artifact_slug="song",
            audio_path=audio,
        ),
        direct_report=report_path,
        ass_output=ass,
        video_output=video,
        lossless_video_output=tmp_path / "must-not-exist.mkv",
        duration_seconds=1.0,
        fonts_dir=tmp_path,
    )
    language_identity = {"code": "ja"}
    ruby_identity = {"status": "pass"}
    report_path.write_text(
        json.dumps(
            {
                "profile": "standard",
                "song_id": "1",
                "artifact_slug": "song",
                "render_mode": "direct-av1-420",
                "intermediate_video": False,
                "intermediate_h264": False,
                "intermediate_hevc": False,
                "sources": {},
                "output_sha256": direct.sha256_file(video),
                "lossless_output_sha256": None,
                "lossless_companion": {
                    "status": "omitted",
                    "requested": False,
                    "performed": False,
                    "reason": "lossless-companion-not-requested",
                },
                "language_identity": language_identity,
                "ruby_identity": ruby_identity,
                "libass_font_probe": {"ok": True, "probe_kind": "real_lyrics"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        direct, "validate_track_render_report", lambda *_a, **_k: None
    )
    # This test isolates the MP3/MKV selection policy.  ASS generation identity
    # is covered by the direct-renderer gate tests with real minimal ASS data.
    monkeypatch.setattr(
        direct,
        "validate_ass_report_generation",
        lambda *_a, **_k: {"ok": True},
    )
    monkeypatch.setattr(direct, "_source_record", lambda *_a, **_k: {})
    monkeypatch.setattr(
        direct.render_core,
        "_validate_ass_file",
        lambda *_a, **_k: {"ok": True},
    )
    monkeypatch.setattr(
        direct,
        "probe_libass_font",
        lambda *_a, **_k: {"ok": True, "probe_kind": "real_lyrics"},
    )
    monkeypatch.setattr(direct, "default_ffmpeg", lambda: tmp_path / "ffmpeg.exe")
    monkeypatch.setattr(direct, "verify_av1_420_output", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(
        direct,
        "_validate_report_durable_paths",
        lambda *_a, **_k: None,
    )

    def fail_lossless(*_args, **_kwargs):
        raise AssertionError("MP3 report-only must not inspect an MKV")

    monkeypatch.setattr(direct, "verify_lossless_av1_420_output", fail_lossless)
    monkeypatch.setattr(
        direct,
        "build_language_ruby_identity",
        lambda *_a, **_k: {"language": language_identity, "ruby": ruby_identity},
    )

    (result,) = direct.collect_existing_results(
        [task],
        ffmpeg=None,
        av1_cq=38,
        full_decode=False,
        lossless_companion=False,
    )
    assert result["lossless_video"] is None
    assert result["lossless_media"] is None
    assert result["lossless_companion"]["reason"] == "lossless-companion-not-requested"


def test_aggregate_with_no_requested_companions_lists_only_mp4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        direct,
        "aggregate_language_ruby_identity",
        lambda _results: {"status": "pass", "songs": []},
    )
    report = direct.build_av1_420_report(
        [
            {
                "profile": "standard",
                "song_id": "1",
                "title": "Song",
                "artifact_slug": "song",
                "sources": {},
                "video": str(tmp_path / "song.mp4"),
                "lossless_video": None,
                "report": str(tmp_path / "report.json"),
                "output_size_bytes": 1,
                "sha256": "mp4-hash",
                "lossless_output_size_bytes": None,
                "lossless_sha256": None,
                "elapsed_seconds": 1.0,
                "media": {"ok": True},
                "lossless_media": None,
                "lossless_companion": {
                    "status": "omitted",
                    "requested": False,
                    "performed": False,
                    "reason": "lossless-companion-not-requested",
                },
            }
        ],
        root=tmp_path,
        av1_cq=38,
        full_decode=False,
        profiles=("standard",),
        expected_song_count=1,
    )
    assert report["containers"] == ["mp4"]
    assert report["audio"]["lossless"] is None
    assert report["audio"]["lossless_omission_reason"] == (
        "lossless-companion-not-requested"
    )
    assert report["outputs"][0]["lossless_output"] is None


@pytest.mark.parametrize("lossless_companion", (False, True))
def test_fresh_render_full_decode_flows_into_real_media_verifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lossless_companion: bool,
):
    audio = tmp_path / ("source.flac" if lossless_companion else "source.mp3")
    audio.write_bytes(b"audio")
    task = SimpleNamespace(
        root=tmp_path,
        profile="standard",
        track=SimpleNamespace(
            song_id="1",
            title="Song",
            artist="Artist",
            artifact_slug="song",
            audio_path=audio,
        ),
        sug_path=tmp_path / "timing.sug",
        composition_path=tmp_path / "composition.png",
        vinyl_path=tmp_path / "vinyl.png",
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=1.0,
        ass_output=tmp_path / "published.ass",
        video_output=tmp_path / "published.mp4",
        lossless_video_output=tmp_path / "published.mkv",
        direct_report=tmp_path / "published.json",
    )
    mp4_decode_flags: list[bool] = []
    mkv_decode_flags: list[bool] = []

    def fake_run_track_renderer(command):
        Path(command[command.index("--output") + 1]).write_bytes(b"mp4")
        Path(command[command.index("--ass-output") + 1]).write_text(
            "ass",
            encoding="utf-8",
        )
        Path(command[command.index("--report-output") + 1]).write_text(
            json.dumps({"status": "ok", "ass": {}, "video": {}}),
            encoding="utf-8",
        )
        if "--lossless-output" in command:
            Path(command[command.index("--lossless-output") + 1]).write_bytes(b"mkv")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_mp4_verify(_path, **kwargs):
        performed = bool(kwargs.get("full_decode", False))
        mp4_decode_flags.append(performed)
        return {
            "ok": True,
            "size_bytes": 3,
            "decode": {"returncode": 0} if performed else None,
        }

    def fake_mkv_verify(_path, **kwargs):
        performed = bool(kwargs.get("full_decode", False))
        mkv_decode_flags.append(performed)
        return {
            "ok": True,
            "size_bytes": 3,
            "decode": {"returncode": 0} if performed else None,
        }

    monkeypatch.setattr(direct, "run_track_renderer", fake_run_track_renderer)
    monkeypatch.setattr(
        direct, "validate_track_render_report", lambda *_a, **_k: None
    )
    # This test isolates full-decode flag propagation.  The ASS/report
    # generation gate has dedicated tests in test_render_karaoke_direct_av1_420_album.
    monkeypatch.setattr(
        direct,
        "validate_ass_report_generation",
        lambda *_a, **_k: {"ok": True},
    )
    monkeypatch.setattr(
        direct.render_core,
        "_validate_ass_file",
        lambda *_a, **_k: {"ok": True},
    )
    monkeypatch.setattr(direct, "verify_av1_420_output", fake_mp4_verify)
    monkeypatch.setattr(direct, "verify_lossless_av1_420_output", fake_mkv_verify)
    monkeypatch.setattr(direct, "_source_record", lambda *_a, **_k: {})
    monkeypatch.setattr(
        direct,
        "build_language_ruby_identity",
        lambda *_a, **_k: {
            "language": {"identity": "language"},
            "ruby": {"identity": "ruby"},
        },
    )

    result = direct.render_one(
        task,
        track_renderer_script=tmp_path / "renderer.py",
        ffmpeg=None,
        av1_cq=38,
        libass_font_probe={"ok": True, "probe_kind": "real_lyrics"},
        lossless_companion=lossless_companion,
        full_decode=True,
    )

    assert mp4_decode_flags == [True, False]
    assert result["media"]["decode"]["returncode"] == 0
    published_report = json.loads(task.direct_report.read_text(encoding="utf-8"))
    assert published_report["video"]["media_checks"]["decode"]["returncode"] == 0
    if lossless_companion:
        assert mkv_decode_flags == [True, False]
        assert result["lossless_media"]["decode"]["returncode"] == 0
        assert published_report["lossless_companion"]["media_checks"]["decode"][
            "returncode"
        ] == 0
    else:
        assert mkv_decode_flags == []

    aggregate = direct.build_av1_420_report(
        [result],
        root=tmp_path,
        av1_cq=38,
        full_decode=True,
        profiles=("standard",),
        expected_song_count=1,
    )
    assert aggregate["full_decode_gate"]["performed"] is True
    assert aggregate["full_decode_evidence"] == [
        {
            "profile": "standard",
            "song_id": "1",
            "mp4_returncode": 0,
            "lossless_mkv_returncode": 0 if lossless_companion else None,
        }
    ]


def _vinyl_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, metadata: dict):
    generator = tmp_path / "render_vinyl_karaoke.py"
    generator.write_text('VINYL_STYLE_VERSION = "vinyl-v9"\n', encoding="utf-8")
    monkeypatch.setattr(direct, "VINYL_GENERATOR", generator)
    vinyl = tmp_path / "artwork" / "song" / "vinyl.png"
    vinyl.parent.mkdir(parents=True)
    vinyl.write_bytes(b"latest-style-vinyl")
    complete = {
        "generated_files": {"vinyl": "vinyl.png"},
        "vinyl_style_version": "vinyl-v9",
        "vinyl_generator_sha256": hashlib.sha256(generator.read_bytes()).hexdigest(),
        "vinyl_sha256": hashlib.sha256(vinyl.read_bytes()).hexdigest(),
        **metadata,
    }
    (vinyl.parent / "artwork.json").write_text(
        json.dumps(complete),
        encoding="utf-8",
    )
    return SimpleNamespace(vinyl_path=vinyl)


def test_current_vinyl_gate_binds_style_generator_and_asset_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    task = _vinyl_task(tmp_path, monkeypatch, metadata={})
    (result,) = direct.validate_current_vinyl_assets([task])
    assert result["status"] == "pass"
    assert result["vinyl_style_version"] == "vinyl-v9"
    assert result["vinyl_sha256"] == direct.sha256_file(task.vinyl_path)
    assert result["generator_sha256"] == direct.sha256_file(direct.VINYL_GENERATOR)
    assert result["mtime_role"] == "auxiliary-only"


def test_real_build_artwork_output_passes_current_vinyl_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cover_buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (80, 120, 160)).save(cover_buffer, format="PNG")
    monkeypatch.setattr(
        vinyl_renderer,
        "embedded_cover",
        lambda _audio: (
            cover_buffer.getvalue(),
            {"present": True, "source": "test", "mime": "image/png"},
        ),
    )
    monkeypatch.setattr(vinyl_renderer, "inspect_font_dir", lambda _path: {})
    monkeypatch.setattr(vinyl_renderer, "_draw_envelope", lambda *_args: None)
    audio = tmp_path / "source.flac"
    audio.write_bytes(b"test-audio")
    artwork_dir = tmp_path / "real-artwork"

    metadata = vinyl_renderer.build_artwork(
        audio,
        artwork_dir,
        "Title",
        "Artist",
        "",
        tmp_path,
        allow_network=False,
    )
    task = SimpleNamespace(vinyl_path=artwork_dir / "vinyl.png")
    (result,) = direct.validate_current_vinyl_assets([task])

    assert metadata["vinyl_style_version"] == result["vinyl_style_version"]
    assert metadata["vinyl_sha256"] == result["vinyl_sha256"]
    assert result["generator_sha256"] == direct.sha256_file(
        direct.VINYL_GENERATOR
    )


@pytest.mark.parametrize(
    "missing_field",
    ("vinyl_style_version", "vinyl_generator_sha256", "vinyl_sha256"),
)
def test_current_vinyl_gate_treats_missing_metadata_as_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_field: str,
):
    task = _vinyl_task(tmp_path, monkeypatch, metadata={missing_field: ""})
    with pytest.raises(direct.DirectAV1420RenderError, match=missing_field):
        direct.validate_current_vinyl_assets([task])


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("vinyl_style_version", "stale vinyl style"),
        ("vinyl_generator_sha256", "stale vinyl generator hash"),
        ("vinyl_sha256", "vinyl hash does not match"),
    ),
)
def test_current_vinyl_gate_rejects_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    message: str,
):
    task = _vinyl_task(tmp_path, monkeypatch, metadata={field: "wrong"})
    with pytest.raises(direct.DirectAV1420RenderError, match=message):
        direct.validate_current_vinyl_assets([task])
