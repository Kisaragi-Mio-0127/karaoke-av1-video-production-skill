from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.sync_karaoke_editable_ruby as sync_module
from scripts.sug_ruby import span_hash, sug_hash, timing_fingerprint
from scripts.sync_karaoke_editable_ruby import (
    album_sug_paths,
    sync_file,
    synchronize_document,
)


def _document(
    text: str,
    readings: dict[int, str] | None = None,
    *,
    language: str = "ja",
    sentence_id: str = "line-1",
) -> dict:
    readings = readings or {}
    characters = []
    for index, char in enumerate(text):
        character = {
            "char": char,
            "check_count": 0 if char.isspace() else 1,
            "timestamps": [] if char.isspace() else [1_000 + index * 100],
            "linked_to_next": False,
        }
        if index in readings:
            character["ruby"] = {
                "parts": [{"text": readings[index], "offset_ms": 0}]
            }
        characters.append(character)
    if characters:
        characters[-1]["sentence_end_ts"] = 2_000
    return {
        "id": "sync-fixture",
        "metadata": {"language": language},
        "sentences": [{"id": sentence_id, "characters": characters}],
    }


def _reading(character: dict) -> str:
    return "".join(
        part.get("text", "")
        for part in (character.get("ruby") or {}).get("parts", [])
    )


def _write_json(path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_no_patch_path_is_a_read_only_canonical_audit():
    document = _document("\u96e8\u304c\u964d\u308a\u305d\u3046", {2: "\u3075"})
    before = json.dumps(document, ensure_ascii=False, sort_keys=True)

    changes, unresolved = synchronize_document(document)

    assert changes == []
    assert unresolved == []
    assert json.dumps(document, ensure_ascii=False, sort_keys=True) == before


def test_agent_patch_can_correct_machine_fill_and_writes_provenance(tmp_path):
    document = _document("\u597d", {0: "\u304b"})
    before_timing = timing_fingerprint(document)
    before_hash = span_hash(document, 0, 0, 1)
    sidecar = {
        "records": [
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "source": "pykakasi",
                "review_status": "machine-fill",
            }
        ]
    }
    patch = {
        "sentence_id": "line-1",
        "start": 0,
        "end": 1,
        "surface": "\u597d",
        "reading": "\u3059",
        "review_status": "ai-reviewed",
        "confidence": 0.98,
        "source": "agent-review",
        "evidence": ["whole-sentence-context", "lexical-boundary"],
        "model_prompt_version": "ruby-agent-v2",
        "before_hash": before_hash,
    }
    sidecar_path = tmp_path / "song.ruby-review.json"
    sug_path = tmp_path / "song.sug"
    _write_json(sug_path, document)

    changes, unresolved = synchronize_document(
        document,
        [patch],
        sidecar=sidecar,
        sidecar_path=sidecar_path,
        sug_path=sug_path,
    )

    assert unresolved == []
    assert [change.after for change in changes if change.kind == "reading"] == ["\u3059"]
    assert _reading(document["sentences"][0]["characters"][0]) == "\u3059"
    assert timing_fingerprint(document) == before_timing
    written = json.loads(sidecar_path.read_text(encoding="utf-8"))
    record = written["records"][-1]
    assert written["sug_hash_before"] != written["sug_hash_after"]
    assert record["review_status"] == "ai-approved"
    assert record["source"] == "agent-review"
    assert record["evidence"] == ["whole-sentence-context", "lexical-boundary"]
    assert record["model_prompt_version"] == "ruby-agent-v2"
    assert record["before_hash"] == before_hash
    assert record["after_hash"] == span_hash(document, 0, 0, 1)


