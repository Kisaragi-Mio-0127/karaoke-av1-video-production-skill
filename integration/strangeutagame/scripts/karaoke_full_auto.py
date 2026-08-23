#!/usr/bin/env python3
"""Reusable single-song, private, full-auto karaoke orchestration."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import audit_karaoke_asr_recognition as asr
    from . import karaoke_timing
    from . import prepare_karaoke_msst_vocals as msst
    from .karaoke_album import AlbumManifest, AlbumTrack, load_album_manifest
    from .karaoke_common.device import DEFAULT_DEVICE, add_device_argument
    from .karaoke_model_paths import (
        MMS_BACKEND_LOCAL,
        MMS_BACKEND_NEXTFIRE_JA_LATN,
        MMS_BACKENDS,
        NEXTFIRE_JA_LATN_MODEL_RELATIVE_DIR,
        resolve_nextfire_ja_latn_model_path,
    )
    from .karaoke_netease_metadata import (
        NeteaseMetadataError,
        read_netease_song_id,
    )
    from .karaoke_workflow import (
        KaraokeWorkflowError,
        validate_output_mode_options,
    )
except ImportError:  # pragma: no cover - direct script execution
    import audit_karaoke_asr_recognition as asr  # type: ignore[no-redef]
    import karaoke_timing  # type: ignore[no-redef]
    import prepare_karaoke_msst_vocals as msst  # type: ignore[no-redef]
    from karaoke_album import (  # type: ignore[no-redef]
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
    )
    from karaoke_common.device import (  # type: ignore[no-redef]
        DEFAULT_DEVICE,
        add_device_argument,
    )
    from karaoke_model_paths import (  # type: ignore[no-redef]
        MMS_BACKEND_LOCAL,
        MMS_BACKEND_NEXTFIRE_JA_LATN,
        MMS_BACKENDS,
        NEXTFIRE_JA_LATN_MODEL_RELATIVE_DIR,
        resolve_nextfire_ja_latn_model_path,
    )
    from karaoke_netease_metadata import (  # type: ignore[no-redef]
        NeteaseMetadataError,
        read_netease_song_id,
    )
    from karaoke_workflow import (  # type: ignore[no-redef]
        KaraokeWorkflowError,
        validate_output_mode_options,
    )

SUPPORTED_LANGUAGES = frozenset({"ja", "zh", "en"})
REPORT_NAME = "full-auto-report.json"
SCHEMA_VERSION = "karaoke-full-auto/v1"


class FullAutoError(RuntimeError):
    """Raised when the full-auto boundary or a reusable stage fails."""


def _mms_module(language: str) -> Any:
    """Load only the selected language route.

    Keeping this import lazy lets a Japanese/general integration bundle omit
    the private Chinese/English entry point without breaking Japanese runs.
    """

    if language == "ja":
        try:
            from . import run_karaoke_japanese_mms_workflow as module
        except ImportError:  # pragma: no cover - direct script execution
            import run_karaoke_japanese_mms_workflow as module
        return module
    try:
        from . import run_karaoke_zh_en_mms_workflow as module
    except ImportError:  # pragma: no cover - direct script execution
        import run_karaoke_zh_en_mms_workflow as module
    return module


@dataclass(frozen=True)
class FullAutoPlan:
    album: AlbumManifest
    track: AlbumTrack
    manifest: Path
    source: Path
    lyrics_file: Path | None
    output_dir: Path
    initial_root: Path
    initial_sug: Path
    vocals_root: Path
    vocals: Path
    mms_model: Path
    mms_backend: str
    whisper_models: Path
    asr_cache: Path
    netease_song_id: str | None
    netease_song_id_source: str | None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise FullAutoError(f"{label} does not exist: {path}")
    return path


def _require_model_path(path: Path, models_root: Path, label: str) -> Path:
    resolved = _require_file(path.expanduser().resolve(), label)
    if not _is_relative_to(resolved, models_root):
        raise FullAutoError(f"{label} must be selected from {models_root}")
    return resolved


def _manual_lyrics_source(plan: FullAutoPlan) -> dict[str, Any] | None:
    lyrics_file = plan.lyrics_file
    if lyrics_file is None:
        return None
    try:
        raw_lyrics = lyrics_file.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise FullAutoError(
            f"manual lyrics must be UTF-8 text: {lyrics_file}"
        ) from error

    parsed_entries = karaoke_timing.parse_lrc(raw_lyrics)
    if lyrics_file.suffix.casefold() == ".lrc" or parsed_entries:
        if not any(entry.text.strip() for entry in parsed_entries):
            raise FullAutoError(
                f"manual LRC contains no timestamped lyric lines: {lyrics_file}"
            )
        normalized_lrc = raw_lyrics
        timing_mode = "provided-lrc"
        line_count = sum(1 for entry in parsed_entries if entry.text.strip())
    else:
        lines = [line.strip() for line in raw_lyrics.splitlines() if line.strip()]
        if not lines:
            raise FullAutoError(f"manual text lyrics contain no lyric lines: {lyrics_file}")
        _duration_seconds, duration_ms = karaoke_timing.read_mutagen_duration(
            plan.track.audio_path
        )
        if duration_ms <= 0:
            raise FullAutoError(
                f"selected audio has no usable duration: {plan.track.audio_path}"
            )
        generated_lines: list[str] = []
        for index, line in enumerate(lines):
            timestamp_ms = duration_ms * index // len(lines)
            minutes, remainder = divmod(timestamp_ms, 60_000)
            seconds, milliseconds = divmod(remainder, 1_000)
            generated_lines.append(
                f"[{minutes:02d}:{seconds:02d}.{milliseconds:03d}]{line}"
            )
        normalized_lrc = "\n".join(generated_lines) + "\n"
        timing_mode = "uniform-coarse-anchors"
        line_count = len(lines)

    source_document = {
        "schema_version": "manual-lyrics-source/v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(lyrics_file),
            "format": lyrics_file.suffix.casefold().removeprefix("."),
            "timing_mode": timing_mode,
            "line_count": line_count,
        },
        "songs": {
            plan.track.song_id: {
                "song_id": plan.track.song_id,
                "title": plan.track.title,
                "artist": plan.track.artist,
                "audio_file": plan.track.audio_path.name,
                "lrc": normalized_lrc,
            }
        },
    }
    _write_report(plan.source, source_document)
    return dict(source_document["input"])


def build_plan(
    args: argparse.Namespace,
    *,
    allowed_languages: frozenset[str] = SUPPORTED_LANGUAGES,
) -> FullAutoPlan:
    """Resolve all immutable inputs before any output or cache is created."""

    try:
        validate_output_mode_options(
            output_mode=args.output_mode,
            background_video=args.background_video,
        )
    except KaraokeWorkflowError as error:
        raise FullAutoError(str(error)) from error
    manifest = args.manifest.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    source_argument = getattr(args, "source", None)
    lyrics_argument = getattr(args, "lyrics_file", None)
    if args.refresh_source and lyrics_argument is not None:
        raise FullAutoError("--refresh-source cannot be used with --lyrics-file")
    if args.refresh_source and source_argument is None:
        raise FullAutoError("--refresh-source requires --source as its JSON destination")
    if source_argument is None and lyrics_argument is None:
        raise FullAutoError("one of --source or --lyrics-file is required")
    if source_argument is not None and lyrics_argument is not None:
        raise FullAutoError("--source and --lyrics-file cannot be used together")
    lyrics_file = (
        lyrics_argument.expanduser().resolve()
        if lyrics_argument is not None
        else None
    )
    source = (
        source_argument.expanduser().resolve()
        if source_argument is not None
        else (output_dir / "inputs" / "manual-lyrics.json").resolve()
    )
    _require_file(manifest, "manifest")
    if lyrics_file is None and not args.refresh_source:
        _require_file(source, "frozen lyric source")
    for label, optional_path in (
        ("explicit composition", args.composition),
        ("explicit cover", args.cover),
        ("explicit background", args.background),
        ("background video", args.background_video),
        ("explicit cover source audio", args.cover_source_audio),
        ("explicit metadata source audio", args.metadata_source_audio),
        ("manual lyrics file", lyrics_file),
    ):
        if optional_path is not None:
            _require_file(optional_path.expanduser().resolve(), label)
    album = load_album_manifest(manifest, require_five_tracks=False)
    matches = [track for track in album.tracks if str(track.song_id) == args.song_id]
    if len(matches) != 1:
        raise FullAutoError(
            f"manifest must contain exactly one selected song-id: {args.song_id}"
        )
    track = matches[0]
    if track.language not in allowed_languages:
        expected = ", ".join(sorted(allowed_languages))
        raise FullAutoError(
            f"this entry supports only {expected}; {track.song_id} is {track.language}"
        )
    _require_file(track.audio_path.resolve(), "selected mix audio")
    if lyrics_file is not None and lyrics_file.suffix.casefold() not in {".lrc", ".txt"}:
        raise FullAutoError("--lyrics-file supports only .lrc or .txt files")
    if source.is_file() and lyrics_file is None and not args.refresh_source:
        source_document = json.loads(source.read_text(encoding="utf-8"))
        songs = (
            source_document.get("songs")
            if isinstance(source_document, Mapping)
            else None
        )
        if not isinstance(songs, Mapping) or track.song_id not in songs:
            raise FullAutoError("frozen lyric source must contain the selected song-id")
    if args.netease_song_id and not args.refresh_source:
        raise FullAutoError("--netease-song-id requires --refresh-source")
    netease_song_id = args.netease_song_id
    netease_song_id_source = "cli" if netease_song_id else None
    if args.refresh_source and not netease_song_id:
        try:
            netease_song_id = read_netease_song_id(track.audio_path)
        except NeteaseMetadataError as error:
            raise FullAutoError(
                "could not infer a NetEase song id from the selected audio; "
                "pass --netease-song-id explicitly"
            ) from error
        netease_song_id_source = "audio-metadata"
    if netease_song_id and not netease_song_id.isdigit():
        raise FullAutoError("NetEase song id must contain digits only")

    project_root = album.project_root.resolve()
    private_root = (project_root / ".render-work").resolve()
    if output_dir.exists():
        raise FullAutoError(f"output directory already exists: {output_dir}")
    if output_dir == private_root or not _is_relative_to(output_dir, private_root):
        raise FullAutoError(f"output must be a new child of {private_root}")

    models_root = (project_root / "models").resolve()
    if args.mms_backend == MMS_BACKEND_NEXTFIRE_JA_LATN:
        if track.language != "ja":
            raise FullAutoError("nextfire-ja-latn supports only Japanese tracks")
        if args.mms_model_path is not None:
            raise FullAutoError(
                "--mms-model-path applies only to the local-mms-fa backend"
            )
        try:
            mms_model = resolve_nextfire_ja_latn_model_path(
                models_root
                / NEXTFIRE_JA_LATN_MODEL_RELATIVE_DIR
                / "model.safetensors"
            )
        except (FileNotFoundError, ValueError) as error:
            raise FullAutoError(str(error)) from error
    else:
        mms_model = _require_model_path(
            (args.mms_model_path or models_root / "mms" / "model.pt").resolve(),
            models_root,
            "MMS model",
        )
    whisper_models = (args.whisper_models or models_root / "whisper").resolve()
    needs_whisper = (
        track.language in {"zh", "en"} or args.timing_alignment != "deterministic"
    )
    if needs_whisper and (
        not whisper_models.is_dir()
        or not _is_relative_to(whisper_models, models_root)
    ):
        raise FullAutoError(
            f"Whisper model directory must exist below {models_root}: "
            f"{whisper_models}"
        )

    initial_root = output_dir / "initial"
    initial_sug = initial_root / "timing" / f"{track.timing_stem}.sug"
    vocals_root = (project_root / ".cache" / "msst-vocals").resolve()
    return FullAutoPlan(
        album=album,
        track=track,
        manifest=manifest,
        source=source,
        lyrics_file=lyrics_file,
        output_dir=output_dir,
        initial_root=initial_root,
        initial_sug=initial_sug,
        vocals_root=vocals_root,
        vocals=vocals_root / track.audio_path.stem / "Vocals.wav",
        mms_model=mms_model,
        mms_backend=args.mms_backend,
        whisper_models=whisper_models,
        asr_cache=(project_root / ".cache" / "asr-recognition").resolve(),
        netease_song_id=netease_song_id,
        netease_song_id_source=netease_song_id_source,
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _timing_arguments(plan: FullAutoPlan, args: argparse.Namespace) -> list[str]:
    values = [
        "--manifest",
        str(plan.manifest),
        "--allow-partial-manifest",
        "--song-id",
        plan.track.song_id,
        "--source",
        str(plan.source),
        "--output-root",
        str(plan.initial_root),
        "--model-cache",
        str(plan.whisper_models),
        "--vocal-stems-dir",
        str(plan.vocals_root),
        "--device",
        getattr(args, "device", DEFAULT_DEVICE),
    ]
    if args.timing_alignment:
        values.extend(("--alignment", args.timing_alignment))
    if args.refresh_source:
        values.append("--refresh-source")
    if plan.netease_song_id:
        values.extend(("--netease-song-id", plan.netease_song_id))
    return values


def _wrapper_args(plan: FullAutoPlan, args: argparse.Namespace) -> argparse.Namespace:
    module = _mms_module(plan.track.language)
    values = [
        "--manifest",
        str(plan.manifest),
        "--song-id",
        plan.track.song_id,
        "--source",
        str(plan.source),
        "--output-dir",
        str(plan.output_dir / "workflow"),
        "--mms-model-path",
        str(plan.mms_model),
        "--vocals-root",
        str(plan.vocals_root),
        "--visual-style",
        args.visual_style,
        "--output-mode",
        args.output_mode,
        "--device",
        getattr(args, "device", DEFAULT_DEVICE),
    ]
    if plan.mms_backend != MMS_BACKEND_LOCAL:
        model_option = values.index("--mms-model-path")
        del values[model_option : model_option + 2]
        values.extend(("--mms-backend", plan.mms_backend))
    values.extend(("--quality-policy", args.quality_policy))
    for option, value in (
        ("--composition", args.composition),
        ("--cover", args.cover),
        ("--background", args.background),
        ("--cover-source-audio", args.cover_source_audio),
        ("--metadata-source-audio", args.metadata_source_audio),
        ("--background-video", args.background_video),
    ):
        if value is not None:
            values.extend((option, str(value.expanduser().resolve())))
    if plan.track.language != "ja":
        values.extend(("--language", plan.track.language))
    if args.output_mode == "subtitle-overlay":
        values.extend(("--color-policy", "project"))
    parsed = module.make_parser().parse_args(values)
    parsed.sug = plan.initial_sug
    return parsed


def run_full_auto(
    args: argparse.Namespace,
    *,
    allowed_languages: frozenset[str] = SUPPORTED_LANGUAGES,
) -> dict[str, Any]:
    plan = build_plan(args, allowed_languages=allowed_languages)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "song_id": plan.track.song_id,
        "language": plan.track.language,
        "mms_backend": plan.mms_backend,
        "quality_policy": args.quality_policy,
        "requested_device": getattr(args, "device", DEFAULT_DEVICE),
        "resolved_device": None,
        "netease_song_id": plan.netease_song_id,
        "netease_song_id_source": plan.netease_song_id_source,
        "lyrics_input": "manual-file" if plan.lyrics_file is not None else (
            "netease-refresh" if args.refresh_source else "frozen-json"
        ),
        "stages": [],
        "outputs": {},
    }
    try:
        manual_lyrics = _manual_lyrics_source(plan)
        if manual_lyrics is not None:
            report["stages"].append(
                {"name": "manual-lyrics-source", "status": "ok", **manual_lyrics}
            )
        stems = msst.prepare(
            [plan.track.audio_path], force=False, manifest=plan.album
        )
        if len(stems) != 1 or not plan.vocals.is_file():
            raise FullAutoError("single-song MSST preparation did not produce Vocals.wav")
        report["stages"].append({"name": "msst", "status": "ok"})

        timing_exit = karaoke_timing.main(_timing_arguments(plan, args))
        _require_file(plan.initial_sug, "private initial SUG")
        if timing_exit not in (0, 1):
            raise FullAutoError(f"timing builder returned unexpected status {timing_exit}")
        report["stages"].append(
            {
                "name": "initial-sug",
                "status": "ok" if timing_exit == 0 else "quality-fallback",
            }
        )
        report["outputs"]["initial_sug"] = str(
            plan.initial_sug.relative_to(plan.album.project_root.resolve())
        )

        recognition_paths: list[Path] = []
        if plan.track.language in {"zh", "en"}:
            asr_dir = plan.output_dir / "asr"
            asr_dir.mkdir(parents=True, exist_ok=False)
            for lane in ("stem", "mix"):
                output = asr_dir / f"{lane}-recognition.json"
                asr.run_manifest_audit(
                    manifest_path=plan.manifest,
                    source_path=plan.source,
                    song_ids=(plan.track.song_id,),
                    audio_kind=lane,
                    model_name=args.asr_model,
                    model_cache=plan.whisper_models,
                    vocals_root=plan.vocals_root,
                    cache_dir=plan.asr_cache,
                    output_path=output,
                    allow_partial_manifest=True,
                    device=getattr(args, "device", DEFAULT_DEVICE),
                )
                _require_file(output, f"{lane} recognition report")
                recognition_paths.append(output)
            report["stages"].append(
                {"name": "dual-asr", "status": "ok", "lanes": ["stem", "mix"]}
            )

        wrapper_args = _wrapper_args(plan, args)
        wrapper_args.recognition_audits = recognition_paths
        module = _mms_module(plan.track.language)
        workflow_report = module.run_mms_workflow(wrapper_args)
        report["stages"].append({"name": "mms-render", "status": "ok"})
        report["outputs"]["workflow"] = workflow_report
        report["requested_device"] = getattr(args, "device", DEFAULT_DEVICE)
        report["resolved_device"] = workflow_report.get("resolved_device")
        report["status"] = (
            "rendered-with-fallback"
            if workflow_report.get("status") == "rendered-with-fallback"
            else "ok"
        )
        return report
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if plan.output_dir.exists():
            _write_report(plan.output_dir / REPORT_NAME, report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--song-id", required=True)
    lyrics_input = parser.add_mutually_exclusive_group()
    lyrics_input.add_argument(
        "--source",
        type=Path,
        help="frozen lyric JSON, or the JSON destination used with --refresh-source",
    )
    lyrics_input.add_argument(
        "--lyrics-file",
        type=Path,
        help=(
            "manual UTF-8 .lrc or .txt lyrics; plain text receives uniform "
            "coarse timing anchors before acoustic alignment"
        ),
    )
    parser.add_argument(
        "--refresh-source",
        action="store_true",
        help="refresh --source from NetEase; requires --source",
    )
    parser.add_argument(
        "--netease-song-id",
        help=(
            "explicit NetEase numeric song id; refresh-source otherwise reads "
            "the id from the selected audio metadata"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--quality-policy",
        choices=("auto-fallback", "strict"),
        default="auto-fallback",
    )
    parser.add_argument("--mms-model-path", type=Path)
    parser.add_argument(
        "--mms-backend",
        choices=MMS_BACKENDS,
        default=MMS_BACKEND_LOCAL,
        help=(
            "explicit Japanese alignment backend; local-mms-fa remains the default"
        ),
    )
    parser.add_argument("--whisper-models", type=Path)
    parser.add_argument("--asr-model", default="base")
    add_device_argument(parser)
    parser.add_argument(
        "--timing-alignment",
        choices=("auto", "forced", "deterministic"),
        default="auto",
    )
    parser.add_argument(
        "--visual-style", choices=("vinyl", "spectrum", "spectrum-line"), default="spectrum"
    )
    parser.add_argument(
        "--output-mode",
        choices=("standard", "subtitle-overlay"),
        default="standard",
    )
    parser.add_argument("--background-video", type=Path)
    parser.add_argument("--composition", type=Path)
    parser.add_argument("--cover", type=Path)
    parser.add_argument("--background", type=Path)
    parser.add_argument("--cover-source-audio", type=Path)
    parser.add_argument("--metadata-source-audio", type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    allowed_languages: frozenset[str] = SUPPORTED_LANGUAGES,
) -> int:
    report = run_full_auto(
        build_parser().parse_args(argv), allowed_languages=allowed_languages
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
