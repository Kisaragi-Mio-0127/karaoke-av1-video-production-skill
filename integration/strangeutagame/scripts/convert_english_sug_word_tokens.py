#!/usr/bin/env python3
"""Convert an existing English SUG to one editable checkpoint per word."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from .karaoke_language import normalize_language
    from .karaoke_timing import collapse_english_sentence_to_word_tokens
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_language import normalize_language  # type: ignore[no-redef]
    from karaoke_timing import (  # type: ignore[no-redef]
        collapse_english_sentence_to_word_tokens,
    )
from strange_uta_game.backend.infrastructure.persistence.sug_io import (
    SugProjectParser,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    source = args.input.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists() and not args.force:
        raise FileExistsError(output)

    project = SugProjectParser.load(str(source))
    if normalize_language(project.metadata.language) != "en":
        raise ValueError("word-token conversion is only valid for English SUG files")
    extras = SugProjectParser.load_extras(str(source))
    original_texts = [sentence.text for sentence in project.sentences]
    before_characters = sum(len(sentence.characters) for sentence in project.sentences)
    before_points = sum(
        character.check_count
        for sentence in project.sentences
        for character in sentence.characters
    )

    for sentence in project.sentences:
        collapse_english_sentence_to_word_tokens(sentence)

    if [sentence.text for sentence in project.sentences] != original_texts:
        raise ValueError("conversion changed English lyric text")
    output.parent.mkdir(parents=True, exist_ok=True)
    SugProjectParser.save(
        project,
        str(output),
        nicokara_tags=extras.get("nicokara_tags"),
        media_path=extras.get("media_path"),
    )
    reopened = SugProjectParser.load(str(output))
    if [sentence.text for sentence in reopened.sentences] != original_texts:
        raise ValueError("saved word-token SUG did not round-trip its lyric text")

    after_characters = sum(len(sentence.characters) for sentence in reopened.sentences)
    after_points = sum(
        character.check_count
        for sentence in reopened.sentences
        for character in sentence.characters
    )
    print(
        json.dumps(
            {
                "status": "pass",
                "input": str(source),
                "output": str(output),
                "input_sha256": sha256(source),
                "output_sha256": sha256(output),
                "sentence_count": len(reopened.sentences),
                "characters_before": before_characters,
                "tokens_after": after_characters,
                "timing_points_before": before_points,
                "timing_points_after": after_points,
                "editable_timing_unit": "word",
                "media_path": extras.get("media_path"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
