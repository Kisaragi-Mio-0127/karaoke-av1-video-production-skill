from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import prepare_karaoke_msst_vocals as prepare


def test_parser_exposes_partial_manifest_gate():
    args = prepare.build_parser().parse_args(
        ["--manifest", "album.json", "--allow-partial-manifest"]
    )

    assert args.allow_partial_manifest is True


def test_cli_requires_an_explicit_manifest():
    with pytest.raises(SystemExit):
        prepare.build_parser().parse_args([])


def test_import_does_not_load_a_default_manifest():
    assert not hasattr(prepare, "DEFAULT_ALBUM")


def test_msst_script_prefers_explicit_environment_path(tmp_path: Path, monkeypatch):
    script = tmp_path / "custom-msst.py"
    script.write_text("# adapter\n", encoding="utf-8")
    monkeypatch.setenv(prepare.MSST_PREPARATION_ENV, str(script))

    assert prepare.resolve_msst_preparation_script() == script.resolve()


def test_msst_script_auto_discovers_supported_sibling(tmp_path: Path, monkeypatch):
    root = tmp_path / "submaker" / "StrangeUtaGame"
    root.mkdir(parents=True)
    script = tmp_path / "TTS_Test" / "scripts" / prepare.MSST_PREPARATION_NAME
    script.parent.mkdir(parents=True)
    script.write_text("# adapter\n", encoding="utf-8")
    monkeypatch.delenv(prepare.MSST_PREPARATION_ENV, raising=False)
    monkeypatch.setattr(prepare, "ROOT", root)

    assert prepare.resolve_msst_preparation_script() == script.resolve()


def test_missing_msst_script_reports_configuration_instead_of_cwd(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "isolated" / "StrangeUtaGame"
    root.mkdir(parents=True)
    monkeypatch.delenv(prepare.MSST_PREPARATION_ENV, raising=False)
    monkeypatch.setattr(prepare, "ROOT", root)

    with pytest.raises(FileNotFoundError, match=prepare.MSST_PREPARATION_ENV):
        prepare.resolve_msst_preparation_script()


def test_prepare_accepts_one_source_and_injected_cache_root(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "single.mp3"
    source.write_bytes(b"audio")
    cache_root = tmp_path / "custom-cache"
    dependencies = {
        "model": tmp_path / "model.ckpt",
        "config": tmp_path / "config.yaml",
        "script": tmp_path / "runner.py",
    }
    monkeypatch.setattr(prepare, "_dependency_paths", lambda _module: dependencies)
    monkeypatch.setattr(prepare, "_is_valid_output", lambda **_kwargs: True)

    result = prepare.prepare(
        source,
        msst_module=SimpleNamespace(),
        cache_root=cache_root,
    )

    assert result == [(cache_root / "msst-vocals" / "single" / "Vocals.wav").resolve()]
    assert (cache_root / "msst-input").is_dir()
    assert (cache_root / "msst-runtime").is_dir()


def test_main_passes_partial_manifest_policy_to_loader(
    tmp_path: Path,
    monkeypatch,
):
    manifest_path = tmp_path / "album.json"
    manifest_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[Path, bool]] = []

    def fake_load(path: Path, *, require_five_tracks: bool):
        calls.append((path, require_five_tracks))
        return object()

    monkeypatch.setattr(prepare, "load_album_manifest", fake_load)
    monkeypatch.setattr(prepare, "prepare", lambda *_args, **_kwargs: [])

    prepare.main(
        [
            "--manifest",
            str(manifest_path),
            "--allow-partial-manifest",
        ]
    )

    assert calls == [(manifest_path.resolve(), False)]
