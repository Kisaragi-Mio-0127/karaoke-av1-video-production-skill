#!/usr/bin/env python3
"""Install the sanitized karaoke pipeline into a StrangeUtaGame checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath


SKILL_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = SKILL_ROOT / "integration" / "strangeutagame"
DEPENDENCY_MANIFEST = BUNDLE_ROOT / "dependency-manifest.json"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    """Return true for symlinks and Windows reparse points, including junctions."""

    if not os.path.lexists(path):
        return False
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT)


def _assert_safe_destination(target: Path, destination: Path) -> None:
    try:
        relative = destination.relative_to(target)
    except ValueError as error:
        raise SystemExit(f"Destination escapes target checkout: {destination}") from error
    current = target
    for part in relative.parts:
        current = current / part
        if _is_reparse_point(current):
            raise SystemExit(f"Refusing symlink or reparse-point destination: {current}")
    if os.path.lexists(destination) and not destination.is_file():
        raise SystemExit(f"Destination is not a regular file: {destination}")


def validate_target(target: Path) -> None:
    required = (
        target / "pyproject.toml",
        target / "src" / "strange_uta_game",
        target / "scripts",
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        raise SystemExit(
            "Not a compatible StrangeUtaGame checkout; missing: "
            + ", ".join(str(path) for path in missing)
        )
    for path in (target / "src", target / "src" / "strange_uta_game", target / "scripts"):
        if _is_reparse_point(path):
            raise SystemExit(f"Refusing target directory reparse point: {path}")
    backup_parent = target / ".karaoke-skill-backup"
    if _is_reparse_point(backup_parent):
        raise SystemExit(f"Refusing backup directory reparse point: {backup_parent}")


def _manifest_python_paths() -> list[Path]:
    """Return the manifest-authorized Python paths below integration/scripts."""

    try:
        manifest = json.loads(DEPENDENCY_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read dependency manifest: {DEPENDENCY_MANIFEST}") from error

    paths: list[Path] = []
    seen: set[str] = set()
    for section in ("scripts", "shared_modules", "package_files"):
        records = manifest.get(section, [])
        if not isinstance(records, list):
            raise SystemExit(f"Dependency manifest section is not a list: {section}")
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise SystemExit(f"Dependency manifest record is not an object: {section}[{index}]")
            raw_path = record.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise SystemExit(f"Dependency manifest path is missing: {section}[{index}]")
            normalized = raw_path.replace("\\", "/")
            relative = PurePosixPath(normalized)
            if (
                normalized != raw_path
                or relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
                or re.match(r"^[A-Za-z]:$", relative.parts[0])
                or relative.suffix.casefold() != ".py"
            ):
                raise SystemExit(
                    f"Unsafe manifest Python path in {section}[{index}]: {raw_path!r}"
                )
            key = relative.as_posix().casefold()
            if key in seen:
                raise SystemExit(f"Duplicate manifest Python path: {raw_path}")
            seen.add(key)
            paths.append(Path(*relative.parts))

    if not paths:
        raise SystemExit("Dependency manifest authorizes no Python integration files")
    return sorted(paths, key=lambda path: path.as_posix().casefold())


def _manifest_requirement_paths() -> list[tuple[Path, Path]]:
    """Return manifest-authorized requirement sources and target names."""

    try:
        manifest = json.loads(DEPENDENCY_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read dependency manifest: {DEPENDENCY_MANIFEST}") from error

    records = manifest.get("requirements", [])
    if not isinstance(records, list) or not records:
        raise SystemExit("Dependency manifest authorizes no requirement files")
    result: list[tuple[Path, Path]] = []
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise SystemExit(f"Dependency manifest record is not an object: requirements[{index}]")
        raw_path = record.get("path")
        raw_destination = record.get("destination")
        if not isinstance(raw_path, str) or not isinstance(raw_destination, str):
            raise SystemExit(f"Requirement path or destination is missing: requirements[{index}]")
        relative = PurePosixPath(raw_path)
        destination = PurePosixPath(raw_destination)
        if (
            "\\" in raw_path
            or "\\" in raw_destination
            or ":" in raw_path
            or ":" in raw_destination
            or raw_path.startswith("//")
            or raw_destination.startswith("//")
            or relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.parts[0] != "requirements"
            or destination.is_absolute()
            or len(destination.parts) != 1
            or destination.name in {"", ".", ".."}
        ):
            raise SystemExit(f"Unsafe requirement mapping: requirements[{index}]")
        source_key = relative.as_posix().casefold()
        destination_key = destination.as_posix().casefold()
        if source_key in seen_sources or destination_key in seen_destinations:
            raise SystemExit(f"Duplicate requirement mapping: requirements[{index}]")
        seen_sources.add(source_key)
        seen_destinations.add(destination_key)
        result.append((Path(*relative.parts), Path(destination.name)))
    return sorted(result, key=lambda item: item[0].as_posix().casefold())


def _assert_regular_bundle_source(source: Path, relative: Path) -> None:
    """Reject missing files and reparse points in a manifest-authorized path."""

    current = BUNDLE_ROOT / "scripts"
    for part in relative.parts:
        current = current / part
        if _is_reparse_point(current):
            raise SystemExit(f"Bundled source is a symlink or reparse point: {current}")
    if not source.is_file():
        raise SystemExit(f"Bundled source is not a regular file: {source}")


def _mapping(target: Path) -> list[tuple[Path, Path]]:
    result: list[tuple[Path, Path]] = []
    scripts_root = BUNDLE_ROOT / "scripts"
    for relative in _manifest_python_paths():
        source = scripts_root / relative
        _assert_regular_bundle_source(source, relative)
        destination = target / "scripts" / relative
        _assert_safe_destination(target, destination)
        result.append((source, destination))

    for source_relative, destination_relative in _manifest_requirement_paths():
        source = BUNDLE_ROOT / source_relative
        if not source.is_file() or _is_reparse_point(source):
            raise SystemExit(f"Bundled source is not a regular file: {source}")
        destination = target / destination_relative
        _assert_safe_destination(target, destination)
        result.append((source, destination))
    return result


def install(target: Path, *, force: bool, dry_run: bool) -> dict[str, object]:
    requested_target = target.expanduser().absolute()
    if _is_reparse_point(requested_target):
        raise SystemExit(f"Refusing target checkout reparse point: {requested_target}")
    target = requested_target.resolve()
    validate_target(target)
    mapping = _mapping(target)

    planned: list[tuple[Path, Path, str]] = []
    conflicts: list[Path] = []
    for source, destination in mapping:
        if destination.is_file() and sha256(source) == sha256(destination):
            action = "unchanged"
        elif destination.is_file():
            conflicts.append(destination)
            action = "replace" if force else "conflict"
        else:
            action = "install"
        planned.append((source, destination, action))
    if conflicts and not force and not dry_run:
        raise SystemExit(
            "Refusing to overwrite modified files. Re-run with --force after review: "
            + ", ".join(str(path) for path in conflicts)
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = uuid.uuid4().hex
    backup_root = target / ".karaoke-skill-backup" / f"{stamp}-{run_id}"
    stage_root = target / f".karaoke-skill-stage-{run_id}"
    backup_by_destination = {
        destination: backup_root / destination.relative_to(target)
        for _source, destination, action in planned
        if action in {"replace", "conflict"}
    }
    report = {
        "schema_version": "karaoke-skill-install/v1",
        "dry_run": dry_run,
        "target": str(target),
        "files": [
            {
                "source": source.relative_to(SKILL_ROOT).as_posix(),
                "destination": destination.relative_to(target).as_posix(),
                "sha256": sha256(source),
                "action": action,
            }
            for source, destination, action in planned
        ],
        "conflicts": [path.relative_to(target).as_posix() for path in conflicts],
        "backups": [
            path.relative_to(target).as_posix() for path in backup_by_destination.values()
        ],
    }
    if dry_run:
        return report

    changed: list[tuple[Path, Path | None]] = []
    try:
        stage_root.mkdir(parents=False, exist_ok=False)
        # Stage and hash every changed file before the first target mutation.
        for source, destination, action in planned:
            if action == "unchanged":
                continue
            staged = stage_root / destination.relative_to(target)
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            if sha256(staged) != sha256(source):
                raise RuntimeError(f"staged file hash mismatch: {source}")

        # Finish all backups before the first replacement.
        for destination, backup in backup_by_destination.items():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)

        for _source, destination, action in planned:
            if action == "unchanged":
                continue
            _assert_safe_destination(target, destination)
            staged = stage_root / destination.relative_to(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, destination)
            changed.append((destination, backup_by_destination.get(destination)))
    except Exception as error:
        rollback_errors: list[str] = []
        for destination, backup in reversed(changed):
            try:
                if backup is None:
                    destination.unlink(missing_ok=True)
                elif backup.is_file():
                    os.replace(backup, destination)
            except OSError as rollback_error:
                rollback_errors.append(f"{destination}: {rollback_error}")
        detail = f"; rollback errors: {rollback_errors}" if rollback_errors else ""
        raise RuntimeError(f"installation failed and was rolled back: {error}{detail}") from error
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            install(args.target, force=args.force, dry_run=args.dry_run),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
