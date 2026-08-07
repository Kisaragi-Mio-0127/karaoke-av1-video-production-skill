#!/usr/bin/env python3
"""Run one isolated Japanese MMS audit/build/render workflow."""

from __future__ import annotations

import argparse
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import audit_karaoke_mms_alignment as mms_audit
    from scripts import build_karaoke_mms_overrides as mms_build
    from scripts.karaoke_album import AlbumManifest, AlbumTrack, load_album_manifest
    from scripts.karaoke_review_preview import SHARED_FONT_DIR, SHARED_FONT_FILE
    from scripts.karaoke_workflow import (
        KaraokeWorkflowError,
        WorkflowConfig,
        print_result,
        run_workflow,
        sha256_file,
    )
except ImportError:  # pragma: no cover - direct script execution
    import audit_karaoke_mms_alignment as mms_audit  # type: ignore[no-redef]
    import build_karaoke_mms_overrides as mms_build  # type: ignore[no-redef]
    from karaoke_album import (  # type: ignore[no-redef]
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
    )
    from karaoke_review_preview import (  # type: ignore[no-redef]
        SHARED_FONT_DIR,
        SHARED_FONT_FILE,
    )
    from karaoke_workflow import (  # type: ignore[no-redef]
        KaraokeWorkflowError,
        WorkflowConfig,
        print_result,
        run_workflow,
        sha256_file,
    )


REPORT_NAME = "workflow-report.json"
AUDIT_SCHEMA = "karaoke-mms-dual-audio-audit/v1"
OVERRIDES_SCHEMA = "karaoke-timing-overrides/v2"


