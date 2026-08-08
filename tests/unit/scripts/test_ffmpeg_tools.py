from __future__ import annotations

from pathlib import Path

import pytest

from integration.strangeutagame.scripts.karaoke_common import ffmpeg_tools


def _tool(root: Path, name: str) -> Path:
    suffix = ".exe" if ffmpeg_tools.os.name == "nt" else ""
    return root / "tools" / "ffmpeg" / "bin" / f"{name}{suffix}"


def test_project_pair_is_preferred_over_path(tmp_path: Path, monkeypatch):
    ffmpeg = _tool(tmp_path, "ffmpeg")
    ffprobe = _tool(tmp_path, "ffprobe")
    ffmpeg.parent.mkdir(parents=True)
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")
    monkeypatch.setattr(ffmpeg_tools.shutil, "which", lambda _name: "C:/path/tool.exe")

    assert ffmpeg_tools.resolve_ffmpeg(root=tmp_path) == ffmpeg.resolve()
    assert ffmpeg_tools.resolve_ffprobe(root=tmp_path) == ffprobe.resolve()


def test_explicit_override_is_first(tmp_path: Path):
    project_ffmpeg = _tool(tmp_path, "ffmpeg")
    project_ffmpeg.parent.mkdir(parents=True)
    project_ffmpeg.write_bytes(b"project")
    explicit = tmp_path / "custom-ffmpeg.exe"
    explicit.write_bytes(b"custom")

    assert ffmpeg_tools.resolve_ffmpeg(explicit, root=tmp_path) == explicit.resolve()


def test_ffprobe_missing_has_install_hint(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FFPROBE", raising=False)
    monkeypatch.setattr(ffmpeg_tools.shutil, "which", lambda _name: None)

    with pytest.raises(ffmpeg_tools.FFmpegToolError, match="tools/ffmpeg/bin"):
        ffmpeg_tools.resolve_ffprobe(root=tmp_path)
