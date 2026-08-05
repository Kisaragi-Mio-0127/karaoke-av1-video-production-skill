#!/usr/bin/env python3
"""Create, verify, or restore a hash-checked karaoke release snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def _source_files(repo: Path, values: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for value in values:
        source = _inside(repo / value, repo)
        if source.is_dir():
            files.update(path for path in source.rglob("*") if path.is_file())
        elif source.is_file():
            files.add(source)
        else:
            raise FileNotFoundError(source)
    return sorted(files, key=lambda path: path.relative_to(repo).as_posix())


def snapshot(repo: Path, destination: Path, values: list[Path]) -> dict:
    destination = _inside(destination, repo)
    if destination.exists():
        raise FileExistsError(f"snapshot destination already exists: {destination}")
    entries = []
    try:
        for source in _source_files(repo, values):
            relative = source.relative_to(repo)
            backup = destination / "files" / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
            digest = sha256_file(backup)
            if digest != sha256_file(source):
                raise OSError(f"snapshot hash mismatch: {relative}")
            entries.append(
                {
                    "path": relative.as_posix(),
                    "size_bytes": source.stat().st_size,
                    "sha256": digest,
                }
            )
        manifest = {
            "schema_version": "karaoke-release-snapshot/v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "verified",
            "file_count": len(entries),
            "total_bytes": sum(entry["size_bytes"] for entry in entries),
            "restore_command": (
                f"python scripts/karaoke_release_snapshot.py restore "
                f"--snapshot {destination.relative_to(repo).as_posix()}"
            ),
            "entries": entries,
        }
        manifest_path = destination / "ROLLBACK_MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        verify(repo, destination)
        return manifest
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        raise


def add(repo: Path, destination: Path, values: list[Path]) -> dict:
    """Append newly discovered rollback files to an existing snapshot."""

    manifest = verify(repo, destination)
    existing = {entry["path"] for entry in manifest["entries"]}
    for source in _source_files(repo, values):
        relative = source.relative_to(repo)
        key = relative.as_posix()
        if key in existing:
            raise ValueError(f"snapshot already contains: {key}")
        backup = destination / "files" / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)
        digest = sha256_file(backup)
        if digest != sha256_file(source):
            raise OSError(f"snapshot hash mismatch: {relative}")
        manifest["entries"].append(
            {
                "path": key,
                "size_bytes": source.stat().st_size,
                "sha256": digest,
            }
        )
        existing.add(key)
    manifest["entries"].sort(key=lambda entry: entry["path"])
    manifest["file_count"] = len(manifest["entries"])
    manifest["total_bytes"] = sum(
        entry["size_bytes"] for entry in manifest["entries"]
    )
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    (destination / "ROLLBACK_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return verify(repo, destination)


def verify(repo: Path, destination: Path) -> dict:
    destination = _inside(destination, repo)
    manifest = json.loads(
        (destination / "ROLLBACK_MANIFEST.json").read_text(encoding="utf-8")
    )
    for entry in manifest["entries"]:
        backup = _inside(destination / "files" / entry["path"], destination)
        if backup.stat().st_size != entry["size_bytes"]:
            raise OSError(f"snapshot size mismatch: {entry['path']}")
        if sha256_file(backup) != entry["sha256"]:
            raise OSError(f"snapshot hash mismatch: {entry['path']}")
    return manifest


def restore(repo: Path, destination: Path) -> dict:
    manifest = verify(repo, destination)
    for entry in manifest["entries"]:
        target = _inside(repo / entry["path"], repo)
        backup = _inside(destination / "files" / entry["path"], destination)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.restore")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, temporary)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        if sha256_file(target) != entry["sha256"]:
            raise OSError(f"restored hash mismatch: {entry['path']}")
    return manifest


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("snapshot", "add", "verify", "restore"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--path", type=Path, action="append", default=[])
    return parser


def main() -> int:
    args = make_parser().parse_args()
    repo = args.repo.resolve()
    destination = args.snapshot if args.snapshot.is_absolute() else repo / args.snapshot
    if args.action == "snapshot":
        if not args.path:
            raise ValueError("snapshot requires at least one --path")
        result = snapshot(repo, destination, args.path)
    elif args.action == "add":
        if not args.path:
            raise ValueError("add requires at least one --path")
        result = add(repo, destination, args.path)
    elif args.action == "verify":
        result = verify(repo, destination)
    else:
        result = restore(repo, destination)
    print(
        json.dumps(
            {
                "status": "pass",
                "action": args.action,
                "file_count": result["file_count"],
                "total_bytes": result["total_bytes"],
                "restore_command": result["restore_command"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