@dataclass(frozen=True)
class Preflight:
    album: AlbumManifest
    track: AlbumTrack
    manifest: Path
    source: Path
    sug: Path
    audio: Path
    vocals_root: Path
    vocals: Path
    output_dir: Path
    mms_model: Path | None
    mms_model_selection: str


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise KaraokeWorkflowError(f"required non-empty file is missing: {resolved}")
    return {
        "path": str(resolved),
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _resolve_report_path(value: Any, project_root: Path) -> Path:
    path = Path(str(value or ""))
    return (path if path.is_absolute() else project_root / path).resolve()


def _find_local_mms_model(project_root: Path) -> Path | None:
    preferred = project_root / ".cache" / "torch" / "hub" / "checkpoints" / "model.pt"
    if preferred.is_file() and preferred.stat().st_size > 0:
        return preferred.resolve()
    return next(
        (
            path.resolve()
            for path in sorted(
                (project_root / ".cache" / "torch" / "hub" / "checkpoints").glob("*.pt")
            )
            if path.is_file() and path.stat().st_size > 0
        ),
        None,
    )


def preflight(args: argparse.Namespace) -> Preflight:
    """Validate every local/input contract without creating the output tree."""

    manifest = args.manifest.expanduser().resolve()
    album = load_album_manifest(manifest)
    matches = [track for track in album.tracks if track.song_id == args.song_id]
    if len(matches) != 1:
        raise KaraokeWorkflowError(
            f"manifest must contain exactly one selected song-id: {args.song_id}"
        )
    track = matches[0]
    if track.language != "ja":
        raise KaraokeWorkflowError(
            f"MMS workflow supports only ja; {track.song_id} is {track.language}"
        )

    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise KaraokeWorkflowError(f"output directory already exists: {output_dir}")
    if not _is_relative_to(output_dir, album.project_root.resolve()):
        raise KaraokeWorkflowError(
            "isolated MMS output must stay inside the project root so provenance "
            "paths remain portable"
        )
    forbidden_roots = {
        album.deliverable_dir.resolve(),
        (album.project_root / "deliverables").resolve(),
    }
    for root in forbidden_roots:
        if _is_relative_to(output_dir, root):
            raise KaraokeWorkflowError(
                f"isolated MMS output must stay outside deliverables: {root}"
            )

    source = (
        args.source.expanduser().resolve()
        if args.source is not None
        else (album.deliverable_dir / "sources" / "netease_lyrics.json").resolve()
    )
    sug = (album.deliverable_dir / "timing" / f"{track.timing_stem}.sug").resolve()
    audio = track.audio_path.resolve()
    vocals_root = (
        args.vocals_root.expanduser().resolve()
        if args.vocals_root is not None
        else (album.project_root / ".cache" / "msst-vocals").resolve()
    )
    vocals = (vocals_root / audio.stem / "Vocals.wav").resolve()
    required_paths = [manifest, source, sug, audio, vocals, args.composition]
    if args.cover_source_audio is not None:
        required_paths.append(args.cover_source_audio)
    for path in required_paths:
        _identity(path)
    if sha256_file(audio).casefold() != track.audio_sha256.casefold():
        raise KaraokeWorkflowError("manifest audio hash is stale for selected song")
    if args.visual_style == "vinyl":
        if args.canonical_vinyl is None:
            raise KaraokeWorkflowError("--vinyl is required for vinyl rendering")
        _identity(args.canonical_vinyl)
    elif args.canonical_vinyl is not None:
        raise KaraokeWorkflowError("spectrum rendering must not receive --vinyl")
    if not args.fonts_dir.expanduser().resolve().is_dir():
        raise KaraokeWorkflowError(f"fonts directory does not exist: {args.fonts_dir}")
    _identity(args.font_file)

    if args.mms_model_path is not None:
        model = args.mms_model_path.expanduser().resolve()
        if not model.is_file():
            raise KaraokeWorkflowError(
                f"explicit MMS model checkpoint does not exist: {model}"
            )
        _identity(model)
        model_selection = "explicit"
    else:
        model = _find_local_mms_model(album.project_root)
        model_selection = "default-cache" if model is not None else "network"
    if model is None and not args.allow_mms_network:
        raise KaraokeWorkflowError(
            "local MMS model is missing; use --allow-mms-network to permit retrieval"
        )
    return Preflight(
        album=album,
        track=track,
        manifest=manifest,
        source=source,
        sug=sug,
        audio=audio,
        vocals_root=vocals_root,
        vocals=vocals,
        output_dir=output_dir,
        mms_model=model,
        mms_model_selection=model_selection,
    )


def _invoke(function: Callable[..., Any], **kwargs: Any) -> Any:
    """Call a reusable stage while tolerating additive peer-owned parameters."""

    signature = inspect.signature(function)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    selected = (
        kwargs
        if accepts_kwargs
        else {
            key: value for key, value in kwargs.items() if key in signature.parameters
        }
    )
    return function(**selected)


def _load_stage_document(path: Path, returned: Any, label: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise KaraokeWorkflowError(f"{label} did not write a non-empty artifact")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise KaraokeWorkflowError(
            f"{label} artifact is invalid JSON: {error}"
        ) from error
    if not isinstance(document, dict):
        raise KaraokeWorkflowError(f"{label} artifact root must be an object")
    if isinstance(returned, Mapping) and dict(returned) != document:
        raise KaraokeWorkflowError(f"{label} return value differs from its artifact")
    return document


def _validate_audit(document: dict[str, Any], pre: Preflight) -> dict[str, Any]:
    songs = document.get("songs")
    checks = {
        "schema": document.get("schema_version") == AUDIT_SCHEMA,
        "gate_ok": document.get("gate_ok") is True,
        "single_nonempty_song": isinstance(songs, list) and len(songs) == 1,
        "manifest_sha256": document.get("manifest_sha256") == sha256_file(pre.manifest),
        "lyric_source_sha256": document.get(
            "lyric_source_sha256", document.get("netease_lyrics_sha256")
        )
        == sha256_file(pre.source),
    }
    song = songs[0] if isinstance(songs, list) and len(songs) == 1 else {}
    checks.update(
        {
            "song_id": isinstance(song, Mapping)
            and song.get("song_id") == pre.track.song_id,
            "language": isinstance(song, Mapping) and song.get("language") == "ja",
            "lines_nonempty": isinstance(song, Mapping)
            and isinstance(song.get("lines"), list)
            and bool(song["lines"]),
            "sug_sha256": isinstance(song, Mapping)
            and song.get("sug_sha256") == sha256_file(pre.sug),
            "vocals_sha256": isinstance(song, Mapping)
            and song.get("vocals_sha256") == sha256_file(pre.vocals),
            "mix_sha256": isinstance(song, Mapping)
            and song.get("mix_sha256") == sha256_file(pre.audio),
        }
    )
    model_path = _resolve_report_path(
        document.get("model_path"), pre.album.project_root
    )
    checks["model_provenance"] = model_path.is_file() and document.get(
        "model_sha256"
    ) == sha256_file(model_path)
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise KaraokeWorkflowError("MMS audit gate failed: " + ", ".join(failed))
    return checks


def _validate_overrides(
    document: dict[str, Any], pre: Preflight, audit_path: Path, audit: dict[str, Any]
) -> dict[str, Any]:
    songs = document.get("songs")
    song = songs.get(pre.track.song_id) if isinstance(songs, dict) else None
    lines = song.get("lines") if isinstance(song, Mapping) else None
    visual_release_count = 0
    character_override_count = 0
    if isinstance(lines, Mapping):
        for line in lines.values():
            if not isinstance(line, Mapping):
                continue
            releases = line.get("visual_release_overrides_ms")
            if isinstance(releases, Mapping):
                visual_release_count += len(releases)
            characters = line.get("character_overrides_ms")
            if isinstance(characters, Mapping):
                character_override_count += len(characters)
    provenance = document.get("mms_provenance")
    checks = {
        "schema": document.get("schema_version") == OVERRIDES_SCHEMA,
        "gate_ok": document.get("gate_ok") is True,
        "single_song": isinstance(songs, dict) and set(songs) == {pre.track.song_id},
        "lines_nonempty": isinstance(lines, Mapping) and bool(lines),
        "visual_release_nonempty": visual_release_count > 0,
        "audit_sha256": isinstance(provenance, Mapping)
        and provenance.get("audit_sha256") == sha256_file(audit_path),
        "model_sha256": isinstance(provenance, Mapping)
        and provenance.get("model_sha256") == audit.get("model_sha256"),
        "lyric_source_sha256": isinstance(provenance, Mapping)
        and provenance.get("lyric_source_sha256")
        == audit.get("lyric_source_sha256", audit.get("netease_lyrics_sha256")),
        "target_song_ids": isinstance(provenance, Mapping)
        and provenance.get("target_song_ids") == [pre.track.song_id],
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise KaraokeWorkflowError("MMS build gate failed: " + ", ".join(failed))
    return {
        "checks": checks,
        "visual_release_override_count": visual_release_count,
        "character_override_count": character_override_count,
        "render_contract": {
            "visual_release": {"applied_to_render": True},
            "character_overrides": {"applied_to_render": False},
        },
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def run_mms_workflow(
    args: argparse.Namespace,
    *,
    audit_runner: Callable[..., Any] | None = None,
    build_runner: Callable[..., Any] | None = None,
    renderer: Callable[[WorkflowConfig], dict[str, Any]] = run_workflow,
) -> dict[str, Any]:
    pre = preflight(args)
    audit_callable = audit_runner or getattr(mms_audit, "run_audit", None)
    build_callable = build_runner or getattr(mms_build, "run_build", None)
    if not callable(audit_callable):
        raise KaraokeWorkflowError("audit module does not expose reusable run_audit")

    pre.output_dir.mkdir(parents=True, exist_ok=False)
    audit_dir = pre.output_dir / "audit"
    build_dir = pre.output_dir / "build"
    render_dir = pre.output_dir / "render"
    audit_dir.mkdir()
    build_dir.mkdir()
    report: dict[str, Any] = {
        "schema_version": "karaoke-japanese-mms-workflow/v1",
        "status": "running",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "song_id": pre.track.song_id,
        "language": "ja",
        "network": {
            "mms_allowed": bool(args.allow_mms_network),
            "cover_allowed": bool(args.allow_cover_network),
        },
        "mms_model": {
            "selection": pre.mms_model_selection,
            **(_identity(pre.mms_model) if pre.mms_model is not None else {}),
        },
        "paths": {
            "audit": str(audit_dir),
            "build": str(build_dir),
            "render": str(render_dir),
        },
        "stages": [],
        "outputs": {},
    }
    report_path = pre.output_dir / REPORT_NAME
    try:
        audit_path = audit_dir / "mms_alignment_audit.json"
        returned_audit = _invoke(
            audit_callable,
            song_ids=(pre.track.song_id,),
            manifest_path=pre.manifest,
            source_path=pre.source,
            output_path=audit_path,
            vocals_root=pre.vocals_root,
            allow_partial_manifest=False,
            model_path=pre.mms_model,
            allow_network=bool(args.allow_mms_network),
        )
        audit = _load_stage_document(audit_path, returned_audit, "MMS audit")
        audit_checks = _validate_audit(audit, pre)
        report["stages"].append(
            {"name": "audit", "status": "ok", "checks": audit_checks}
        )
        report["outputs"]["audit"] = _identity(audit_path)

        if not callable(build_callable):
            raise KaraokeWorkflowError(
                "build module does not expose reusable run_build; await peer API integration"
            )
        overrides_path = build_dir / "timing_overrides.json"
        returned_overrides = _invoke(
            build_callable,
            song_ids=(pre.track.song_id,),
            release_song_ids=(pre.track.song_id,),
            manifest_path=pre.manifest,
            source_path=pre.source,
            audit_path=audit_path,
            output_path=overrides_path,
            allow_partial_manifest=False,
        )
        overrides = _load_stage_document(
            overrides_path, returned_overrides, "MMS override build"
        )
        override_gate = _validate_overrides(overrides, pre, audit_path, audit)
        override_identity = {
            **_identity(overrides_path),
            "song_id": pre.track.song_id,
        }
        report["stages"].append({"name": "build", "status": "ok", **override_gate})
        report["outputs"]["timing_overrides"] = override_identity

        config = WorkflowConfig(
            sug=pre.sug,
            audio=pre.audio,
            cover_source_audio=(
                args.cover_source_audio.expanduser().resolve()
                if args.cover_source_audio is not None
                else None
            ),
            composition=args.composition.expanduser().resolve(),
            canonical_vinyl=(
                args.canonical_vinyl.expanduser().resolve()
                if args.canonical_vinyl is not None
                else None
            ),
            output_dir=render_dir,
            language="ja",
            layout="wide",
            title=pre.track.title,
            artist=pre.track.artist,
            album_title=pre.album.title,
            album_artist=pre.album.artist,
            fonts_dir=args.fonts_dir.expanduser().resolve(),
            font_file=args.font_file.expanduser().resolve(),
            smoke_duration=args.smoke_duration,
            pronunciation_validation=args.pronunciation_validation,
            visual_style=args.visual_style,
            color_policy=args.color_policy,
            singer_colors=tuple(args.singer_color),
            spectrum_color=args.spectrum_color,
            progress_color=args.progress_color,
            cover_url=args.cover_url,
            allow_network=bool(args.allow_cover_network),
            ffmpeg=args.ffmpeg,
            lossless_companion=args.lossless_companion,
            full_decode=args.full_decode,
            canonical_deliverables=(pre.album.deliverable_dir,),
            timing_overrides=overrides_path.resolve(),
            timing_override_song_id=pre.track.song_id,
        )
        render_report = renderer(config)
        if not isinstance(render_report, dict) or render_report.get("status") != "ok":
            raise KaraokeWorkflowError(
                "renderer did not return a successful workflow report"
            )
        report["stages"].append(
            {
                "name": "render",
                "status": "ok",
                "timing_overrides": override_identity,
                "visual_release_applied_to_render": True,
                "character_overrides_applied_to_render": False,
            }
        )
        report["outputs"]["render_workflow_report"] = _identity(
            render_dir / "workflow-report.json"
        )
        report["status"] = "ok"
        return report
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        _write_report(report_path, report)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--song-id", required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--vocals-root", type=Path)
    parser.add_argument(
        "--mms-model-path",
        type=Path,
        help=(
            "explicit local MMS checkpoint; takes priority over the project "
            ".cache/torch checkpoint"
        ),
    )
    parser.add_argument("--allow-mms-network", action="store_true")
    parser.add_argument("--composition", type=Path, required=True)
    parser.add_argument(
        "--visual-style", choices=("vinyl", "spectrum"), default="vinyl"
    )
    parser.add_argument("--vinyl", dest="canonical_vinyl", type=Path)
    parser.add_argument("--color-policy", choices=("cover", "project"), default="cover")
    parser.add_argument("--singer-color", action="append", default=[])
    parser.add_argument("--spectrum-color")
    parser.add_argument("--progress-color")
    parser.add_argument("--cover-source-audio", type=Path)
    parser.add_argument("--cover-url", default="")
    parser.add_argument("--allow-cover-network", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fonts-dir", type=Path, default=SHARED_FONT_DIR)
    parser.add_argument("--font-file", type=Path, default=SHARED_FONT_FILE)
    parser.add_argument("--smoke-duration", type=float)
    parser.add_argument(
        "--pronunciation-validation",
        choices=("off", "optional", "required"),
        default="optional",
    )
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--lossless-companion", action="store_true")
    parser.add_argument("--full-decode", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    return print_result(run_mms_workflow(args))


if __name__ == "__main__":
    raise SystemExit(main())
