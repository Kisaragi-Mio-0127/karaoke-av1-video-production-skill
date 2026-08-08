from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import render_karaoke_direct_av1_420_album as renderer


def test_generic_renderer_has_no_language_specific_branches_or_imports():
    source = Path(renderer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    exact_language_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in {"zh", "en"}
    }
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert exact_language_literals == set()
    assert not {
        module
        for module in imported_modules
        if "karaoke_zh_en" in module or "karaoke_japanese" in module
    }


def test_source_record_binds_vinyl_path_to_exact_hash(tmp_path: Path):
    audio = tmp_path / "audio.flac"
    composition = tmp_path / "composition.png"
    vinyl = tmp_path / "vinyl.png"
    sug = tmp_path / "timing.sug"
    ass = tmp_path / "timing.ass"
    for path, payload in (
        (audio, b"audio"),
        (composition, b"composition"),
        (vinyl, b"vinyl"),
        (sug, b"sug"),
        (ass, b"ass"),
    ):
        path.write_bytes(payload)
    task = SimpleNamespace(
        root=tmp_path,
        track=SimpleNamespace(audio_path=audio),
        composition_path=composition,
        vinyl_path=vinyl,
        sug_path=sug,
        ass_output=ass,
    )

    sources = renderer._source_record(task)

    assert sources["vinyl"].endswith("vinyl.png")
    assert sources["vinyl_sha256"] == renderer.sha256_file(vinyl)


def test_source_record_can_bind_hash_to_new_ass_before_atomic_publish(
    tmp_path: Path,
):
    published_ass = tmp_path / "published.ass"
    temporary_ass = tmp_path / ".published.partial.ass"
    published_ass.write_bytes(b"old-ass")
    temporary_ass.write_bytes(b"new-ass")
    task = SimpleNamespace(
        root=tmp_path,
        track=SimpleNamespace(audio_path=tmp_path / "missing.flac"),
        composition_path=tmp_path / "missing-composition.png",
        vinyl_path=tmp_path / "missing-vinyl.png",
        sug_path=tmp_path / "missing.sug",
        ass_output=published_ass,
    )

    sources = renderer._source_record(task, ass_path=temporary_ass)

    assert sources["latest_ass"].endswith("published.ass")
    assert sources["latest_ass_sha256"] == renderer.sha256_file(temporary_ass)


