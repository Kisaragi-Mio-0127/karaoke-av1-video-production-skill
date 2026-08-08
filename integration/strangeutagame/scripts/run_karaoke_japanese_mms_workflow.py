#!/usr/bin/env python3
"""Run one private Japanese MMS audit/build/render workflow."""

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
    from scripts.karaoke_common.device import DEFAULT_DEVICE, add_device_argument
    from scripts.karaoke_mms_editable import create_mms_editable_companion
    from scripts.karaoke_model_paths import (
        MMS_BACKEND_LOCAL,
        MMS_BACKEND_NEXTFIRE_JA_LATN,
        MMS_BACKENDS,
        resolve_alignment_model_path,
        resolve_mms_model_path,
    )
    from scripts.karaoke_workflow import (
        KaraokeWorkflowError,
        WorkflowConfig,
        print_result,
        run_workflow,
        sha256_file,
    )
    from scripts.render_karaoke_track import SHARED_FONT_DIR, SHARED_FONT_FILE
except ImportError:  # pragma: no cover - direct script execution
    import audit_karaoke_mms_alignment as mms_audit  # type: ignore[no-redef]
    import build_karaoke_mms_overrides as mms_build  # type: ignore[no-redef]
    from karaoke_album import (  # type: ignore[no-redef]
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
    )
    from karaoke_common.device import (  # type: ignore[no-redef]
        DEFAULT_DEVICE,
        add_device_argument,
    )
    from karaoke_mms_editable import (
        create_mms_editable_companion,  # type: ignore[no-redef]
    )
    from karaoke_model_paths import (  # type: ignore[no-redef]
        MMS_BACKEND_LOCAL,
        MMS_BACKEND_NEXTFIRE_JA_LATN,
        MMS_BACKENDS,
        resolve_alignment_model_path,
        resolve_mms_model_path,
    )
    from karaoke_workflow import (  # type: ignore[no-redef]
        KaraokeWorkflowError,
        WorkflowConfig,
        print_result,
        run_workflow,
        sha256_file,
    )
    from render_karaoke_track import (  # type: ignore[no-redef]
        SHARED_FONT_DIR,
        SHARED_FONT_FILE,
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
    mms_backend: str


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


def preflight(args: argparse.Namespace) -> Preflight:
    """Validate every local/input contract without creating the output tree."""

    if (
        args.mms_backend == MMS_BACKEND_NEXTFIRE_JA_LATN
        and args.allow_mms_network
    ):
        raise KaraokeWorkflowError(
            "nextfire-ja-latn is local-only; --allow-mms-network is unsupported"
        )

    manifest = args.manifest.expanduser().resolve()
    album = load_album_manifest(manifest, require_five_tracks=False)
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
            "private MMS output must stay inside the project root so provenance "
            "paths remain portable"
        )
    forbidden_roots = {
        album.deliverable_dir.resolve(),
        (album.project_root / "deliverables").resolve(),
    }
    for root in forbidden_roots:
        if _is_relative_to(output_dir, root):
            raise KaraokeWorkflowError(
                f"private MMS output must stay outside deliverables: {root}"
            )

    source = (
        args.source.expanduser().resolve()
        if args.source is not None
        else (album.deliverable_dir / "sources" / "netease_lyrics.json").resolve()
    )
    sug = (
        args.sug.expanduser().resolve()
        if args.sug is not None
        else (album.deliverable_dir / "timing" / f"{track.timing_stem}.sug").resolve()
    )
    audio = track.audio_path.resolve()
    vocals_root = (
        args.vocals_root.expanduser().resolve()
        if args.vocals_root is not None
        else (album.project_root / ".cache" / "msst-vocals").resolve()
    )
    vocals = (vocals_root / audio.stem / "Vocals.wav").resolve()
    required_paths = [manifest, source, sug, audio, vocals]
    for optional_artwork in (
        args.composition,
        args.cover,
        args.background,
        args.cover_source_audio,
    ):
        if optional_artwork is not None:
            required_paths.append(optional_artwork)
    for path in required_paths:
        _identity(path)
    if args.visual_style == "spectrum" and args.canonical_vinyl is not None:
        raise KaraokeWorkflowError("spectrum rendering must not receive --vinyl")
    if not args.fonts_dir.expanduser().resolve().is_dir():
        raise KaraokeWorkflowError(f"fonts directory does not exist: {args.fonts_dir}")
    _identity(args.font_file)

    try:
        model = (
            resolve_mms_model_path(args.mms_model_path)
            if args.mms_backend == MMS_BACKEND_LOCAL
            else resolve_alignment_model_path(
                args.mms_backend,
                explicit_mms_model=args.mms_model_path,
            )
        )
    except (FileNotFoundError, ValueError) as error:
        raise KaraokeWorkflowError(str(error)) from error
    _identity(model)
    model_selection = (
        "explicit"
        if args.mms_model_path is not None
        else (
            "project-models"
            if args.mms_backend == MMS_BACKEND_LOCAL
            else f"project-models:{args.mms_backend}"
        )
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
        mms_backend=args.mms_backend,
    )


