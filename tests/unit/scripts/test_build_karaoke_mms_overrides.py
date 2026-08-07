import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.build_karaoke_mms_overrides import (
    AUDIT_SCHEMA_V1,
    AUDIT_SCHEMA_V2,
    _accepted,
    _v2_token_display_mapping,
    build_overrides,
    make_parser,
    recommended_release_override,
    release_tail_disposition,
    run_build,
    validate_audit_source_hashes,
    validate_recognition_audio_sources,
)


def _item(index, character, current, vocal, mix, vocal_score=0.9, mix_score=0.8):
    return {
        "character_index": index,
        "character": character,
        "current_ms": current,
        "vocal_mms_ms": vocal,
        "mix_mms_ms": mix,
        "vocal_minus_mix_ms": vocal - mix,
        "vocal_score": vocal_score,
        "mix_score": mix_score,
    }


def _v2_item(
    index,
    display,
    current,
    vocal,
    mix,
    vocal_score=0.9,
    mix_score=0.8,
    **extra,
):
    return {
        "source_token_index": index,
        "source_token_display": display,
        "current_ms": current,
        "vocal_mms_ms": vocal,
        "mix_mms_ms": mix,
        "vocal_minus_mix_ms": vocal - mix,
        "vocal_score": vocal_score,
        "mix_score": mix_score,
        **extra,
    }


def _release_line(vocal_end, mix_end, vocal_score=0.9, mix_score=0.8):
    return {
        "sug_release_ms": 10_000,
        "crop_end_ms": 11_000,
        "units": [
            {
                "unit": "a",
                "end_ms": vocal_end,
                "score": vocal_score,
            }
        ],
        "mix_units": [
            {
                "unit": "a",
                "end_ms": mix_end,
                "score": mix_score,
            }
        ],
    }


def _recognition_audit(
    audio_kind, disposition, *, text="a", start_ms=900, end_ms=2_000
):
    return {
        "song_id": "generic-song",
        "audio_kind": audio_kind,
        "audio": {
            "path": f"C:/evidence/generic-song-{audio_kind}.wav",
            "sha256": ("a" if audio_kind == "stem" else "b") * 64,
        },
        "model": "base",
        "model_path": "C:/models/base.pt",
        "model_sha256": "c" * 64,
        "recognized_token_count": 1,
        "cache": {"key": ("d" if audio_kind == "stem" else "e") * 64},
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": text,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "disposition": disposition,
                        "gate_ok": True,
                    }
                ],
            }
        ],
    }


def _single_line_audit():
    return {
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "a",
                        "timed_character_indices": [0],
                        "dual_audio_comparisons": [
                            _item(0, "a", 1_000, 1_010, 1_015),
                        ],
                    }
                ],
            }
        ]
    }


def _build_with_recognition(recognition_audits, *, allow_single=False):
    count = len(recognition_audits)
    return build_overrides(
        _single_line_audit(),
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "a"},
        target_song_ids=("generic-song",),
        recognition_audit=recognition_audits,
        recognition_audit_relative_path=tuple(
            f"recognition-{index}.json" for index in range(count)
        ),
        recognition_audit_sha256=tuple(
            f"report-{index}-sha256" for index in range(count)
        ),
        allow_single_recognition_lane_review_only=allow_single,
    )


def test_character_overrides_are_explicitly_non_rendering_evidence():
    audit = _single_line_audit()
    text = audit["songs"][0]["lines"][0]["text"]
    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): text},
        target_song_ids=("generic-song",),
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["character_overrides_ms"]
    assert line["character_overrides_ms_semantics"] == {
        "role": "evidence",
        "applied_to_render": False,
    }
    assert result["mms_provenance"]["policy"]["character_overrides_ms"] == {
        "role": "evidence",
        "applied_to_render": False,
    }


def test_empty_audit_lines_are_rejected_instead_of_vacuously_passing():
    with pytest.raises(ValueError, match="contains no timing lines"):
        build_overrides(
            {"songs": [{"song_id": "generic-song", "lines": []}]},
            {"songs": {}},
            audit_relative_path="audit.json",
            line_windows={},
            line_texts={},
            target_song_ids=("generic-song",),
        )


@pytest.mark.parametrize("song_id", ["generic-song", "unrelated-song"])
def test_same_dual_audio_evidence_has_the_same_result_for_every_song(song_id):
    accepted = _item(0, "x", 1_000, 1_500, 1_500)
    rejected = _item(0, "x", 1_000, 1_500, 1_500, 0.01, 0.01)

    assert _accepted(song_id, 5, accepted) is True
    assert _accepted(song_id, 5, rejected) is False


def test_release_extension_requires_confident_dual_audio_tail_agreement():
    accepted = recommended_release_override(_release_line(10_700, 10_680))

    assert accepted is not None
    assert accepted["release_override_ms"] == 10_820
    assert accepted["extension_ms"] == 820
    short_tail = recommended_release_override(_release_line(10_080, 10_060))
    assert short_tail is not None
    assert short_tail["release_override_ms"] == 10_200
    assert recommended_release_override(_release_line(10_700, 10_300)) is None
    assert release_tail_disposition(_release_line(10_700, 10_300))["status"] == (
        "rejected-dual-audio-end-disagreement"
    )
    assert recommended_release_override(_release_line(10_700, 10_680, 0.1)) is None


