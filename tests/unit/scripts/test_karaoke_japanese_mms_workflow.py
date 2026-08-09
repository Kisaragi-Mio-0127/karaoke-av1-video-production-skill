from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_karaoke_japanese_mms_workflow as mms_workflow
from scripts.karaoke_workflow import KaraokeWorkflowError
from scripts.sug_ruby import span_hash, sug_hash, write_review_sidecar


def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "repo"
    deliverables = project / "private-canonical"
    source_dir = deliverables / "sources"
    timing_dir = deliverables / "timing"
    audio_dir = project / "audio"
    vocals_root = project / ".cache" / "msst-vocals"
    for directory in (source_dir, timing_dir, audio_dir):
        directory.mkdir(parents=True, exist_ok=True)

    song_id = "song-ja"
    audio = audio_dir / "mix.flac"
    audio.write_bytes(b"mix-audio")
    source = source_dir / "netease_lyrics.json"
    source.write_text('{"songs":{}}', encoding="utf-8")
    sug = timing_dir / f"{song_id}_slug.sug"
    _sug_document = {
        "version": "0.3.0",
        "metadata": {"language": "ja"},
        "singers": [{"id": "singer", "name": "Singer", "is_default": True}],
        "sentences": [
            {
                "singer_id": "singer",
                "characters": [
                    {
                        "char": "今",
                        "timestamps": [1000],
                        "linked_to_next": True,
                        "ruby": {"parts": [{"text": "きょ", "offset_ms": 0}]},
                    },
                    {
                        "char": "日",
                        "timestamps": [1400],
                        "sentence_end_ts": 2000,
                        "linked_to_next": False,
                        "ruby": {"parts": [{"text": "う", "offset_ms": 0}]},
                    },
                ]
            }
        ],
        "media_path": "old-audio.flac",
    }
    sug.write_text(json.dumps(_sug_document, ensure_ascii=False), encoding="utf-8")
    vocals = vocals_root / audio.stem / "Vocals.wav"
    vocals.parent.mkdir(parents=True)
    vocals.write_bytes(b"msst-vocals")
    model = project / ".cache" / "torch" / "hub" / "checkpoints" / "model.pt"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"mms-model")
    manifest = project / "album.json"
    manifest.write_text('{"schema_version":"karaoke-album/v1"}', encoding="utf-8")
    composition = project / "composition.png"
    composition.write_bytes(b"composition")
    vinyl = project / "vinyl.png"
    vinyl.write_bytes(b"vinyl")
    fonts = project / "fonts"
    fonts.mkdir()
    font = fonts / "font.ttf"
    font.write_bytes(b"font")

    track = SimpleNamespace(
        song_id=song_id,
        language="ja",
        timing_stem=f"{song_id}_slug",
        artifact_slug="generic-artwork",
        audio_path=audio,
        audio_sha256=mms_workflow.sha256_file(audio),
        title="Song",
        artist="Singer",
    )
    album = SimpleNamespace(
        tracks=(track,),
        deliverable_dir=deliverables,
        project_root=project,
        title="Album",
        artist="Album Artist",
    )
    monkeypatch.setattr(
        mms_workflow,
        "load_album_manifest",
        lambda _path, *, require_five_tracks=True: album,
    )
    args = mms_workflow.make_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--song-id",
            song_id,
            "--composition",
            str(composition),
            "--mms-model-path",
            str(model),
            "--vinyl",
            str(vinyl),
            "--fonts-dir",
            str(fonts),
            "--font-file",
            str(font),
            "--output-dir",
            str(project / "private-run"),
        ]
    )
    return SimpleNamespace(
        args=args,
        album=album,
        track=track,
        project=project,
        deliverables=deliverables,
        source=source,
        sug=sug,
        audio=audio,
        vocals=vocals,
        model=model,
        sug_document=_sug_document,
    )


