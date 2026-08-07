from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "integration" / "strangeutagame" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

planning = importlib.import_module("karaoke_direct_album_planning")
av1 = importlib.import_module("render_karaoke_direct_av1_420_album")
legacy = importlib.import_module("render_karaoke_direct_av1_album")
hevc = importlib.import_module("render_karaoke_direct_hevc444_album")


def _task(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        sug_path=tmp_path / "timing.sug",
        track=SimpleNamespace(audio_path=tmp_path / "audio.flac"),
        composition_path=tmp_path / "composition.png",
        vinyl_path=tmp_path / "vinyl.png",
        fonts_dir=tmp_path / "fonts",
        font_file=tmp_path / "font.ttf",
        duration_seconds=12.5,
        profile="standard",
    )


def test_public_legacy_entry_preserves_argv_exit_and_warns(monkeypatch, capsys) -> None:
    observed = []

    def fake_main(argv):
        observed.append(argv)
        return 19

    monkeypatch.setattr(legacy, "_hevc_main", fake_main)

    assert legacy.main(["--profile", "wide"]) == 19
    assert observed == [["--profile", "wide"]]
    assert "DEPRECATED" in capsys.readouterr().err


def test_public_legacy_entry_supports_direct_and_module_execution() -> None:
    script = SCRIPTS / "render_karaoke_direct_av1_album.py"
    commands = (
        [sys.executable, str(script), "--help"],
        [sys.executable, "-m", "render_karaoke_direct_av1_album", "--help"],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=SCRIPTS,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert completed.returncode == 0
        assert "DEPRECATED" in completed.stderr
        assert "--ass-only" in completed.stdout


def test_public_renderers_share_neutral_planning_and_keep_codecs_isolated(
    tmp_path: Path,
) -> None:
    assert hevc.plan_tasks is planning.plan_tasks
    assert av1.render_core is planning
    assert "render_karaoke_direct_av1_album as render_core" not in Path(
        av1.__file__
    ).read_text(encoding="utf-8")

    task = _task(tmp_path)
    hevc_command = hevc.build_preview_command(
        task,
        temporary_video=tmp_path / "hevc.mp4",
        temporary_ass=tmp_path / "hevc.ass",
        temporary_report=tmp_path / "hevc.json",
        preview_script=tmp_path / "preview.py",
    )
    av1_command = av1.build_preview_command(
        task,
        temporary_video=tmp_path / "av1.mp4",
        temporary_lossless_video=tmp_path / "av1.mkv",
        temporary_ass=tmp_path / "av1.ass",
        temporary_report=tmp_path / "av1.json",
        preview_script=tmp_path / "preview.py",
        av1_cq=38,
    )

    assert hevc_command[hevc_command.index("--video-encoder") + 1] == "hevc_nvenc_444"
    assert "--hevc-cq" in hevc_command and "--av1-cq" not in hevc_command
    assert av1_command[av1_command.index("--video-encoder") + 1] == "av1_nvenc"
    assert "--av1-cq" in av1_command and "--hevc-cq" not in av1_command
    assert "--lossless-output" not in av1_command


def test_public_hevc_batch_size_is_manifest_driven() -> None:
    source = Path(hevc.__file__).read_text(encoding="utf-8")
    for project_specific_phrase in (
        "five-track",
        "all five",
        "five songs",
        "all 10",
        "exactly 10",
        "ten (or selected)",
    ):
        assert project_specific_phrase not in source.casefold()
    assert "len(PROFILES) * len(song_ids)" in source
    assert "every manifest song and both profiles" in source