def test_render_report_publishes_real_probe_against_durable_ass_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    audio = tmp_path / "source.mp3"
    audio.write_bytes(b"audio")
    task = SimpleNamespace(
        root=tmp_path,
        profile="standard",
        visual_style="spectrum",
        track=SimpleNamespace(
            song_id="1",
            title="Song",
            artist="Artist",
            artifact_slug="song",
            audio_path=audio,
        ),
        sug_path=tmp_path / "timing.sug",
        composition_path=tmp_path / "composition.png",
        vinyl_path=None,
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=1.0,
        ass_output=tmp_path / "published.ass",
        video_output=tmp_path / "published.mp4",
        lossless_video_output=tmp_path / "published.mkv",
        direct_report=tmp_path / "published.json",
    )

    def fake_run_track_renderer(command):
        Path(command[command.index("--output") + 1]).write_bytes(b"mp4")
        Path(command[command.index("--ass-output") + 1]).write_text(
            "ass fixture",
            encoding="utf-8",
        )
        Path(command[command.index("--report-output") + 1]).write_text(
            json.dumps({"status": "ok", "ass": {}, "video": {}}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_probe(*_args, ass_path: Path, **_kwargs):
        return {
            "ok": True,
            "probe_kind": "real_lyrics",
            "ass_path": str(ass_path.resolve()),
            "returncode": 0,
            "fontselect": "real font selection evidence",
            "ass_sha256": renderer.sha256_file(ass_path),
        }

    monkeypatch.setattr(renderer, "run_track_renderer", fake_run_track_renderer)
    monkeypatch.setattr(
        renderer, "validate_track_render_report", lambda *_a, **_k: None
    )
    generation_paths = []

    def fake_generation_gate(_task, ass_path, _report, *, require_current_sources):
        generation_paths.append((Path(ass_path), require_current_sources))
        return {"ok": True}

    monkeypatch.setattr(renderer, "validate_ass_report_generation", fake_generation_gate)
    monkeypatch.setattr(
        renderer,
        "verify_av1_420_output",
        lambda *_a, **_k: {"ok": True, "size_bytes": 3},
    )
    monkeypatch.setattr(renderer, "probe_libass_font", fake_probe)
    monkeypatch.setattr(
        renderer,
        "build_language_ruby_identity",
        lambda *_a, **_k: {
            "language": {"identity": "language"},
            "ruby": {"identity": "ruby"},
        },
    )

    result = renderer.render_one(
        task,
        track_renderer_script=tmp_path / "renderer.py",
        ffmpeg=tmp_path / "ffmpeg.exe",
        av1_cq=38,
    )

    report_text = task.direct_report.read_text(encoding="utf-8")
    report = json.loads(report_text)
    probe = report["libass_font_probe"]
    assert probe["ass_path"] == str(task.ass_output.resolve())
    assert Path(probe["ass_path"]).is_file()
    assert ".partial.ass" not in report_text
    assert probe["returncode"] == 0
    assert probe["fontselect"] == "real font selection evidence"
    assert probe["ass_sha256"] == renderer.sha256_file(task.ass_output)
    assert len(generation_paths) == 2
    assert generation_paths[0][0].parent == tmp_path
    assert generation_paths[0][0].name.startswith(".published.")
    assert generation_paths[0][0].name.endswith(".partial.ass")
    assert generation_paths[0][1] is False
    assert generation_paths[1] == (task.ass_output, True)
    assert result["visual_style"] == "spectrum"

    aggregate = renderer.build_av1_420_report(
        [result],
        root=tmp_path,
        av1_cq=38,
        full_decode=False,
        profiles=("standard",),
        visual_styles=("spectrum",),
        expected_song_count=1,
    )
    assert aggregate["status"] == "pass"
    assert aggregate["outputs"][0]["visual_style"] == "spectrum"


def test_av1_output_uses_canonical_numbered_filename(tmp_path: Path):
    track = SimpleNamespace(
        artifact_slug="rain",
        numbered_video_filename="01 Example Track.mp4",
    )
    task = SimpleNamespace(track=track, profile="wide")

    (configured,) = renderer.configure_av1_tasks((task,), root=tmp_path)

    assert configured.video_output == (
        tmp_path / "video" / "av1-420" / "wide" / "01 Example Track.mp4"
    ).resolve()
    assert configured.lossless_video_output == (
        tmp_path / "video" / "av1-420-lossless" / "wide" / "01 Example Track.mkv"
    ).resolve()
    assert configured.direct_report.name == "rain_direct_av1_420_render_report.json"


def test_preview_command_is_direct_av1_420(tmp_path: Path):
    track = SimpleNamespace(audio_path=tmp_path / "audio.flac")
    task = SimpleNamespace(
        sug_path=tmp_path / "timing.sug",
        track=track,
        composition_path=tmp_path / "composition.png",
        vinyl_path=tmp_path / "vinyl.png",
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=12.5,
        profile="standard",
    )
    command = renderer.build_track_render_command(
        task,
        temporary_video=tmp_path / "output.mp4",
        temporary_lossless_video=tmp_path / "output.mkv",
        temporary_ass=tmp_path / "output.ass",
        temporary_report=tmp_path / "output.json",
        track_renderer_script=tmp_path / "renderer.py",
        av1_cq=38,
        singer_colors=("lead=#112233", "guest=#AABBCC"),
    )

    renderer.validate_direct_source_command(command)
    assert command[command.index("--video-encoder") + 1] == "av1_nvenc"
    assert command[command.index("--av1-cq") + 1] == "38"
    assert "--lossless-output" not in command
    assert [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--singer-color"
    ] == ["lead=#112233", "guest=#AABBCC"]


def test_spectrum_command_omits_vinyl_and_uses_distinct_artifacts(tmp_path: Path):
    task = SimpleNamespace(
        root=tmp_path,
        visual_style="spectrum",
        sug_path=tmp_path / "timing.sug",
        track=SimpleNamespace(
            audio_path=tmp_path / "audio.flac",
            numbered_video_filename="01 song.mp4",
            artifact_slug="song",
        ),
        composition_path=tmp_path / "composition_spectrum.png",
        vinyl_path=None,
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=12.5,
        profile="wide",
        ass_output=tmp_path / "timing" / "wide" / "song.ass",
    )
    (configured,) = renderer.configure_av1_tasks((task,), root=tmp_path)
    command = renderer.build_track_render_command(
        configured,
        temporary_video=tmp_path / "output.mp4",
        temporary_lossless_video=None,
        temporary_ass=tmp_path / "output.ass",
        temporary_report=tmp_path / "output.json",
        track_renderer_script=tmp_path / "renderer.py",
        av1_cq=38,
    )

    renderer.validate_direct_source_command(command)
    assert command[command.index("--visual-style") + 1] == "spectrum"
    assert "--vinyl" not in command
    assert "--vinyl-motion" not in command
    assert configured.video_output.parent == (
        tmp_path / "video" / "av1-420" / "spectrum" / "wide"
    ).resolve()
    assert configured.direct_report.parent == (
        tmp_path / "validation" / "spectrum" / "wide"
    ).resolve()
    assert "spectrum" in configured.direct_report.name
    assert not any(key.startswith("vinyl") for key in renderer._source_record(configured))


def test_both_styles_share_a_serial_render_group_per_song_profile():
    tasks = [
        SimpleNamespace(
            profile="wide",
            visual_style=style,
            track=SimpleNamespace(song_id=song_id),
        )
        for song_id in ("one", "two")
        for style in ("vinyl", "spectrum")
    ]

    groups = renderer.group_tasks_for_render(tasks)

    assert [[task.visual_style for task in group] for group in groups] == [
        ["vinyl", "spectrum"],
        ["vinyl", "spectrum"],
    ]


def test_preview_command_carries_timing_override_generation(tmp_path: Path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    timing_overrides = source_dir / "timing_overrides.json"
    timing_overrides.write_text('{"songs": {}}', encoding="utf-8")
    track = SimpleNamespace(audio_path=tmp_path / "audio.flac", song_id="123")
    task = SimpleNamespace(
        root=tmp_path,
        sug_path=tmp_path / "timing.sug",
        track=track,
        composition_path=tmp_path / "composition.png",
        vinyl_path=tmp_path / "vinyl.png",
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=12.5,
        profile="wide",
    )

    command = renderer.build_track_render_command(
        task,
        temporary_video=tmp_path / "output.mp4",
        temporary_lossless_video=tmp_path / "output.mkv",
        temporary_ass=tmp_path / "output.ass",
        temporary_report=tmp_path / "output.json",
        track_renderer_script=tmp_path / "renderer.py",
        av1_cq=38,
    )

    assert command[command.index("--timing-overrides") + 1] == str(
        timing_overrides.resolve()
    )
    assert command[command.index("--song-id") + 1] == "123"


def test_direct_dual_delivery_rejects_lossy_audio_source(tmp_path: Path):
    command = [
        "python",
        "preview.py",
        "--video-encoder",
        "av1_nvenc",
        "--sug",
        str(tmp_path / "timing.sug"),
        "--audio",
        str(tmp_path / "audio.mp3"),
        "--composition",
        str(tmp_path / "composition.png"),
        "--vinyl",
        str(tmp_path / "vinyl.png"),
        "--lossless-output",
        str(tmp_path / "output.mkv"),
    ]

    with pytest.raises(renderer.DirectAV1420RenderError, match="lossless source"):
        renderer.validate_direct_source_command(command)


def test_artifact_set_publish_rolls_back_when_a_later_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first_target = tmp_path / "first.mp4"
    second_target = tmp_path / "second.mkv"
    first_temporary = tmp_path / ".first.partial.mp4"
    second_temporary = tmp_path / ".second.partial.mkv"
    first_target.write_bytes(b"old-first")
    second_target.write_bytes(b"old-second")
    first_temporary.write_bytes(b"new-first")
    second_temporary.write_bytes(b"new-second")
    real_replace = renderer.os.replace

    def fail_on_second_publish(source, target):
        if Path(source) == second_temporary and Path(target) == second_target:
            raise OSError("simulated publish failure")
        return real_replace(source, target)

    monkeypatch.setattr(renderer.os, "replace", fail_on_second_publish)

    with pytest.raises(OSError, match="simulated publish failure"):
        renderer._publish_atomically(
            ((first_temporary, first_target), (second_temporary, second_target))
        )

    assert first_target.read_bytes() == b"old-first"
    assert second_target.read_bytes() == b"old-second"


def test_artifact_set_publish_rolls_back_when_post_publish_check_fails(
    tmp_path: Path,
):
    target = tmp_path / "video.mp4"
    temporary = tmp_path / ".video.partial.mp4"
    target.write_bytes(b"old")
    temporary.write_bytes(b"new")

    def reject_published_generation():
        assert target.read_bytes() == b"new"
        raise RuntimeError("published hash mismatch")

    with pytest.raises(RuntimeError, match="published hash mismatch"):
        renderer._publish_atomically(
            ((temporary, target),),
            post_publish_check=reject_published_generation,
        )

    assert target.read_bytes() == b"old"


def test_verify_av1_420_output_requires_expected_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"fixture")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fixture")
    probe = (
        "Stream #0:0: Video: av1 (libdav1d) (Main) (av01 / 0x31307661), "
        "yuv420p(tv, bt709/unknown/unknown, progressive), 1920x1080, 30 fps\n"
        "Stream #0:1: Audio: aac (LC), 44100 Hz, stereo, 320 kb/s"
    )
    monkeypatch.setattr(
        renderer.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=probe,
        ),
    )
    monkeypatch.setattr(
        renderer,
        "_stream_packet_timeline",
        lambda *_args, **_kwargs: {
            "packet_count": 60,
            "time_base": "1/1000",
            "first_pts_seconds": 0.0,
            "end_pts_seconds": 2.0,
            "duration_seconds": 2.0,
            "dts_monotonic": True,
        },
    )

    result = renderer.verify_av1_420_output(output, ffmpeg=ffmpeg)

    assert result["ok"] is True
    assert result["checks"]["codec_av1"] is True
    assert result["checks"]["pixel_format_yuv420p"] is True
    assert result["checks"]["limited_range_bt709"] is True


def test_lossless_verifier_rejects_shifted_audio_with_equal_container_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    compatibility = tmp_path / "video.mp4"
    lossless = tmp_path / "video.mkv"
    source = tmp_path / "audio.flac"
    ffmpeg = tmp_path / "ffmpeg.exe"
    for path in (compatibility, lossless, source, ffmpeg):
        path.write_bytes(b"fixture")
    probe = (
        "Duration: 00:00:02.00\n"
        "Stream #0:0: Video: av1 (libdav1d), "
        "yuv420p(tv, bt709/unknown/unknown), 1920x1080, 30 fps\n"
        "Stream #0:1: Audio: flac, 44100 Hz, stereo"
    )
    monkeypatch.setattr(
        renderer.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=probe,
        ),
    )
    monkeypatch.setattr(renderer, "_video_stream_sha256", lambda *_a, **_k: "V")
    monkeypatch.setattr(renderer, "_audio_pcm_sha256", lambda *_a, **_k: "A")

    def timeline(path, stream_specifier, **_kwargs):
        shifted = Path(path) == lossless and stream_specifier == "0:a:0"
        return {
            "packet_count": 60,
            "time_base": "1/1000",
            "first_pts_seconds": 0.2 if shifted else 0.0,
            "end_pts_seconds": 2.2 if shifted else 2.0,
            "duration_seconds": 2.0,
            "dts_monotonic": True,
        }

    monkeypatch.setattr(renderer, "_stream_packet_timeline", timeline)

    result = renderer.verify_lossless_av1_420_output(
        lossless,
        compatibility_output=compatibility,
        source_audio=source,
        start_seconds=0.0,
        duration_seconds=2.0,
        ffmpeg=ffmpeg,
    )

    assert result["ok"] is False
    assert result["checks"]["duration_matches_mp4"] is True
    assert result["checks"]["lossless_av_start_boundaries_match"] is False
    assert result["checks"]["lossless_av_end_boundaries_match"] is False


