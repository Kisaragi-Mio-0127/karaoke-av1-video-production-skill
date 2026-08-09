from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import scripts.karaoke_mms_editable as karaoke_mms_editable
from scripts.karaoke_mms_editable import MmsEditableError, create_mms_editable_companion
from scripts.sug_ruby import span_hash, sug_hash, write_review_sidecar
from strange_uta_game.backend.infrastructure.persistence.sug_io import SugProjectParser


def test_companion_is_loadable_atomic_non_overwriting_and_preserves_canonical(
    tmp_path: Path,
):
    canonical = tmp_path / "canonical.sug"
    audio = tmp_path / "audio" / "mix.flac"
    build = tmp_path / "private-run" / "build"
    audio.parent.mkdir()
    audio.write_bytes(b"selected-audio")
    document = {
        "version": "0.3.0",
        "metadata": {"language": "ja", "custom": {"reviewed": True}},
        "singers": [{"id": "singer", "name": "Singer", "is_default": True}],
        "styles": {"main": {"font": "Reviewed Font", "size": 72}},
        "sentences": [
            {
                "id": "line-0",
                "singer_id": "singer",
                "style": "main",
                "characters": [
                    {
                        "char": "今",
                        "check_count": 2,
                        "timestamps": [1000, 1200],
                        "linked_to_next": True,
                        "ruby": {
                            "parts": [
                                {"text": "きょ", "offset_ms": 0},
                                {"text": "う", "offset_ms": 0},
                            ]
                        },
                    },
                    {
                        "char": "日",
                        "timestamps": [1500],
                        "sentence_end_ts": 2100,
                        "linked_to_next": False,
                    },
                ],
            }
        ],
        "media_path": "old.flac",
    }
    canonical.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    original_bytes = canonical.read_bytes()
    canonical_sidecar = canonical.with_suffix(".ruby-review.json")
    write_review_sidecar(
        canonical_sidecar,
        sug_hash_before=sug_hash(document),
        sug_hash_after=sug_hash(document),
        records=[
            {
                "sentence_id": "line-0",
                "start": 0,
                "end": 2,
                "surface": "今日",
                "source": "project-auto-check",
                "review_status": "machine-fill",
                "confidence": None,
                "evidence": ["whole-sentence-tokenizer"],
                "model_prompt_version": None,
                "generation_id": "generated-ruby",
                "before_hash": span_hash(document, 0, 0, 2),
                "after_hash": span_hash(document, 0, 0, 2),
            }
        ],
        generation_id="canonical-generation",
    )
    original_sidecar_bytes = canonical_sidecar.read_bytes()
    overrides = {
        "songs": {
            "song-ja": {
                "lines": {
                    "0": {
                        "character_overrides_ms": {"0": 900, "1": 1600},
                        "release_override_ms": 2400,
                        "visual_release_overrides_ms": {"0": 1750},
                    }
                }
            }
        }
    }

    companion = create_mms_editable_companion(
        canonical_sug=canonical,
        audio=audio,
        build_dir=build,
        song_id="song-ja",
        overrides=overrides,
    )

    assert companion == build / "canonical.mms-editable.sug"
    assert canonical.read_bytes() == original_bytes
    assert canonical_sidecar.read_bytes() == original_sidecar_bytes
    saved = json.loads(companion.read_text(encoding="utf-8"))
    companion_sidecar_path = companion.with_suffix(".ruby-review.json")
    companion_sidecar = json.loads(companion_sidecar_path.read_text(encoding="utf-8"))
    assert companion_sidecar["generation_id"] == "canonical-generation"
    assert companion_sidecar["sug_hash_after"] == sug_hash(saved)
    assert companion_sidecar["records"][0]["after_hash"] == span_hash(
        saved, 0, 0, 2
    )
    assert companion_sidecar["records"][0]["source"] == "project-auto-check"
    assert companion_sidecar["records"][0]["review_status"] == "machine-fill"
    first, second = saved["sentences"][0]["characters"]
    assert first["timestamps"] == [900, 1200]
    assert second["timestamps"] == [1600]
    assert second["sentence_end_ts"] == 2400
    assert first["linked_to_next"] is True
    assert first["ruby"] == document["sentences"][0]["characters"][0]["ruby"]
    assert [character["char"] for character in saved["sentences"][0]["characters"]] == [
        character["char"] for character in document["sentences"][0]["characters"]
    ]
    assert len(saved["sentences"][0]["characters"]) == len(
        document["sentences"][0]["characters"]
    )
    assert saved["sentences"][0]["singer_id"] == "singer"
    assert saved["sentences"][0]["style"] == "main"
    assert saved["singers"] == document["singers"]
    assert saved["styles"] == document["styles"]
    assert "visual_release_overrides_ms" not in json.dumps(saved)
    assert (companion.parent / saved["media_path"]).resolve() == audio.resolve()
    loaded = SugProjectParser.load(str(companion))
    assert loaded.metadata.language == "ja"
    assert loaded.sentences[0].characters[0].timestamps == [900, 1200]

    normalized = copy.deepcopy(saved)
    normalized["media_path"] = document["media_path"]
    for canonical_sentence, companion_sentence in zip(
        document["sentences"], normalized["sentences"], strict=True
    ):
        for canonical_character, companion_character in zip(
            canonical_sentence["characters"],
            companion_sentence["characters"],
            strict=True,
        ):
            companion_character["timestamps"][0] = canonical_character["timestamps"][0]
            if "sentence_end_ts" in canonical_character:
                companion_character["sentence_end_ts"] = canonical_character[
                    "sentence_end_ts"
                ]
            else:
                companion_character.pop("sentence_end_ts", None)
    assert normalized == document

    with pytest.raises(FileExistsError, match="already exists"):
        create_mms_editable_companion(
            canonical_sug=canonical,
            audio=audio,
            build_dir=build,
            song_id="song-ja",
            overrides=overrides,
        )
    assert companion.read_text(encoding="utf-8") == json.dumps(
        saved, ensure_ascii=False, indent=2
    ) + "\n"