def _audit_document(env, *, model: Path | None = None) -> dict[str, object]:
    selected_model = model or env.model
    return {
        "schema_version": mms_workflow.AUDIT_SCHEMA,
        "gate_ok": True,
        "manifest_sha256": mms_workflow.sha256_file(env.args.manifest),
        "manifest_path": str(env.args.manifest),
        "lyric_source_sha256": mms_workflow.sha256_file(env.source),
        "lyric_source_path": str(env.source),
        "model_path": str(selected_model),
        "model_sha256": mms_workflow.sha256_file(selected_model),
        "songs": [
            {
                "song_id": env.track.song_id,
                "language": "ja",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "今日",
                        "units": [
                            {"character_index": 0},
                            {"character_index": 1},
                        ],
                        "comparisons": [
                            {"character_index": 0},
                            {"character_index": 1},
                        ],
                        "mix_units": [
                            {"character_index": 0},
                            {"character_index": 1},
                        ],
                        "dual_audio_comparisons": [
                            {"character_index": 0},
                            {"character_index": 1},
                        ],
                    }
                ],
                "sug_path": str(env.sug),
                "sug_sha256": mms_workflow.sha256_file(env.sug),
                "vocals_path": str(env.vocals),
                "vocals_sha256": mms_workflow.sha256_file(env.vocals),
                "mix_path": str(env.audio),
                "mix_sha256": mms_workflow.sha256_file(env.audio),
            }
        ],
    }


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _build_document(env, audit_path: Path) -> dict[str, object]:
    return {
        "schema_version": mms_workflow.OVERRIDES_SCHEMA,
        "gate_ok": True,
        "unresolved": [],
        "mms_provenance": {
            "audit": str(audit_path),
            "audit_sha256": "6" * 64,
            "model_path": str(env.model),
            "model_sha256": "7" * 64,
            "lyric_source_path": str(env.source),
            "lyric_source_sha256": "8" * 64,
            "target_song_ids": [env.track.song_id],
        },
        "songs": {
            env.track.song_id: {
                "lines": {
                    "0": {
                        "character_overrides_ms": {"0": 100},
                        "release_override_ms": 1800,
                        "visual_release_overrides_ms": {},
                    }
                }
            }
        },
    }


