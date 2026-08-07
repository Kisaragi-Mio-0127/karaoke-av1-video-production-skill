from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

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

from scripts import run_karaoke_japanese_mms_workflow as mms_workflow  # noqa: E402
from scripts.karaoke_workflow import KaraokeWorkflowError  # noqa: E402


def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "repo"
    deliverables = project / "isolated-canonical"
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
    sug.write_text('{"sentences":[]}', encoding="utf-8")
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
    monkeypatch.setattr(mms_workflow, "load_album_manifest", lambda _path: album)
    args = mms_workflow.make_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--song-id",
            song_id,
            "--composition",
            str(composition),
            "--vinyl",
            str(vinyl),
            "--fonts-dir",
            str(fonts),
            "--font-file",
            str(font),
            "--output-dir",
            str(project / "isolated-run"),
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
    )


def _audit_document(env, *, model: Path | None = None) -> dict[str, object]:
    selected_model = model or env.model
    return {
        "schema_version": mms_workflow.AUDIT_SCHEMA,
        "gate_ok": True,
        "manifest_sha256": mms_workflow.sha256_file(env.args.manifest),
        "lyric_source_sha256": mms_workflow.sha256_file(env.source),
        "model_path": str(selected_model),
        "model_sha256": mms_workflow.sha256_file(selected_model),
        "songs": [
            {
                "song_id": env.track.song_id,
                "language": "ja",
                "lines": [{"line_index": 0}],
                "sug_sha256": mms_workflow.sha256_file(env.sug),
                "vocals_sha256": mms_workflow.sha256_file(env.vocals),
                "mix_sha256": mms_workflow.sha256_file(env.audio),
            }
        ],
    }


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def test_isolated_mms_workflow_chains_reusable_stages_and_only_renders_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)
    calls: dict[str, object] = {}

    def audit_runner(**kwargs):
        calls["audit"] = kwargs
        document = _audit_document(env)
        _write_json(kwargs["output_path"], document)
        return document

    def build_runner(**kwargs):
        calls["build"] = kwargs
        audit_sha256 = mms_workflow.sha256_file(kwargs["audit_path"])
        document = {
            "schema_version": mms_workflow.OVERRIDES_SCHEMA,
            "gate_ok": True,
            "mms_provenance": {
                "audit_sha256": audit_sha256,
                "model_sha256": mms_workflow.sha256_file(env.model),
                "lyric_source_sha256": mms_workflow.sha256_file(env.source),
                "target_song_ids": [env.track.song_id],
            },
            "songs": {
                env.track.song_id: {
                    "lines": {
                        "0": {
                            "character_overrides_ms": {"0": 100},
                            "visual_release_overrides_ms": {"0": 220},
                        }
                    }
                }
            },
        }
        _write_json(kwargs["output_path"], document)
        return document

    def renderer(config):
        calls["render"] = config
        assert (
            config.timing_overrides
            == (env.args.output_dir / "build" / "timing_overrides.json").resolve()
        )
        assert config.timing_override_song_id == env.track.song_id
        assert config.allow_network is False
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
    assert (
        build_stage["render_contract"]["character_overrides"]["applied_to_render"]
        is False
    )
    render_stage = next(
        stage for stage in report["stages"] if stage["name"] == "render"
    )
    assert render_stage["character_overrides_applied_to_render"] is False
    assert calls["audit"]["song_ids"] == (env.track.song_id,)
    assert calls["audit"]["model_path"] == env.model.resolve()
    assert calls["build"]["song_ids"] == (env.track.song_id,)


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

    with pytest.raises(KaraokeWorkflowError, match="local MMS model is missing"):
        mms_workflow.run_mms_workflow(env.args)

    assert not env.args.output_dir.exists()


def test_post_preflight_failure_writes_unmistakably_failed_total_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env = _environment(tmp_path, monkeypatch)

    def failed_audit(**kwargs):
        document = _audit_document(env)
        document["gate_ok"] = False
        _write_json(kwargs["output_path"], document)
        return document

    with pytest.raises(KaraokeWorkflowError, match="gate failed"):
        mms_workflow.run_mms_workflow(
            env.args,
            audit_runner=failed_audit,
            build_runner=lambda **_kwargs: {},
        )

    report = json.loads(
        (env.args.output_dir / mms_workflow.REPORT_NAME).read_text(encoding="utf-8")
    )
    assert report["status"] == "failed"
    assert "gate failed" in report["error"]
    assert not (env.args.output_dir / "render").exists()


def test_parser_requires_manifest_and_exactly_one_song_id():
    parser = mms_workflow.make_parser()
    assert "--mms-model-path" in parser.format_help()
    option_strings = {
        option for action in parser._actions for option in action.option_strings
    }
    assert "--model-path" not in option_strings
    assert "--allow-network" not in option_strings
    required_args = [
        "--manifest",
        "album.json",
        "--song-id",
        "one",
        "--composition",
        "c.png",
        "--output-dir",
        "out",
    ]
    with pytest.raises(SystemExit):
        parser.parse_args([*required_args, "--model-path", "model.pt"])
    with pytest.raises(SystemExit):
        parser.parse_args([*required_args, "--allow-network"])
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
    assert parsed.allow_mms_network is True
    assert parsed.allow_cover_network is True
