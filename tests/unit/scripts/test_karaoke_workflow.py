from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[3] / "integration" / "strangeutagame"
if str(BUNDLE) not in sys.path:
    sys.path.insert(0, str(BUNDLE))

from scripts import karaoke_album as _karaoke_album  # noqa: E402

_karaoke_album.DEFAULT_MANIFEST_PATH = (
    BUNDLE.parents[1] / "examples" / "album.example.json"
)
_load_public_manifest = _karaoke_album.load_album_manifest


def _load_example_manifest(path, *, require_five_tracks=True):
    del require_five_tracks
    return _load_public_manifest(path, require_five_tracks=False)


_karaoke_album.load_album_manifest = _load_example_manifest

from scripts import karaoke_workflow as workflow  # noqa: E402
from scripts.run_karaoke_japanese_workflow import (  # noqa: E402
    make_parser as japanese_parser,
)


def _config(tmp_path: Path) -> workflow.WorkflowConfig:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    paths = {
        name: inputs / filename
        for name, filename in {
            "sug": "song.sug",
            "audio": "song.flac",
            "composition": "composition.png",
            "vinyl": "vinyl.png",
            "font": "font.ttf",
            "ffmpeg": "ffmpeg.exe",
        }.items()
    }
    paths["sug"].write_text('{"sentences":[]}', encoding="utf-8")
    for name, path in paths.items():
        if name != "sug":
            path.write_bytes(name.encode("ascii"))
    return workflow.WorkflowConfig(
        sug=paths["sug"],
        audio=paths["audio"],
        composition=paths["composition"],
        canonical_vinyl=paths["vinyl"],
        output_dir=tmp_path / "output",
        language="ja",
        layout="wide",
        title="Example",
        artist="Singer",
        album_title="Album",
        album_artist="Album Artist",
        fonts_dir=inputs,
        font_file=paths["font"],
        smoke_duration=5.0,
        pronunciation_validation="optional",
        ffmpeg=paths["ffmpeg"],
    )


def test_internal_timing_override_is_paired_in_preflight_and_final_commands(
    tmp_path: Path,
):
    overrides = tmp_path / "evidence" / "timing_overrides.json"
    overrides.parent.mkdir()
    overrides.write_text(
        '{"schema_version":"karaoke-timing-overrides/v2"}',
        encoding="utf-8",
    )
    config = replace(
        _config(tmp_path),
        timing_overrides=overrides,
        timing_override_song_id="song-ja",
    )
    kwargs = {
        "generated_vinyl": tmp_path / "generated-vinyl.png",
        "ass_path": tmp_path / "karaoke.ass",
        "report_path": tmp_path / "render-report.json",
        "output_path": tmp_path / "karaoke.mp4",
        "duration": 5.0,
    }

    for command in (
        workflow.build_ass_command(config, **kwargs),
        workflow.build_render_command(config, **kwargs),
    ):
        assert command[command.index("--timing-overrides") + 1] == str(
            overrides.resolve()
        )
        assert command[command.index("--song-id") + 1] == "song-ja"


def test_internal_timing_override_configuration_must_be_paired(tmp_path: Path):
    config = replace(
        _config(tmp_path),
        timing_overrides=tmp_path / "timing_overrides.json",
    )

    with pytest.raises(workflow.KaraokeWorkflowError, match="provided together"):
        workflow.validate_visual_contract(config)


def test_legacy_japanese_cli_exposes_no_mms_interface():
    parser = japanese_parser()
    option_strings = {
        option for action in parser._actions for option in action.option_strings
    }

    assert "--timing-overrides" not in option_strings
    assert "--model-path" not in option_strings
    assert all("mms" not in option.casefold() for option in option_strings)