def test_track_render_report_rejects_wrong_pixel_format():
    report = {
        "status": "ok",
        "ass": {"ass": "lyrics.ass"},
        "video": {
            "video_encoder": "av1_nvenc",
            "pixel_format": "gbrp",
            "av1_cq": 44,
        },
    }

    with pytest.raises(renderer.DirectAV1420RenderError, match="pixel_format"):
        renderer.validate_track_render_report(report, av1_cq=38)


def test_track_render_report_lossless_companion_is_opt_in():
    color_plan = {
        "schema_version": "karaoke-color-plan/v1",
        "color_plan_sha256": "test-color-plan",
    }
    video = {
        "video_encoder": "av1_nvenc",
        "pixel_format": "yuv420p",
        "av1_cq": 38,
        "av1_preset": "p7",
        "preferred_output": "compatibility-mp4",
        "audio_codec": "aac",
        "audio_profile": "aac_low",
        "audio_bitrate": "320k",
        "color_plan_sha256": "test-color-plan",
    }
    report = {
        "status": "ok",
        "ass": {"ass": "lyrics.ass", "color_plan": color_plan},
        "video": video,
    }

    renderer.validate_track_render_report(report, av1_cq=38)
    report["video"]["lossless"] = {
        "status": "omitted",
        "requested": False,
        "performed": False,
        "reason": "lossless-companion-not-requested",
        "path": None,
    }
    renderer.validate_track_render_report(report, av1_cq=38)
    report["video"].pop("lossless")
    with pytest.raises(renderer.DirectAV1420RenderError, match="no lossless"):
        renderer.validate_track_render_report(
            report,
            av1_cq=38,
            lossless_companion=True,
        )

    report["video"]["lossless"] = {
        "audio_codec": "flac",
        "video_codec": "copy",
    }
    renderer.validate_track_render_report(
        report,
        av1_cq=38,
        lossless_companion=True,
    )
    with pytest.raises(renderer.DirectAV1420RenderError, match="unexpected"):
        renderer.validate_track_render_report(report, av1_cq=38)


