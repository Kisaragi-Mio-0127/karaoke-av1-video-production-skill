from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "integration" / "strangeutagame" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

renderer = importlib.import_module("render_karaoke_direct_av1_420_album")


def _spectrum_task(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=tmp_path,
        visual_style="spectrum",
        sug_path=tmp_path / "timing.sug",
        track=SimpleNamespace(
            song_id="sample",
            title="Sample Song",
            audio_path=tmp_path / "audio.flac",
            numbered_video_filename="01 Sample Song.mp4",
            artifact_slug="sample-song",
        ),
        composition_path=tmp_path / "composition_spectrum.png",
        vinyl_path=None,
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=12.5,
        profile="wide",
        ass_output=tmp_path / "timing" / "wide" / "sample-song.ass",
    )


def test_public_spectrum_command_omits_vinyl_and_isolates_artifacts(
    tmp_path: Path,
) -> None:
    (task,) = renderer.configure_av1_tasks(
        (_spectrum_task(tmp_path),),
        root=tmp_path,
    )
    command = renderer.build_preview_command(
        task,
        temporary_video=tmp_path / "output.mp4",
        temporary_lossless_video=None,
        temporary_ass=tmp_path / "output.ass",
        temporary_report=tmp_path / "output.json",
        preview_script=tmp_path / "preview.py",
        av1_cq=38,
    )

    renderer.validate_direct_source_command(command)
    assert command[command.index("--visual-style") + 1] == "spectrum"
    assert "--vinyl" not in command
    assert "--vinyl-motion" not in command
    assert task.video_output.parent == (
        tmp_path / "video" / "av1-420" / "spectrum" / "wide"
    ).resolve()
    assert task.direct_report.parent == (
        tmp_path / "validation" / "spectrum" / "wide"
    ).resolve()
    assert not any(key.startswith("vinyl") for key in renderer._source_record(task))


def test_public_both_styles_serialize_per_song_profile() -> None:
    tasks = [
        SimpleNamespace(
            profile="wide",
            visual_style=style,
            track=SimpleNamespace(song_id=song_id),
        )
        for song_id in ("sample-a", "sample-b")
        for style in ("vinyl", "spectrum")
    ]

    groups = renderer.group_tasks_for_render(tasks)

    assert [[task.visual_style for task in group] for group in groups] == [
        ["vinyl", "spectrum"],
        ["vinyl", "spectrum"],
    ]


def test_public_parser_keeps_container_diagnostics_opt_in() -> None:
    default = renderer.make_parser().parse_args([])
    both = renderer.make_parser().parse_args(["--visual-style", "both"])

    assert default.visual_style == "vinyl"
    assert default.lossless_companion is False
    assert default.full_decode is False
    assert both.visual_style == "both"


def test_public_render_one_result_declares_visual_style() -> None:
    tree = ast.parse(Path(renderer.__file__).read_text(encoding="utf-8"))
    render_one = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_one"
    )
    return_keys = {
        key.value
        for node in ast.walk(render_one)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
        for key in node.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    assert "visual_style" in return_keys


def test_public_main_accepts_single_track_with_both_visual_styles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    track = SimpleNamespace(song_id="sample")
    album = SimpleNamespace(deliverable_dir=tmp_path, tracks=(track,))
    tasks = tuple(
        SimpleNamespace(profile="wide", visual_style=style, track=track)
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
            {"visual_style": style, "profile": "wide", "song_id": "sample"}
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