def _write_shared_checkpoint_project(
    path: Path, *, first_timestamps: list[int] | None = None
) -> None:
    first_timestamps = first_timestamps or [30139]
    path.write_text(
        json.dumps(
            {
                "version": "0.3.0",
                "metadata": {"language": "ja"},
                "singers": [
                    {"id": "singer", "name": "Singer", "is_default": True}
                ],
                "sentences": [
                    {
                        "id": "line-0",
                        "singer_id": "singer",
                        "characters": [
                            {
                                "char": "A",
                                "check_count": len(first_timestamps),
                                "timestamps": first_timestamps,
                            },
                            {"char": "B", "timestamps": [30139]},
                            {
                                "char": "C",
                                "timestamps": [30139],
                                "sentence_end_ts": 31000,
                            },
                        ],
                    }
                ],
                "media_path": "old.flac",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_companion_allows_shared_onsets_and_equal_checkpoints(tmp_path: Path):
    canonical = tmp_path / "canonical.sug"
    audio = tmp_path / "mix.flac"
    audio.write_bytes(b"audio")
    _write_shared_checkpoint_project(canonical)

    companion = create_mms_editable_companion(
        canonical_sug=canonical,
        audio=audio,
        build_dir=tmp_path / "build",
        song_id="song-ja",
        overrides={
            "songs": {
                "song-ja": {
                    "lines": {
                        "0": {
                            "character_overrides_ms": {
                                "0": 30135,
                                "1": 30135,
                                "2": 30135,
                            }
                        }
                    }
                }
            }
        },
    )

    saved = json.loads(companion.read_text(encoding="utf-8"))
    assert [
        character["timestamps"]
        for character in saved["sentences"][0]["characters"]
    ] == [[30135], [30135], [30135]]


def test_companion_allows_equal_checkpoints_within_one_token(tmp_path: Path):
    canonical = tmp_path / "canonical.sug"
    audio = tmp_path / "mix.flac"
    audio.write_bytes(b"audio")
    _write_shared_checkpoint_project(
        canonical, first_timestamps=[30139, 30139]
    )

    companion = create_mms_editable_companion(
        canonical_sug=canonical,
        audio=audio,
        build_dir=tmp_path / "build",
        song_id="song-ja",
        overrides={"songs": {"song-ja": {"lines": {"0": {}}}}},
    )

    saved = json.loads(companion.read_text(encoding="utf-8"))
    assert saved["sentences"][0]["characters"][0]["timestamps"] == [
        30139,
        30139,
    ]


def test_companion_rejects_decreasing_checkpoint_timeline(tmp_path: Path):
    canonical = tmp_path / "canonical.sug"
    audio = tmp_path / "mix.flac"
    audio.write_bytes(b"audio")
    _write_shared_checkpoint_project(canonical)

    with pytest.raises(MmsEditableError, match="timeline is not non-decreasing"):
        create_mms_editable_companion(
            canonical_sug=canonical,
            audio=audio,
            build_dir=tmp_path / "build",
            song_id="song-ja",
            overrides={
                "songs": {
                    "song-ja": {
                        "lines": {
                            "0": {
                                "character_overrides_ms": {
                                    "0": 30140,
                                    "1": 30135,
                                }
                            }
                        }
                    }
                }
            },
        )


def _write_reviewed_ruby_project(tmp_path: Path) -> tuple[Path, Path, dict]:
    canonical = tmp_path / "canonical.sug"
    audio = tmp_path / "mix.flac"
    audio.write_bytes(b"audio")
    document = {
        "version": "0.3.0",
        "metadata": {"language": "ja"},
        "singers": [{"id": "singer", "name": "Singer", "is_default": True}],
        "sentences": [
            {
                "id": "line-0",
                "singer_id": "singer",
                "characters": [
                    {
                        "char": "今",
                        "timestamps": [1000],
                        "ruby": {"parts": [{"text": "いま", "offset_ms": 0}]},
                    },
                    {
                        "char": "日",
                        "timestamps": [1500],
                        "sentence_end_ts": 2000,
                        "ruby": {"parts": [{"text": "ひ", "offset_ms": 0}]},
                    },
                ],
            }
        ],
        "media_path": "old.flac",
    }
    canonical.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    records = [
        {
            "sentence_id": "line-0",
            "start": index,
            "end": index + 1,
            "surface": character["char"],
            "source": "human-review",
            "review_status": "human-locked",
            "generation_id": f"record-{index}",
            "after_hash": span_hash(document, 0, index, index + 1),
        }
        for index, character in enumerate(document["sentences"][0]["characters"])
    ]
    write_review_sidecar(
        canonical.with_suffix(".ruby-review.json"),
        sug_hash_before=sug_hash(document),
        sug_hash_after=sug_hash(document),
        records=records,
        generation_id="reviewed-generation",
    )
    return canonical, audio, document


def test_companion_rejects_stale_canonical_sidecar_sug_hash(tmp_path: Path):
    canonical, audio, _ = _write_reviewed_ruby_project(tmp_path)
    sidecar_path = canonical.with_suffix(".ruby-review.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["sug_hash_after"] = "stale-sug-hash"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    with pytest.raises(MmsEditableError, match="sug_hash_after is stale"):
        create_mms_editable_companion(
            canonical_sug=canonical,
            audio=audio,
            build_dir=tmp_path / "build",
            song_id="song-ja",
            overrides={"songs": {"song-ja": {"lines": {"0": {}}}}},
        )

    assert sidecar["generation_id"] == "reviewed-generation"
    assert not (tmp_path / "build" / "canonical.mms-editable.sug").exists()


def test_companion_rejects_partially_stale_canonical_sidecar_records(tmp_path: Path):
    canonical, audio, _ = _write_reviewed_ruby_project(tmp_path)
    sidecar_path = canonical.with_suffix(".ruby-review.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["records"][1]["after_hash"] = "stale-span-hash"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    with pytest.raises(MmsEditableError, match="record 1 after_hash is stale"):
        create_mms_editable_companion(
            canonical_sug=canonical,
            audio=audio,
            build_dir=tmp_path / "build",
            song_id="song-ja",
            overrides={"songs": {"song-ja": {"lines": {"0": {}}}}},
        )

    assert sidecar["generation_id"] == "reviewed-generation"
    assert not (tmp_path / "build" / "canonical.mms-editable.sug").exists()


@pytest.mark.parametrize("retain_publish_state", [True, False])
def test_companion_recovers_sidecar_orphan_after_interrupted_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retain_publish_state: bool,
):
    canonical, audio, _ = _write_reviewed_ruby_project(tmp_path)
    build = tmp_path / "build"
    destination = build / "canonical.mms-editable.sug"
    destination_sidecar = destination.with_suffix(".ruby-review.json")
    publish_state = build / ".canonical.mms-editable.sug.publish.json"
    real_link = karaoke_mms_editable.os.link

    def fail_before_sug_publish(source: Path, target: Path) -> None:
        if Path(target) == destination:
            raise RuntimeError("injected failure between sidecar and SUG publish")
        real_link(source, target)

    monkeypatch.setattr(karaoke_mms_editable.os, "link", fail_before_sug_publish)
    with pytest.raises(RuntimeError, match="injected failure"):
        create_mms_editable_companion(
            canonical_sug=canonical,
            audio=audio,
            build_dir=build,
            song_id="song-ja",
            overrides={"songs": {"song-ja": {"lines": {"0": {}}}}},
        )

    assert publish_state.is_file()
    assert destination_sidecar.is_file()
    assert not destination.exists()
    if not retain_publish_state:
        publish_state.unlink()

    monkeypatch.setattr(karaoke_mms_editable.os, "link", real_link)
    retried = create_mms_editable_companion(
        canonical_sug=canonical,
        audio=audio,
        build_dir=build,
        song_id="song-ja",
        overrides={"songs": {"song-ja": {"lines": {"0": {}}}}},
    )

    assert retried == destination
    assert destination.is_file()
    assert destination_sidecar.is_file()
    assert not publish_state.exists()