def test_track_render_report_requires_nonempty_matching_color_plan_hash():
    report = {
        "status": "ok",
        "ass": {
            "ass": "lyrics.ass",
            "color_plan": {"schema_version": "karaoke-color-plan/v1"},
        },
        "video": {
            "video_encoder": "av1_nvenc",
            "pixel_format": "yuv420p",
            "av1_cq": 38,
            "av1_preset": "p7",
            "preferred_output": "compatibility-mp4",
            "audio_codec": "aac",
            "audio_profile": "aac_low",
            "audio_bitrate": "320k",
        },
    }

    with pytest.raises(renderer.DirectAV1420RenderError, match="color-plan.*hash"):
        renderer.validate_track_render_report(report, av1_cq=38)


def _generation_gate_fixture(tmp_path: Path, *, secondary: bool = True):
    sug = tmp_path / "timing.sug"
    sug.write_text('{"sentences": [{"characters": []}]}', encoding="utf-8")
    ass = tmp_path / "lyrics.ass"
    ass.write_text("generated ASS", encoding="utf-8")
    role = "opera" if secondary else None
    line = {
        "source_line_index": 0,
        "phrase_index": 0,
        "voice_role": role,
        "singer_group": None,
        "effective_singer_id": "main",
        "effective_singer_ids": ["main"],
        "effective_singer_runs": [{"singer_id": "main"}],
        "ruby": [],
    }
    report = {
        "ass": {
            "ass": str(ass),
            "sug_hash": renderer.sha256_file(sug),
            "ruby_consistency_gate": {
                "status": "pass",
                "sug": "canonical-sug",
                "ass": "canonical-sug",
                "report": "canonical-sug",
                "sug_hash": renderer.sha256_file(sug),
            },
            "singer_color_mapping": [{"singer_id": "main"}],
            "lines": [] if secondary else [line],
            "secondary_lines": [line] if secondary else [],
            "source_sentence_to_display_phrases": [
                {
                    "source_line_index": 0,
                    "display_phrases": ["line"],
                    "voice_role": role,
                    "singer_group": None,
                }
            ],
            "layout_contract": {"secondary": {"line_count": int(secondary)}},
        }
    }
    task = SimpleNamespace(sug_path=sug, font_family="HarmonyOS Sans SC")
    return task, ass, report