def test_private_mms_workflow_chains_reusable_stages_and_only_renders_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    canonical_sidecar = env.sug.with_suffix(".ruby-review.json")
    write_review_sidecar(
        canonical_sidecar,
        sug_hash_before=sug_hash(env.sug_document),
        sug_hash_after=sug_hash(env.sug_document),
        records=[
            {
                "sentence_id": "sentence:0",
                "start": 0,
                "end": 2,
                "surface": "今日",
                "source": "project-auto-check",
                "review_status": "machine-fill",
                "confidence": None,
                "evidence": ["whole-sentence-tokenizer"],
                "model_prompt_version": None,
                "generation_id": "generated-ruby",
                "before_hash": span_hash(env.sug_document, 0, 0, 2),
                "after_hash": span_hash(env.sug_document, 0, 0, 2),
            }
        ],
        generation_id="canonical-generation",
    )
    canonical_sidecar_bytes = canonical_sidecar.read_bytes()
    background_video = env.project / "footage.mp4"
    background_video.write_bytes(b"video")
    env.args.output_mode = "subtitle-overlay"
    env.args.background_video = background_video
    calls: dict[str, object] = {}

    def audit_runner(**kwargs):
        calls["audit"] = kwargs
        document = _audit_document(env)
        document["manifest_sha256"] = "0" * 64
        document["lyric_source_sha256"] = "1" * 64
        document["model_sha256"] = "2" * 64
        document["songs"][0]["sug_sha256"] = "3" * 64
        document["songs"][0]["vocals_sha256"] = "4" * 64
        document["songs"][0]["mix_sha256"] = "5" * 64
        _write_json(kwargs["output_path"], document)
        return document

    def build_runner(**kwargs):
        calls["build"] = kwargs
        document = _build_document(env, kwargs["audit_path"])
        document["songs"][env.track.song_id]["lines"]["0"][
            "visual_release_overrides_ms"
        ] = {"0": 220}
        _write_json(kwargs["output_path"], document)
        return document

    def renderer(config):
        calls["render"] = config
        assert config.timing_overrides == (
            env.args.output_dir / "build" / "timing_overrides.json"
        ).resolve()
        assert config.timing_overrides is not None
        assert config.sug == (
            env.args.output_dir / "build" / f"{env.sug.stem}.mms-editable.sug"
        ).resolve()
        assert config.timing_override_song_id == env.track.song_id
        assert config.timing_override_song_id is not None
        assert config.allow_network is False
        assert config.output_mode == "subtitle-overlay"
        assert config.background_video == background_video.resolve()
        assert config.color_policy == "project"
        companion_sidecar = config.sug.with_suffix(".ruby-review.json")
        assert companion_sidecar.is_file()
        current_sug = json.loads(config.sug.read_text(encoding="utf-8"))
        current_sidecar = json.loads(companion_sidecar.read_text(encoding="utf-8"))
        assert current_sidecar["sug_hash_after"] == sug_hash(current_sug)
        assert current_sidecar["records"][0]["after_hash"] == span_hash(
            current_sug, 0, 0, 2
        )
        config.output_dir.mkdir()
        _write_json(config.output_dir / "workflow-report.json", {"status": "ok"})
        return {"status": "ok"}

    report = mms_workflow.run_mms_workflow(
        env.args,
        audit_runner=audit_runner,
        build_runner=build_runner,
        renderer=renderer,
    )

    assert report["status"] == "ok"
    assert {path.name for path in env.args.output_dir.iterdir()} == {
        "audit",
        "build",
        "render",
        mms_workflow.REPORT_NAME,
    }
    build_stage = next(stage for stage in report["stages"] if stage["name"] == "build")
    assert build_stage["render_contract"]["visual_release"]["applied_to_render"] is True
    assert build_stage["render_contract"]["visual_release"]["fallback"] is None
    assert build_stage["render_contract"]["character_overrides"]["applied_to_render"] is True
    render_stage = next(stage for stage in report["stages"] if stage["name"] == "render")
    assert render_stage["character_overrides_applied_to_render"] is True
    timing_output = report["outputs"]["timing_overrides"]
    companion_output = report["outputs"]["mms_editable_sug"]
    assert companion_output["paired_timing_overrides"] == timing_output
    assert companion_output["ruby_review_sidecar"] == report["outputs"][
        "ruby_review_sidecar"
    ]
    assert companion_output["ruby_review_sidecar"]["path"].endswith(
        ".mms-editable.ruby-review.json"
    )
    assert render_stage["timing_overrides"] == timing_output
    assert render_stage["mms_editable_sug"] == companion_output
    assert calls["audit"]["song_ids"] == (env.track.song_id,)
    assert calls["audit"]["model_path"] == env.model.resolve()
    assert calls["audit"]["allow_partial_manifest"] is True
    assert calls["build"]["song_ids"] == (env.track.song_id,)
    assert calls["build"]["allow_partial_manifest"] is True
    companion = json.loads(calls["render"].sug.read_text(encoding="utf-8"))
    assert companion["sentences"][0]["characters"][0]["timestamps"] == [100]
    assert companion["sentences"][0]["characters"][1]["sentence_end_ts"] == 1800
    assert companion["sentences"][0]["characters"][0]["linked_to_next"] is True
    assert companion["sentences"][0]["characters"][0]["ruby"]["parts"][0]["text"] == "きょ"
    assert "visual_release_overrides_ms" not in companion
    assert env.sug.read_text(encoding="utf-8") == json.dumps(
        env.sug_document, ensure_ascii=False
    )
    assert canonical_sidecar.read_bytes() == canonical_sidecar_bytes


