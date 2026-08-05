#!/usr/bin/env python3
"""Render the five-track album directly to NVIDIA AV1 YUV 4:2:0.

This lane is intentionally separate from both ``video/hevc444`` and the old
mixed ``video/av1`` experiments.  Every output is built from manifest audio,
profile artwork, latest SUG timing, and regenerated profile ASS.  Existing
video masters are never accepted as inputs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import render_karaoke_direct_av1_album as render_core
    from .karaoke_album import (
        DEFAULT_MANIFEST_PATH,
        AlbumManifest,
        project_relative,
        sha256_file,
    )
    from .render_vinyl_karaoke import probe_libass_font
    from .sync_karaoke_editable_ruby import synchronize_document
except ImportError:  # pragma: no cover - direct script execution
    import render_karaoke_direct_av1_album as render_core  # type: ignore[no-redef]
    from karaoke_album import (  # type: ignore[no-redef]
        DEFAULT_MANIFEST_PATH,
        AlbumManifest,
        project_relative,
        sha256_file,
    )
    from render_vinyl_karaoke import probe_libass_font  # type: ignore[no-redef]
    from sync_karaoke_editable_ruby import (
        synchronize_document,  # type: ignore[no-redef]
    )

try:
    from .karaoke_language import language_identity as _bingbu_language_identity
except ImportError:  # pragma: no cover - direct script execution/compatibility
    try:
        from karaoke_language import (
            language_identity as _bingbu_language_identity,  # type: ignore[no-redef]
        )
    except ImportError:  # pragma: no cover - compatibility before Bingbu lands
        _bingbu_language_identity = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIEW_SCRIPT = REPO_ROOT / "scripts" / "karaoke_review_preview.py"
PROFILES = ("standard", "wide")
AV1_OUTPUT_DIR = "av1-420"
AV1_REPORT_NAME = "av1_420_report.json"


class DirectAV1420RenderError(RuntimeError):
    """Raised when an AV1 4:2:0 artifact fails a publication gate."""


def verify_editable_ruby_sources(tasks: Iterable[render_core.RenderTask]) -> None:
    """Fail before encoding when an editable SUG lags reviewed ruby rules."""

    checked: set[Path] = set()
    for task in tasks:
        path = task.sug_path.resolve()
        if path in checked:
            continue
        checked.add(path)
        document = json.loads(path.read_text(encoding="utf-8"))
        changes, unresolved = synchronize_document(document)
        if unresolved:
            raise DirectAV1420RenderError(
                f"editable ruby has unresolved contextual spans: {path.name}"
            )
        if changes:
            raise DirectAV1420RenderError(
                f"editable ruby is stale ({len(changes)} changes): {path.name}; "
                "run scripts/sync_karaoke_editable_ruby.py"
            )


def _resolve_path(value: Path | str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _temporary_path(target: Path) -> Path:
    return target.with_name(
        f".{target.stem}.{uuid.uuid4().hex}.partial{target.suffix}"
    )


def _remove_if_present(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()


def _publish_atomically(pairs: Sequence[tuple[Path, Path]]) -> None:
    for temporary, target in pairs:
        if not temporary.is_file():
            raise DirectAV1420RenderError(
                f"validated temporary artifact disappeared: {temporary}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(temporary), str(target))


def write_json_atomically(path: Path, value: dict[str, Any]) -> None:
    temporary = _temporary_path(path)
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _publish_atomically([(temporary, path)])
    finally:
        _remove_if_present(temporary)


def _timing_overrides_path(task: Any) -> Path | None:
    root = getattr(task, "root", None)
    if root is None:
        return None
    path = Path(root) / "sources" / "timing_overrides.json"
    return path.resolve() if path.is_file() else None


def _source_record(
    task: render_core.RenderTask,
    *,
    ass_path: Path | None = None,
) -> dict[str, str | None]:
    def relative(path: Path | None) -> str | None:
        return project_relative(path, REPO_ROOT) if path is not None else None

    def identity(path: Path | None) -> str | None:
        return sha256_file(path) if path is not None and path.is_file() else None

    timing_overrides = _timing_overrides_path(task)
    published_ass = ass_path or task.ass_output
    return {
        "audio": relative(task.track.audio_path),
        "audio_sha256": identity(task.track.audio_path),
        "composition": relative(task.composition_path),
        "composition_sha256": identity(task.composition_path),
        "vinyl": relative(task.vinyl_path),
        "vinyl_sha256": identity(task.vinyl_path),
        "sug": relative(task.sug_path),
        "sug_sha256": identity(task.sug_path),
        # Record the profile ASS destination, not the pre-render discovery
        # candidate.  Once the profile ASS is published, task planning finds
        # that file as ``ass_source``; using the discovery candidate here
        # otherwise makes a freshly rendered report immediately look stale.
        "latest_ass": relative(task.ass_output),
        "latest_ass_sha256": identity(published_ass),
        "timing_overrides": relative(timing_overrides),
        "timing_overrides_sha256": (
            sha256_file(timing_overrides) if timing_overrides is not None else None
        ),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identity_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _read_sug_document(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _source_ruby_entries(document: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if document is None:
        return []
    sentences = document.get("sentences")
    if not isinstance(sentences, list):
        return []
    entries: list[dict[str, Any]] = []
    for sentence_index, sentence in enumerate(sentences):
        if not isinstance(sentence, Mapping):
            continue
        characters = sentence.get("characters")
        if not isinstance(characters, list):
            continue
        for character_index, character in enumerate(characters):
            if not isinstance(character, Mapping):
                continue
            ruby = character.get("ruby")
            parts = ruby.get("parts") if isinstance(ruby, Mapping) else None
            if not isinstance(parts, list):
                continue
            reading = "".join(
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, Mapping)
            )
            if not reading:
                continue
            entries.append(
                {
                    "sentence_index": sentence_index,
                    "character_index": character_index,
                    "text": str(character.get("char") or ""),
                    "reading": reading,
                }
            )
    return entries


def _rendered_ruby_entries(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    ass = report.get("ass")
    lines = ass.get("lines") if isinstance(ass, Mapping) else None
    if not isinstance(lines, list):
        return []
    entries: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        if not isinstance(line, Mapping):
            continue
        ruby = line.get("ruby")
        if not isinstance(ruby, list):
            continue
        for ruby_index, token in enumerate(ruby):
            if not isinstance(token, Mapping):
                continue
            text = str(token.get("text") or "")
            reading = str(token.get("reading") or "")
            if not text or not reading:
                continue
            entries.append(
                {
                    "line_index": line_index,
                    "ruby_index": ruby_index,
                    "text": text,
                    "reading": reading,
                }
            )
    return entries


def _language_interface() -> Any:
    """Return Bingbu's optional language resolver when it is available.

    The 420 lane is intentionally compatible with both the current core and
    the upcoming Bingbu interface.  The fallback remains deterministic and
    reads the language from the SUG metadata; it is not a second source of
    project language policy.
    """

    if callable(_bingbu_language_identity):
        return _bingbu_language_identity
    for name in (
        "language_identity",
        "get_language_identity",
        "resolve_language_identity",
    ):
        candidate = getattr(render_core, name, None)
        if callable(candidate):
            return candidate
    return None


def _call_language_interface(
    callback: Any,
    *,
    task: render_core.RenderTask,
    document: Mapping[str, Any] | None,
    fallback_language: Any,
) -> Any:
    try:
        parameters = list(inspect.signature(callback).parameters.values())
    except (TypeError, ValueError):
        parameters = []
    if not parameters:
        return callback()
    parameter = parameters[0]
    name = parameter.name.casefold()
    if callback is _bingbu_language_identity or "language" in name:
        argument: Any = fallback_language
    elif "path" in name or "sug" in name or "source" in name:
        argument: Any = task.sug_path
    elif "document" in name or "project" in name or "metadata" in name:
        argument = document
    else:
        argument = task
    return callback(argument)


def _normalise_language_identity(
    value: Any,
    *,
    fallback_language: Any = None,
    source: str,
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        payload = dict(value)
        code = next(
            (
                payload.get(key)
                for key in ("code", "language", "language_code", "locale")
                if payload.get(key) not in (None, "")
            ),
            fallback_language,
        )
        identity = payload.get("identity") or payload.get("sha256")
        identity = str(identity) if identity else _identity_digest(payload)
    else:
        code = value if value not in (None, "") else fallback_language
        payload = {"code": code} if code not in (None, "") else {}
        identity = _identity_digest(payload)
    result = {
        "code": str(code) if code not in (None, "") else None,
        "identity": identity,
        "source": source,
    }
    if isinstance(value, Mapping):
        result.update(payload)
        result["code"] = str(code) if code not in (None, "") else None
        result["identity"] = identity
        result["source"] = source
    return result


def language_identity(
    task: render_core.RenderTask,
    report: Mapping[str, Any] | None = None,
    *,
    prefer_report: bool = True,
) -> dict[str, Any]:
    """Resolve one track's language through Bingbu or deterministic SUG fallback."""

    if (
        prefer_report
        and report is not None
        and isinstance(report.get("language_identity"), Mapping)
    ):
        return dict(report["language_identity"])
    document = _read_sug_document(task.sug_path)
    metadata = document.get("metadata") if isinstance(document, Mapping) else None
    fallback_language = (
        metadata.get("language")
        if isinstance(metadata, Mapping)
        else document.get("language") if isinstance(document, Mapping) else None
    )
    callback = _language_interface()
    if callback is not None:
        try:
            value = _call_language_interface(
                callback,
                task=task,
                document=document,
                fallback_language=fallback_language,
            )
        except (OSError, TypeError, KeyError):
            value = None
        if value is not None:
            return _normalise_language_identity(
                value,
                fallback_language=fallback_language,
                source="bingbu-language-interface",
            )
    return _normalise_language_identity(
        fallback_language,
        fallback_language=fallback_language,
        source="sug-metadata",
    )