def test_approved_patch_without_reading_change_publishes_new_sidecar(tmp_path):
    document = _document("\u597d", {0: "\u304b"})
    sug_path = tmp_path / "song.sug"
    sidecar_path = tmp_path / "song.ruby-review.json"
    patches_path = tmp_path / "patches.json"
    _write_json(sug_path, document)
    sync_module.write_review_sidecar(
        sidecar_path,
        sug_hash_before=sug_hash(document),
        sug_hash_after=sug_hash(document),
        records=[
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "source": "machine-fill",
            }
        ],
    )
    _write_json(
        patches_path,
        [
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "surface": "\u597d",
                "reading": "\u304b",
                "review_status": "ai-reviewed",
                "confidence": 0.99,
                "source": "agent-review",
            }
        ],
    )

    changes, unresolved = sync_file(
        sug_path,
        check=False,
        patches_path=patches_path,
    )

    assert changes == 0
    assert unresolved == []
    published = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert len(published["records"]) == 2
    assert published["sug_hash_before"] == sug_hash(document)
    assert published["sug_hash_after"] == sug_hash(document)


def test_check_mode_performs_zero_writes(tmp_path):
    document = _document("\u597d")
    sug_path = tmp_path / "song.sug"
    sidecar_path = tmp_path / "song.ruby-review.json"
    patches_path = tmp_path / "patches.json"
    _write_json(sug_path, document)
    _write_json(
        patches_path,
        [
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "surface": "\u597d",
                "reading": "\u3059",
                "review_status": "ai-reviewed",
                "confidence": 0.99,
                "source": "agent-review",
            }
        ],
    )
    before_sug = sug_path.read_bytes()
    before_names = sorted(path.name for path in tmp_path.iterdir())

    changes, unresolved = sync_file(
        sug_path,
        check=True,
        patches_path=patches_path,
    )

    assert changes == 1
    assert unresolved == []
    assert sug_path.read_bytes() == before_sug
    assert not sidecar_path.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names


def test_sug_replace_failure_does_not_publish_sidecar(tmp_path, monkeypatch):
    document = _document("\u597d")
    sug_path = tmp_path / "song.sug"
    sidecar_path = tmp_path / "song.ruby-review.json"
    patches_path = tmp_path / "patches.json"
    _write_json(sug_path, document)
    _write_json(
        patches_path,
        [
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "surface": "\u597d",
                "reading": "\u3059",
                "review_status": "ai-reviewed",
                "confidence": 0.99,
                "source": "agent-review",
            }
        ],
    )
    before_sug = sug_path.read_bytes()
    calls = []

    def fail_sug_replace(source, destination):
        calls.append(Path(destination))
        if Path(destination) == sug_path:
            raise OSError("injected SUG replace failure")
        return original_replace(source, destination)

    original_replace = sync_module.os.replace
    monkeypatch.setattr(sync_module.os, "replace", fail_sug_replace)

    with pytest.raises(OSError, match="injected SUG replace failure"):
        sync_file(sug_path, check=False, patches_path=patches_path)

    assert calls == [sug_path]
    assert sug_path.read_bytes() == before_sug
    assert not sidecar_path.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_sidecar_replace_failure_leaves_updated_sug_and_old_sidecar(
    tmp_path, monkeypatch
):
    document = _document("\u597d")
    sug_path = tmp_path / "song.sug"
    sidecar_path = tmp_path / "song.ruby-review.json"
    patches_path = tmp_path / "patches.json"
    _write_json(sug_path, document)
    sync_module.write_review_sidecar(
        sidecar_path,
        sug_hash_before=sug_hash(document),
        sug_hash_after=sug_hash(document),
        records=[],
    )
    _write_json(
        patches_path,
        [
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "surface": "\u597d",
                "reading": "\u3059",
                "review_status": "ai-reviewed",
                "confidence": 0.99,
                "source": "agent-review",
            }
        ],
    )
    before_sug = sug_path.read_bytes()
    before_sidecar = sidecar_path.read_bytes()
    calls = []

    def fail_sidecar_replace(source, destination):
        calls.append(Path(destination))
        if Path(destination) == sidecar_path:
            raise OSError("injected sidecar replace failure")
        return original_replace(source, destination)

    original_replace = sync_module.os.replace
    monkeypatch.setattr(sync_module.os, "replace", fail_sidecar_replace)

    with pytest.raises(OSError, match="injected sidecar replace failure"):
        sync_file(sug_path, check=False, patches_path=patches_path)

    assert calls == [sug_path, sidecar_path]
    assert sug_path.read_bytes() != before_sug
    assert sidecar_path.read_bytes() == before_sidecar
    assert not list(tmp_path.glob(".*.tmp"))