def test_generation_gate_uses_shared_validator_and_accepts_secondary_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    task, ass, report = _generation_gate_fixture(tmp_path)
    calls = []
    source_checks = []

    def fake_ass_gate(path, font_family):
        calls.append((path, font_family))
        return {"ok": True, "secondary": {"present": True, "style_pair": True}}

    monkeypatch.setattr(renderer, "validate_ass_for_render", fake_ass_gate)
    monkeypatch.setattr(
        renderer,
        "_validate_report_sources",
        lambda *args, **kwargs: source_checks.append((args, kwargs)),
    )

    renderer.validate_ass_report_generation(
        task,
        ass,
        report,
        require_current_sources=True,
    )

    assert calls == [(ass, "HarmonyOS Sans SC")]
    assert len(source_checks) == 1
    assert source_checks[0][1] == {"allow_stale_paths": False}


def test_generation_gate_rejects_secondary_role_count_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    task, ass, report = _generation_gate_fixture(tmp_path)
    report["ass"]["secondary_lines"] = []
    report["ass"]["layout_contract"]["secondary"]["line_count"] = 0
    monkeypatch.setattr(
        renderer,
        "validate_ass_for_render",
        lambda *_a, **_k: {"ok": True, "secondary": {"present": False}},
    )

    with pytest.raises(renderer.DirectAV1420RenderError, match="count or role mapping"):
        renderer.validate_ass_report_generation(
            task,
            ass,
            report,
            require_current_sources=False,
        )


def test_generation_gate_rejects_stale_sug_and_false_ruby_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    task, ass, report = _generation_gate_fixture(tmp_path, secondary=False)
    monkeypatch.setattr(
        renderer,
        "validate_ass_for_render",
        lambda *_a, **_k: {"ok": True, "secondary": {"present": False}},
    )
    report["ass"]["sug_hash"] = "stale"
    with pytest.raises(renderer.DirectAV1420RenderError, match="sug_hash is stale"):
        renderer.validate_ass_report_generation(
            task,
            ass,
            report,
            require_current_sources=False,
        )

    report["ass"]["sug_hash"] = renderer.sha256_file(task.sug_path)
    report["ass"]["ruby_consistency_gate"]["status"] = "pass"
    report["ass"]["ruby_consistency_gate"]["sug_hash"] = "stale"
    with pytest.raises(renderer.DirectAV1420RenderError, match="ruby consistency is stale"):
        renderer.validate_ass_report_generation(
            task,
            ass,
            report,
            require_current_sources=False,
        )