def test_mms_override_policy_has_no_song_specific_exceptions():
    audit = {
        "model": "MMS_FA",
        "model_path": ".cache/torch/model.pt",
        "model_sha256": "a" * 64,
        "songs": [
            {
                "song_id": "generic-song-a",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "b",
                        "dual_audio_comparisons": [
                            _item(0, "b", 1_000, 1_300, 1_280),
                        ],
                    }
                ],
            },
            {
                "song_id": "generic-song-b",
                "lines": [
                    {
                        "line_index": 6,
                        "text": "xxxxxx1xxxxxxxxxxxxxxxz",
                        "dual_audio_comparisons": [
                            _item(6, "1", 2_000, 3_000, 3_000, 0.0, 0.0),
                            _item(22, "z", 107_500, 108_688, 108_588, 0.02, 0.05),
                        ],
                    }
                ],
            },
        ],
    }

    result = build_overrides(
        audit,
        {"songs": {"other": {"lines": {}}}},
        audit_relative_path="deliverables/album/sources/mms_alignment_audit.json",
        line_windows={
            ("generic-song-a", 0): (1_100, 1_500),
            ("generic-song-b", 6): (1_900, 109_000),
        },
        line_texts={
            ("generic-song-a", 0): "b",
            ("generic-song-b", 6): "xxxxxx1xxxxxxxxxxxxxxxz",
        },
        target_song_ids=("generic-song-a", "generic-song-b"),
    )

    first_track = result["songs"]["generic-song-a"]["lines"]["0"]
    second_track = result["songs"]["generic-song-b"]["lines"]["6"]
    assert first_track["character_overrides_ms"]["0"] == 1_300
    assert second_track["character_overrides_ms"]["6"] == 2_000
    assert second_track["character_overrides_ms"]["22"] == 107_500
    assert result["songs"]["other"] == {"lines": {}}
    policy = result["mms_provenance"]["policy"]
    assert policy["human_reviewed"] is False
    assert "low_score_explicit_accepts" not in policy
    assert "explicit_candidate_lanes" not in policy


def test_digit_with_phoneme_evidence_is_not_forced_back_to_stable_ts():
    audit = {
        "model": "MMS_FA",
        "model_path": ".cache/torch/model.pt",
        "model_sha256": "a" * 64,
        "songs": [
            {
                "song_id": "generic-song-b",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "100x",
                        "dual_audio_comparisons": [
                            _item(0, "1", 1_000, 1_400, 1_420, 0.9, 0.8),
                        ],
                    }
                ],
            }
        ],
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song-b", 0): (900, 2_000)},
        line_texts={("generic-song-b", 0): "100x"},
        target_song_ids=("generic-song-b",),
    )

    assert (
        result["songs"]["generic-song-b"]["lines"]["0"]["character_overrides_ms"]["0"]
        == 1_400
    )


def test_mms_override_is_clamped_to_the_lrc_window():
    audit = {
        "songs": [
            {
                "song_id": "generic-song-a",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "b",
                        "dual_audio_comparisons": [
                            _item(0, "b", 1_050, 980, 990),
                        ],
                    }
                ],
            },
            {
                "song_id": "generic-song-b",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "c",
                        "dual_audio_comparisons": [
                            _item(0, "c", 2_050, 2_020, 2_010),
                        ],
                    }
                ],
            },
        ],
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={
            ("generic-song-a", 0): (1_000, 1_500),
            ("generic-song-b", 0): (2_000, 2_500),
        },
        line_texts={
            ("generic-song-a", 0): "b",
            ("generic-song-b", 0): "c",
        },
        target_song_ids=("generic-song-a", "generic-song-b"),
    )

    first_track = result["songs"]["generic-song-a"]["lines"]["0"]
    assert first_track["character_overrides_ms"]["0"] == 1_000
    assert "LRC window clamps: 1" in first_track["evidence"]


def test_arbitrary_target_preserves_prior_disposition_and_rejects_weak_mix():
    audit = {
        "songs": [
            {
                "song_id": "generic-song-c",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "ねぇ",
                        "dual_audio_comparisons": [
                            _item(0, "ね", 1_000, 1_300, 1_280),
                            _item(1, "ぇ", 1_050, 1_300, 1_280, 0.9, 0.01),
                        ],
                    }
                ],
            }
        ]
    }
    existing = {
        "songs": {
            "generic-song-c": {
                "lines": {
                    "0": {
                        "reason": "prior phrase-gap disposition",
                        "evidence": ["prior evidence"],
                    }
                }
            }
        }
    }

    result = build_overrides(
        audit,
        existing,
        audit_relative_path="audit.json",
        line_windows={("generic-song-c", 0): (900, 2_000)},
        line_texts={("generic-song-c", 0): "ねぇ"},
        target_song_ids=("generic-song-c",),
    )

    line = result["songs"]["generic-song-c"]["lines"]["0"]
    assert line["character_overrides_ms"] == {"0": 1_300, "1": 1_300}
    assert line["character_dispositions"]["1"] == ("mora-joining-small-kana-inherits-0")
    assert "stable-ts retained characters: 0" in line["evidence"]
    assert "monotonic clamps: 0" in line["evidence"]
    assert line["prior_review_reason"] == "prior phrase-gap disposition"
    assert "prior evidence" in line["evidence"]


