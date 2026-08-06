"""Prepare the project's MSST karaoke vocal stems.

The MSST implementation and model are supplied separately.  This adapter keeps
the StrangeUtaGame side small: decode project inputs into a local cache, import
an explicitly configured MSST runner, and write auditable per-stem provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import uuid
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from typing import Any

import soundfile as sf

try:
    from .karaoke_album import (
        DEFAULT_MANIFEST_PATH,
        AlbumManifest,
        load_album_manifest,
        project_relative,
    )
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_album import (  # type: ignore[no-redef]
        DEFAULT_MANIFEST_PATH,
        AlbumManifest,
        load_album_manifest,
        project_relative,
    )

ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / ".cache"
MSST_INPUT_DIR = CACHE_ROOT / "msst-input"
MSST_OUTPUT_DIR = CACHE_ROOT / "msst-vocals"
MSST_RUNTIME_DIR = CACHE_ROOT / "msst-runtime"

_EXTERNAL_SCRIPT_ENV = "KARAOKE_MSST_HELPER"

EXPECTED_SAMPLE_RATE = 44_100
EXPECTED_CHANNELS = 2
EXPECTED_SUBTYPE = "FLOAT"


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it all at once."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_msst_module(external_script: Path | None = None) -> ModuleType:
    """Import a separately supplied MSST preparation script."""

    configured = external_script or (
        Path(os.environ[_EXTERNAL_SCRIPT_ENV]).expanduser()
        if os.environ.get(_EXTERNAL_SCRIPT_ENV)
        else None
    )
    if configured is None:
        raise RuntimeError(
            "MSST helper is not configured; pass --external-script or set "
            f"{_EXTERNAL_SCRIPT_ENV}"
        )
    script = configured.resolve()
    if not script.is_file():
        raise FileNotFoundError(f"MSST preparation script does not exist: {script}")

    spec = importlib.util.spec_from_file_location(
        "karaoke_external_msst_helper", str(script)
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load MSST preparation script: {script}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dependency_paths(msst_module: ModuleType) -> dict[str, Path]:
    """Resolve the external runner's script, executable, config, and model."""

    names = {
        "python": "MSST_PYTHON",
        "inference": "MSST_INFER",
        "config": "KARAOKE_CONFIG",
        "model": "KARAOKE_MODEL",
    }
    paths = {
        label: Path(getattr(msst_module, name)).resolve()
        for label, name in names.items()
    }
    paths["script"] = Path(msst_module.__file__ or "").resolve()

    missing = [
        f"{label}: {path}" for label, path in paths.items() if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError("Missing MSST dependency files:\n" + "\n".join(missing))
    return paths


def _wav_metadata(path: Path) -> dict[str, Any]:
    info = sf.info(str(path))
    return {
        "format": info.format.upper(),
        "subtype": info.subtype.upper(),
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "frames": int(info.frames),
        "duration_seconds": float(info.frames / info.samplerate),
    }


def _expected_wav_metadata(path: Path) -> dict[str, Any]:
    metadata = _wav_metadata(path)
    if metadata["format"] != "WAV":
        raise ValueError(f"Expected a WAV file: {path} ({metadata['format']})")
    if metadata["subtype"] != EXPECTED_SUBTYPE:
        raise ValueError(f"Expected a 32-bit float WAV: {path} ({metadata['subtype']})")
    if metadata["sample_rate"] != EXPECTED_SAMPLE_RATE:
        raise ValueError(
            f"Expected {EXPECTED_SAMPLE_RATE} Hz: {path} ({metadata['sample_rate']} Hz)"
        )
    if metadata["channels"] != EXPECTED_CHANNELS:
        raise ValueError(
            f"Expected stereo audio: {path} ({metadata['channels']} channels)"
        )
    if metadata["frames"] <= 0:
        raise ValueError(f"Audio is empty: {path}")
    return metadata


def _temporary_wav_path(output: Path) -> Path:
    return output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.wav")


def default_ffmpeg() -> Path:
    """Return the project environment's bundled ffmpeg executable."""

    try:
        import imageio_ffmpeg
    except ImportError as error:  # pragma: no cover - environment packaging error
        raise RuntimeError(
            "imageio-ffmpeg is required; run this script through the karaoke uv environment"
        ) from error

    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    if not ffmpeg.is_file():
        raise FileNotFoundError(f"ffmpeg executable does not exist: {ffmpeg}")
    return ffmpeg


def decode_to_wav(
    source: Path,
    output: Path,
    *,
    msst_module: ModuleType | None = None,
    ffmpeg: Path | None = None,
) -> None:
    """Decode an MP3 to a project-local 44.1 kHz stereo FLOAT WAV."""

    if msst_module is None:
        msst_module = load_msst_module()
    source = source.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = _temporary_wav_path(output)
    command = [
        str(ffmpeg or default_ffmpeg()),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        str(EXPECTED_CHANNELS),
        "-ar",
        str(EXPECTED_SAMPLE_RATE),
        "-c:a",
        "pcm_f32le",
        str(temporary_output),
    ]
    print("$", subprocess.list2cmdline(command), flush=True)
    try:
        environment = msst_module.runtime_environment(MSST_RUNTIME_DIR / "ffmpeg")
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        _expected_wav_metadata(temporary_output)
        os.replace(temporary_output, output)
    finally:
        with suppress(FileNotFoundError):
            temporary_output.unlink()


def _artifact_record(path: Path, project_root: Path) -> dict[str, str]:
    return {"path": project_relative(path, project_root), "sha256": sha256(path)}


def _build_report(
    *,
    source: Path,
    model: Path,
    config: Path,
    script: Path,
    stem: Path,
    metadata: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    audio = {
        "format": metadata["format"],
        "subtype": metadata["subtype"],
        "sample_rate": metadata["sample_rate"],
        "channels": metadata["channels"],
        "frames": metadata["frames"],
        "duration_seconds": metadata["duration_seconds"],
    }
    return {
        "schema_version": 1,
        "kind": "msst_vocals",
        "source": _artifact_record(source, project_root),
        "model": _artifact_record(model, project_root),
        "config": _artifact_record(config, project_root),
        "script": _artifact_record(script, project_root),
        "stem": _artifact_record(stem, project_root),
        "sample_rate": metadata["sample_rate"],
        "channels": metadata["channels"],
        "frames": metadata["frames"],
        "duration_seconds": metadata["duration_seconds"],
        "audio": audio,
    }


def _report_artifact_matches(
    report: dict[str, Any], key: str, path: Path, project_root: Path
) -> bool:
    value = report.get(key)
    return (
        isinstance(value, dict)
        and value.get("path") == project_relative(path, project_root)
        and value.get("sha256") == sha256(path)
    )


def _is_valid_output(
    *,
    source: Path,
    output: Path,
    report_path: Path,
    model: Path,
    config: Path,
    script: Path,
    project_root: Path,
) -> bool:
    """Validate both the output audio and its content-addressed provenance."""

    if not output.is_file() or not report_path.is_file():
        return False
    try:
        metadata = _expected_wav_metadata(output)
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(report, dict) or report.get("kind") != "msst_vocals":
        return False

    for key, path in (
        ("source", source),
        ("model", model),
        ("config", config),
        ("script", script),
        ("stem", output),
    ):
        if not _report_artifact_matches(report, key, path, project_root):
            return False

    if report.get("sample_rate") != metadata["sample_rate"]:
        return False
    if report.get("channels") != metadata["channels"]:
        return False
    if report.get("frames") != metadata["frames"]:
        return False
    try:
        reported_duration = float(report["duration_seconds"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isclose(
        reported_duration,
        metadata["duration_seconds"],
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _resolve_sources(
    sources: Sequence[Path] | None,
    album: AlbumManifest | None = None,
) -> tuple[Path, ...]:
    if sources is None and album is None:
        raise ValueError("a manifest or explicit --audio inputs are required")
    selected = (
        tuple(track.audio_path for track in album.tracks)  # type: ignore[union-attr]
        if sources is None
        else tuple(sources)
    )
    resolved = tuple(path.expanduser().resolve() for path in selected)
    missing = [str(path) for path in resolved if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing source MP3 files:\n" + "\n".join(missing))

    stems = [path.stem for path in resolved]
    if len(stems) != len(set(stems)):
        raise ValueError("Source MP3 stems must be unique: " + ", ".join(stems))
    return resolved


def prepare(
    sources: Sequence[Path] | None = None,
    *,
    force: bool = False,
    msst_module: ModuleType | None = None,
    manifest: AlbumManifest | None = None,
    external_script: Path | None = None,
) -> list[Path]:
    """Prepare the manifest's five vocal stems in one external MSST batch."""

    album = manifest
    if album is None:
        raise ValueError("manifest is required")
    if msst_module is None:
        msst_module = load_msst_module(external_script)
    dependencies = _dependency_paths(msst_module)
    selected = _resolve_sources(sources, album)
    MSST_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    MSST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MSST_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    pending: list[tuple[Path, Path, Path, Path]] = []
    result_paths: list[Path] = []
    for source in selected:
        output_dir = MSST_OUTPUT_DIR / source.stem
        output = output_dir / "Vocals.wav"
        report = output_dir / "separation_report.json"
        if not force and _is_valid_output(
            source=source,
            output=output,
            report_path=report,
            model=dependencies["model"],
            config=dependencies["config"],
            script=dependencies["script"],
            project_root=album.project_root,
        ):
            print(f"[{source.stem}] valid output exists; skipping MSST", flush=True)
            result_paths.append(output)
            continue

        input_wav = MSST_INPUT_DIR / f"{source.stem}.wav"
        decode_to_wav(source, input_wav, msst_module=msst_module)
        pending.append((source, input_wav, output, report))

    if not pending:
        return result_paths

    run_input_dir = MSST_INPUT_DIR / f".run-{uuid.uuid4().hex}"
    run_input_dir.mkdir(parents=True, exist_ok=False)
    try:
        for _source, input_wav, _output, _report in pending:
            shutil.copy2(input_wav, run_input_dir / input_wav.name)
        print(
            f"Running MSST for {len(pending)} source(s) with the configured external runner",
            flush=True,
        )
        msst_module.run_msst(
            config=dependencies["config"],
            model=dependencies["model"],
            input_dir=run_input_dir,
            output_dir=MSST_OUTPUT_DIR,
            temp=MSST_RUNTIME_DIR,
        )
    finally:
        shutil.rmtree(run_input_dir, ignore_errors=True)

    for source, _input_wav, output, report in pending:
        metadata = _expected_wav_metadata(output)
        document = _build_report(
            source=source,
            model=dependencies["model"],
            config=dependencies["config"],
            script=dependencies["script"],
            stem=output,
            metadata=metadata,
            project_root=album.project_root,
        )
        _write_report(report, document)
        print(f"[{source.stem}] ready: {output}", flush=True)
        result_paths.append(output)
    return result_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare 44.1 kHz stereo FLOAT MSST vocal stems in "
            "StrangeUtaGame/.cache."
        )
    )
    parser.add_argument(
        "--audio",
        nargs="+",
        type=Path,
        metavar="MP3",
        help="override the five manifest MP3 inputs",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="album.json manifest that owns the default five inputs",
    )
    parser.add_argument(
        "--allow-partial-manifest",
        action="store_true",
        help="allow an explicitly supplied manifest with fewer than five tracks",
    )
    parser.add_argument(
        "--external-script",
        type=Path,
        help=f"external MSST helper (or set {_EXTERNAL_SCRIPT_ENV})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-decode and re-run MSST even when a matching report exists",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    prepare(
        args.audio,
        force=args.force,
        manifest=load_album_manifest(
            args.manifest,
            require_five_tracks=not args.allow_partial_manifest,
        ),
        external_script=args.external_script,
    )


if __name__ == "__main__":
    main()