def test_ruby_identity_fails_when_source_and_rendered_content_differ(tmp_path: Path):
    sug = tmp_path / "timing.sug"
    sug.write_text(
        json.dumps(
            {
                "sentences": [
                    {
                        "characters": [
                            {"char": "A", "ruby": {"parts": [{"text": "source"}]}}
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    identity = renderer.ruby_identity(
        SimpleNamespace(sug_path=sug),
        {"ass": {"lines": [{"ruby": [{"text": "A", "reading": "rendered"}]}]}},
    )

    assert identity["status"] == "fail"
    assert identity["consistent"] is False
    assert identity["identity"] is None


def test_aggregate_report_treats_full_decode_as_optional(tmp_path: Path):
    results = []
    for index in range(5):
        results.append(
            {
                "profile": "wide",
                "song_id": str(index),
                "title": f"song-{index}",
                "artifact_slug": f"{index:02d}",
                "sources": {},
                "video": tmp_path / f"{index}.mp4",
                "lossless_video": tmp_path / f"{index}.mkv",
                "report": tmp_path / f"{index}.json",
                "output_size_bytes": 1,
                "sha256": "0" * 64,
                "lossless_output_size_bytes": 2,
                "lossless_sha256": "1" * 64,
                "elapsed_seconds": 1.0,
                "media": {"ok": True},
                "lossless_media": {"ok": True},
            }
        )

    report = renderer.build_av1_420_report(
        results,
        root=tmp_path,
        av1_cq=38,
        full_decode=False,
        full_decode_exception_reason="explicit-user-choice",
        profiles=("wide",),
    )

    assert report["status"] == "pass"
    assert report["verification_status"] == "complete"
    assert report["release_decision"] == "verified"
    assert report["schema_version"] == "karaoke-av1-420/v2"
    assert report["audio"]["compatibility"] == "AAC-LC 320 kb/s"
    assert report["audio"]["lossless"].startswith("FLAC")
    assert report["settings"]["cq"] == renderer.DEFAULT_AV1_CQ
    assert report["settings"]["preset"] == renderer.DEFAULT_AV1_PRESET
    assert report["full_decode"] is False
    assert report["full_decode_gate"] == {
        "performed": False,
        "required": False,
        "recommended": False,
        "reason": "explicit-user-choice",
        "risk": None,
    }


def test_aggregate_report_accepts_single_track_manifest(tmp_path: Path):
    result = {
        "profile": "wide",
        "song_id": "single",
        "title": "single",
        "artifact_slug": "single",
        "sources": {},
        "video": tmp_path / "single.mp4",
        "lossless_video": tmp_path / "single.mkv",
        "report": tmp_path / "single.json",
        "output_size_bytes": 1,
        "sha256": "0" * 64,
        "lossless_output_size_bytes": 2,
        "lossless_sha256": "1" * 64,
        "elapsed_seconds": 1.0,
        "media": {"ok": True},
        "lossless_media": {"ok": True},
    }

    report = renderer.build_av1_420_report(
        [result],
        root=tmp_path,
        av1_cq=38,
        full_decode=False,
        profiles=("wide",),
        expected_song_count=1,
    )

    assert report["status"] == "pass"
    assert len(report["outputs"]) == 1


def test_parser_requires_one_task_for_single_track_mode():
    args = renderer.make_parser().parse_args(
        [
            "--manifest",
            "synthetic-album.json",
            "--allow-partial-manifest",
            "--single-track",
            "--singer-color",
            "lead=#112233",
            "--singer-color",
            "guest=#AABBCC",
        ]
    )

    assert args.allow_partial_manifest is True
    assert args.single_track is True
    assert args.visual_style == "vinyl"
    assert args.lossless_companion is False
    assert args.full_decode is False
    assert args.singer_color == ["lead=#112233", "guest=#AABBCC"]


def test_main_accepts_single_track_with_both_visual_styles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    track = SimpleNamespace(song_id="single")
    album = SimpleNamespace(deliverable_dir=tmp_path, tracks=(track,))
    tasks = tuple(
        SimpleNamespace(
            profile="wide",
            visual_style=style,
            track=track,
        )
        for style in ("vinyl", "spectrum")
    )
    observed: dict[str, object] = {}

    monkeypatch.setattr(renderer.render_core, "load_album_manifest", lambda *_a, **_k: album)
    monkeypatch.setattr(renderer.render_core, "select_tracks", lambda *_a, **_k: album.tracks)
    monkeypatch.setattr(renderer.render_core, "select_profiles", lambda *_a, **_k: ("wide",))

    def fake_plan_tasks(*_args, visual_styles, **_kwargs):
        observed["planned_styles"] = visual_styles
        return tasks

    monkeypatch.setattr(renderer.render_core, "plan_tasks", fake_plan_tasks)
    monkeypatch.setattr(renderer, "configure_av1_tasks", lambda value, **_k: value)
    monkeypatch.setattr(renderer, "validate_editable_pronunciation_sources", lambda *_a, **_k: [])
    monkeypatch.setattr(renderer, "validate_current_vinyl_assets", lambda *_a, **_k: [])
    monkeypatch.setattr(renderer, "validate_current_wide_compositions", lambda *_a, **_k: [])
    monkeypatch.setattr(
        renderer,
        "collect_existing_results",
        lambda *_a, **_k: [
            {"visual_style": style, "profile": "wide", "song_id": "single"}
            for style in ("vinyl", "spectrum")
        ],
    )

    def fake_aggregate(_results, **kwargs):
        observed["aggregate_styles"] = kwargs["visual_styles"]
        return {"status": "pass"}

    monkeypatch.setattr(renderer, "build_av1_420_report", fake_aggregate)
    monkeypatch.setattr(renderer, "write_json_atomically", lambda *_a, **_k: None)

    assert renderer.main(
        [
            "--manifest",
            str(tmp_path / "synthetic-album.json"),
            "--allow-partial-manifest",
            "--single-track",
            "--profile",
            "wide",
            "--visual-style",
            "both",
            "--report-only",
        ]
    ) == 0
    assert observed == {
        "planned_styles": ("vinyl", "spectrum"),
        "aggregate_styles": ("vinyl", "spectrum"),
    }


def test_main_forwards_repeatable_singer_colors_to_each_render_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    track = SimpleNamespace(song_id="single")
    album = SimpleNamespace(deliverable_dir=tmp_path, tracks=(track,))
    tasks = tuple(
        SimpleNamespace(profile="wide", visual_style=style, track=track)
        for style in ("vinyl", "spectrum")
    )
    track_renderer_script = tmp_path / "renderer.py"
    track_renderer_script.write_text("", encoding="utf-8")
    observed: list[tuple[str, ...]] = []

    monkeypatch.setattr(renderer.render_core, "load_album_manifest", lambda *_a, **_k: album)
    monkeypatch.setattr(renderer.render_core, "select_tracks", lambda *_a, **_k: album.tracks)
    monkeypatch.setattr(renderer.render_core, "select_profiles", lambda *_a, **_k: ("wide",))
    monkeypatch.setattr(
        renderer.render_core,
        "select_visual_styles",
        lambda *_a, **_k: ("vinyl", "spectrum"),
    )
    monkeypatch.setattr(renderer.render_core, "plan_tasks", lambda *_a, **_k: tasks)
    monkeypatch.setattr(renderer, "configure_av1_tasks", lambda value, **_k: value)
    monkeypatch.setattr(renderer, "validate_editable_pronunciation_sources", lambda *_a, **_k: [])
    monkeypatch.setattr(renderer, "validate_current_vinyl_assets", lambda *_a, **_k: [])
    monkeypatch.setattr(renderer, "validate_current_wide_compositions", lambda *_a, **_k: [])

    def fake_render_one(task, **kwargs):
        observed.append(tuple(kwargs["singer_colors"]))
        return {"status": "ok", "visual_style": task.visual_style}

    monkeypatch.setattr(renderer, "render_one", fake_render_one)
    monkeypatch.setattr(renderer, "build_av1_420_report", lambda *_a, **_k: {"status": "pass"})
    monkeypatch.setattr(renderer, "write_json_atomically", lambda *_a, **_k: None)

    assert renderer.main(
        [
            "--manifest",
            str(tmp_path / "synthetic-album.json"),
            "--allow-partial-manifest",
            "--single-track",
            "--profile",
            "wide",
            "--track-renderer-script",
            str(track_renderer_script),
            "--visual-style",
            "both",
            "--singer-color",
            "lead=#112233",
            "--singer-color",
            "guest=#AABBCC",
        ]
    ) == 0
    assert observed == [
        ("lead=#112233", "guest=#AABBCC"),
        ("lead=#112233", "guest=#AABBCC"),
    ]


def test_aggregate_report_identifies_both_visual_styles(tmp_path: Path):
    results = []
    for style in ("vinyl", "spectrum"):
        results.append(
            {
                "profile": "wide",
                "visual_style": style,
                "song_id": "single",
                "title": "single",
                "artifact_slug": "single",
                "sources": {},
                "video": tmp_path / style / "single.mp4",
                "lossless_video": None,
                "report": tmp_path / style / "single.json",
                "output_size_bytes": 1,
                "sha256": style,
                "lossless_output_size_bytes": None,
                "lossless_sha256": None,
                "elapsed_seconds": 1.0,
                "media": {"ok": True},
                "lossless_media": None,
            }
        )

    report = renderer.build_av1_420_report(
        results,
        root=tmp_path,
        av1_cq=38,
        full_decode=False,
        profiles=("wide",),
        visual_styles=("vinyl", "spectrum"),
        expected_song_count=1,
    )

    assert report["visual_styles"] == ["vinyl", "spectrum"]
    assert [item["visual_style"] for item in report["outputs"]] == [
        "spectrum",
        "vinyl",
    ]


def test_language_identity_delegates_to_shared_interface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sug = tmp_path / "song.sug"
    sug.write_text('{"metadata": {"language": "ja"}}', encoding="utf-8")
    task = SimpleNamespace(sug_path=sug)
    calls: list[object] = []

    def shared_language_identity(language):
        calls.append(language)
        return {"code": "ja", "identity": "shared-language-v1"}

    monkeypatch.setattr(
        renderer,
        "_shared_language_identity",
        shared_language_identity,
    )

    result = renderer.language_identity(task)

    assert result == {
        "code": "ja",
        "identity": "shared-language-v1",
        "source": "shared-language-interface",
    }
    assert calls == ["ja"]


def test_ruby_identity_keeps_source_and_rendered_facts_separate(tmp_path: Path):
    sug = tmp_path / "song.sug"
    sug.write_text(
        '{"metadata": {"language": "ja"}, "sentences": [{'
        '"characters": [{"char": "雨", "ruby": {'
        '"parts": [{"text": "あめ"}]}}]}]}',
        encoding="utf-8",
    )
    task = SimpleNamespace(sug_path=sug)
    identity = renderer.ruby_identity(
        task,
        {"ass": {"lines": [{"ruby": [{"text": "雨", "reading": "あめ"}]}]}},
    )

    assert identity["status"] == "pass"
    assert identity["source"]["count"] == 1
    assert identity["rendered"]["count"] == 1
    assert identity["source"]["identity"] != identity["rendered"]["identity"]


def test_aggregate_language_and_ruby_identity_must_match_profiles(tmp_path: Path):
    results = []
    for profile in ("standard", "wide"):
        results.append(
            {
                "profile": profile,
                "song_id": "single",
                "title": "single",
                "artifact_slug": "single",
                "sources": {},
                "video": tmp_path / f"{profile}.mp4",
                "lossless_video": tmp_path / f"{profile}.mkv",
                "report": tmp_path / f"{profile}.json",
                "output_size_bytes": 1,
                "sha256": "0" * 64,
                "lossless_output_size_bytes": 2,
                "lossless_sha256": "1" * 64,
                "elapsed_seconds": 1.0,
                "media": {"ok": True},
                "lossless_media": {"ok": True},
                "language_identity": {"code": "ja", "identity": "lang-1"},
                "ruby_identity": {"status": "pass", "identity": "ruby-1"},
            }
        )

    report = renderer.build_av1_420_report(
        results,
        root=tmp_path,
        av1_cq=38,
        full_decode=False,
        profiles=("standard", "wide"),
        expected_song_count=1,
    )

    assert report["language_identity"]["status"] == "pass"
    assert report["ruby_identity"]["status"] == "pass"
    assert report["language_ruby_identity"]["songs"][0]["profiles"] == [
        "standard",
        "wide",
    ]

    results[1]["ruby_identity"] = {"status": "pass", "identity": "ruby-2"}
    with pytest.raises(renderer.DirectAV1420RenderError, match="ruby identity"):
        renderer.build_av1_420_report(
            results,
            root=tmp_path,
            av1_cq=38,
            full_decode=False,
            profiles=("standard", "wide"),
            expected_song_count=1,
        )


def _make_refresh_fixture(tmp_path: Path):
    audio = tmp_path / "sources" / "audio.mp3"
    composition = tmp_path / "artwork" / "wide" / "composition.png"
    vinyl = tmp_path / "artwork" / "song" / "vinyl.png"
    provenance = vinyl.parent / "artwork.json"
    sug = tmp_path / "timing" / "song.sug"
    ass = tmp_path / "timing" / "wide" / "song.ass"
    video = tmp_path / "video" / "av1-420" / "wide" / "song.mp4"
    direct_report = tmp_path / "validation" / "wide" / "song.json"
    for path, payload in (
        (audio, b"audio"),
        (composition, b"composition"),
        (vinyl, b"vinyl"),
        (sug, b"sug"),
        (ass, b"ass"),
        (video, b"video"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    provenance.write_text("{}", encoding="utf-8")
    task = SimpleNamespace(
        root=tmp_path,
        profile="wide",
        track=SimpleNamespace(
            song_id="song",
            title="Song",
            artist="Artist",
            artifact_slug="song",
            audio_path=audio,
        ),
        composition_path=composition,
        vinyl_path=vinyl,
        sug_path=sug,
        ass_output=ass,
        video_output=video,
        lossless_video_output=tmp_path / "video" / "song.mkv",
        direct_report=direct_report,
    )
    staging = str(tmp_path / ".staging" / "copied")
    sources = renderer._source_record(task)
    for key in renderer.SOURCE_PATH_KEYS:
        if sources[key] is not None:
            sources[key] = f".staging/{key}"
    omission = {
        "status": "omitted",
        "requested": False,
        "performed": False,
        "reason": "lossless-companion-not-requested",
        "source_suffix": ".mp3",
        "audio_source": f"{staging}/audio.mp3",
        "path": None,
    }
    report = {
        "ass": {"ass": f"{staging}/song.ass", "lines": []},
        "video": {
            "video": f"{staging}/song.mp4",
            "compatibility_mp4": {"path": f"{staging}/song.mp4"},
            "primary_delivery": {"path": f"{staging}/song.mp4"},
            "media_checks": {
                "path": f"{staging}/song.mp4",
                "checks": {"codec_av1": True},
                "decode": None,
            },
            "vinyl_asset": {
                "path": f"{staging}/vinyl.png",
                "provenance_path": f"{staging}/artwork.json",
                "sha256": renderer.sha256_file(vinyl),
            },
            "lossless": dict(omission),
            "lossless_audio_delivery": dict(omission),
        },
        "sources": sources,
        "lossless_companion": dict(omission),
        "libass_font_probe": {
            "ok": True,
            "probe_kind": "real_lyrics",
            "ass_path": f"{staging}/song.ass",
            "returncode": 0,
            "fontselect": "measured font selection",
        },
        "output_sha256": renderer.sha256_file(video),
        "render_elapsed_seconds": 12.345,
    }
    direct_report.parent.mkdir(parents=True, exist_ok=True)
    direct_report.write_text(json.dumps(report), encoding="utf-8")
    return task, report


def test_refresh_report_rebinds_durable_paths_and_preserves_evidence(tmp_path: Path):
    task, report = _make_refresh_fixture(tmp_path)

    refreshed = renderer._refresh_report_durable_paths(
        report,
        task,
        lossless_companion=False,
    )

    video_path = str(task.video_output.resolve())
    assert refreshed["sources"] == renderer._source_record(task)
    assert refreshed["ass"]["ass"] == str(task.ass_output.resolve())
    assert refreshed["video"]["video"] == video_path
    assert refreshed["video"]["compatibility_mp4"]["path"] == video_path
    assert refreshed["video"]["primary_delivery"]["path"] == video_path
    assert refreshed["video"]["media_checks"]["path"] == video_path
    assert refreshed["video"]["vinyl_asset"]["path"] == str(
        task.vinyl_path.resolve()
    )
    assert refreshed["libass_font_probe"]["ass_path"] == str(
        task.ass_output.resolve()
    )
    assert refreshed["lossless_companion"]["audio_source"] == str(
        task.track.audio_path.resolve()
    )
    assert refreshed["lossless_companion"]["path"] is None
    assert refreshed["video"]["lossless"] == refreshed["lossless_companion"]
    assert refreshed["video"]["lossless_audio_delivery"] == refreshed[
        "lossless_companion"
    ]
    assert refreshed["output_sha256"] == report["output_sha256"]
    assert refreshed["render_elapsed_seconds"] == 12.345
    assert refreshed["video"]["media_checks"]["checks"] == {
        "codec_av1": True
    }
    assert refreshed["video"]["media_checks"]["decode"] is None
    assert refreshed["libass_font_probe"]["fontselect"] == (
        "measured font selection"
    )
    renderer._validate_report_durable_paths(
        refreshed,
        task,
        lossless_companion=False,
    )


def test_refresh_existing_reports_validates_before_and_after_atomic_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    task, _report = _make_refresh_fixture(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_collect(_tasks, **kwargs):
        calls.append(kwargs)
        return [{"status": "ok"}] if len(calls) == 2 else []

    monkeypatch.setattr(renderer, "collect_existing_results", fake_collect)

    results = renderer.refresh_existing_reports(
        [task],
        ffmpeg=tmp_path / "ffmpeg.exe",
        av1_cq=38,
    )

    assert results == [{"status": "ok"}]
    assert calls[0]["allow_stale_durable_paths"] is True
    assert "allow_stale_durable_paths" not in calls[1]
    assert [call["full_decode"] for call in calls] == [False, False]
    refreshed = json.loads(task.direct_report.read_text(encoding="utf-8"))
    assert refreshed["video"]["video"] == str(task.video_output.resolve())


def test_parser_exposes_explicit_report_refresh_flag():
    args = renderer.make_parser().parse_args(
        [
            "--manifest",
            "synthetic-album.json",
            "--report-only",
            "--refresh-existing-reports",
        ]
    )

    assert args.report_only is True
    assert args.refresh_existing_reports is True