def test_stale_audit_character_identity_is_rejected():
    audit = {
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "c",
                        "dual_audio_comparisons": [
                            _item(0, "d", 1_000, 1_000, 1_000),
                        ],
                    }
                ],
            }
        ]
    }

    with pytest.raises(ValueError, match="character identity mismatch"):
        build_overrides(
            audit,
            {"songs": {}},
            audit_relative_path="audit.json",
            line_windows={("generic-song", 0): (900, 2_000)},
            line_texts={("generic-song", 0): "c"},
            target_song_ids=("generic-song",),
        )


def test_large_dual_audio_delta_is_unresolved_even_when_an_override_is_emitted():
    audit = {
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "a",
                        "timed_character_indices": [0],
                        "dual_audio_comparisons": [
                            _item(0, "a", 1_000, 1_800, 1_200),
                        ],
                    }
                ],
            }
        ]
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "a"},
        target_song_ids=("generic-song",),
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["character_overrides_ms"]["0"] == 1_000
    assert line["review_status"] == "unresolved"
    assert line["review_gate"]["ok"] is False
    assert "large-vocal-mix-delta" in line["candidate_failure_reasons"]["0"]
    assert result["gate_ok"] is False
    assert result["unresolved"]


def test_low_confidence_candidate_is_not_machine_reviewed():
    audit = {
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "a",
                        "timed_character_indices": [0],
                        "dual_audio_comparisons": [
                            _item(0, "a", 1_000, 1_010, 1_015, 0.01, 0.01),
                        ],
                    }
                ],
            }
        ]
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "a"},
        target_song_ids=("generic-song",),
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["review_status"] == "unresolved"
    assert "low-vocal-confidence" in line["candidate_failure_reasons"]["0"]
    assert "low-mix-confidence" in line["candidate_failure_reasons"]["0"]


@pytest.mark.parametrize("failure", ["low-confidence", "missing-lane", "lane-delta"])
def test_untrusted_token_preserves_exact_canonical_current_ms(failure):
    fallback = _item(1, "b", 1_200, 1_450, 1_440)
    if failure == "low-confidence":
        fallback["vocal_score"] = 0.01
        fallback["mix_score"] = 0.01
    elif failure == "missing-lane":
        fallback["mix_mms_ms"] = None
        fallback["vocal_minus_mix_ms"] = None
    else:
        fallback["mix_mms_ms"] = 1_000
        fallback["vocal_minus_mix_ms"] = 450
    audit = {
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "ab",
                        "timed_character_indices": [0, 1],
                        "dual_audio_comparisons": [
                            _item(0, "a", 1_000, 1_300, 1_290),
                            fallback,
                        ],
                    }
                ],
            }
        ]
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "ab"},
        target_song_ids=("generic-song",),
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["character_overrides_ms"]["1"] == 1_200
    assert line["candidate_dispositions"]["1"] == "stable-ts-retained-unresolved"


def test_conflicting_accepted_mms_candidates_are_rolled_back_not_fallback():
    audit = {
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "abc",
                        "timed_character_indices": [0, 1, 2],
                        "dual_audio_comparisons": [
                            _item(0, "a", 1_000, 1_250, 1_240),
                            _item(1, "b", 1_200, 1_400, 1_390),
                            _item(2, "c", 1_300, 1_450, 1_440, 0.01, 0.01),
                        ],
                    }
                ],
            }
        ]
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "abc"},
        target_song_ids=("generic-song",),
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["character_overrides_ms"] == {"0": 1_000, "1": 1_200, "2": 1_300}
    assert [item["character_index"] for item in line["monotonic_rollbacks"]] == [
        1,
        0,
    ]
    assert all(item["reason"] for item in line["monotonic_rollbacks"])
    assert line["candidate_dispositions"]["0"] == (
        "stable-ts-retained-monotonic-rollback"
    )
    assert line["candidate_dispositions"]["1"] == (
        "stable-ts-retained-monotonic-rollback"
    )
    assert list(line["character_overrides_ms"].values()) == sorted(
        line["character_overrides_ms"].values()
    )


def test_asr_veto_preserves_every_canonical_current_ms_exactly():
    audit = {
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "ab",
                        "timed_character_indices": [0, 1],
                        "dual_audio_comparisons": [
                            _item(0, "a", 1_000, 1_300, 1_290),
                            _item(1, "b", 1_200, 1_500, 1_490),
                        ],
                    }
                ],
            }
        ]
    }
    recognition = [
        _recognition_audit("stem", "support", text="ab"),
        _recognition_audit("mix", "veto", text="ab"),
    ]

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "ab"},
        target_song_ids=("generic-song",),
        recognition_audit=recognition,
        recognition_audit_relative_path=("stem.json", "mix.json"),
        recognition_audit_sha256=(None, None),
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["character_overrides_ms"] == {"0": 1_000, "1": 1_200}
    assert set(line["candidate_dispositions"].values()) == {
        "stable-ts-retained-unresolved"
    }
    assert line["review_gate"]["recognition_disposition"] == "veto"


def test_uncovered_character_is_explicitly_unresolved_and_has_no_fake_override():
    audit = {
        "songs": [
            {
                "song_id": "generic-song",
                "lines": [
                    {
                        "line_index": 0,
                        "text": "ab",
                        "timed_character_indices": [0, 1],
                        "dual_audio_comparisons": [
                            _item(0, "a", 1_000, 1_010, 1_015),
                        ],
                    }
                ],
            }
        ]
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "ab"},
        target_song_ids=("generic-song",),
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["review_status"] == "unresolved"
    assert line["unresolved_character_indices"] == [1]
    assert line["candidate_dispositions"]["1"] == "uncovered"
    assert "1" not in line["character_overrides_ms"]


