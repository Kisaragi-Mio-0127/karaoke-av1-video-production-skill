from types import SimpleNamespace

import pytest

from scripts.audit_karaoke_mms_alignment import (
    _MORA_JOINING_SMALL_KANA,
    ALIGNMENT_EVIDENCE_CONTRACT,
    SCHEMA_VERSION_V2,
    UNIT_OVERRIDES_SCHEMA_VERSION,
    _report_gate_ok,
    _validate_mms_model_access,
    build_alignment_input_units,
    build_comparisons,
    build_dual_audio_comparisons,
    build_parser,
    crop_window_ms,
    inherit_display_group_candidates,
    inherit_small_kana_candidates,
    japanese_line_units,
    line_units,
    normalize_song_ids,
    normalize_unit_overrides,
    select_tracks,
    sentence_release_ms,
    validate_mms_units,
)


def test_forced_alignment_contract_is_not_described_as_independent_phoneme_asr():
    assert ALIGNMENT_EVIDENCE_CONTRACT["stable_ts"]["independent_recognition"] is False
    assert ALIGNMENT_EVIDENCE_CONTRACT["mms_fa"]["independent_recognition"] is False
    assert ALIGNMENT_EVIDENCE_CONTRACT["visual_interpolation"]["phoneme_alignment"] is False


def test_mms_model_access_is_offline_and_fail_closed_by_default(tmp_path):
    missing = tmp_path / "missing-model.pt"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        _validate_mms_model_access(missing, allow_network=True)

    model = tmp_path / "model.pt"
    model.write_bytes(b"local MMS checkpoint")
    assert _validate_mms_model_access(model, allow_network=False) == model.resolve()


def test_empty_audit_collection_cannot_pass_gate():
    assert _report_gate_ok([], 0) is False
    assert _report_gate_ok([{"gate_ok": True}], 0) is True
    assert _report_gate_ok([{"gate_ok": False}], 0) is False


def _character(char, timestamp=None, release=None):
    return {
        "char": char,
        "timestamps": [] if timestamp is None else [timestamp],
        "sentence_end_ts": release,
    }


def _unit(character_index, start_ms=1_000, score=0.8):
    return {
        "unit": "a",
        "character_index": character_index,
        "start_ms": start_ms,
        "end_ms": start_ms + 100,
        "score": score,
    }


def test_sentence_release_uses_non_timed_punctuation_release():
    characters = [
        _character("歌", 1_000),
        _character("う", 1_200),
        _character("？", release=1_650),
    ]

    assert sentence_release_ms(characters) == 1_650
    assert crop_window_ms(characters, 3_000) == (500, 2_650)


def test_sentence_release_falls_back_only_when_no_release_metadata_exists():
    characters = [_character("歌", 1_000), _character("う", 1_200)]

    assert sentence_release_ms(characters) == 1_200
    assert crop_window_ms(characters, 3_000) == (500, 2_200)


def test_small_kana_inherits_the_previous_mora_candidate():
    text = "ねぇ"
    units = [_unit(0, start_ms=1_234, score=0.456789)]

    candidates = inherit_small_kana_candidates(text, units)

    assert "っ" not in _MORA_JOINING_SMALL_KANA
    assert candidates[1] == {
        "start_ms": 1_234,
        "end_ms": 1_334,
        "score": 0.456789,
        "inherited_from_character_index": 0,
    }


def test_small_kana_overrides_an_independently_allocated_candidate():
    candidates = inherit_small_kana_candidates(
        "ちょ",
        [_unit(0, start_ms=1_000), _unit(1, start_ms=1_240)],
    )

    assert candidates[1]["start_ms"] == 1_000
    assert candidates[1]["inherited_from_character_index"] == 0


