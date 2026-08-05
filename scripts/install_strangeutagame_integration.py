#!/usr/bin/env python3
"""Install the sanitized karaoke pipeline into a StrangeUtaGame checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = SKILL_ROOT / "integration" / "strangeutagame"
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


def _mapping(target: Path) -> list[tuple[Path, Path]]:
    sources = sorted((BUNDLE_ROOT / "scripts").glob("*.py"))
    sources += sorted((BUNDLE_ROOT / "requirements").glob("*"))
    result: list[tuple[Path, Path]] = []
    for source in sources:
        if not source.is_file() or _is_reparse_point(source):
            raise SystemExit(f"Bundled source is not a regular file: {source}")
        if source.parent.name == "scripts":
            destination = target / "scripts" / source.name
        else:
            suffix = source.name.removeprefix("requirements-karaoke")
            destination = target / f"requirements-karaoke.skill{suffix}"
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
