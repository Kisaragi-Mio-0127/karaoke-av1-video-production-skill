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
    output_dir: Path
    initial_root: Path
    initial_sug: Path
    vocals_root: Path
    vocals: Path
    mms_model: Path
    mms_backend: str
    whisper_models: Path
    asr_cache: Path


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


def build_plan(
    args: argparse.Namespace,
    *,
    allowed_languages: frozenset[str] = SUPPORTED_LANGUAGES,
) -> FullAutoPlan:
    """Resolve all immutable inputs before any output or cache is created."""

    manifest = args.manifest.expanduser().resolve()
    source = args.source.expanduser().resolve()
    _require_file(manifest, "manifest")
    if not args.refresh_source:
        _require_file(source, "frozen lyric source")
    for label, optional_path in (
        ("explicit composition", args.composition),
        ("explicit cover", args.cover),
        ("explicit background", args.background),
        ("explicit cover source audio", args.cover_source_audio),
        ("explicit metadata source audio", args.metadata_source_audio),
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
    if source.is_file() and not args.refresh_source:
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

    project_root = album.project_root.resolve()
    private_root = (project_root / ".render-work").resolve()
    output_dir = args.output_dir.expanduser().resolve()
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
        output_dir=output_dir,
        initial_root=initial_root,
        initial_sug=initial_sug,
        vocals_root=vocals_root,
        vocals=vocals_root / track.audio_path.stem / "Vocals.wav",
        mms_model=mms_model,
        mms_backend=args.mms_backend,
        whisper_models=whisper_models,
        asr_cache=(project_root / ".cache" / "asr-recognition").resolve(),
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
    if args.netease_song_id:
        values.extend(("--netease-song-id", args.netease_song_id))
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
    ):
        if value is not None:
            values.extend((option, str(value.expanduser().resolve())))
    if plan.track.language != "ja":
        values.extend(("--language", plan.track.language))
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
        "stages": [],
        "outputs": {},
    }
    try:
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
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--refresh-source",
        action="store_true",
        help="explicitly refresh the selected lyric source from NetEase",
    )
    parser.add_argument(
        "--netease-song-id",
        help="NetEase numeric song id when it differs from the manifest song-id",
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
        "--visual-style", choices=("vinyl", "spectrum"), default="spectrum"
    )
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