def ruby_identity(
    task: render_core.RenderTask,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Record source and rendered ruby identities without rewriting either."""

    source_entries = _source_ruby_entries(_read_sug_document(task.sug_path))
    rendered_entries = _rendered_ruby_entries(report)
    source = {
        "identity": _identity_digest(source_entries),
        "count": len(source_entries),
        "available": bool(source_entries),
    }
    rendered = {
        "identity": _identity_digest(rendered_entries),
        "count": len(rendered_entries),
        "available": bool(rendered_entries),
    }
    return {
        "status": "pass" if source["available"] and rendered["available"] else "not-available",
        "source": source,
        "rendered": rendered,
    }


def build_language_ruby_identity(
    task: render_core.RenderTask,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "language": language_identity(task, report, prefer_report=False),
        "ruby": ruby_identity(task, report),
    }


def _identity_key(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        identity = value.get("identity")
        if identity not in (None, ""):
            return str(identity)
    return _canonical_json(value) if isinstance(value, (Mapping, list, tuple)) else str(value)


def aggregate_language_ruby_identity(
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate identity by song and reject cross-profile drift."""

    by_song: dict[str, dict[str, Any]] = {}
    incomplete = False
    for item in results:
        song_id = str(item.get("song_id"))
        language = item.get("language_identity", item.get("language"))
        ruby = item.get("ruby_identity", item.get("ruby"))
        if language is None or ruby is None:
            incomplete = True
            continue
        bucket = by_song.setdefault(
            song_id,
            {
                "song_id": song_id,
                "language_identity": language,
                "ruby_identity": ruby,
                "profiles": [],
            },
        )
        if _identity_key(bucket["language_identity"]) != _identity_key(language):
            raise DirectAV1420RenderError(
                f"aggregate language identity mismatch for song {song_id}"
            )
        if _identity_key(bucket["ruby_identity"]) != _identity_key(ruby):
            raise DirectAV1420RenderError(
                f"aggregate ruby identity mismatch for song {song_id}"
            )
        bucket["profiles"].append(str(item.get("profile")))
    if not by_song:
        status = "not-provided"
    elif incomplete:
        status = "incomplete"
    else:
        status = "pass"
    return {
        "status": status,
        "songs": [by_song[key] for key in sorted(by_song)],
    }


def configure_av1_tasks(
    tasks: Iterable[render_core.RenderTask],
    *,
    root: Path,
) -> tuple[render_core.RenderTask, ...]:
    configured: list[render_core.RenderTask] = []
    for task in tasks:
        task.video_output = (
            root
            / "video"
            / AV1_OUTPUT_DIR
            / task.profile
            / task.track.numbered_video_filename
        ).resolve()
        task.direct_report = (
            root
            / "validation"
            / task.profile
            / f"{task.track.artifact_slug}_direct_av1_420_render_report.json"
        ).resolve()
        configured.append(task)
    return tuple(configured)


def build_preview_command(
    task: render_core.RenderTask,
    *,
    temporary_video: Path,
    temporary_ass: Path,
    temporary_report: Path,
    preview_script: Path,
    av1_cq: int,
) -> list[str]:
    command = [
        sys.executable,
        str(preview_script.resolve()),
        "--sug",
        str(task.sug_path),
        "--audio",
        str(task.track.audio_path),
        "--composition",
        str(task.composition_path),
        "--vinyl",
        str(task.vinyl_path),
        "--fonts-dir",
        str(task.fonts_dir),
        "--font-file",
        str(task.font_file),
        "--output",
        str(temporary_video),
        "--ass-output",
        str(temporary_ass),
        "--report-output",
        str(temporary_report),
        "--start",
        "0",
        "--duration",
        f"{task.duration_seconds:.3f}",
        "--layout",
        task.profile,
        "--video-encoder",
        "av1_nvenc",
        "--av1-cq",
        str(av1_cq),
    ]
    timing_overrides = _timing_overrides_path(task)
    song_id = getattr(getattr(task, "track", None), "song_id", None)
    if timing_overrides is not None and song_id is not None:
        command.extend(
            [
                "--timing-overrides",
                str(timing_overrides),
                "--song-id",
                str(song_id),
            ]
        )
    return command


def _flag_value(command: Sequence[str], flag: str) -> str:
    try:
        return str(command[command.index(flag) + 1])
    except (ValueError, IndexError) as error:
        raise DirectAV1420RenderError(f"preview command is missing {flag}") from error


def validate_direct_source_command(command: Sequence[str]) -> None:
    if _flag_value(command, "--video-encoder") != "av1_nvenc":
        raise DirectAV1420RenderError("AV1 lane must use video_encoder=av1_nvenc")
    expected_suffixes = {
        "--sug": ".sug",
        "--composition": ".png",
        "--vinyl": ".png",
    }
    for flag, suffix in expected_suffixes.items():
        source = Path(_flag_value(command, flag))
        if source.suffix.casefold() != suffix:
            raise DirectAV1420RenderError(
                f"direct AV1 source {flag} must be {suffix}, got {source}"
            )
    audio_source = Path(_flag_value(command, "--audio"))
    allowed_audio_suffixes = {".flac", ".mp3", ".wav"}
    if audio_source.suffix.casefold() not in allowed_audio_suffixes:
        expected = ", ".join(sorted(allowed_audio_suffixes))
        raise DirectAV1420RenderError(
            f"direct AV1 audio source must be one of {expected}, got {audio_source}"
        )
    forbidden_video_suffixes = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
    for flag in ("--sug", "--audio", "--composition", "--vinyl"):
        source = Path(_flag_value(command, flag))
        if source.suffix.casefold() in forbidden_video_suffixes:
            raise DirectAV1420RenderError(f"video master input is forbidden: {source}")
    if "--timing-overrides" in command:
        timing_overrides = Path(_flag_value(command, "--timing-overrides"))
        if timing_overrides.suffix.casefold() != ".json":
            raise DirectAV1420RenderError(
                f"timing overrides must be JSON: {timing_overrides}"
            )
        _flag_value(command, "--song-id")


def run_preview(
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    print("$", subprocess.list2cmdline([str(value) for value in command]), flush=True)
    return subprocess.run(
        list(command),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise DirectAV1420RenderError(f"invalid renderer report {path}: {error}") from error
    if not isinstance(value, dict):
        raise DirectAV1420RenderError(f"renderer report must be an object: {path}")
    return value


def validate_preview_report(report: dict[str, Any], *, av1_cq: int) -> None:
    if report.get("status") != "ok":
        raise DirectAV1420RenderError(
            f"unexpected preview report status: {report.get('status')!r}"
        )
    ass = report.get("ass")
    if not isinstance(ass, dict) or not ass.get("ass"):
        raise DirectAV1420RenderError("preview report has no ASS artifact")
    video = report.get("video")
    if not isinstance(video, dict):
        raise DirectAV1420RenderError("preview report has no video artifact")
    expected = {
        "video_encoder": "av1_nvenc",
        "pixel_format": "yuv420p",
        "av1_cq": av1_cq,
    }
    for key, value in expected.items():
        if video.get(key) != value:
            raise DirectAV1420RenderError(
                f"preview report mismatch: {key}={video.get(key)!r}, expected {value!r}"
            )


def default_ffmpeg() -> Path:
    return render_core.default_ffmpeg()


def verify_av1_420_output(
    output: Path,
    *,
    ffmpeg: Path | None = None,
    full_decode: bool = False,
) -> dict[str, Any]:
    checks: dict[str, bool] = {
        "exists": output.is_file(),
        "non_empty": output.is_file() and output.stat().st_size > 0,
    }
    if not checks["non_empty"]:
        return {"ok": False, "path": str(output), "checks": checks, "probe": ""}

    executable = (ffmpeg or default_ffmpeg()).resolve()
    completed = subprocess.run(
        [str(executable), "-hide_banner", "-i", str(output)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    probe = f"{completed.stdout}\n{completed.stderr}"
    video_line = re.search(r"Video:\s+([^\r\n]+)", probe, re.IGNORECASE)
    checks.update(
        {
            "codec_av1": bool(re.search(r"Video:\s+av1\b", probe, re.IGNORECASE)),
            "codec_tag_av01": "(av01 /" in probe.casefold(),
            "not_h264": not bool(
                re.search(r"Video:\s+(?:h264|h\.264)\b", probe, re.IGNORECASE)
            ),
            "not_hevc": not bool(re.search(r"Video:\s+hevc\b", probe, re.IGNORECASE)),
            "resolution_1920x1080": "1920x1080" in probe,
            "pixel_format_yuv420p": bool(
                re.search(r"Video:.*\byuv420p\b", probe, re.IGNORECASE)
            ),
            "limited_range_bt709": bool(
                re.search(r"\byuv420p\(tv,\s*bt709", probe, re.IGNORECASE)
            ),
            "cfr_30fps": bool(re.search(r"\b30\s+fps\b", probe)),
            "aac_audio": bool(re.search(r"Audio:\s+aac\b", probe, re.IGNORECASE)),
        }
    )
    decode: dict[str, Any] | None = None
    if full_decode:
        started = time.perf_counter()
        decoded = subprocess.run(
            [
                str(executable),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(output),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                os.devnull,
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        checks["full_decode"] = decoded.returncode == 0
        decode = {
            "returncode": decoded.returncode,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stderr_tail": decoded.stderr[-1200:],
        }
    return {
        "ok": all(checks.values()),
        "path": str(output),
        "size_bytes": output.stat().st_size,
        "checks": checks,
        "video_line": video_line.group(0) if video_line else None,
        "decode": decode,
    }


def normalise_report(
    report: dict[str, Any],
    task: render_core.RenderTask,
    *,
    media: dict[str, Any],
    video_sha256: str,
    elapsed_seconds: float,
    av1_cq: int,
    ass_source_path: Path | None = None,
    libass_font_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(report)
    ass = result.setdefault("ass", {})
    if not isinstance(ass, dict):
        ass = {}
        result["ass"] = ass
    ass["ass"] = str(task.ass_output.resolve())
    video = result.setdefault("video", {})
    if not isinstance(video, dict):
        video = {}
        result["video"] = video
    video.update(
        {
            "video": str(task.video_output.resolve()),
            "video_encoder": "av1_nvenc",
            "pixel_format": "yuv420p",
            "color_range": "tv",
            "color_matrix": "bt709",
            "av1_cq": av1_cq,
            "bytes": task.video_output.stat().st_size
            if task.video_output.is_file()
            else media["size_bytes"],
            "media_checks": media,
        }
    )
    result.update(
        {
            "profile": task.profile,
            "song_id": str(task.track.song_id),
            "title": task.track.title,
            "artist": task.track.artist,
            "artifact_slug": task.track.artifact_slug,
            "render_mode": "direct-av1-420",
            "intermediate_video": False,
            "intermediate_h264": False,
            "intermediate_hevc": False,
            "source_chain": (
                "manifest audio + composition + vinyl + latest SUG/ASS -> "
                "av1_nvenc/yuv420p"
            ),
            "sources": _source_record(task, ass_path=ass_source_path),
            "output_sha256": video_sha256,
            "render_elapsed_seconds": elapsed_seconds,
        }
    )
    identities = build_language_ruby_identity(task, result)
    result["language_identity"] = identities["language"]
    result["ruby_identity"] = identities["ruby"]
    if libass_font_probe is not None:
        result["libass_font_probe"] = dict(libass_font_probe)
    return result


def render_one(
    task: render_core.RenderTask,
    *,
    preview_script: Path,
    ffmpeg: Path | None,
    av1_cq: int,
    libass_font_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    temporary_video = _temporary_path(task.video_output)
    temporary_ass = _temporary_path(task.ass_output)
    temporary_report = _temporary_path(task.direct_report)
    command = build_preview_command(
        task,
        temporary_video=temporary_video,
        temporary_ass=temporary_ass,
        temporary_report=temporary_report,
        preview_script=preview_script,
        av1_cq=av1_cq,
    )
    validate_direct_source_command(command)
    started = time.perf_counter()
    try:
        completed = run_preview(command)
        if completed.returncode != 0:
            raise DirectAV1420RenderError(
                f"preview render failed for {task.profile}:{task.track.artifact_slug} "
                f"(returncode={completed.returncode})\n"
                f"{completed.stderr[-2500:] or completed.stdout[-1000:]}"
            )
        ass_gate = render_core._validate_ass_file(temporary_ass, task.profile)
        if not ass_gate.get("ok"):
            raise DirectAV1420RenderError(f"ASS validation failed: {ass_gate}")
        preview_report = _read_json(temporary_report)
        validate_preview_report(preview_report, av1_cq=av1_cq)
        media = verify_av1_420_output(temporary_video, ffmpeg=ffmpeg)
        if not media.get("ok"):
            raise DirectAV1420RenderError(
                f"AV1 yuv420p media validation failed: {media}"
            )
        actual_libass_probe = (
            dict(libass_font_probe)
            if libass_font_probe is not None
            else probe_libass_font(
                (ffmpeg or default_ffmpeg()).resolve(),
                task.fonts_dir,
                str(
                    getattr(
                        task,
                        "font_family",
                        getattr(render_core, "FONT_FAMILY", "HarmonyOS Sans SC"),
                    )
                ),
                "subtitles",
                ass_path=temporary_ass,
            )
        )
        if (
            not actual_libass_probe.get("ok")
            or actual_libass_probe.get("probe_kind") != "real_lyrics"
        ):
            raise DirectAV1420RenderError(
                f"real lyric libass/font gate failed: {actual_libass_probe}"
            )
        elapsed_seconds = round(time.perf_counter() - started, 3)
        output_sha256 = sha256_file(temporary_video)
        normalised = normalise_report(
            preview_report,
            task,
            media=media,
            video_sha256=output_sha256,
            elapsed_seconds=elapsed_seconds,
            av1_cq=av1_cq,
            ass_source_path=temporary_ass,
            libass_font_probe=actual_libass_probe,
        )
        normalised["video"]["bytes"] = temporary_video.stat().st_size
        temporary_report.write_text(
            json.dumps(normalised, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _publish_atomically(
            [
                (temporary_ass, task.ass_output),
                (temporary_report, task.direct_report),
                (temporary_video, task.video_output),
            ]
        )
        return {
            "status": "ok",
            "profile": task.profile,
            "song_id": str(task.track.song_id),
            "title": task.track.title,
            "artifact_slug": task.track.artifact_slug,
            "render_mode": "direct-av1-420",
            "video": str(task.video_output),
            "report": str(task.direct_report),
            "output_size_bytes": task.video_output.stat().st_size,
            "sha256": output_sha256,
            "elapsed_seconds": elapsed_seconds,
            "sources": _source_record(task),
            "media": media,
            "language_identity": normalised["language_identity"],
            "ruby_identity": normalised["ruby_identity"],
            "libass_font_probe": normalised["libass_font_probe"],
        }
    finally:
        for temporary in (temporary_video, temporary_ass, temporary_report):
            _remove_if_present(temporary)


def collect_existing_results(
    tasks: Sequence[render_core.RenderTask],
    *,
    ffmpeg: Path | None,
    av1_cq: int,
    full_decode: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for task in tasks:
        if not task.direct_report.is_file():
            raise DirectAV1420RenderError(f"missing AV1 render report: {task.direct_report}")
        report = _read_json(task.direct_report)
        validate_preview_report(report, av1_cq=av1_cq)
        expected = {
            "profile": task.profile,
            "song_id": str(task.track.song_id),
            "artifact_slug": task.track.artifact_slug,
            "render_mode": "direct-av1-420",
            "intermediate_video": False,
            "intermediate_h264": False,
            "intermediate_hevc": False,
        }
        for key, value in expected.items():
            if report.get(key) != value:
                raise DirectAV1420RenderError(
                    f"published report mismatch for {task.profile}:"
                    f"{task.track.artifact_slug}: {key}={report.get(key)!r}"
                )
        if report.get("sources") != _source_record(task):
            raise DirectAV1420RenderError(
                f"published AV1 source chain is stale: {task.profile}:"
                f"{task.track.artifact_slug}"
            )
        ass_gate = render_core._validate_ass_file(task.ass_output, task.profile)
        if not ass_gate.get("ok"):
            raise DirectAV1420RenderError(f"published ASS failed validation: {ass_gate}")
        actual_libass_probe = probe_libass_font(
            (ffmpeg or default_ffmpeg()).resolve(),
            task.fonts_dir,
            str(
                getattr(
                    task,
                    "font_family",
                    getattr(render_core, "FONT_FAMILY", "HarmonyOS Sans SC"),
                )
            ),
            "subtitles",
            ass_path=task.ass_output,
        )
        if (
            not actual_libass_probe.get("ok")
            or actual_libass_probe.get("probe_kind") != "real_lyrics"
        ):
            raise DirectAV1420RenderError(
                f"published real lyric libass/font gate failed: {actual_libass_probe}"
            )
        recorded_libass_probe = report.get("libass_font_probe")
        if not isinstance(recorded_libass_probe, Mapping) or recorded_libass_probe.get(
            "probe_kind"
        ) != "real_lyrics":
            raise DirectAV1420RenderError(
                f"published report lacks a real lyric libass/font gate: {task.direct_report}"
            )
        media = verify_av1_420_output(
            task.video_output,
            ffmpeg=ffmpeg,
            full_decode=full_decode,
        )
        if not media.get("ok"):
            raise DirectAV1420RenderError(
                f"published AV1 failed validation: {task.video_output}: {media}"
            )
        output_sha256 = sha256_file(task.video_output)
        if report.get("output_sha256") != output_sha256:
            raise DirectAV1420RenderError(
                f"published AV1 hash differs from report: {task.video_output}"
            )
        identities = build_language_ruby_identity(task, report)
        identity_keys = {
            "language_identity": "language",
            "ruby_identity": "ruby",
        }
        for key, identity_key in identity_keys.items():
            if report.get(key) is None:
                raise DirectAV1420RenderError(
                    f"published report lacks {key}: {task.direct_report}"
                )
            if _identity_key(report.get(key)) != _identity_key(
                identities[identity_key]
            ):
                raise DirectAV1420RenderError(
                    f"published report {key} is stale: {task.direct_report}"
                )
        results.append(
            {
                "status": "ok",
                "profile": task.profile,
                "song_id": str(task.track.song_id),
                "title": task.track.title,
                "artifact_slug": task.track.artifact_slug,
                "render_mode": "direct-av1-420",
                "video": str(task.video_output),
                "report": str(task.direct_report),
                "output_size_bytes": task.video_output.stat().st_size,
                "sha256": output_sha256,
                "elapsed_seconds": report.get("render_elapsed_seconds"),
                "sources": _source_record(task),
                "media": media,
                "language_identity": identities["language"],
                "ruby_identity": identities["ruby"],
                "libass_font_probe": dict(recorded_libass_probe),
            }
        )
    return results


def build_av1_420_report(
    results: Sequence[dict[str, Any]],
    *,
    root: Path,
    av1_cq: int,
    full_decode: bool,
    full_decode_exception_reason: str | None = None,
    profiles: Sequence[str] = PROFILES,
    expected_song_count: int = 5,
) -> dict[str, Any]:
    selected_profiles = tuple(dict.fromkeys(str(profile) for profile in profiles))
    if not selected_profiles or any(profile not in PROFILES for profile in selected_profiles):
        raise DirectAV1420RenderError(
            f"AV1 aggregate report has invalid profiles: {selected_profiles!r}"
        )
    if expected_song_count <= 0:
        raise DirectAV1420RenderError("expected_song_count must be positive")
    expected_output_count = expected_song_count * len(selected_profiles)
    if len(results) != expected_output_count:
        raise DirectAV1420RenderError(
            "AV1 aggregate report has an unexpected output count: "
            f"expected {expected_output_count}, got {len(results)}"
        )
    keys = {(str(item["profile"]), str(item["song_id"])) for item in results}
    if len(keys) != expected_output_count:
        raise DirectAV1420RenderError("AV1 aggregate report has duplicate outputs")
    for profile in selected_profiles:
        if sum(item["profile"] == profile for item in results) != expected_song_count:
            raise DirectAV1420RenderError(
                f"AV1 report requires {expected_song_count} {profile} outputs"
            )
    outputs: list[dict[str, Any]] = []
    for item in sorted(
        results,
        key=lambda value: (str(value["profile"]), str(value["artifact_slug"])),
    ):
        output = {
            "profile": item["profile"],
            "song_id": str(item["song_id"]),
            "title": item["title"],
            "artifact_slug": item["artifact_slug"],
            "source": f"manifest audio + artwork + timing/{item['profile']}/ASS",
            "source_paths": item["sources"],
            "output": project_relative(Path(str(item["video"])), root),
            "direct_av1_420_render_report": project_relative(
                Path(str(item["report"])), root
            ),
            "render_mode": "direct-av1-420",
            "intermediate_video": False,
            "intermediate_h264": False,
            "intermediate_hevc": False,
            "audio": "aac-192k",
            "output_size_bytes": item["output_size_bytes"],
            "sha256": item["sha256"],
            "elapsed_seconds": item["elapsed_seconds"],
            "media_checks": item["media"],
        }
        for key in ("language_identity", "ruby_identity", "libass_font_probe"):
            if key in item:
                output[key] = item[key]
        outputs.append(output)
    identity = aggregate_language_ruby_identity(results)
    verification_status = "complete"
    release_decision = "verified"
    return {
        "schema_version": "karaoke-av1-420/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "verification_status": verification_status,
        "release_decision": release_decision,
        "profiles": list(selected_profiles),
        "encoder": "av1_nvenc",
        "container": "mp4",
        "codec_tag": "av01",
        "profile": "Main",
        "pixel_format": "yuv420p",
        "color_range": "tv",
        "color_matrix": "bt709",
        "audio": "AAC 192 kb/s",
        "full_decode": full_decode,
        "full_decode_gate": {
            "performed": full_decode,
            "required": False,
            "recommended": False,
            "reason": (
                None
                if full_decode
                else full_decode_exception_reason or "not-required-by-workflow"
            ),
            "risk": None,
        },
        "direct_render": {
            "song_ids": sorted({str(item["song_id"]) for item in results}),
            "source_chain": (
                "manifest audio + artwork + timing/{profile}/ASS -> "
                "av1_nvenc/yuv420p"
            ),
            "intermediate_video": False,
            "reports": (
                "validation/{profile}/{track}_direct_av1_420_render_report.json"
            ),
        },
        "language_identity": {
            "status": identity["status"],
            "songs": [
                {
                    "song_id": item["song_id"],
                    "identity": item["language_identity"],
                    "profiles": item["profiles"],
                }
                for item in identity["songs"]
            ],
        },
        "ruby_identity": {
            "status": identity["status"],
            "songs": [
                {
                    "song_id": item["song_id"],
                    "identity": item["ruby_identity"],
                    "profiles": item["profiles"],
                }
                for item in identity["songs"]
            ],
        },
        "language_ruby_identity": identity,
        "settings": {
            "preset": "p7",
            "tune": "hq",
            "rate_control": "vbr",
            "cq": av1_cq,
            "multipass": "fullres",
            "lookahead": 32,
            "spatial_aq": True,
            "temporal_aq": True,
            "aq_strength": 8,
            "gop_frames": 240,
        },
        "outputs": outputs,
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--allow-partial-manifest",
        action="store_true",
        help="allow an explicitly supplied manifest with fewer than five tracks",
    )
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument(
        "--song",
        "--songs",
        dest="songs",
        action="append",
        default=[],
        metavar="ID|TITLE|SLUG",
    )
    parser.add_argument(
        "--single-track",
        action="store_true",
        help="require exactly one song and one profile task",
    )
    parser.add_argument(
        "--profile",
        "--profiles",
        dest="profiles",
        action="append",
        choices=PROFILES,
        default=[],
    )
    parser.add_argument("--timing-dir", type=Path, default=None)
    parser.add_argument("--artwork-dir", type=Path, default=None)
    parser.add_argument("--fonts-dir", type=Path, default=None)
    parser.add_argument("--font-file", type=Path, default=None)
    parser.add_argument("--preview-script", type=Path, default=PREVIEW_SCRIPT)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--full-decode", action="store_true")
    parser.add_argument(
        "--skip-full-decode-reason",
        help=(
            "Record why the optional full decode was not run. Omitting this "
            "diagnostic does not lower the aggregate verification status."
        ),
    )
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 5))
    parser.add_argument(
        "--av1-cq",
        type=int,
        default=44,
        choices=range(0, 64),
        metavar="0..63",
    )
    return parser


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "media"}


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        album: AlbumManifest = render_core.load_album_manifest(
            _resolve_path(args.manifest),
            require_five_tracks=not args.allow_partial_manifest,
        )
        root = _resolve_path(args.root) if args.root else album.deliverable_dir.resolve()
        tracks = render_core.select_tracks(album, args.songs)
        profiles = render_core.select_profiles(args.profiles)
        if args.single_track and (len(tracks) != 1 or len(profiles) != 1):
            raise ValueError(
                "--single-track requires exactly one selected song and one profile"
            )
        preview_script = _resolve_path(args.preview_script)
        if not args.report_only and not preview_script.is_file():
            raise FileNotFoundError(f"preview script does not exist: {preview_script}")
        ffmpeg = _resolve_path(args.ffmpeg) if args.ffmpeg else None
        tasks = configure_av1_tasks(
            render_core.plan_tasks(
                album,
                root=root,
                tracks=tracks,
                profiles=profiles,
                timing_dir=_resolve_path(args.timing_dir) if args.timing_dir else None,
                artwork_root=_resolve_path(args.artwork_dir)
                if args.artwork_dir
                else None,
                fonts_dir=_resolve_path(args.fonts_dir) if args.fonts_dir else None,
                font_file=_resolve_path(args.font_file) if args.font_file else None,
            ),
            root=root,
        )
        if args.single_track and len(tasks) != 1:
            raise ValueError(
                f"--single-track produced {len(tasks)} tasks; expected exactly one"
            )
        verify_editable_ruby_sources(tasks)
        complete_track_selection = len(tracks) == len(album.tracks)
        if args.report_only and not complete_track_selection:
            raise ValueError("--report-only requires the complete manifest selection")
        if args.full_decode and args.skip_full_decode_reason:
            raise ValueError(
                "--full-decode and --skip-full-decode-reason are mutually exclusive"
            )

        if args.report_only:
            results = collect_existing_results(
                tasks,
                ffmpeg=ffmpeg,
                av1_cq=args.av1_cq,
                full_decode=args.full_decode,
            )
        else:
            results = []
            with ThreadPoolExecutor(max_workers=min(args.jobs, len(tasks))) as executor:
                futures = {
                    executor.submit(
                        render_one,
                        task,
                        preview_script=preview_script,
                        ffmpeg=ffmpeg,
                        av1_cq=args.av1_cq,
                    ): task
                    for task in tasks
                }
                for future in as_completed(futures):
                    item = future.result()
                    results.append(item)
                    print(json.dumps(_summary(item), ensure_ascii=False), flush=True)

        aggregate_report: Path | None = None
        if complete_track_selection:
            aggregate_report = root / "validation" / AV1_REPORT_NAME
            write_json_atomically(
                aggregate_report,
                build_av1_420_report(
                    results,
                    root=root,
                    av1_cq=args.av1_cq,
                    full_decode=args.full_decode,
                    full_decode_exception_reason=args.skip_full_decode_reason,
                    profiles=profiles,
                    expected_song_count=len(tracks),
                ),
            )
        print(
            json.dumps(
                {
                    "status": "pass",
                    "release_decision": "verified",
                    "task_count": len(results),
                    "profiles": list(profiles),
                    "songs": [str(track.song_id) for track in tracks],
                    "pixel_format": "yuv420p",
                    "aggregate_report": str(aggregate_report)
                    if aggregate_report
                    else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
