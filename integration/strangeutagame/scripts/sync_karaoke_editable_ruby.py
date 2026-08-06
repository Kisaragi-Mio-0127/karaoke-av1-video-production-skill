#!/usr/bin/env python3
"""Apply reviewed ruby patches to canonical editable SUG documents.

The no-patch path is intentionally a read-only structural audit.  Candidate
readings are never inferred here: an Agent patch must carry its sentence
context, review state, confidence, and provenance before it can reach SUG.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.karaoke_timing import SONGS  # noqa: E402
from scripts.sug_ruby import (  # noqa: E402
    RubyValidationError,
    apply_review_patches,
    load_review_sidecar,
    validate_sug_ruby,
    write_review_sidecar,
)


@dataclass(frozen=True)
class RubyChange:
    line_index: int
    char_index: int
    text: str
    before: str
    after: str
    kind: str = "reading"


def _temporary_path(path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    return Path(name)


def _remove_temporary_path(path: Path | None) -> None:
    if path is None:
        return
    with suppress(FileNotFoundError):
        path.unlink()


def _publish_review_bundle(
    sug_path: Path,
    sidecar_path: Path,
    document: dict[str, Any],
    *,
    sug_hash_before: str,
    sug_hash_after: str,
    records: list[dict[str, Any]],
    model_prompt_version: str | None,
) -> None:
    """Publish SUG and its review sidecar in a fail-closed order."""

    sug_temporary: Path | None = None
    sidecar_temporary: Path | None = None
    try:
        sug_temporary = _temporary_path(sug_path)
        sidecar_temporary = _temporary_path(sidecar_path)
        sug_temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_review_sidecar(
            sidecar_temporary,
            sug_hash_before=sug_hash_before,
            sug_hash_after=sug_hash_after,
            records=records,
            model_prompt_version=model_prompt_version,
        )
        os.replace(sug_temporary, sug_path)
        os.replace(sidecar_temporary, sidecar_path)
    finally:
        _remove_temporary_path(sug_temporary)
        _remove_temporary_path(sidecar_temporary)


def _change_objects(document: dict[str, Any], result: dict[str, Any]) -> list[RubyChange]:
    changes: list[RubyChange] = []
    sentence_indices = {
        str(sentence.get("id") or f"sentence:{index}"): index
        for index, sentence in enumerate(document.get("sentences", []) or [])
        if isinstance(sentence, dict)
    }
    for change in result.get("changes", []) or []:
        if not isinstance(change, dict):
            continue
        sid = str(change.get("sentence_id", ""))
        line_index = sentence_indices.get(sid, 0)
        char_index = int(change.get("char_index", 0))
        sentence = (document.get("sentences", []) or [])[line_index]
        characters = sentence.get("characters", [])
        text = str(characters[char_index].get("char") or "")
        changes.append(
            RubyChange(
                line_index=line_index,
                char_index=char_index,
                text=text,
                before=str(change.get("before", "")),
                after=str(change.get("after", "")),
                kind=str(change.get("kind", "reading")),
            )
        )
    return changes


def synchronize_document(
    document: dict[str, Any],
    patches: list[dict[str, Any]] | None = None,
    *,
    sidecar: dict[str, Any] | None = None,
    sidecar_path: Path | None = None,
    write_sidecar: bool = True,
    model_prompt_version: str | None = None,
    sug_path: Path | None = None,
) -> tuple[list[RubyChange], list[dict[str, Any]]]:
    """Audit SUG or apply an explicit atomic Agent patch set."""

    if patches is None:
        errors = validate_sug_ruby(document)
        return [], [
            {"reason": "invalid-canonical-sug", "error": error}
            for error in errors
        ]

    try:
        result = apply_review_patches(document, patches, sidecar=sidecar)
    except (TypeError, ValueError, RubyValidationError) as error:
        return [], [{"reason": "ruby-patch-failed-closed", "error": str(error)}]

    unresolved = [
        dict(item) if isinstance(item, dict) else {"reason": str(item)}
        for item in result.get("unresolved", [])
    ]
    if unresolved:
        return [], unresolved

    if sidecar_path is not None and write_sidecar:
        if sug_path is None:
            raise RubyValidationError(
                "sug_path is required for atomic sidecar publication"
            )
        old_records = []
        if isinstance(sidecar, dict) and isinstance(sidecar.get("records"), list):
            old_records = [
                record for record in sidecar["records"] if isinstance(record, dict)
            ]
        records = [*old_records, *result.get("records", [])]
        _publish_review_bundle(
            sug_path,
            sidecar_path,
            document,
            sug_hash_before=result["before_sug_hash"],
            sug_hash_after=result["after_sug_hash"],
            records=records,
            model_prompt_version=model_prompt_version,
        )
    return _change_objects(document, result), []


def album_sug_paths() -> list[Path]:
    if not SONGS:
        raise RuntimeError(
            "no public album manifest is bundled; pass canonical SUG paths explicitly"
        )
    paths: list[Path] = []
    for song in SONGS:
        candidates = sorted((song.deliverable_dir / "timing").glob(f"{song.song_id}_*.sug"))
        if len(candidates) != 1:
            raise RuntimeError(
                f"expected one SUG for {song.song_id}, found {len(candidates)}"
            )
        paths.append(candidates[0])
    return paths


def _load_patches(path: Path | None) -> list[dict[str, Any]] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("patches")
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RubyValidationError(f"ruby patch file must contain a list: {path}")
    return payload


def sync_file(
    path: Path,
    *,
    check: bool,
    patches_path: Path | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RubyValidationError(f"SUG document must be an object: {path}")
    sidecar_path = path.with_suffix(".ruby-review.json")
    sidecar = None
    if sidecar_path.exists():
        sidecar = load_review_sidecar(sidecar_path)
    patches = _load_patches(patches_path)
    changes, unresolved = synchronize_document(
        document,
        patches,
        sidecar=sidecar,
        sidecar_path=sidecar_path,
        write_sidecar=not check,
        sug_path=path,
    )
    if unresolved:
        return len(changes), unresolved
    return len(changes), unresolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--patches", type=Path)
    args = parser.parse_args()
    paths = [path.resolve() for path in args.paths] or album_sug_paths()
    total_changes = 0
    unresolved_all: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        changes, unresolved = sync_file(
            path,
            check=args.check,
            patches_path=args.patches,
        )
        total_changes += changes
        unresolved_all.extend((path, item) for item in unresolved)
        print(f"{path.name}: {changes} ruby changes")
    if unresolved_all:
        for path, item in unresolved_all:
            print(f"UNRESOLVED {path.name}: {item}")
        return 2
    if args.check and total_changes:
        print(f"editable ruby is stale: {total_changes} changes required")
        return 1
    print(f"editable ruby synchronized: {total_changes} changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