def test_explicit_mms_model_takes_priority_is_passed_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    explicit_model = env.project / "models" / "approved-mms.pt"
    explicit_model.parent.mkdir()
    explicit_model.write_bytes(b"explicit-approved-model")
    env.args.mms_model_path = explicit_model
    captured: dict[str, object] = {}

    def stop_after_capture(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop-after-model-capture")

    with pytest.raises(RuntimeError, match="stop-after-model-capture"):
        mms_workflow.run_mms_workflow(
            env.args,
            audit_runner=stop_after_capture,
            build_runner=lambda **_kwargs: {},
        )

    assert captured["model_path"] == explicit_model.resolve()
    report = json.loads(
        (env.args.output_dir / mms_workflow.REPORT_NAME).read_text(encoding="utf-8")
    )
    assert report["mms_model"] == {
        "selection": "explicit",
        "path": str(explicit_model.resolve()),
        "size": explicit_model.stat().st_size,
        "sha256": mms_workflow.sha256_file(explicit_model),
    }


def test_nextfire_backend_is_explicit_and_forwards_pinned_local_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    model = env.project / "models" / "hf" / "nextfire-mms-ja-latn" / "model.safetensors"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"nextfire-model")
    env.args.mms_backend = "nextfire-ja-latn"
    env.args.mms_model_path = None
    monkeypatch.setattr(
        mms_workflow,
        "resolve_alignment_model_path",
        lambda backend, *, explicit_mms_model: (
            model.resolve()
            if backend == "nextfire-ja-latn" and explicit_mms_model is None
            else pytest.fail("unexpected backend resolution")
        ),
    )
    captured: dict[str, object] = {}

    def stop_after_capture(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop-after-nextfire-capture")

    with pytest.raises(RuntimeError, match="stop-after-nextfire-capture"):
        mms_workflow.run_mms_workflow(
            env.args,
            audit_runner=stop_after_capture,
            build_runner=lambda **_kwargs: {},
        )

    assert captured["backend"] == "nextfire-ja-latn"
    assert captured["model_path"] == model.resolve()
    report = json.loads(
        (env.args.output_dir / mms_workflow.REPORT_NAME).read_text(encoding="utf-8")
    )
    assert report["mms_backend"] == "nextfire-ja-latn"
    assert report["mms_model"]["selection"] == "project-models:nextfire-ja-latn"


def test_nextfire_backend_rejects_network_flag_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    env.args.mms_backend = "nextfire-ja-latn"
    env.args.mms_model_path = None
    env.args.allow_mms_network = True

    with pytest.raises(KaraokeWorkflowError, match="local-only"):
        mms_workflow.run_mms_workflow(env.args)

    assert not env.args.output_dir.exists()


def test_fully_empty_sidecar_creates_copy_companion_and_allows_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    rendered: dict[str, object] = {}

    def audit_runner(**kwargs):
        document = _audit_document(env)
        _write_json(kwargs["output_path"], document)
        return document

    def build_runner(**kwargs):
        document = _build_document(env, kwargs["audit_path"])
        document["songs"][env.track.song_id]["lines"] = {}
        _write_json(kwargs["output_path"], document)
        return document

    def renderer(config):
        rendered["config"] = config
        assert config.sug.name == f"{env.sug.stem}.mms-editable.sug"
        assert config.sug.parent.name == "build"
        assert config.timing_overrides is None
        assert config.timing_override_song_id is None
        companion = json.loads(config.sug.read_text(encoding="utf-8"))
        assert companion["sentences"][0]["characters"][0]["timestamps"] == [1000]
        assert companion["sentences"][0]["characters"][1]["sentence_end_ts"] == 2000
        config.output_dir.mkdir()
        _write_json(config.output_dir / "workflow-report.json", {"status": "ok"})
        return {"status": "ok"}

    report = mms_workflow.run_mms_workflow(
        env.args,
        audit_runner=audit_runner,
        build_runner=build_runner,
        renderer=renderer,
    )

    assert report["status"] == "ok"
    assert rendered["config"].timing_overrides is None
    assert rendered["config"].timing_override_song_id is None
    companion = Path(report["outputs"]["mms_editable_sug"]["path"])
    assert companion.is_file()
    assert report["outputs"]["release_sug"]["selection"] == (
        "mms-editable-companion"
    )
    assert report["outputs"]["release_sug"]["release_timing"] == (
        "companion-preserved-canonical-sentence-end"
    )
    build_stage = next(stage for stage in report["stages"] if stage["name"] == "build")
    assert build_stage["visual_release_override_count"] == 0
    assert build_stage["render_contract"]["visual_release"]["fallback"] == (
        "companion-preserved-canonical-sentence-end"
    )


def test_empty_visual_release_renders_mms_onset_with_preserved_sug_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    rendered: dict[str, object] = {}

    def audit_runner(**kwargs):
        document = _audit_document(env)
        _write_json(kwargs["output_path"], document)
        return document

    def build_runner(**kwargs):
        document = _build_document(env, kwargs["audit_path"])
        line = document["songs"][env.track.song_id]["lines"]["0"]
        line.pop("release_override_ms")
        _write_json(kwargs["output_path"], document)
        return document

    def renderer(config):
        rendered["config"] = config
        companion = json.loads(config.sug.read_text(encoding="utf-8"))
        assert companion["sentences"][0]["characters"][0]["timestamps"] == [100]
        assert companion["sentences"][0]["characters"][1]["sentence_end_ts"] == 2000
        assert config.timing_overrides is None
        assert config.timing_override_song_id is None
        config.output_dir.mkdir()
        _write_json(config.output_dir / "workflow-report.json", {"status": "ok"})
        return {"status": "ok"}

    report = mms_workflow.run_mms_workflow(
        env.args,
        audit_runner=audit_runner,
        build_runner=build_runner,
        renderer=renderer,
    )

    assert report["status"] == "ok"
    assert rendered["config"].sug != env.sug
    assert report["outputs"]["release_sug"]["selection"] == (
        "mms-editable-companion"
    )
    assert report["outputs"]["release_sug"]["release_timing"] == (
        "companion-preserved-canonical-sentence-end"
    )


def test_default_mms_model_records_project_models_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    env.args.mms_model_path = None
    monkeypatch.setattr(
        mms_workflow,
        "resolve_mms_model_path",
        lambda explicit: env.model.resolve() if explicit is None else explicit.resolve(),
    )

    preflight = mms_workflow.preflight(env.args)

    assert preflight.mms_model == env.model.resolve()
    assert preflight.mms_model_selection == "project-models"


def test_preflight_allows_single_track_manifest_and_disables_exact_five_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def load_manifest(path, *, require_five_tracks=True):
        captured["path"] = path
        captured["require_five_tracks"] = require_five_tracks
        return env.album

    monkeypatch.setattr(mms_workflow, "load_album_manifest", load_manifest)

    result = mms_workflow.preflight(env.args)

    assert result.track is env.track
    assert len(result.album.tracks) == 1
    assert captured == {
        "path": env.args.manifest.resolve(),
        "require_five_tracks": False,
    }


def test_preflight_rejects_output_mode_and_explicit_input_errors_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    background_video = env.project / "footage.mp4"
    background_video.write_bytes(b"video")
    env.args.background_video = background_video

    with pytest.raises(mms_workflow.KaraokeWorkflowError, match="requires"):
        mms_workflow.preflight(env.args)
    assert not env.args.output_dir.exists()

    env.args.output_mode = "subtitle-overlay"
    env.args.background_video = None
    env.args.metadata_source_audio = env.project / "missing-metadata.flac"
    with pytest.raises(mms_workflow.KaraokeWorkflowError, match="required non-empty file"):
        mms_workflow.preflight(env.args)
    assert not env.args.output_dir.exists()


def test_preflight_still_allows_five_track_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    other_tracks = tuple(
        SimpleNamespace(
            song_id=f"other-{index}",
            language="ja",
            timing_stem=f"other-{index}",
            audio_path=env.audio,
            title=f"Other {index}",
            artist="Singer",
        )
        for index in range(1, 5)
    )
    album = SimpleNamespace(**vars(env.album))
    album.tracks = (env.track, *other_tracks)
    monkeypatch.setattr(
        mms_workflow,
        "load_album_manifest",
        lambda _path, *, require_five_tracks=True: album,
    )

    result = mms_workflow.preflight(env.args)

    assert result.track is env.track
    assert len(result.album.tracks) == 5


@pytest.mark.parametrize("tracks_kind", ["empty", "duplicate-song-id"])
def test_preflight_rejects_missing_or_duplicate_selected_song_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tracks_kind: str,
):
    env = _environment(tmp_path, monkeypatch)
    album = SimpleNamespace(**vars(env.album))
    if tracks_kind == "empty":
        album.tracks = ()
    else:
        duplicate = SimpleNamespace(**vars(env.track))
        album.tracks = (env.track, duplicate)
    monkeypatch.setattr(
        mms_workflow,
        "load_album_manifest",
        lambda _path, *, require_five_tracks=True: album,
    )

    with pytest.raises(KaraokeWorkflowError, match="exactly one selected song-id"):
        mms_workflow.preflight(env.args)


