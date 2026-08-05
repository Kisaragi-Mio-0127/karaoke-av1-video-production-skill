#!/usr/bin/env python3
"""Open a StrangeUtaGame SUG under guarded review and record media evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUTO_SAVE_METHODS = {
    "_schedule_auto_save",
    "_do_auto_save",
    "_start_periodic_save",
    "_do_periodic_save",
    "save_sync_for_exit",
}
DESTRUCTIVE_CLEANUP_METHODS = {
    "cleanup_temp_files",
    "_cleanup_temp_for_path",
    "delete_crash_recovery",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def same_path(left: str | Path | None, right: str | Path | None) -> bool:
    if left is None or right is None:
        return False
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
        str(Path(right).resolve())
    )


def git_output(repo: Path, *arguments: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout


def repository_identity(repo: Path) -> dict[str, Any]:
    commit_code, commit_output = git_output(repo, "rev-parse", "HEAD")
    status_code, status_output = git_output(repo, "status", "--porcelain=v1")
    critical_paths = (
        repo / "main.py",
        repo / "src/strange_uta_game/app_dirs.py",
        repo / "src/strange_uta_game/frontend/main_window.py",
        repo / "src/strange_uta_game/frontend/project_store.py",
        repo / "src/strange_uta_game/frontend/editor/timing_interface.py",
        repo / "src/strange_uta_game/frontend/editor/timing/file_loader.py",
    )
    source_hashes = {
        str(path.relative_to(repo)): sha256_file(path)
        for path in critical_paths
        if path.is_file()
    }
    return {
        "repository_commit": commit_output.strip()
        if commit_code == 0 and commit_output.strip()
        else None,
        "repository_worktree_status_sha256": sha256_text(status_output)
        if status_code == 0
        else None,
        "repository_worktree_dirty": bool(status_output.strip())
        if status_code == 0
        else None,
        "critical_source_sha256": source_hashes,
    }


def load_video_suffixes(repo: Path) -> set[str]:
    source_path = (
        repo
        / "src/strange_uta_game/backend/infrastructure/audio/video_converter.py"
    )
    if not source_path.is_file():
        raise FileNotFoundError(f"video extension source is missing: {source_path}")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "VIDEO_EXTENSIONS"
                for target in targets
            ):
                value = ast.literal_eval(node.value)
                if not isinstance(value, (set, frozenset)) or not value:
                    break
                suffixes = {str(item).lower() for item in value}
                if not all(item.startswith(".") for item in suffixes):
                    break
                return suffixes
    raise RuntimeError(f"could not read VIDEO_EXTENSIONS from {source_path}")


def adjacent_recovery_paths(project: Path) -> list[Path]:
    return [
        project.parent / f".{project.name}.autosave",
        Path(f"{project}.autosave"),
        Path(f"{project}.autosave.sug"),
    ]


def snapshot_paths(paths: list[Path]) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for path in paths:
        resolved = path.resolve()
        exists = resolved.is_file()
        snapshot[str(resolved)] = {
            "exists": exists,
            "size": resolved.stat().st_size if exists else None,
            "sha256": sha256_file(resolved) if exists else None,
        }
    return snapshot


def classify_dirty(
    sync: Any,
    dirty: bool | None,
    delta_ms: int | None,
    full_callback_non_duration_unchanged: bool,
) -> str:
    if not full_callback_non_duration_unchanged:
        return "review-required"
    if dirty is False:
        return "clean"
    duration_only = bool(
        dirty is True
        and isinstance(sync, dict)
        and sync.get("changed")
        and sync.get("non_duration_state_unchanged")
        and sync.get("before", {}).get("dirty") is False
        and sync.get("after", {}).get("dirty") is True
        and full_callback_non_duration_unchanged
        and delta_ms is not None
        and abs(delta_ms) <= 1
    )
    if duration_only:
        return "do-not-save-duration-normalization"
    return "review-required"


def dirty_disposition_is_accepted(disposition: str | None) -> bool:
    return disposition in {"clean", "do-not-save-duration-normalization"}


def final_gate_pass(runtime: dict[str, Any]) -> bool:
    return bool(
        runtime.get("graceful_exit")
        and runtime.get("callback_gate_pass")
        and runtime.get("audio_callback_verified")
        and runtime.get("dirty_disposition_accepted")
        and runtime.get("full_callback_non_duration_unchanged")
        and runtime.get("canonical_project_unchanged")
        and runtime.get("adjacent_recovery_unchanged_after_exit")
        and not runtime.get("runtime_error")
    )


def load_preflight(repo: Path, project: Path, status: Path) -> dict[str, Any]:
    required = (repo / "main.py", repo / "pyproject.toml", repo / "uv.lock")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required repository files are missing: {missing}")
    if not project.is_file():
        raise FileNotFoundError(f"editable project not found: {project}")
    document = json.loads(project.read_text(encoding="utf-8"))
    media_field = document.get("media_path")
    if not isinstance(media_field, str) or not media_field.strip():
        raise ValueError(f"SUG has no usable media_path: {project}")
    media = Path(media_field).expanduser()
    media = (project.parent / media).resolve() if not media.is_absolute() else media.resolve()
    if not media.is_file():
        raise FileNotFoundError(f"resolved SUG media does not exist: {media}")
    expected_prefix = (repo / ".venv").resolve()
    runtime_prefix = Path(sys.prefix).resolve()
    if not same_path(runtime_prefix, expected_prefix):
        raise RuntimeError(
            "run with the repository-local environment: "
            f"expected sys.prefix={expected_prefix}, got {runtime_prefix}"
        )
    stored_duration = document.get("audio_duration_ms")
    recovery_paths = adjacent_recovery_paths(project)
    video_suffixes = load_video_suffixes(repo)
    return {
        "status": "preflight-pass",
        "recorded_at_utc": utc_now(),
        "repo": str(repo),
        **repository_identity(repo),
        "runtime_prefix": str(runtime_prefix),
        "requested_project_path": str(project),
        "project_sha256_before": sha256_file(project),
        "media_path_field": media_field,
        "resolved_media_path": str(media),
        "media_kind": "video" if media.suffix.lower() in video_suffixes else "audio",
        "video_extensions_source": str(
            repo
            / "src/strange_uta_game/backend/infrastructure/audio/video_converter.py"
        ),
        "video_extensions_sha256": sha256_text(
            "\n".join(sorted(video_suffixes))
        ),
        "source_media_sha256_before": sha256_file(media),
        "stored_duration_ms": int(stored_duration)
        if isinstance(stored_duration, (int, float))
        else None,
        "adjacent_recovery_paths": [str(path.resolve()) for path in recovery_paths],
        "adjacent_recovery_before": snapshot_paths(recovery_paths),
        "status_path": str(status),
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--status",
        type=Path,
        help=(
            "private evidence path; defaults to "
            "<repo>/.cache/karaoke-editor-review/<project>.audio-load.json"
        ),
    )
    parser.add_argument(
        "--allow-update-dialog",
        action="store_true",
        help="do not suppress the unrelated startup update check",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate project/media identity without opening the GUI",
    )
    return parser


def run_gui(
    repo: Path,
    project: Path,
    status: Path,
    preflight: dict[str, Any],
    *,
    allow_update_dialog: bool,
) -> int:
    session_parent = repo / ".cache" / "karaoke-editor-review"
    session_parent.mkdir(parents=True, exist_ok=True)
    session_root = Path(tempfile.mkdtemp(prefix="editor-session-", dir=session_parent))
    session_config = session_root / "config"
    session_cache = session_root / "cache"
    session_backup = session_root / "backup"
    for directory in (session_config, session_cache, session_backup):
        directory.mkdir(parents=True, exist_ok=True)
    source_config = repo / "config.json"
    if source_config.is_file():
        shutil.copy2(source_config, session_config / "config.json")

    runtime: dict[str, Any] = {
        **preflight,
        "status": "launching",
        "recorded_at_utc": utc_now(),
        "session_root": str(session_root),
        "opened_project_path": None,
        "engine_media_path": None,
        "source_media_callback_path": None,
        "autosave_guard_installed_before_open": False,
        "recovery_state_isolated": False,
        "manual_save_attempt_count": 0,
        "blocked_cleanup_attempt_count": 0,
        "audio_callback_verified": False,
        "callback_gate_pass": False,
        "final_hash_checked": False,
        "graceful_exit": False,
    }
    write_json(status, runtime)
    exit_code = 1
    exit_kind = "exception"

    try:
        # Redirect configuration, cache, recovery, and backups before importing
        # the application entry point or constructing MainWindow.
        sys.argv = [str(repo / "main.py"), str(project)]
        sys.path.insert(0, str(repo / "src"))
        sys.path.insert(0, str(repo))
        app_dirs = importlib.import_module("strange_uta_game.app_dirs")
        app_dirs.config_dir = lambda: session_config
        app_dirs.cache_dir = lambda: session_cache
        app_dirs.default_backup_dir = lambda: session_backup
        app_dirs.backup_dir = lambda custom=None: session_backup
        os.environ["SUG_CACHE_DIR"] = str(session_cache)
        os.environ["SUG_BACKUP_DIR"] = str(session_backup)

        app_main = importlib.import_module("main")
        timing_module = importlib.import_module(
            "strange_uta_game.frontend.editor.timing_interface"
        )
        loader_module = importlib.import_module(
            "strange_uta_game.frontend.editor.timing.file_loader"
        )
        store_module = importlib.import_module(
            "strange_uta_game.frontend.project_store"
        )
        persistence_module = importlib.import_module(
            "strange_uta_game.backend.infrastructure.persistence.sug_io"
        )
        numpy = importlib.import_module("numpy")
        editor_type = timing_module.EditorInterface
        loader_type = loader_module.FileLoader
        store_type = store_module.ProjectStore
        parser_type = persistence_module.SugProjectParser

        required_hooks = {
            "MainWindow._check_for_app_update": (
                app_main.MainWindow,
                "_check_for_app_update",
            ),
            "MainWindow._schedule_network_dict_auto_update": (
                app_main.MainWindow,
                "_schedule_network_dict_auto_update",
            ),
            "FileLoader._on_project_loaded": (loader_type, "_on_project_loaded"),
            "FileLoader._on_video_loaded": (loader_type, "_on_video_loaded"),
            "FileLoader._on_video_error": (loader_type, "_on_video_error"),
            "EditorInterface._sync_project_audio_duration": (
                editor_type,
                "_sync_project_audio_duration",
            ),
            "EditorInterface._on_audio_loaded": (editor_type, "_on_audio_loaded"),
            "EditorInterface._on_audio_load_error": (
                editor_type,
                "_on_audio_load_error",
            ),
            "ProjectStore.save": (store_type, "save"),
        }
        for method_name in AUTO_SAVE_METHODS | DESTRUCTIVE_CLEANUP_METHODS:
            required_hooks[f"ProjectStore.{method_name}"] = (
                store_type,
                method_name,
            )
        missing_hooks = [
            name
            for name, (owner, attribute) in required_hooks.items()
            if not hasattr(owner, attribute)
        ]
        if missing_hooks:
            runtime["missing_hooks"] = missing_hooks
            raise RuntimeError(f"private hook mismatch: {missing_hooks}")

        original_project_loaded = loader_type._on_project_loaded
        original_sync_duration = editor_type._sync_project_audio_duration
        original_audio_loaded = editor_type._on_audio_loaded
        original_audio_error = editor_type._on_audio_load_error
        original_video_loaded = loader_type._on_video_loaded
        original_video_error = loader_type._on_video_error

        def project_state_without_duration(editor: Any) -> str | None:
            active = getattr(editor, "_project", None)
            if active is None:
                return None
            document = parser_type._project_to_dict(active)
            document.pop("audio_duration_ms", None)
            encoded = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            return hashlib.sha256(encoded).hexdigest()

        def record_project_loaded(
            loader: Any,
            loaded_project: Any,
            file_path: str,
        ) -> None:
            runtime["opened_project_path"] = str(Path(file_path).resolve())
            runtime["opened_project_matches_requested"] = same_path(file_path, project)
            original_project_loaded(loader, loaded_project, file_path)
            runtime["project_loaded_non_duration_state_sha256"] = (
                project_state_without_duration(loader._editor)
            )

        def record_duration_sync(
            editor: Any,
            duration_ms: int,
            *,
            mark_dirty: bool = True,
        ) -> bool:
            store = getattr(editor, "_store", None)
            active = getattr(editor, "_project", None)
            before = {
                "stored_duration_ms": int(
                    getattr(active, "audio_duration_ms", 0) or 0
                ),
                "dirty": bool(getattr(store, "dirty", False))
                if store is not None
                else None,
                "non_duration_state_sha256": project_state_without_duration(editor),
            }
            changed = original_sync_duration(
                editor,
                duration_ms,
                mark_dirty=mark_dirty,
            )
            after = {
                "engine_duration_ms": int(
                    getattr(active, "audio_duration_ms", 0) or 0
                ),
                "dirty": bool(getattr(store, "dirty", False))
                if store is not None
                else None,
                "non_duration_state_sha256": project_state_without_duration(editor),
            }
            runtime["duration_sync"] = {
                "before": before,
                "after": after,
                "changed": bool(changed),
                "mark_dirty_requested": bool(mark_dirty),
                "non_duration_state_unchanged": (
                    before["non_duration_state_sha256"]
                    == after["non_duration_state_sha256"]
                ),
            }
            return changed

        def record_loaded_media(
            editor: Any,
            engine_path: str,
            source_path: str,
            callback_kind: str,
        ) -> None:
            service = getattr(editor, "_timing_service", None)
            engine = getattr(service, "_audio_engine", None) if service is not None else None
            info = service.get_audio_info() if service is not None else None
            samples = service.get_original_samples() if service is not None else None
            array = (
                numpy.asarray(samples).reshape(-1)
                if samples is not None
                else numpy.asarray([])
            )
            if array.size > 1_000_000:
                indices = numpy.linspace(
                    0,
                    array.size - 1,
                    1_000_000,
                    dtype=numpy.int64,
                )
                sample_window = array[indices]
            else:
                sample_window = array
            finite = (
                numpy.isfinite(sample_window)
                if sample_window.size
                else numpy.asarray([])
            )
            finite_count = (
                int(numpy.count_nonzero(finite)) if sample_window.size else 0
            )
            finite_values = sample_window[finite] if finite_count else numpy.asarray([])
            nonzero_count = (
                int(numpy.count_nonzero(finite_values)) if finite_count else 0
            )
            peak = float(numpy.max(numpy.abs(finite_values))) if finite_count else 0.0
            rms = (
                float(math.sqrt(float(numpy.mean(numpy.square(finite_values)))))
                if finite_count
                else 0.0
            )
            engine_duration = int(info.duration_ms) if info is not None else 0
            stored_duration = preflight.get("stored_duration_ms")
            delta = (
                engine_duration - int(stored_duration)
                if stored_duration is not None and engine_duration > 0
                else None
            )
            source = Path(source_path).resolve()
            engine_media = Path(engine_path).resolve()
            source_hash_after = sha256_file(source) if source.is_file() else None
            engine_hash = sha256_file(engine_media) if engine_media.is_file() else None
            runtime["media_callback_kind"] = callback_kind
            runtime["source_media_callback_path"] = str(source)
            runtime["engine_media_path"] = str(engine_media)
            runtime["source_media_sha256_after_callback"] = source_hash_after
            runtime["engine_media_sha256"] = engine_hash
            runtime["duration_ms"] = engine_duration
            runtime["duration_delta_ms"] = delta
            runtime["sample_rate"] = int(info.sample_rate) if info is not None else 0
            runtime["channels"] = int(info.channels) if info is not None else 0
            runtime["playback_engine"] = type(engine).__name__ if engine is not None else None
            runtime["waveform"] = {
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "sample_count": int(array.size),
                "summary_window_count": int(sample_window.size),
                "finite_count": finite_count,
                "nonzero_count": nonzero_count,
                "peak": peak,
                "rms": rms,
            }
            recovery_paths = [
                Path(value) for value in preflight["adjacent_recovery_paths"]
            ]
            recovery_after = snapshot_paths(recovery_paths)
            runtime["adjacent_recovery_after_callback"] = recovery_after
            runtime["adjacent_recovery_unchanged_after_callback"] = (
                recovery_after == preflight["adjacent_recovery_before"]
            )
            checks = {
                "opened_project_matches_requested": bool(
                    runtime.get("opened_project_matches_requested")
                ),
                "callback_kind_matches_media": callback_kind
                == preflight["media_kind"],
                "source_path_matches_resolved_media": same_path(
                    source,
                    preflight["resolved_media_path"],
                ),
                "source_media_hash_unchanged": source_hash_after
                == preflight["source_media_sha256_before"],
                "engine_media_exists": engine_media.is_file(),
                "audio_callback_path_matches_source": callback_kind != "audio"
                or same_path(engine_media, source),
                "playback_engine_present": bool(runtime["playback_engine"])
                and runtime["playback_engine"] != "NoneType",
                "positive_duration": engine_duration > 0,
                "positive_sample_rate": int(info.sample_rate) > 0
                if info is not None
                else False,
                "positive_channels": int(info.channels) > 0
                if info is not None
                else False,
                "nonempty_waveform": int(array.size) > 0,
                "finite_waveform_window": sample_window.size > 0
                and finite_count == int(sample_window.size),
                "nonzero_waveform_window": nonzero_count > 0
                and peak > 0.0
                and rms > 0.0,
                "adjacent_recovery_unchanged": runtime[
                    "adjacent_recovery_unchanged_after_callback"
                ],
            }
            runtime["audio_checks"] = checks
            runtime["audio_callback_verified"] = all(checks.values())
            callback_non_duration_state = project_state_without_duration(editor)
            runtime["media_callback_non_duration_state_sha256"] = (
                callback_non_duration_state
            )
            runtime["full_callback_non_duration_unchanged"] = bool(
                runtime.get("project_loaded_non_duration_state_sha256")
                and runtime["project_loaded_non_duration_state_sha256"]
                == callback_non_duration_state
            )
            store = getattr(editor, "_store", None)
            dirty = (
                bool(getattr(store, "dirty", False))
                if store is not None
                else None
            )
            disposition = classify_dirty(
                runtime.get("duration_sync"),
                dirty,
                delta,
                runtime["full_callback_non_duration_unchanged"],
            )
            runtime["project_dirty"] = dirty
            runtime["dirty_disposition"] = disposition
            runtime["dirty_disposition_accepted"] = dirty_disposition_is_accepted(
                disposition
            )
            runtime["project_sha256_after_callback"] = sha256_file(project)
            runtime["canonical_project_unchanged_after_callback"] = (
                runtime["project_sha256_after_callback"]
                == preflight["project_sha256_before"]
            )
            runtime["callback_gate_pass"] = bool(
                runtime["audio_callback_verified"]
                and runtime["dirty_disposition_accepted"]
                and runtime["full_callback_non_duration_unchanged"]
                and runtime["canonical_project_unchanged_after_callback"]
            )
            runtime["status"] = (
                "loaded-awaiting-exit"
                if runtime["callback_gate_pass"]
                else "loaded-review-required"
                if runtime["audio_callback_verified"]
                and not runtime["dirty_disposition_accepted"]
                else "audio-load-verification-failed"
            )
            runtime["recorded_at_utc"] = utc_now()
            write_json(status, runtime)

        def record_audio_loaded(editor: Any, file_path: str) -> None:
            original_audio_loaded(editor, file_path)
            record_loaded_media(editor, file_path, file_path, "audio")

        def record_video_loaded(
            loader: Any,
            temp_path: str,
            original_path: str,
        ) -> None:
            original_video_loaded(loader, temp_path, original_path)
            record_loaded_media(
                loader._editor,
                temp_path,
                original_path,
                "video",
            )

        def record_load_error(error_message: str, callback_kind: str) -> None:
            runtime["status"] = "media-load-error"
            runtime["media_callback_kind"] = callback_kind
            runtime["error"] = str(error_message)
            runtime["recorded_at_utc"] = utc_now()
            write_json(status, runtime)

        def record_audio_error(editor: Any, error_message: str) -> None:
            original_audio_error(editor, error_message)
            record_load_error(error_message, "audio")

        def record_video_error(loader: Any, error_message: str) -> None:
            original_video_error(loader, error_message)
            record_load_error(error_message, "video")

        def guard_manual_save(store: Any, path: str | None = None) -> bool:
            runtime["manual_save_attempt_count"] = int(
                runtime.get("manual_save_attempt_count", 0)
            ) + 1
            target = path or getattr(store, "save_path", "")
            runtime["last_blocked_save_target"] = str(target)
            write_json(status, runtime)
            return False

        def no_auto_save(*_args: Any, **_kwargs: Any) -> None:
            return None

        def block_cleanup(*_args: Any, **_kwargs: Any) -> None:
            runtime["blocked_cleanup_attempt_count"] = int(
                runtime.get("blocked_cleanup_attempt_count", 0)
            ) + 1
            write_json(status, runtime)
            return None

        loader_type._on_project_loaded = record_project_loaded
        loader_type._on_video_loaded = record_video_loaded
        loader_type._on_video_error = record_video_error
        editor_type._sync_project_audio_duration = record_duration_sync
        editor_type._on_audio_loaded = record_audio_loaded
        editor_type._on_audio_load_error = record_audio_error
        store_type.save = guard_manual_save
        for method_name in AUTO_SAVE_METHODS:
            setattr(store_type, method_name, no_auto_save)
        for method_name in DESTRUCTIVE_CLEANUP_METHODS:
            replacement = (
                staticmethod(block_cleanup)
                if method_name == "delete_crash_recovery"
                else block_cleanup
            )
            setattr(store_type, method_name, replacement)
        store_type._crash_recovery_dirs = staticmethod(
            lambda: [session_backup / ".temp", session_cache]
        )
        runtime["autosave_guard_installed_before_open"] = True
        runtime["recovery_state_isolated"] = True

        if not allow_update_dialog:
            app_main.MainWindow._check_for_app_update = (
                lambda window: window._on_update_check_done()
            )
        app_main.MainWindow._schedule_network_dict_auto_update = lambda _window: None
        write_json(status, runtime)

        try:
            app_main.main()
        except SystemExit as error:
            exit_code = (
                int(error.code or 0)
                if isinstance(error.code, (int, type(None)))
                else 1
            )
            exit_kind = "system-exit"
            runtime["graceful_exit"] = exit_code == 0
    except Exception as error:
        runtime["runtime_error"] = f"{type(error).__name__}: {error}"
        if runtime.get("missing_hooks"):
            runtime["status"] = "hook-mismatch"
        elif runtime.get("status") == "launching":
            runtime["status"] = "launch-failed"
    finally:
        try:
            final_hash = sha256_file(project)
        except Exception as error:
            final_hash = None
            runtime["final_hash_error"] = f"{type(error).__name__}: {error}"
        runtime["project_sha256_after_exit"] = final_hash
        runtime["final_hash_checked"] = final_hash is not None
        runtime["canonical_project_unchanged"] = (
            final_hash == preflight["project_sha256_before"]
        )
        recovery_paths = [Path(value) for value in preflight["adjacent_recovery_paths"]]
        try:
            recovery_after_exit = snapshot_paths(recovery_paths)
        except Exception as error:
            recovery_after_exit = None
            runtime["adjacent_recovery_check_error"] = (
                f"{type(error).__name__}: {error}"
            )
        runtime["adjacent_recovery_after_exit"] = recovery_after_exit
        runtime["adjacent_recovery_unchanged_after_exit"] = (
            recovery_after_exit == preflight["adjacent_recovery_before"]
        )
        runtime["exit_code"] = exit_code
        runtime["exit_kind"] = exit_kind
        runtime["recorded_at_utc"] = utc_now()
        final_pass = final_gate_pass(runtime)
        if final_pass:
            runtime["status"] = "pass"
        elif runtime.get("status") == "loaded-review-required":
            runtime["status"] = "review-required"
        elif runtime.get("status") in {
            "launching",
            "loaded-awaiting-exit",
            "audio-load-verification-failed",
        }:
            runtime["status"] = "inconclusive"
        write_json(status, runtime)
        shutil.rmtree(session_root, ignore_errors=True)
    return 0 if runtime["status"] == "pass" else 1


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    repo = args.repo.expanduser().resolve()
    project = args.project.expanduser().resolve()
    status = (
        args.status.expanduser().resolve()
        if args.status is not None
        else repo
        / ".cache"
        / "karaoke-editor-review"
        / f"{project.stem}.audio-load.json"
    )
    initial = {
        "status": "preflight-running",
        "recorded_at_utc": utc_now(),
        "repo": str(repo),
        "requested_project_path": str(project),
        "status_path": str(status),
    }
    write_json(status, initial)
    try:
        preflight = load_preflight(repo, project, status)
    except Exception as error:
        initial["status"] = "preflight-failed"
        initial["error"] = f"{type(error).__name__}: {error}"
        initial["recorded_at_utc"] = utc_now()
        write_json(status, initial)
        return 1
    preflight["update_bypass_process_local"] = not args.allow_update_dialog
    write_json(status, preflight)
    if args.preflight_only:
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return 0
    return run_gui(
        repo,
        project,
        status,
        preflight,
        allow_update_dialog=args.allow_update_dialog,
    )


if __name__ == "__main__":
    raise SystemExit(main())
