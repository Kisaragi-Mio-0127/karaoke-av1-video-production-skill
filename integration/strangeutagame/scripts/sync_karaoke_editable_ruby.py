#!/usr/bin/env python3
"""Synchronize context-aware reviewed ruby back into editable SUG projects."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.karaoke_language import (  # noqa: E402
    DEFAULT_LANGUAGE,
    normalize_language,
    uses_ruby,
)
from scripts.karaoke_review_preview import (  # noqa: E402
    RUBY_GROUP_OVERRIDES,
    contextual_ruby_tokens,
)
from scripts.karaoke_timing import SONGS  # noqa: E402


@dataclass(frozen=True)
class RubyChange:
    line_index: int
    char_index: int
    text: str
    before: str
    after: str
    kind: str = "reading"


def _reading(character: dict[str, Any]) -> str:
    return "".join(
        str(part.get("text") or "")
        for part in (character.get("ruby") or {}).get("parts", [])
    )


def _set_reading(character: dict[str, Any], reading: str) -> None:
    if reading:
        character["ruby"] = {"parts": [{"text": reading, "offset_ms": 0}]}
    else:
        character.pop("ruby", None)


def synchronize_document(
    document: dict[str, Any],
) -> tuple[list[RubyChange], list[tuple[int, str, str]]]:
    changes: list[RubyChange] = []
    unresolved: list[tuple[int, str, str]] = []
    metadata = document.get("metadata")
    metadata_language = metadata.get("language") if isinstance(metadata, dict) else None
    language = normalize_language(metadata_language, default=DEFAULT_LANGUAGE)
    if not uses_ruby(language):
        return changes, unresolved
    for line_index, sentence in enumerate(document.get("sentences", [])):
        characters = sentence.get("characters", [])
        text = "".join(str(character.get("char") or "") for character in characters)
        for token in contextual_ruby_tokens(text, language=language):
            width = token.end - token.start
            if width == 1:
                readings = (token.reading,)
            else:
                readings = RUBY_GROUP_OVERRIDES["multi_kanji_splits"].get(
                    (token.text, token.reading)
                )
                if readings is None:
                    unresolved.append((line_index, token.text, token.reading))
                    continue
            if len(readings) != width:
                raise ValueError(
                    f"ruby split width mismatch for {token.text!r}: "
                    f"{len(readings)} readings for {width} characters"
                )
            for offset, reading in enumerate(readings):
                char_index = token.start + offset
                character = characters[char_index]
                before = _reading(character)
                if before != reading:
                    _set_reading(character, reading)
                    changes.append(
                        RubyChange(
                            line_index=line_index,
                            char_index=char_index,
                            text=str(character.get("char") or ""),
                            before=before,
                            after=reading,
                        )
                    )
                if token.text in RUBY_GROUP_OVERRIDES["linked_spans"]:
                    linked_before = bool(character.get("linked_to_next", False))
                    linked_after = char_index < token.end - 1
                    if linked_before != linked_after:
                        character["linked_to_next"] = linked_after
                        changes.append(
                            RubyChange(
                                line_index=line_index,
                                char_index=char_index,
                                text=str(character.get("char") or ""),
                                before=str(linked_before).lower(),
                                after=str(linked_after).lower(),
                                kind="linked_to_next",
                            )
                        )
    return changes, unresolved


def album_sug_paths() -> list[Path]:
    paths: list[Path] = []
    for song in SONGS:
        candidates = sorted((song.deliverable_dir / "timing").glob(f"{song.song_id}_*.sug"))
        if len(candidates) != 1:
            raise RuntimeError(
                f"expected one SUG for {song.song_id}, found {len(candidates)}"
            )
        paths.append(candidates[0])
    return paths


def sync_file(path: Path, *, check: bool) -> tuple[int, list[tuple[int, str, str]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    changes, unresolved = synchronize_document(document)
    if unresolved:
        return len(changes), unresolved
    if changes and not check:
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return len(changes), unresolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    paths = [path.resolve() for path in args.paths] or album_sug_paths()
    total_changes = 0
    unresolved_all: list[tuple[Path, int, str, str]] = []
    for path in paths:
        changes, unresolved = sync_file(path, check=args.check)
        total_changes += changes
        for line_index, text, reading in unresolved:
            unresolved_all.append((path, line_index, text, reading))
        print(f"{path.name}: {changes} ruby changes")
    if unresolved_all:
        for path, line_index, text, reading in unresolved_all:
            print(
                f"UNRESOLVED {path.name} line={line_index} "
                f"text={text!r} reading={reading!r}"
            )
        return 2
    if args.check and total_changes:
        print(f"editable ruby is stale: {total_changes} changes required")
        return 1
    print(f"editable ruby synchronized: {total_changes} changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