def test_missing_explicit_mms_model_fails_before_output_without_cache_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    env.args.mms_model_path = env.project / "models" / "missing.pt"

    with pytest.raises(KaraokeWorkflowError, match="does not exist"):
        mms_workflow.run_mms_workflow(env.args)

    assert env.model.is_file()
    assert not env.args.output_dir.exists()


def test_preflight_failure_does_not_create_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    env.model.unlink()

    with pytest.raises(KaraokeWorkflowError, match="does not exist"):
        mms_workflow.run_mms_workflow(env.args)

    assert not env.args.output_dir.exists()


def test_quality_failure_preserves_companion_and_blocks_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    calls: list[str] = []

    def failed_audit(**kwargs):
        calls.append("audit")
        document = _audit_document(env)
        document["gate_ok"] = False
        document["unresolved"] = [{"reason": "manual-review"}]
        _write_json(kwargs["output_path"], document)
        return document

    def unresolved_build(**kwargs):
        calls.append("build")
        document = _build_document(env, kwargs["audit_path"])
        document["gate_ok"] = False
        document["unresolved"] = [{"line_index": 0}]
        _write_json(kwargs["output_path"], document)
        return document

    def fail_render(_config):
        calls.append("render")
        raise AssertionError("quality failure must not invoke renderer")

    with pytest.raises(KaraokeWorkflowError, match="quality gate failed"):
        mms_workflow.run_mms_workflow(
            env.args,
            audit_runner=failed_audit,
            build_runner=unresolved_build,
            renderer=fail_render,
        )

    report = json.loads(
        (env.args.output_dir / mms_workflow.REPORT_NAME).read_text(encoding="utf-8")
    )
    assert calls == ["audit", "build"]
    assert report["status"] == "review-required"
    assert report["quality_gate"]["ok"] is False
    assert report["release_decision"] == {
        "policy": "strict",
        "outcome": "review-required",
        "quality_gate_overridden": False,
    }
    companion = Path(report["outputs"]["mms_editable_sug"]["path"])
    assert companion.is_file()
    assert report["outputs"]["mms_editable_sug"]["paired_timing_overrides"] == report[
        "outputs"
    ]["timing_overrides"]
    assert report["stages"][-1]["status"] == "blocked"
    assert not (env.args.output_dir / "render").exists()


