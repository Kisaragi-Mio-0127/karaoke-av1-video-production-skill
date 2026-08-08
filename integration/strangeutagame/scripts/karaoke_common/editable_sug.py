"""Export a relocatable editable SUG beside a completed karaoke render."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


class EditableSugError(RuntimeError):
    """Raised when an editable SUG snapshot cannot be produced safely."""


def export_editable_sug(
    source_sug: Path,
    audio_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Copy a SUG and rewrite its media path relative to the new location."""

    source = source_sug.expanduser().resolve()
    audio = audio_path.expanduser().resolve()
    if not source.is_file():
        raise EditableSugError(f"source SUG does not exist: {source}")
    if not audio.is_file():
        raise EditableSugError(f"SUG audio does not exist: {audio}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EditableSugError(f"source SUG is not valid UTF-8 JSON: {source}") from error
    if not isinstance(document, dict):
        raise EditableSugError(f"source SUG root must be a JSON object: {source}")
    sentences = document.get("sentences")
    if not isinstance(sentences, list):
        raise EditableSugError(f"source SUG has no sentences array: {source}")

    destination_dir = output_dir.expanduser().resolve() / "editable-project"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    media_path = os.path.relpath(audio, destination_dir).replace(os.sep, "/")
    document["media_path"] = media_path

    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    verified = json.loads(destination.read_text(encoding="utf-8"))
    resolved_media = (destination.parent / verified["media_path"]).resolve()
    if resolved_media != audio or not resolved_media.is_file():
        raise EditableSugError(
            f"exported SUG media path did not resolve to the render audio: {destination}"
        )
    return {
        "path": str(destination),
        "source": str(source),
        "media_path": media_path,
        "resolved_media": str(resolved_media),
        "sentence_count": len(sentences),
    }
