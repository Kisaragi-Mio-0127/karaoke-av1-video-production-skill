from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import karaoke_full_auto as full_auto
from scripts import karaoke_model_paths


def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str):
    project = tmp_path / "repo"
    audio = project / "audio" / "mix.flac"
    source = project / "frozen.json"
    model = project / "models" / "mms" / "model.pt"
    whisper = project / "models" / "whisper"
    for directory in (audio.parent, model.parent, whisper):
        directory.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"mix")
    model.write_bytes(b"mms")
    source.write_text(
        json.dumps({"songs": {"song": {"lrc": "[00:01.00]line"}}}),
        encoding="utf-8",
    )
    manifest = project / "album.json"
    manifest.write_text("{}", encoding="utf-8")
    cover = project / "cover.jpg"
    cover.write_bytes(b"cover")
    track = SimpleNamespace(
        song_id="song",
        language=language,
        audio_path=audio,
        timing_stem="song_slug",
    )
    album = SimpleNamespace(tracks=(track,), project_root=project)
    monkeypatch.setattr(full_auto, "load_album_manifest", lambda *_a, **_k: album)
    args = full_auto.build_parser().parse_args(
        [
            "--manifest", str(manifest), "--song-id", "song",
            "--source", str(source),
            "--output-dir", str(project / ".render-work" / "run"),
            "--cover", str(cover),
        ]
    )
    return SimpleNamespace(
        project=project, audio=audio, source=source, manifest=manifest, cover=cover,
        model=model, whisper=whisper, track=track, album=album, args=args,
    )


def test_ja_full_auto_forwards_nonblocking_defaults_to_staged_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch, "ja")
    calls: list[str] = []

    def fake_msst(sources, **_kwargs):
        calls.append("msst")
        vocals = env.project / ".cache" / "msst-vocals" / env.audio.stem / "Vocals.wav"
        vocals.parent.mkdir(parents=True)
        vocals.write_bytes(b"stem")
        assert sources == [env.audio]
        return [vocals]

    def fake_timing(argv):
        calls.append("timing")
        output = Path(argv[argv.index("--output-root") + 1])
        sug = output / "timing" / "song_slug.sug"
        sug.parent.mkdir(parents=True)
        sug.write_text("{}", encoding="utf-8")
        return 1

    captured = {}

    def fake_wrapper(args):
        calls.append("wrapper")
        captured["args"] = args
        return {"status": "rendered-with-fallback"}

    monkeypatch.setattr(full_auto.msst, "prepare", fake_msst)
    monkeypatch.setattr(full_auto.karaoke_timing, "main", fake_timing)
    real_module = full_auto._mms_module("ja")
    monkeypatch.setattr(
        full_auto,
        "_mms_module",
        lambda _language: SimpleNamespace(
            make_parser=real_module.make_parser,
            run_mms_workflow=fake_wrapper,
        ),
    )

    report = full_auto.run_full_auto(env.args, allowed_languages=frozenset({"ja"}))

    assert calls == ["msst", "timing", "wrapper"]
    assert report["status"] == "rendered-with-fallback"
    assert captured["args"].quality_policy == "auto-fallback"
    assert captured["args"].pronunciation_validation == "optional"
    assert captured["args"].recognition_audits == []


def test_plan_rejects_existing_nonprivate_and_wrong_language_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch, "ja")
    env.args.output_dir = env.project / "outside"
    with pytest.raises(full_auto.FullAutoError, match="new child"):
        full_auto.build_plan(env.args)

    env.args.output_dir = env.project / ".render-work" / "run"
    with pytest.raises(full_auto.FullAutoError, match="supports only en, zh"):
        full_auto.build_plan(env.args, allowed_languages=frozenset({"zh", "en"}))


def test_full_auto_validates_and_forwards_subtitle_overlay_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch, "ja")
    background_video = env.project / "footage.mp4"
    background_video.write_bytes(b"video")
    env.args.output_mode = "subtitle-overlay"
    env.args.background_video = background_video

    plan = full_auto.build_plan(env.args)
    wrapper_args = full_auto._wrapper_args(plan, env.args)

    assert wrapper_args.output_mode == "subtitle-overlay"
    assert wrapper_args.background_video == background_video.resolve()
    assert wrapper_args.color_policy == "project"


def test_full_auto_rejects_background_video_errors_before_output_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch, "ja")
    background_video = env.project / "footage.mp4"
    background_video.write_bytes(b"video")
    env.args.background_video = background_video

    with pytest.raises(full_auto.FullAutoError, match="requires"):
        full_auto.build_plan(env.args)

    env.args.output_mode = "subtitle-overlay"
    env.args.background_video = env.project / "missing.mp4"
    with pytest.raises(full_auto.FullAutoError, match="background video does not exist"):
        full_auto.build_plan(env.args)
    assert not env.args.output_dir.exists()


def test_models_cannot_be_selected_from_cache(tmp_path: Path, monkeypatch):
    env = _environment(tmp_path, monkeypatch, "ja")
    cached = env.project / ".cache" / "torch" / "model.pt"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached")
    env.args.mms_model_path = cached
    with pytest.raises(full_auto.FullAutoError, match="must be selected from"):
        full_auto.build_plan(env.args)


def test_japanese_full_auto_selects_nextfire_only_by_explicit_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch, "ja")
    model_dir = (
        env.project
        / "models"
        / "hf"
        / "nextfire-mms-ja-latn"
    )
    model_dir.mkdir(parents=True)
    for name in (
        "config.json",
        "model.safetensors",
        "processor_config.json",
        "tokenizer_config.json",
        "vocab.json",
    ):
        (model_dir / name).write_bytes(b"model")
    (model_dir / "MODEL_PROVENANCE.json").write_text(
        json.dumps(
            {
                "repository": karaoke_model_paths.NEXTFIRE_JA_LATN_REPOSITORY,
                "revision": karaoke_model_paths.NEXTFIRE_JA_LATN_REVISION,
                "model_license": "AGPL-3.0",
                "base_model_license": "CC-BY-NC-4.0",
            }
        ),
        encoding="utf-8",
    )
    env.args.mms_backend = "nextfire-ja-latn"

    plan = full_auto.build_plan(env.args, allowed_languages=frozenset({"ja"}))
    wrapper = full_auto._wrapper_args(plan, env.args)

    assert plan.mms_model == (model_dir / "model.safetensors").resolve()
    assert plan.mms_backend == "nextfire-ja-latn"
    assert wrapper.mms_backend == "nextfire-ja-latn"
    assert wrapper.mms_model_path is None


def test_explicit_artwork_must_exist_before_output_is_created(tmp_path, monkeypatch):
    env = _environment(tmp_path, monkeypatch, "zh")
    env.args.cover = env.project / "missing-cover.jpg"
    with pytest.raises(full_auto.FullAutoError, match="explicit cover does not exist"):
        full_auto.build_plan(env.args)
    assert not env.args.output_dir.exists()


def test_refresh_source_allows_missing_destination_and_forwards_netease_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch, "ja")
    env.source.unlink()
    env.args.refresh_source = True
    env.args.netease_song_id = "123456"

    plan = full_auto.build_plan(env.args, allowed_languages=frozenset({"ja"}))
    timing_args = full_auto._timing_arguments(plan, env.args)

    assert timing_args[timing_args.index("--netease-song-id") + 1] == "123456"
    assert "--refresh-source" in timing_args