def test_consecutive_numeric_glyphs_share_a_general_romanizer_onset():
    candidates = inherit_display_group_candidates(
        "123甲",
        [
            _unit(0, start_ms=2_000, score=0.9),
            _unit(2, start_ms=2_240, score=0.8),
            _unit(3, start_ms=2_500, score=0.95),
        ],
    )

    assert [candidates[index]["start_ms"] for index in range(3)] == [
        2_000,
        2_000,
        2_000,
    ]
    assert candidates[1]["inherited_from_character_index"] == 0
    assert candidates[2]["end_ms"] == 2_340
    assert candidates[3]["start_ms"] == 2_500


def test_comparisons_mark_inherited_small_kana_and_dual_lane_sources():
    characters = [_character("ね", 1_000), _character("ぇ", 1_100)]
    vocal_units = [_unit(0, start_ms=1_234, score=0.7)]
    mix_units = [_unit(0, start_ms=1_250, score=0.6)]

    comparisons = build_comparisons(characters, vocal_units)
    paired = build_dual_audio_comparisons(
        characters,
        comparisons,
        vocal_units,
        mix_units,
    )

    vocal_small = next(item for item in comparisons if item["character_index"] == 1)
    dual_small = next(item for item in paired if item["character_index"] == 1)
    assert vocal_small["mms_ms"] == 1_234
    assert vocal_small["mms_inherited_from_character_index"] == 0
    assert dual_small["vocal_mms_ms"] == 1_234
    assert dual_small["vocal_mms_end_ms"] == 1_334
    assert dual_small["mix_mms_ms"] == 1_250
    assert dual_small["mix_mms_inherited_from_character_index"] == 0


def test_missing_timed_character_candidate_is_not_silently_skipped():
    characters = [_character("歌", 1_000), _character("う", 1_100)]

    import pytest

    with pytest.raises(ValueError, match="lacks candidate.*1"):
        build_comparisons(characters, [_unit(0)])


def test_missing_mix_candidate_is_not_silently_skipped():
    characters = [_character("歌", 1_000)]
    comparisons = build_comparisons(characters, [_unit(0)])

    import pytest

    with pytest.raises(ValueError, match="original mix"):
        build_dual_audio_comparisons(characters, comparisons, [_unit(0)], [])


def test_ascii_fallback_is_an_explicit_stable_ts_disposition():
    characters = [_character("1", 1_000)]
    units, retained = validate_mms_units("1", [("x", 0)])
    comparisons = build_comparisons(
        characters,
        units,
        retained_character_indices=retained,
    )
    paired = build_dual_audio_comparisons(characters, comparisons, units, units)

    assert units == []
    assert retained == frozenset({0})
    assert paired[0]["alignment_disposition"] == "stable-ts-retained-ascii"
    assert paired[0]["vocal_mms_ms"] == 1_000


def test_non_ascii_fallback_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="unsupported MMS fallback"):
        validate_mms_units("☆", [("x", 0)])


def test_song_id_selection_is_manifest_ordered_and_supports_remaining_tracks():
    tracks = tuple(
        SimpleNamespace(song_id=song_id)
        for song_id in (
            "track-a",
            "track-b",
            "track-c",
            "track-d",
            "track-e",
        )
    )

    assert normalize_song_ids(["track-c,track-e", "track-c"]) == (
        "track-c",
        "track-e",
    )
    assert [
        track.song_id for track in select_tracks(tracks, ["track-e", "track-c"])
    ] == ["track-c", "track-e"]


def test_japanese_line_units_prioritize_canonical_ruby_then_fallback(monkeypatch):
    class Converter:
        def convert(self, text):
            if text == "仮ナ":
                return [
                    {"orig": "仮", "hira": "かり", "hepburn": "kari"},
                    {"orig": "ナ", "hira": "な", "hepburn": "na"},
                ]
            roman = {"か": "ka", "り": "ri", "よ": "yo", "み": "mi", "な": "na"}
            return [{"orig": text, "hira": text, "hepburn": roman.get(text, text)}]

    class Helper:
        _converter = Converter()

        def weight(self, _char):
            return 1.0

    monkeypatch.setattr(
        "scripts.audit_karaoke_mms_alignment._romanizer",
        lambda: Converter(),
    )

    sentence = {
        "id": "synthetic-line",
        "characters": [
            {
                **_character("仮", 1_000),
                "ruby": {"parts": [{"text": "よみ", "offset_ms": 0}]},
            },
            _character("ナ", 1_200),
        ],
    }

    assert japanese_line_units(sentence, Helper()) == [
        ("yo", 0),
        ("mi", 0),
        ("na", 1),
    ]