def _invoke(function: Callable[..., Any], **kwargs: Any) -> Any:
    """Call a reusable stage while tolerating additive peer-owned parameters."""

    signature = inspect.signature(function)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    selected = kwargs if accepts_kwargs else {
        key: value for key, value in kwargs.items() if key in signature.parameters
    }
    return function(**selected)


def _load_stage_document(path: Path, returned: Any, label: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise KaraokeWorkflowError(f"{label} did not write a non-empty artifact")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise KaraokeWorkflowError(f"{label} artifact is invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise KaraokeWorkflowError(f"{label} artifact root must be an object")
    if isinstance(returned, Mapping) and dict(returned) != document:
        raise KaraokeWorkflowError(f"{label} return value differs from its artifact")
    return document


def _validate_audit_lines(song: Mapping[str, Any], pre: Preflight) -> bool:
    sug = json.loads(pre.sug.read_text(encoding="utf-8"))
    sentences = sug.get("sentences") if isinstance(sug, Mapping) else None
    lines = song.get("lines")
    if not isinstance(sentences, list) or not isinstance(lines, list) or not lines:
        return False
    seen: set[int] = set()
    for line in lines:
        if not isinstance(line, Mapping) or isinstance(line.get("line_index"), bool):
            return False
        try:
            line_index = int(line.get("line_index"))
        except (TypeError, ValueError):
            return False
        if line_index in seen or not 0 <= line_index < len(sentences):
            return False
        seen.add(line_index)
        sentence = sentences[line_index]
        characters = sentence.get("characters") if isinstance(sentence, Mapping) else None
        if not isinstance(characters, list) or not characters:
            return False
        text = "".join(
            str(character.get("char") or "")
            for character in characters
            if isinstance(character, Mapping)
        )
        if line.get("text") != text:
            return False
        index_field = (
            "source_token_index"
            if line.get("unit_axis") == "structured-sug-token"
            else "character_index"
        )
        for collection in ("units", "comparisons", "mix_units", "dual_audio_comparisons"):
            records = line.get(collection, [])
            if not isinstance(records, list):
                return False
            for record in records:
                if not isinstance(record, Mapping) or record.get(index_field) is None:
                    return False
                try:
                    token_index = int(record[index_field])
                except (TypeError, ValueError):
                    return False
                if not 0 <= token_index < len(characters):
                    return False
    return True


def _validate_audit(document: dict[str, Any], pre: Preflight) -> dict[str, Any]:
    songs = document.get("songs")
    checks = {
        "schema": document.get("schema_version") == AUDIT_SCHEMA,
        "single_nonempty_song": isinstance(songs, list) and len(songs) == 1,
        "manifest_path": _resolve_report_path(
            document.get("manifest_path"), pre.album.project_root
        )
        == pre.manifest,
        "lyric_source_path": _resolve_report_path(
            document.get("lyric_source_path", document.get("netease_lyrics_path")),
            pre.album.project_root,
        )
        == pre.source,
    }
    song = songs[0] if isinstance(songs, list) and len(songs) == 1 else {}
    checks.update(
        {
            "song_id": isinstance(song, Mapping)
            and song.get("song_id") == pre.track.song_id,
            "language": isinstance(song, Mapping) and song.get("language") == "ja",
            "token_text_index": isinstance(song, Mapping)
            and _validate_audit_lines(song, pre),
            "sug_path": isinstance(song, Mapping)
            and _resolve_report_path(song.get("sug_path"), pre.album.project_root)
            == pre.sug,
            "vocals_path": isinstance(song, Mapping)
            and _resolve_report_path(song.get("vocals_path"), pre.album.project_root)
            == pre.vocals,
            "mix_path": isinstance(song, Mapping)
            and _resolve_report_path(song.get("mix_path"), pre.album.project_root)
            == pre.audio,
        }
    )
    model_path = _resolve_report_path(document.get("model_path"), pre.album.project_root)
    checks["model_path"] = model_path == pre.mms_model and model_path.is_file()
    checks["mms_backend"] = (
        document.get("mms_backend", MMS_BACKEND_LOCAL) == pre.mms_backend
    )
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise KaraokeWorkflowError("MMS audit structure failed: " + ", ".join(failed))
    quality_gate = {
        "gate_ok": document.get("gate_ok") is True,
        "unresolved_empty": not bool(document.get("unresolved"))
        and int(document.get("unresolved_count", 0) or 0) == 0,
    }
    return {
        "checks": checks,
        "quality_gate": quality_gate,
        "quality_gate_ok": all(quality_gate.values()),
    }


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
        "single_song": isinstance(songs, dict)
        and set(songs) == {pre.track.song_id},
        "lines_mapping": isinstance(lines, Mapping),
        "audit_path": isinstance(provenance, Mapping)
        and _resolve_report_path(provenance.get("audit"), pre.album.project_root)
        == audit_path.resolve(),
        "model_path": isinstance(provenance, Mapping)
        and _resolve_report_path(provenance.get("model_path"), pre.album.project_root)
        == pre.mms_model,
        "lyric_source_path": isinstance(provenance, Mapping)
        and _resolve_report_path(provenance.get("lyric_source_path"), pre.album.project_root)
        == pre.source,
        "target_song_ids": isinstance(provenance, Mapping)
        and provenance.get("target_song_ids") == [pre.track.song_id],
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise KaraokeWorkflowError("MMS build structure failed: " + ", ".join(failed))
    quality_gate = {
        "gate_ok": document.get("gate_ok") is True,
        "unresolved_empty": not bool(document.get("unresolved"))
        and int(document.get("unresolved_count", 0) or 0) == 0,
    }
    return {
        "checks": checks,
        "quality_gate": quality_gate,
        "quality_gate_ok": all(quality_gate.values()),
        "visual_release_override_count": visual_release_count,
        "character_override_count": character_override_count,
        "render_contract": {
            "visual_release": {
                "applied_to_render": visual_release_count > 0,
                "fallback": (
                    None
                    if visual_release_count > 0
                    else "companion-preserved-canonical-sentence-end"
                ),
            },
            "character_overrides": {
                "applied_to_render": character_override_count > 0,
                "source": "mms-editable-companion",
            },
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
        "requested_device": getattr(args, "device", DEFAULT_DEVICE),
        "resolved_device": None,
        "network": {
            "mms_allowed": bool(args.allow_mms_network),
            "cover_allowed": bool(args.allow_cover_network),
        },
        "mms_backend": pre.mms_backend,
        "mms_model": {
            "selection": pre.mms_model_selection,
            **(_identity(pre.mms_model) if pre.mms_model is not None else {}),
        },
        "paths": {"audit": str(audit_dir), "build": str(build_dir), "render": str(render_dir)},
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
            allow_partial_manifest=True,
            model_path=pre.mms_model,
            allow_network=bool(args.allow_mms_network),
            sug_path=pre.sug,
            device=getattr(args, "device", DEFAULT_DEVICE),
            backend=pre.mms_backend,
        )
        audit = _load_stage_document(audit_path, returned_audit, "MMS audit")
        report["resolved_device"] = audit.get("resolved_device")
        audit_validation = _validate_audit(audit, pre)
        report["stages"].append(
            {"name": "audit", "status": "ok", **audit_validation}
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
            sug_path=pre.sug,
            output_path=overrides_path,
            allow_partial_manifest=True,
        )
        overrides = _load_stage_document(
            overrides_path, returned_overrides, "MMS override build"
        )
        override_gate = _validate_overrides(overrides, pre, audit_path, audit)
        override_identity = {
            **_identity(overrides_path),
            "song_id": pre.track.song_id,
        }
        report["stages"].append(
            {"name": "build", "status": "ok", **override_gate}
        )
        report["outputs"]["timing_overrides"] = override_identity

        try:
            companion = create_mms_editable_companion(
                canonical_sug=pre.sug,
                audio=pre.audio,
                build_dir=build_dir,
                song_id=pre.track.song_id,
                overrides=overrides,
            )
        except (ValueError, FileExistsError) as error:
            raise KaraokeWorkflowError(
                f"MMS companion structure failed: {error}"
            ) from error
        companion_identity = _identity(companion)
        companion_output = {
            **companion_identity,
            "paired_timing_overrides": override_identity,
        }
        report["outputs"]["mms_editable_sug"] = companion_output

        quality_gate = {
            "audit": audit_validation["quality_gate_ok"],
            "build": override_gate["quality_gate_ok"],
        }
        report["quality_gate"] = {
            "ok": all(quality_gate.values()),
            "checks": quality_gate,
        }
        quality_gate_overridden = (
            not report["quality_gate"]["ok"] and args.quality_policy == "auto-fallback"
        )
        report["release_decision"] = {
            "policy": args.quality_policy,
            "outcome": "pending-render",
            "quality_gate_overridden": quality_gate_overridden,
        }
        if not report["quality_gate"]["ok"] and not quality_gate_overridden:
            report["status"] = "review-required"
            report["release_decision"]["outcome"] = "review-required"
            report["stages"].append(
                {
                    "name": "render",
                    "status": "blocked",
                    "reason": "quality-gate-failed",
                    "timing_overrides": override_identity,
                    "mms_editable_sug": companion_output,
                }
            )
            raise KaraokeWorkflowError(
                "MMS quality gate failed after editable companion creation"
            )

        use_visual_release = override_gate["visual_release_override_count"] > 0
        release_timing_overrides = (
            overrides_path.resolve() if use_visual_release else None
        )
        report["outputs"]["release_sug"] = {
            **_identity(companion),
            "selection": "mms-editable-companion",
            "release_timing": (
                "visual-sidecar"
                if use_visual_release
                else "companion-preserved-canonical-sentence-end"
            ),
        }

        default_cover = (
            pre.album.deliverable_dir
            / "artwork"
            / pre.track.artifact_slug
            / "cover.jpg"
        ).resolve()
        resolved_cover = (
            args.cover.expanduser().resolve()
            if args.cover is not None
            else default_cover if default_cover.is_file() else None
        )

        config = WorkflowConfig(
            sug=companion,
            audio=pre.audio,
            cover_source_audio=(
                args.cover_source_audio.expanduser().resolve()
                if args.cover_source_audio is not None
                else None
            ),
            metadata_source_audio=(
                args.metadata_source_audio.expanduser().resolve()
                if args.metadata_source_audio is not None
                else None
            ),
            composition=(
                args.composition.expanduser().resolve()
                if args.composition is not None
                else None
            ),
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
            album_title=None,
            album_artist=None,
            cover=resolved_cover,
            background=(
                args.background.expanduser().resolve()
                if args.background is not None
                else None
            ),
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
            timing_overrides=release_timing_overrides,
            timing_override_song_id=(pre.track.song_id if use_visual_release else None),
        )
        report["render_gate"] = {"ok": False}
        render_report = renderer(config)
        if not isinstance(render_report, dict) or render_report.get("status") != "ok":
            raise KaraokeWorkflowError(
                "renderer did not return a successful workflow report"
            )
        report["render_gate"]["ok"] = True
        report["auto_artwork"] = render_report.get("auto_artwork")
        report["stages"].append(
            {
                "name": "render",
                "status": "ok",
                "timing_overrides": (
                    override_identity if use_visual_release else None
                ),
                "mms_editable_sug": companion_output,
                "release_sug": report["outputs"]["release_sug"],
                "visual_release_applied_to_render": use_visual_release,
                "character_overrides_applied_to_render": (
                    override_gate["character_override_count"] > 0
                ),
            }
        )
        report["outputs"]["render_workflow_report"] = _identity(
            render_dir / "workflow-report.json"
        )
        report["status"] = "rendered-with-fallback" if quality_gate_overridden else "ok"
        report["release_decision"]["outcome"] = (
            "rendered-with-fallback" if quality_gate_overridden else "rendered"
        )
        return report
    except Exception as error:
        if report.get("status") != "review-required":
            report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        _write_report(report_path, report)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--song-id", required=True)
    parser.add_argument(
        "--sug",
        type=Path,
        help="advanced canonical SUG override; default uses the manifest track",
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--vocals-root", type=Path)
    add_device_argument(parser)
    parser.add_argument(
        "--mms-backend",
        choices=MMS_BACKENDS,
        default=MMS_BACKEND_LOCAL,
        help=(
            "explicit alignment backend selection; local-mms-fa remains the "
            "default and never falls through to another model"
        ),
    )
    parser.add_argument(
        "--mms-model-path",
        type=Path,
        help=(
            "explicit local MMS checkpoint; takes priority over the project "
            "models/mms/model.pt checkpoint"
        ),
    )
    parser.add_argument("--allow-mms-network", action="store_true")
    parser.add_argument(
        "--quality-policy",
        choices=("strict", "auto-fallback"),
        default="strict",
        help=(
            "strict blocks rendering when quality evidence fails; auto-fallback "
            "records the uncertainty and renders the structurally valid companion"
        ),
    )
    parser.add_argument(
        "--composition",
        type=Path,
        help="advanced explicit composition override; default builds inside render/",
    )
    parser.add_argument("--cover", type=Path)
    parser.add_argument("--background", type=Path)
    parser.add_argument("--visual-style", choices=("vinyl", "spectrum"), default="vinyl")
    parser.add_argument("--vinyl", dest="canonical_vinyl", type=Path)
    parser.add_argument("--color-policy", choices=("cover", "project"), default="cover")
    parser.add_argument("--singer-color", action="append", default=[])
    parser.add_argument("--spectrum-color")
    parser.add_argument("--progress-color")
    parser.add_argument("--cover-source-audio", type=Path)
    parser.add_argument("--metadata-source-audio", type=Path)
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