def test_auto_fallback_renders_structurally_valid_failed_quality_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    env.args.quality_policy = "auto-fallback"
    rendered: dict[str, object] = {}

    def failed_audit(**kwargs):
        document = _audit_document(env)
        document["gate_ok"] = False
        document["unresolved"] = [{"reason": "manual-review"}]
        document["unresolved_count"] = 1
        _write_json(kwargs["output_path"], document)
        return document

    def unresolved_build(**kwargs):
        document = _build_document(env, kwargs["audit_path"])
        document["gate_ok"] = False
        document["unresolved"] = [{"line_index": 0, "reason": "low-confidence"}]
        document["unresolved_count"] = 1
        _write_json(kwargs["output_path"], document)
        return document

    def renderer(config):
        rendered["config"] = config
        assert config.sug.name == f"{env.sug.stem}.mms-editable.sug"
        assert config.timing_overrides is None
        assert config.timing_override_song_id is None
        config.output_dir.mkdir()
        _write_json(config.output_dir / "workflow-report.json", {"status": "ok"})
        return {"status": "ok"}

    report = mms_workflow.run_mms_workflow(
        env.args,
        audit_runner=failed_audit,
        build_runner=unresolved_build,
        renderer=renderer,
    )

    assert report["status"] == "rendered-with-fallback"
    assert report["quality_gate"]["ok"] is False
    assert report["release_decision"] == {
        "policy": "auto-fallback",
        "outcome": "rendered-with-fallback",
        "quality_gate_overridden": True,
    }
    assert report["render_gate"] == {"ok": True}
    assert rendered["config"].sug == Path(report["outputs"]["mms_editable_sug"]["path"])
    assert report["stages"][-1]["status"] == "ok"
    audit = json.loads(
        (env.args.output_dir / "audit" / "mms_alignment_audit.json").read_text(
            encoding="utf-8"
        )
    )
    overrides = json.loads(
        (env.args.output_dir / "build" / "timing_overrides.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["unresolved"] == [{"reason": "manual-review"}]
    assert overrides["unresolved"] == [{"line_index": 0, "reason": "low-confidence"}]


@pytest.mark.parametrize("corruption", ["schema", "path", "token", "timeline"])
def test_structural_build_errors_never_create_companion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
):
    env = _environment(tmp_path, monkeypatch)
    env.args.quality_policy = "auto-fallback"

    def audit_runner(**kwargs):
        document = _audit_document(env)
        _write_json(kwargs["output_path"], document)
        return document

    def invalid_build(**kwargs):
        document = _build_document(env, kwargs["audit_path"])
        if corruption == "schema":
            document["schema_version"] = "invalid"
        elif corruption == "path":
            document["mms_provenance"]["audit"] = str(env.source)
        elif corruption == "token":
            document["songs"][env.track.song_id]["lines"]["0"][
                "character_overrides_ms"
            ] = {"99": 100}
        else:
            document["songs"][env.track.song_id]["lines"]["0"][
                "character_overrides_ms"
            ] = {"0": 1600}
        _write_json(kwargs["output_path"], document)
        return document

    with pytest.raises(KaraokeWorkflowError, match="structure|timeline|token index"):
        mms_workflow.run_mms_workflow(
            env.args,
            audit_runner=audit_runner,
            build_runner=invalid_build,
            renderer=lambda _config: pytest.fail("invalid structure must not render"),
        )

    assert not any(env.args.output_dir.rglob("*.mms-editable.sug"))
    assert not (env.args.output_dir / "render").exists()


def test_parser_requires_manifest_and_exactly_one_song_id():
    parser = mms_workflow.make_parser()
    help_text = parser.format_help()
    assert "--mms-model-path" in help_text
    assert "--mms-backend" in help_text
    assert "--quality-policy" in help_text
    assert "--sug" in help_text
    assert "models/mms/model.pt" in help_text
    assert ".cache/torch" not in help_text
    with pytest.raises(SystemExit):
        parser.parse_args(["--composition", "c.png", "--output-dir", "out"])
    parsed = parser.parse_args(
        [
            "--manifest",
            "album.json",
            "--song-id",
            "one",
            "--composition",
            "c.png",
            "--output-dir",
            "out",
            "--allow-mms-network",
            "--allow-cover-network",
        ]
    )
    assert parsed.song_id == "one"
    assert parsed.sug is None
    assert parsed.quality_policy == "strict"
    assert parsed.allow_mms_network is True
    assert parsed.allow_cover_network is True
    assert parsed.mms_backend == "local-mms-fa"


def test_advanced_sug_override_replaces_manifest_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    override = env.project / "reviewed-override.sug"
    override.write_bytes(env.sug.read_bytes())
    env.args.sug = override

    pre = mms_workflow.preflight(env.args)

    assert pre.sug == override.resolve()


def test_main_exits_zero_for_rendered_with_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        mms_workflow,
        "run_mms_workflow",
        lambda _args: {"status": "rendered-with-fallback"},
    )

    exit_code = mms_workflow.main(
        [
            "--manifest",
            "album.json",
            "--song-id",
            "one",
            "--composition",
            "c.png",
            "--output-dir",
            "out",
            "--quality-policy",
            "auto-fallback",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "rendered-with-fallback"