def test_line_units_does_not_allocate_middle_dot_as_a_mora():
    from scripts.karaoke_timing import ReadingHelper

    original = "カ・キ"
    assert line_units(original, ReadingHelper()) == [
        ("ka", 0),
        ("ki", 2),
    ]


def test_cli_requires_an_explicit_manifest():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_run_audit_forwards_explicit_mms_model_permissions(monkeypatch, tmp_path):
    import scripts.audit_karaoke_mms_alignment as audit_module

    model_path = tmp_path / "private-model.pt"
    captured = {}

    monkeypatch.setattr(
        audit_module,
        "load_album_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(
            project_root=tmp_path,
            tracks=(SimpleNamespace(song_id="song", language="zh"),),
        ),
    )

    class StopAfterModelForwardingError(Exception):
        pass

    def fake_load_runtime(project_root, *, model_path, allow_network):
        captured.update(
            project_root=project_root,
            model_path=model_path,
            allow_network=allow_network,
        )
        raise StopAfterModelForwardingError

    monkeypatch.setattr(audit_module, "load_mms_runtime", fake_load_runtime)

    with pytest.raises(StopAfterModelForwardingError):
        audit_module.run_audit(
            manifest_path=tmp_path / "album.json",
            model_path=model_path,
            allow_network=True,
            allow_partial_manifest=True,
        )

    assert captured == {
        "project_root": tmp_path,
        "model_path": model_path,
        "allow_network": True,
    }


def test_run_audit_forwards_and_records_single_explicit_sug(monkeypatch, tmp_path):
    import scripts.audit_karaoke_mms_alignment as audit_module

    deliverable_dir = tmp_path / "deliverables" / "album"
    source_dir = deliverable_dir / "sources"
    source_dir.mkdir(parents=True)
    manifest = tmp_path / "album.json"
    source = source_dir / "lyrics.json"
    corrections = source_dir / "lyric_corrections.json"
    model = tmp_path / "model.pt"
    explicit_sug = tmp_path / "private" / "initial.sug"
    output = tmp_path / "audit.json"
    for path in (manifest, source, corrections, model, explicit_sug):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    track = SimpleNamespace(song_id="song", language="zh")
    album = SimpleNamespace(
        project_root=tmp_path,
        deliverable_dir=deliverable_dir,
        tracks=(track,),
    )
    runtime = SimpleNamespace(model_path=model)
    captured = {}

    def fake_audit_track(_track, _album, _runtime, _vocals_root, *, sug_path, **_kwargs):
        captured["sug_path"] = sug_path
        return {
            "song_id": "song",
            "language": "zh",
            "language_identity": audit_module.language_identity("zh"),
            "sug_path": audit_module._report_path(sug_path, tmp_path),
            "lines": [],
            "unresolved": [],
            "unresolved_count": 0,
            "gate_ok": False,
        }

    monkeypatch.setattr(audit_module, "load_album_manifest", lambda *_a, **_k: album)
    monkeypatch.setattr(audit_module, "load_mms_runtime", lambda *_a, **_k: runtime)
    monkeypatch.setattr(audit_module, "audit_track", fake_audit_track)

    report = audit_module.run_audit(
        manifest_path=manifest,
        source_path=source,
        sug_path=explicit_sug,
        output_path=output,
        song_ids=("song",),
        allow_partial_manifest=True,
    )

    assert captured["sug_path"] == explicit_sug.resolve()
    assert report["songs"][0]["sug_path"] == "private/initial.sug"