def test_low_confidence_agent_patch_fails_closed_without_mutation():
    document = _document("\u597d", {0: "\u304b"})
    before = sug_hash(document)
    sidecar = {
        "records": [
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "source": "machine-fill",
            }
        ]
    }

    changes, unresolved = synchronize_document(
        document,
        [
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "surface": "\u597d",
                "reading": "\u3059",
                "review_status": "ai-reviewed",
                "confidence": 0.40,
                "source": "agent-review",
            }
        ],
        sidecar=sidecar,
    )

    assert changes == []
    assert unresolved[0]["reason"] == "low-confidence"
    assert sug_hash(document) == before
    assert _reading(document["sentences"][0]["characters"][0]) == "\u304b"


def test_existing_sug_ruby_is_human_locked_without_machine_provenance():
    document = _document("\u597d", {0: "\u3059"})
    before = sug_hash(document)

    changes, unresolved = synchronize_document(
        document,
        [
            {
                "sentence_id": "line-1",
                "start": 0,
                "end": 1,
                "surface": "\u597d",
                "reading": "\u3044\u3044",
                "review_status": "ai-approved",
                "confidence": 0.99,
                "source": "agent-review",
            }
        ],
    )

    assert changes == []
    assert unresolved[0]["reason"] == "human-locked"
    assert sug_hash(document) == before


def test_zh_and_en_have_no_ruby_entry_point():
    for language in ("zh", "en"):
        document = _document("hello" if language == "en" else "\u597d", language=language)
        changes, unresolved = synchronize_document(document)
        assert changes == []
        assert unresolved == []

        document["sentences"][0]["characters"][0]["ruby"] = {
            "parts": [{"text": "\u3059", "offset_ms": 0}]
        }
        _, blocked = synchronize_document(
            document,
            [
                {
                    "sentence_id": "line-1",
                    "start": 0,
                    "end": 1,
                    "reading": "\u3059",
                    "review_status": "ai-approved",
                    "confidence": 0.99,
                }
            ],
        )
        assert blocked[0]["reason"] == "ruby-disabled-language"


def test_album_editable_sug_ruby_is_only_a_read_only_audit(tmp_path):
    manifest = tmp_path / "album.json"
    timing_dir = tmp_path / "deliverables" / "timing"
    timing_dir.mkdir(parents=True)
    sug_path = timing_dir / "generic-song_generic-track.sug"
    _write_json(sug_path, _document("確認", {0: "かく"}))
    _write_json(
        manifest,
        {
            "schema_version": "karaoke-album/v1",
            "album": {"title": "Generic Album", "artist": "Generic Artist"},
            "paths": {
                "audio_directory": "audio",
                "font_package": "fonts",
                "deliverable_directory": "deliverables",
            },
            "tracks": [
                {
                    "disc": 1,
                    "track_number": 1,
                    "song_id": "generic-song",
                    "title": "Generic Title",
                    "artist": "Generic Artist",
                    "artifact_slug": "generic-track",
                    "audio_file": "generic.flac",
                    "audio_sha256": "0" * 64,
                    "expected_duration_ms": 10_000,
                    "expected_cues": 2,
                    "language": "ja",
                }
            ],
        },
    )

    for path in album_sug_paths(manifest):
        document = json.loads(path.read_text(encoding="utf-8"))
        changes, unresolved = synchronize_document(document)
        assert unresolved == [], path.name
        assert changes == [], path.name