def test_stem_support_plus_mix_veto_aggregates_to_veto():
    result = _build_with_recognition(
        [
            _recognition_audit("stem", "support"),
            _recognition_audit("mix", "veto"),
        ]
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["review_status"] == "unresolved"
    assert line["review_gate"]["recognition_disposition"] == "veto"
    assert line["review_gate"]["recognition_dispositions"] == {
        "stem": "support",
        "mix": "veto",
    }
    assert line["review_gate"]["ok"] is False
    assert result["gate_ok"] is False
    provenance = result["mms_provenance"]["recognition_audits"]
    assert [item["audio_kind"] for item in provenance] == ["stem", "mix"]
    assert provenance[0]["path"] == "recognition-0.json"
    assert provenance[0]["sha256"] == "report-0-sha256"
    assert provenance[0]["audio_records"][0]["audio_sha256"] == "a" * 64


def test_stem_and_mix_support_are_required_for_recognition_support():
    result = _build_with_recognition(
        [
            _recognition_audit("stem", "support"),
            _recognition_audit("mix", "support"),
        ]
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["review_status"] == "dual-audio-machine-reviewed"
    assert line["review_gate"]["recognition_disposition"] == "support"
    assert line["review_gate"]["recognition_missing_lanes"] == []
    assert line["review_gate"]["ok"] is True
    assert result["gate_ok"] is True


def test_any_non_support_lane_keeps_aggregate_unresolved():
    result = _build_with_recognition(
        [
            _recognition_audit("stem", "support"),
            _recognition_audit("mix", "unresolved"),
        ]
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["review_gate"]["recognition_disposition"] == "unresolved"
    assert line["review_gate"]["recognition_dispositions"] == {
        "stem": "support",
        "mix": "unresolved",
    }
    assert line["review_status"] == "unresolved"
    assert line["review_gate"]["ok"] is False


def test_missing_mix_is_rejected_without_review_only_opt_in():
    with pytest.raises(ValueError, match="require both stem and mix; missing: mix"):
        _build_with_recognition([_recognition_audit("stem", "support")])


def test_duplicate_recognition_audio_kind_is_rejected():
    with pytest.raises(
        ValueError, match="duplicate recognition audit audio_kind: stem"
    ):
        _build_with_recognition(
            [
                _recognition_audit("stem", "support"),
                _recognition_audit("stem", "support"),
            ]
        )


def test_single_report_remains_compatible_but_review_only_cannot_release():
    result = _build_with_recognition(
        [_recognition_audit("stem", "support")],
        allow_single=True,
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["review_status"] == "unresolved"
    assert line["review_gate"]["recognition_dispositions"] == {"stem": "support"}
    assert line["review_gate"]["recognition_disposition"] == "unresolved"
    assert line["review_gate"]["recognition_missing_lanes"] == ["mix"]
    assert line["review_gate"]["recognition_review_only"] is True
    assert line["review_gate"]["ok"] is False
    assert result["gate_ok"] is False
    assert result["mms_provenance"]["recognition_audit"] == "recognition-0.json"


def test_single_mapping_call_shape_remains_compatible_for_review_only():
    result = build_overrides(
        _single_line_audit(),
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "a"},
        target_song_ids=("generic-song",),
        recognition_audit=_recognition_audit("stem", "support"),
        recognition_audit_relative_path="stem.json",
        recognition_audit_sha256="stem-report-sha256",
        allow_single_recognition_lane_review_only=True,
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert line["review_gate"]["recognition_disposition"] == "unresolved"
    assert line["review_gate"]["ok"] is False
    assert result["mms_provenance"]["recognition_audit"] == "stem.json"


def test_recognition_audit_argument_is_repeatable_and_single_argument_parses():
    repeated = make_parser().parse_args(
        [
            "--manifest",
            "generic-album.json",
            "--recognition-audit",
            "stem.json",
            "--recognition-audit",
            "mix.json",
        ]
    )
    single = make_parser().parse_args(
        [
            "--manifest",
            "generic-album.json",
            "--recognition-audit",
            "stem.json",
        ]
    )

    assert [path.name for path in repeated.recognition_audits] == [
        "stem.json",
        "mix.json",
    ]
    assert [path.name for path in single.recognition_audits] == ["stem.json"]


def test_manifest_argument_is_required() -> None:
    with pytest.raises(SystemExit):
        make_parser().parse_args([])


def test_recognition_audit_must_match_current_lyrics_and_window():
    with pytest.raises(ValueError, match="lyrics are stale"):
        _build_with_recognition(
            [
                _recognition_audit("stem", "support", text="b"),
                _recognition_audit("mix", "support"),
            ]
        )
    with pytest.raises(ValueError, match="window is stale"):
        _build_with_recognition(
            [
                _recognition_audit("stem", "support", start_ms=901),
                _recognition_audit("mix", "support"),
            ]
        )


def test_recognition_lyrics_hash_mismatch_is_record_only():
    stale_hash = _recognition_audit("stem", "support")
    stale_hash["language"] = "zh"
    stale_hash["lyrics_sha256"] = "0" * 64

    result = _build_with_recognition([stale_hash, _recognition_audit("mix", "support")])

    assert result["gate_ok"] is True
    assert (
        result["mms_provenance"]["recognition_audits"][0]["lyrics_sha256"] == "0" * 64
    )


def test_mix_recognition_audio_path_must_match_current_manifest_audio(tmp_path):
    current_mix = tmp_path / "current.flac"
    audited_mix = tmp_path / "audited.flac"
    current_mix.write_bytes(b"current mix")
    audited_mix.write_bytes(b"stale mix")
    report = _recognition_audit("mix", "support")
    model = tmp_path / "base.pt"
    model.write_bytes(b"model")
    report["model_path"] = str(model)
    report["audio"] = {
        "path": str(audited_mix),
        "sha256": hashlib.sha256(b"stale mix").hexdigest(),
    }
    album = SimpleNamespace(
        project_root=tmp_path,
        tracks=[SimpleNamespace(song_id="generic-song", audio_path=current_mix)],
    )

    with pytest.raises(ValueError, match="path mismatch"):
        validate_recognition_audio_sources(
            [report],
            album,
            ("generic-song",),
        )


def test_recognition_audio_model_report_and_cache_hashes_are_record_only(tmp_path):
    current_mix = tmp_path / "current.flac"
    model = tmp_path / "base.pt"
    current_mix.write_bytes(b"current mix")
    model.write_bytes(b"model")
    report = _recognition_audit("mix", "support")
    report["audio"] = {"path": str(current_mix), "sha256": "0" * 64}
    report["model_path"] = str(model)
    report["model_sha256"] = "1" * 64
    report["cache"] = {"key": "2" * 64, "sha256": "3" * 64}
    album = SimpleNamespace(
        project_root=tmp_path,
        tracks=[SimpleNamespace(song_id="generic-song", audio_path=current_mix)],
    )

    validate_recognition_audio_sources([report], album, ("generic-song",))
    result = build_overrides(
        _single_line_audit(),
        {"songs": {}},
        audit_relative_path="audit.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={
            ("generic-song", 0): _single_line_audit()["songs"][0]["lines"][0]["text"]
        },
        target_song_ids=("generic-song",),
        recognition_audit=[report, _recognition_audit("stem", "support")],
        recognition_audit_relative_path=("mix.json", "stem.json"),
        recognition_audit_sha256=("stale-report-hash",),
    )

    assert result["gate_ok"] is True
    assert result["mms_provenance"]["recognition_audits"][0]["sha256"] == (
        "stale-report-hash"
    )
    assert result["mms_provenance"]["recognition_audits"][1]["sha256"] is None


def test_mms_audit_paths_bind_model_sug_mix_and_vocals_while_hashes_are_record_only(
    tmp_path,
):
    project_root = tmp_path
    deliverable_dir = project_root / "deliverables" / "album"
    source_dir = deliverable_dir / "sources"
    timing_dir = deliverable_dir / "timing"
    vocals = project_root / "evidence" / "Vocals.wav"
    mix = project_root / "audio" / "mix.flac"
    model = project_root / "models" / "mms.pt"
    manifest = project_root / "album.json"
    source = source_dir / "lyrics.json"
    corrections = source_dir / "lyric_corrections.json"
    sug = timing_dir / "song.sug"
    for path, payload in (
        (manifest, b"manifest"),
        (source, b"source"),
        (corrections, b"corrections"),
        (sug, b"sug"),
        (vocals, b"vocals"),
        (mix, b"mix"),
        (model, b"model"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    album = SimpleNamespace(
        project_root=project_root,
        deliverable_dir=deliverable_dir,
        tracks=[SimpleNamespace(song_id="song", timing_stem="song", audio_path=mix)],
    )
    audit = {
        "manifest_path": str(manifest),
        "manifest_sha256": digest(manifest),
        "lyric_source_path": str(source),
        "lyric_source_sha256": digest(source),
        "lyric_corrections_path": str(corrections),
        "lyric_corrections_sha256": digest(corrections),
        "model_path": model.relative_to(project_root).as_posix(),
        "model_sha256": digest(model),
        "songs": [
            {
                "song_id": "song",
                "sug_path": sug.relative_to(project_root).as_posix(),
                "sug_sha256": digest(sug),
                "mix_path": mix.relative_to(project_root).as_posix(),
                "mix_sha256": digest(mix),
                "vocals_path": vocals.relative_to(project_root).as_posix(),
                "vocals_sha256": digest(vocals),
            }
        ],
    }

    validate_audit_source_hashes(
        audit,
        album,
        manifest,
        ("song",),
        source,
    )
    vocals.write_bytes(b"changed vocals")
    audit["model_sha256"] = "0" * 64
    audit["songs"][0]["sug_sha256"] = "1" * 64
    audit["songs"][0]["mix_sha256"] = "2" * 64
    audit["songs"][0]["vocals_sha256"] = "3" * 64

    validate_audit_source_hashes(audit, album, manifest, ("song",), source)


def test_mms_audit_path_mismatch_still_blocks(tmp_path):
    project_root = tmp_path
    deliverable_dir = project_root / "deliverables" / "album"
    source_dir = deliverable_dir / "sources"
    timing_dir = deliverable_dir / "timing"
    files = {
        "manifest": project_root / "album.json",
        "source": source_dir / "lyrics.json",
        "corrections": source_dir / "lyric_corrections.json",
        "sug": timing_dir / "song.sug",
        "mix": project_root / "audio" / "mix.flac",
        "wrong_mix": project_root / "audio" / "wrong.flac",
        "vocals": project_root / "evidence" / "Vocals.wav",
        "model": project_root / "models" / "mms.pt",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"non-empty")
    album = SimpleNamespace(
        project_root=project_root,
        deliverable_dir=deliverable_dir,
        tracks=[
            SimpleNamespace(song_id="song", timing_stem="song", audio_path=files["mix"])
        ],
    )
    audit = {
        "manifest_path": str(files["manifest"]),
        "lyric_source_path": str(files["source"]),
        "lyric_corrections_path": str(files["corrections"]),
        "model_path": str(files["model"]),
        "songs": [
            {
                "song_id": "song",
                "sug_path": str(files["sug"]),
                "mix_path": str(files["wrong_mix"]),
                "vocals_path": str(files["vocals"]),
            }
        ],
    }

    with pytest.raises(ValueError, match="mix.*path mismatch"):
        validate_audit_source_hashes(
            audit, album, files["manifest"], ("song",), files["source"]
        )


def test_mms_audit_explicit_sug_provenance_replaces_manifest_canonical(tmp_path):
    project_root = tmp_path
    deliverable_dir = project_root / "deliverables" / "album"
    source_dir = deliverable_dir / "sources"
    files = {
        "manifest": project_root / "album.json",
        "source": source_dir / "lyrics.json",
        "corrections": source_dir / "lyric_corrections.json",
        "canonical_sug": deliverable_dir / "timing" / "song.sug",
        "explicit_sug": project_root / "private" / "initial.sug",
        "mix": project_root / "audio" / "mix.flac",
        "vocals": project_root / "evidence" / "Vocals.wav",
        "model": project_root / "models" / "mms.pt",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"non-empty")
    album = SimpleNamespace(
        project_root=project_root,
        deliverable_dir=deliverable_dir,
        tracks=[
            SimpleNamespace(song_id="song", timing_stem="song", audio_path=files["mix"])
        ],
    )
    audit = {
        "manifest_path": str(files["manifest"]),
        "lyric_source_path": str(files["source"]),
        "lyric_corrections_path": str(files["corrections"]),
        "model_path": str(files["model"]),
        "songs": [
            {
                "song_id": "song",
                "sug_path": str(files["explicit_sug"]),
                "mix_path": str(files["mix"]),
                "vocals_path": str(files["vocals"]),
            }
        ],
    }

    validate_audit_source_hashes(
        audit,
        album,
        files["manifest"],
        ("song",),
        files["source"],
        files["explicit_sug"],
    )

    with pytest.raises(ValueError, match="SUG.*path mismatch"):
        validate_audit_source_hashes(
            audit,
            album,
            files["manifest"],
            ("song",),
            files["source"],
        )


def test_invalid_schema_and_timeline_semantics_still_block():
    audit = _single_line_audit()
    audit["schema_version"] = "karaoke-mms-dual-audio-audit/v999"
    with pytest.raises(ValueError, match="unsupported MMS audit schema"):
        build_overrides(
            audit,
            {"songs": {}},
            audit_relative_path="audit.json",
            line_windows={("generic-song", 0): (900, 2_000)},
            line_texts={("generic-song", 0): audit["songs"][0]["lines"][0]["text"]},
            target_song_ids=("generic-song",),
        )

    audit.pop("schema_version")
    with pytest.raises(ValueError, match="invalid timeline window"):
        build_overrides(
            audit,
            {"songs": {}},
            audit_relative_path="audit.json",
            line_windows={("generic-song", 0): (2_000, 2_000)},
            line_texts={("generic-song", 0): audit["songs"][0]["lines"][0]["text"]},
            target_song_ids=("generic-song",),
        )


def test_run_build_new_output_does_not_merge_canonical(monkeypatch, tmp_path):
    import scripts.build_karaoke_mms_overrides as build_module

    deliverable_dir = tmp_path / "deliverables" / "album"
    source_dir = deliverable_dir / "sources"
    source_dir.mkdir(parents=True)
    manifest = tmp_path / "album.json"
    audit_path = source_dir / "audit.json"
    source_path = source_dir / "lyrics.json"
    output_path = tmp_path / "dedicated" / "overrides.json"
    canonical_path = source_dir / "timing_overrides.json"
    for path in (manifest, audit_path, source_path, canonical_path):
        path.write_text("{}", encoding="utf-8")

    album = SimpleNamespace(
        project_root=tmp_path,
        deliverable_dir=deliverable_dir,
        tracks=[SimpleNamespace(song_id="song")],
    )
    audit = {"songs": [{"song_id": "song", "lines": [{"line_index": 0}]}]}
    loaded_paths = []

    def fake_load(path):
        path = path.resolve()
        loaded_paths.append(path)
        if path == canonical_path.resolve():
            raise AssertionError("new output must not read the canonical overrides")
        if path == audit_path.resolve():
            return audit
        return {"songs": {}}

    captured = {}

    def fake_build(audit_document, existing, **kwargs):
        captured["audit"] = audit_document
        captured["existing"] = existing
        captured["target_song_ids"] = kwargs["target_song_ids"]
        return {
            "schema_version": "karaoke-timing-overrides/v2",
            "mms_provenance": {"target_song_ids": ["song"]},
            "gate_ok": False,
            "unresolved": [{"song_id": "song", "lines": [0]}],
            "songs": {"song": {"lines": {"0": {"review_status": "unresolved"}}}},
        }

    monkeypatch.setattr(build_module, "load_album_manifest", lambda *_a, **_k: album)
    monkeypatch.setattr(build_module, "_load", fake_load)
    monkeypatch.setattr(
        build_module, "validate_audit_source_hashes", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        build_module,
        "validate_recognition_audio_sources",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        build_module,
        "build_line_windows",
        lambda *_a, **_k: ({("song", 0): (0, 1_000)}, {("song", 0): "x"}),
    )
    monkeypatch.setattr(build_module, "build_overrides", fake_build)

    result = run_build(
        manifest_path=manifest,
        source_path=source_path,
        audit_path=audit_path,
        output_path=output_path,
        song_ids=("song",),
        allow_partial_manifest=True,
    )

    assert canonical_path.resolve() not in loaded_paths
    assert captured["existing"] == {"songs": {}}
    assert captured["target_song_ids"] == ("song",)
    assert result["schema_version"] == "karaoke-timing-overrides/v2"
    assert output_path.is_file()


def test_run_build_rejects_ambiguous_explicit_sug_for_multiple_songs(
    monkeypatch, tmp_path
):
    import scripts.build_karaoke_mms_overrides as build_module

    manifest = tmp_path / "album.json"
    audit_path = tmp_path / "audit.json"
    manifest.write_text("{}", encoding="utf-8")
    audit_path.write_text("{}", encoding="utf-8")
    album = SimpleNamespace(
        project_root=tmp_path,
        deliverable_dir=tmp_path / "deliverables" / "album",
        tracks=[SimpleNamespace(song_id="one"), SimpleNamespace(song_id="two")],
    )
    audit = {
        "songs": [
            {"song_id": "one", "lines": []},
            {"song_id": "two", "lines": []},
        ]
    }
    monkeypatch.setattr(build_module, "load_album_manifest", lambda *_a, **_k: album)
    monkeypatch.setattr(build_module, "_load", lambda _path: audit)
    monkeypatch.setattr(build_module, "_audit_contract", lambda *_a, **_k: None)

    with pytest.raises(ValueError, match="exactly one selected song"):
        run_build(
            manifest_path=manifest,
            audit_path=audit_path,
            sug_path=tmp_path / "private.sug",
            song_ids=("one", "two"),
        )


def test_synthetic_v2_audit_line_parses_and_builds_on_the_source_token_axis(
    tmp_path, monkeypatch
):
    import soundfile

    from scripts import audit_karaoke_mms_alignment as audit_module

    project_root = tmp_path / "project"
    deliverable_dir = project_root / "deliverables" / "album"
    timing_dir = deliverable_dir / "timing"
    timing_dir.mkdir(parents=True)
    mix_path = project_root / "audio" / "english.flac"
    mix_path.parent.mkdir()
    mix_path.write_bytes(b"mix")
    vocals_root = project_root / ".cache" / "msst-vocals"
    vocals_path = vocals_root / mix_path.stem / "Vocals.wav"
    vocals_path.parent.mkdir(parents=True)
    vocals_path.write_bytes(b"vocals")
    characters = [
        {"char": "alpha", "timestamps": [1_000]},
        {"char": " ", "timestamps": []},
        {"char": "beta!", "timestamps": [1_500], "sentence_end_ts": 2_200},
    ]
    sug_path = timing_dir / "english.sug"
    sug_path.write_text(
        json.dumps(
            {
                "metadata": {"language": "en"},
                "sentences": [{"characters": characters}],
            }
        ),
        encoding="utf-8",
    )
    track = SimpleNamespace(
        song_id="generic-song-en",
        title="Generic English Title",
        language="en",
        timing_stem="english",
        audio_path=mix_path,
    )
    album = SimpleNamespace(
        project_root=project_root,
        deliverable_dir=deliverable_dir,
    )

    class FakeAudio:
        samplerate = 1_000

        def __len__(self):
            return 5_000

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(soundfile, "SoundFile", lambda _path: FakeAudio())
    alignment_calls = 0

    def fake_align_audio_units(
        _audio,
        _crop_start_ms,
        _crop_end_ms,
        units,
        _runtime,
        *,
        index_field,
    ):
        nonlocal alignment_calls
        offset = alignment_calls * 10
        alignment_calls += 1
        return [
            {
                "unit": item["unit"],
                index_field: item[index_field],
                "start_ms": int(characters[item[index_field]]["timestamps"][0])
                + offset,
                "end_ms": int(characters[item[index_field]]["timestamps"][0])
                + offset
                + 120,
                "score": 0.95,
            }
            for item in units
        ]

    monkeypatch.setattr(audit_module, "align_audio_units", fake_align_audio_units)
    song = audit_module.audit_track(
        track,
        album,
        SimpleNamespace(allowed_units=None),
        vocals_root,
        schema_version=audit_module.SCHEMA_VERSION_V2,
    )
    line = song["lines"][0]

    assert line["source_token_display_mapping"] == [
        {"source_token_index": 0, "source_token_display": "alpha"},
        {"source_token_index": 1, "source_token_display": " "},
        {"source_token_index": 2, "source_token_display": "beta!"},
    ]
    assert _v2_token_display_mapping(
        line,
        song_id=track.song_id,
        line_index=0,
    ) == {0: "alpha", 1: " ", 2: "beta!"}

    result = build_overrides(
        {
            "schema_version": AUDIT_SCHEMA_V2,
            "language_codes": {track.song_id: "en"},
            "songs": [song],
        },
        {"songs": {}},
        audit_relative_path="audit-v2.json",
        line_windows={(track.song_id, 0): (900, 2_500)},
        line_texts={(track.song_id, 0): "alpha beta!"},
        target_song_ids=(track.song_id,),
    )

    built_line = result["songs"][track.song_id]["lines"]["0"]
    assert built_line["character_overrides_ms"] == {"0": 1_000, "2": 1_500}
    assert built_line["review_gate"]["ok"] is True
    assert result["mms_provenance"]["audit_schema_version"] == AUDIT_SCHEMA_V2


def test_v2_build_uses_source_token_axis_and_keeps_ja_exceptions_out_of_en():
    audit = {
        "schema_version": AUDIT_SCHEMA_V2,
        "language_codes": {"generic-song-c": "en"},
        "songs": [
            {
                "song_id": "generic-song-c",
                "language": "en",
                "lines": [
                    {
                        "line_index": 3,
                        "language": "en",
                        "text": "foo",
                        "timed_source_token_indices": [8],
                        "source_token_display_mapping": {"8": "foo"},
                        "dual_audio_comparisons": [
                            _v2_item(
                                8,
                                "foo",
                                1_000,
                                1_500,
                                1_500,
                                vocal_score=0.01,
                                mix_score=0.01,
                            )
                        ],
                    }
                ],
            }
        ],
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit-v2.json",
        line_windows={("generic-song-c", 3): (900, 2_000)},
        line_texts={("generic-song-c", 3): "foo"},
        target_song_ids=("generic-song-c",),
    )

    line = result["songs"]["generic-song-c"]["lines"]["3"]
    assert result["mms_provenance"]["audit_schema_version"] == AUDIT_SCHEMA_V2
    assert line["character_overrides_ms"] == {"8": 1_000}
    assert line["review_status"] == "unresolved"
    assert "low-vocal-confidence" in line["candidate_failure_reasons"]["8"]
    assert "low-mix-confidence" in line["candidate_failure_reasons"]["8"]
    assert line["review_gate"]["ok"] is False


def test_zh_and_en_do_not_activate_japanese_exception_lanes():
    item = _v2_item(
        8,
        "foo",
        1_000,
        1_500,
        1_500,
        vocal_score=0.01,
        mix_score=0.01,
    )

    assert _accepted("generic-song-c", 3, item, language="zh") is False
    assert _accepted("generic-song-c", 3, item, language="en") is False


@pytest.mark.parametrize("language", ["zh", "en"])
def test_non_japanese_v2_uses_the_generic_vocal_candidate(language):
    audit = {
        "schema_version": AUDIT_SCHEMA_V2,
        "language_codes": {"generic-song-c": language},
        "songs": [
            {
                "song_id": "generic-song-c",
                "language": language,
                "lines": [
                    {
                        "line_index": 3,
                        "language": language,
                        "text": "foo",
                        "timed_source_token_indices": [8],
                        "source_token_display_mapping": {"8": "foo"},
                        "dual_audio_comparisons": [
                            _v2_item(8, "foo", 1_000, 1_500, 1_600)
                        ],
                    }
                ],
            }
        ],
    }

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit-v2.json",
        line_windows={("generic-song-c", 3): (900, 2_000)},
        line_texts={("generic-song-c", 3): "foo"},
        target_song_ids=("generic-song-c",),
    )

    assert result["songs"]["generic-song-c"]["lines"]["3"]["character_overrides_ms"] == {
        "8": 1_500
    }


def test_non_japanese_v2_audit_rejects_small_kana_disposition():
    audit = {
        "schema_version": AUDIT_SCHEMA_V2,
        "language_codes": {"generic-song-c": "zh"},
        "songs": [
            {
                "song_id": "generic-song-c",
                "language": "zh",
                "lines": [
                    {
                        "line_index": 0,
                        "language": "zh",
                        "text": "xy",
                        "timed_source_token_indices": [0],
                        "source_token_display_mapping": {"0": "x"},
                        "dual_audio_comparisons": [
                            _v2_item(
                                0,
                                "x",
                                1_000,
                                1_010,
                                1_015,
                                alignment_disposition=(
                                    "mora-joining-small-kana-inherits-0"
                                ),
                            )
                        ],
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="Japanese small-kana"):
        build_overrides(
            audit,
            {"songs": {}},
            audit_relative_path="audit-v2.json",
            line_windows={("generic-song-c", 0): (900, 2_000)},
            line_texts={("generic-song-c", 0): "xy"},
            target_song_ids=("generic-song-c",),
        )


def test_japanese_v1_still_uses_character_axis_and_records_v1_provenance():
    audit = _single_line_audit()
    audit["schema_version"] = AUDIT_SCHEMA_V1
    audit["songs"][0]["language"] = "ja"

    result = build_overrides(
        audit,
        {"songs": {}},
        audit_relative_path="audit-v1.json",
        line_windows={("generic-song", 0): (900, 2_000)},
        line_texts={("generic-song", 0): "a"},
        target_song_ids=("generic-song",),
    )

    line = result["songs"]["generic-song"]["lines"]["0"]
    assert result["mms_provenance"]["audit_schema_version"] == AUDIT_SCHEMA_V1
    assert line["character_overrides_ms"] == {"0": 1_010}
    assert "source_token_index" not in line["character_overrides_ms"]