def test_run_audit_rejects_ambiguous_explicit_sug_for_multiple_tracks(
    monkeypatch, tmp_path
):
    import scripts.audit_karaoke_mms_alignment as audit_module

    album = SimpleNamespace(
        project_root=tmp_path,
        tracks=(
            SimpleNamespace(song_id="one", language="zh"),
            SimpleNamespace(song_id="two", language="en"),
        ),
    )
    monkeypatch.setattr(audit_module, "load_album_manifest", lambda *_a, **_k: album)

    with pytest.raises(ValueError, match="exactly one selected song"):
        audit_module.run_audit(
            manifest_path=tmp_path / "album.json",
            sug_path=tmp_path / "private.sug",
            allow_partial_manifest=True,
        )


def test_v2_english_sug_word_axis_maps_words_to_sug_token_indices():
    characters = [
        _character("Hello", 1_000),
        _character(" "),
        _character("world!", 2_000),
    ]

    units = build_alignment_input_units(
        characters,
        language="en",
        song_id="english-song",
        line_index=0,
    )

    assert units == [
        {
            "source_token_index": 0,
            "source_text": "Hello",
            "timed": True,
            "alignment_text": "hello",
            "provenance": "sug-word-token",
        },
        {
            "source_token_index": 1,
            "source_text": " ",
            "timed": False,
            "alignment_text": "",
            "provenance": "non-acoustic-display-token",
        },
        {
            "source_token_index": 2,
            "source_text": "world!",
            "timed": True,
            "alignment_text": "world",
            "provenance": "sug-word-token",
        },
    ]
    assert [item["source_token_index"] for item in units if item["alignment_text"]] == [
        0,
        2,
    ]


def test_v2_english_contraction_is_one_mms_unit():
    units = build_alignment_input_units(
        [_character("wouldn't", 1_000)],
        language="en",
        song_id="english-song",
        line_index=0,
    )

    assert [item["alignment_text"] for item in units if item["alignment_text"]] == [
        "wouldn't"
    ]


@pytest.mark.parametrize(
    ("characters", "error"),
    [
        ([_character("Hello world", 1_000)], "contains multiple English words"),
        (
            [_character(char, 1_000 + index * 100) for index, char in enumerate("Hello")],
            "letter-level or multi-token English word axis is not allowed",
        ),
    ],
)
def test_v2_english_multiword_or_letter_level_sug_tokens_fail(characters, error):
    with pytest.raises(ValueError, match=error):
        build_alignment_input_units(
            characters,
            language="en",
            song_id="english-song",
            line_index=0,
        )


def test_v2_chinese_context_uses_chongqing_reading():
    characters = [_character("重", 1_000), _character("庆", 1_200)]

    units = build_alignment_input_units(
        characters,
        language="zh",
        song_id="chinese-song",
        line_index=3,
    )

    assert [item["alignment_text"] for item in units] == ["chong", "qing"]
    assert [item["provenance"] for item in units] == [
        "contextual-pypinyin",
        "contextual-pypinyin",
    ]


def test_v2_unit_override_is_scoped_to_song_line_and_token():
    override_document = {
        "schema_version": UNIT_OVERRIDES_SCHEMA_VERSION,
        "overrides": [
            {
                "song_id": "chinese-song",
                "line_index": 3,
                "token_index": 0,
                "unit": "zhong",
            }
        ],
    }
    overrides = normalize_unit_overrides(override_document)
    matched: set[tuple[str, int, int]] = set()

    overridden = build_alignment_input_units(
        [_character("重", 1_000), _character("庆", 1_200)],
        language="zh",
        song_id="chinese-song",
        line_index=3,
        unit_overrides=overrides,
        matched_override_keys=matched,
    )
    other_song = build_alignment_input_units(
        [_character("重", 1_000), _character("庆", 1_200)],
        language="zh",
        song_id="other-song",
        line_index=3,
        unit_overrides=overrides,
    )

    assert overridden[0]["alignment_text"] == "zhong"
    assert overridden[0]["provenance"] == "explicit-unit-override"
    assert overridden[1]["alignment_text"] == "qing"
    assert other_song[0]["alignment_text"] == "chong"
    assert matched == {("chinese-song", 3, 0)}


def _patch_fake_v2_audit(monkeypatch, tmp_path, *, mark_override: bool):
    import scripts.audit_karaoke_mms_alignment as audit_module
    from scripts.karaoke_language import language_identity

    deliverable_dir = tmp_path / "deliverables" / "album"
    source_dir = deliverable_dir / "sources"
    source_dir.mkdir(parents=True)
    manifest = tmp_path / "album.json"
    source = source_dir / "lyrics.json"
    corrections = source_dir / "lyric_corrections.json"
    model = tmp_path / "model.pt"
    for path, value in (
        (manifest, "{}"),
        (source, '{"songs": {}}'),
        (corrections, "{}"),
        (model, "model"),
    ):
        path.write_text(value, encoding="utf-8")

    track = SimpleNamespace(song_id="chinese-song", language="zh")
    album = SimpleNamespace(
        project_root=tmp_path,
        deliverable_dir=deliverable_dir,
        tracks=(track,),
    )
    runtime = SimpleNamespace(model_path=model)

    def fake_audit_track(
        _track,
        _album,
        _runtime,
        _vocals_root,
        *,
        matched_override_keys,
        **_kwargs,
    ):
        if mark_override:
            matched_override_keys.add(("chinese-song", 0, 0))
        return {
            "song_id": "chinese-song",
            "language": "zh",
            "language_identity": language_identity("zh"),
            "lines": [
                {
                    "line_index": 0,
                    "text": "重庆",
                    "language": "zh",
                    "phoneme_alignment": False,
                    "unresolved": False,
                }
            ],
            "line_count": 1,
            "timed_source_token_count": 2,
            "unresolved": [],
            "unresolved_count": 0,
            "gate_ok": True,
        }

    monkeypatch.setattr(
        audit_module,
        "load_album_manifest",
        lambda *_args, **_kwargs: album,
    )
    monkeypatch.setattr(audit_module, "load_mms_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(audit_module, "audit_track", fake_audit_track)
    return manifest, source, model


def test_v2_report_explicitly_marks_phoneme_alignment_false(monkeypatch, tmp_path):
    manifest, source, model = _patch_fake_v2_audit(
        monkeypatch,
        tmp_path,
        mark_override=True,
    )
    report = __import__("scripts.audit_karaoke_mms_alignment", fromlist=["run_audit"]).run_audit(
        manifest_path=manifest,
        source_path=source,
        output_path=tmp_path / "audit.json",
        song_ids=("chinese-song",),
        schema_version=SCHEMA_VERSION_V2,
        unit_overrides={
            "schema_version": UNIT_OVERRIDES_SCHEMA_VERSION,
            "overrides": [
                {
                    "song_id": "chinese-song",
                    "line_index": 0,
                    "token_index": 0,
                    "unit": "zhong",
                }
            ],
        },
        model_path=model,
    )

    assert report["schema_version"] == SCHEMA_VERSION_V2
    assert report["alignment_contract"]["phoneme_alignment"] is False
    assert report["alignment_contract"]["independent_recognition"] is False
    assert report["songs"][0]["lines"][0]["phoneme_alignment"] is False


def test_v2_unmatched_unit_override_fails_closed(monkeypatch, tmp_path):
    manifest, source, model = _patch_fake_v2_audit(
        monkeypatch,
        tmp_path,
        mark_override=False,
    )

    import scripts.audit_karaoke_mms_alignment as audit_module

    with pytest.raises(ValueError, match="unit overrides did not match SUG tokens"):
        audit_module.run_audit(
            manifest_path=manifest,
            source_path=source,
            output_path=tmp_path / "audit.json",
            song_ids=("chinese-song",),
            schema_version=SCHEMA_VERSION_V2,
            unit_overrides={
                "schema_version": UNIT_OVERRIDES_SCHEMA_VERSION,
                "overrides": [
                    {
                        "song_id": "chinese-song",
                        "line_index": 0,
                        "token_index": 0,
                        "unit": "zhong",
                    }
                ],
            },
            model_path=model,
        )
