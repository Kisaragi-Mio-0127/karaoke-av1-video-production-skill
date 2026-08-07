"""Unit coverage for the reproducible karaoke timing builder."""

from pathlib import Path
from types import SimpleNamespace

import mutagen
import pytest

import scripts.karaoke_timing as karaoke_timing
from scripts.karaoke_timing import (
    ASSDirectExporter,
    LyricLine,
    ReadingHelper,
    SongSpec,
    SugProjectParser,
    _line_is_machine_reviewed,
    apply_lyric_corrections,
    build_project,
    collapse_english_sentence_to_word_tokens,
    derive_line_timing,
    legalize_ass,
    legalize_srt,
    load_or_fetch_source,
    make_lyric_lines,
    parse_lrc,
    project_signature,
    validate_project,
)

TEST_SONG = SongSpec(
    song_id="generic-song",
    title="Generic Title",
    artist="Generic Artist",
    audio_name="generic.flac",
    slug="generic-track",
    expected_duration_ms=10_000,
    sha256_hint="0" * 64,
    language="ja",
)


def test_missing_lyric_source_never_fetches_without_refresh_opt_in(
    tmp_path: Path, monkeypatch
):
    called = False

    def unexpected_fetch(_song_id):
        nonlocal called
        called = True
        raise AssertionError("network fetch must remain opt-in")

    monkeypatch.setattr(karaoke_timing, "fetch_netease_song", unexpected_fetch)

    with pytest.raises(FileNotFoundError, match="--refresh-source"):
        load_or_fetch_source(tmp_path / "lyrics.json", False, [TEST_SONG])

    assert called is False


def test_english_editable_source_uses_one_checkpoint_per_word():
    spec = SongSpec(
        song_id="english-word-axis",
        title="English Word Axis",
        artist="Test",
        audio_name="test.flac",
        slug="english-word-axis",
        expected_duration_ms=4_000,
        sha256_hint="0" * 64,
        language="en",
    )
    line = LyricLine(0, 1, "Can't split these words", 1_000, 3_500, "audio-duration")

    project, reports = build_project(
        spec,
        4_000,
        [line],
        aligned_words={},
        alignment_meta={"status": "skipped"},
    )

    sentence = project.sentences[0]
    assert sentence.text == line.text
    assert [character.char for character in sentence.characters] == [
        "Can't",
        " ",
        "split",
        " ",
        "these",
        " ",
        "words",
    ]
    assert [character.check_count for character in sentence.characters] == [
        1,
        0,
        1,
        0,
        1,
        0,
        1,
    ]
    assert all(character.ruby is None for character in sentence.characters)
    assert sentence.characters[-1].is_sentence_end
    assert sentence.characters[-1].sentence_end_ts == 3_500
    assert reports[0]["editable_timing_unit"] == "word"
    assert reports[0]["editable_timing_point_count"] == 4


def test_english_word_token_collapse_is_idempotent():
    spec = SongSpec(
        song_id="english-word-axis-idempotent",
        title="English Word Axis",
        artist="Test",
        audio_name="test.flac",
        slug="english-word-axis-idempotent",
        expected_duration_ms=3_000,
        sha256_hint="0" * 64,
        language="en",
    )
    line = LyricLine(0, 1, "One point", 1_000, 2_500, "audio-duration")
    project, _ = build_project(
        spec,
        3_000,
        [line],
        aligned_words={},
        alignment_meta={"status": "skipped"},
    )
    sentence = project.sentences[0]
    before = [(character.char, list(character.timestamps)) for character in sentence.characters]

    collapse_english_sentence_to_word_tokens(sentence)

    assert [(character.char, list(character.timestamps)) for character in sentence.characters] == before


def test_lyric_corrections_preserve_frozen_source_and_require_exact_match():
    frozen = "[00:10.00]確認項項目です\n"
    corrected, applied = apply_lyric_corrections(
        frozen,
        [
            {
                "source_text": "確認項項目です",
                "corrected_text": "確認項目です",
                "review_status": "acoustically-reviewed",
                "evidence": ["isolated vocal"],
            }
        ],
    )

    assert frozen == "[00:10.00]確認項項目です\n"
    assert corrected == "[00:10.00]確認項目です\n"
    assert applied[0]["evidence"] == ["isolated vocal"]


def test_named_ffmpeg_alias_is_project_local(tmp_path: Path, monkeypatch):
    source = tmp_path / "package" / "ffmpeg-win-bundled.exe"
    source.parent.mkdir()
    source.write_bytes(b"local ffmpeg probe")
    repository = tmp_path / "repository"
    monkeypatch.setattr(karaoke_timing, "ROOT", repository)

    alias = karaoke_timing._ensure_named_ffmpeg(source)

    assert alias == repository / ".cache" / "bin" / "ffmpeg.exe"
    assert alias.read_bytes() == source.read_bytes()


def test_duration_probe_accepts_non_mp3_audio(monkeypatch):
    monkeypatch.setattr(
        mutagen,
        "File",
        lambda _path: SimpleNamespace(info=SimpleNamespace(length=12.3456)),
    )

    seconds, duration_ms = karaoke_timing.read_mutagen_duration(
        Path("pitch-shifted.flac")
    )

    assert seconds == 12.3456
    assert duration_ms == 12_346


def test_lrc_filtering_and_empty_marker_axis():
    entries = parse_lrc(
        "\n".join(
            [
                "[00:00.00] 作词 : Example Writer",
                "[00:01.00] 編曲：Example Arranger",
                "[00:10.00]第一行",
                "[00:12.00]",
                "[00:13.00]次の行",
                "[00:15.00]【 おわり 】",
            ]
        )
    )
    lines, report = make_lyric_lines(entries, 20_000)

    assert [line.text for line in lines] == ["第一行", "次の行"]
    assert lines[0].start_ms == 10_000
    assert lines[0].end_ms == 12_000
    assert report["excluded"]["composer-credit"] == 2
    assert report["excluded"]["end-marker"] == 1


def test_deterministic_mora_fallback_is_monotonic_and_bounded():
    line = LyricLine(0, 1, "第一行", 1_000, 2_000, "next-lyric")
    timing, diagnostics = derive_line_timing(
        line, ReadingHelper(), aligned_words=None, alignment_error="test fallback"
    )

    timestamps = list(timing.values())
    assert diagnostics["method"] == "deterministic-mora-interpolation"
    assert timestamps == sorted(timestamps)
    assert all(line.start_ms <= value <= line.end_ms for value in timestamps)


def test_aligned_multichar_tokens_are_mapped_by_text_cursor():
    line = LyricLine(0, 1, "第一行", 1_000, 3_000, "next-lyric")
    words = [
        {"word": " 第一", "start": 1.1, "end": 1.8, "probability": 0.9},
        {"word": "行", "start": 2.0, "end": 2.7, "probability": 0.8},
    ]
    timing, diagnostics = derive_line_timing(line, ReadingHelper(), words)

    assert diagnostics["method"] == "stable-ts-constrained"
    assert diagnostics["coverage"] == 1.0
    assert diagnostics["acoustic_start_ms"] == 1_100
    assert diagnostics["acoustic_end_ms"] == 2_700
    assert list(timing.values()) == sorted(timing.values())
    assert min(timing.values()) >= line.start_ms
    assert max(timing.values()) <= line.end_ms

    project, reports = build_project(
        TEST_SONG,
        3_000,
        [line],
        aligned_words={0: words},
        alignment_meta={"status": "ok"},
    )
    assert project.sentences[0].characters[-1].sentence_end_ts == 3_000
    assert reports[0]["release_source"] == "lrc-line-axis:next-lyric"
    assert reports[0]["release_extension_after_acoustic_end_ms"] == 300


def test_build_project_persists_configured_cover_highlight_color():
    line = LyricLine(0, 1, "歌詞", 1_000, 2_000, "audio-duration")

    project, _ = build_project(
        TEST_SONG,
        3_000,
        [line],
        aligned_words={},
        alignment_meta={"status": "ok"},
        singer_color="#E19E84",
    )

    assert project.singers[0].color == "#E19E84"
    assert project.singers[0].complement_color == "#84C7E1"


def test_build_project_assigns_traceable_voice_roles_to_lines_and_characters():
    lines = [
        LyricLine(0, 1, "歌詞", 1_000, 2_000, "audio-duration"),
        LyricLine(1, 2, "副歌", 2_100, 3_000, "audio-duration"),
    ]
    project, reports = build_project(
        TEST_SONG,
        4_000,
        lines,
        aligned_words={},
        alignment_meta={"status": "skipped"},
        timing_overrides={
            "0": {"voice_role": "harmony"},
            "1": {"character_voice_roles": {"1": "secondary"}},
        },
    )

    singers_by_group = {singer.group: singer for singer in project.singers if singer.group}
    assert set(singers_by_group) == {"harmony", "secondary"}
    assert all(not singer.is_default for singer in singers_by_group.values())
    assert all(singer.color != project.singers[0].color for singer in singers_by_group.values())
    assert len({singer.color for singer in singers_by_group.values()}) == 2
    assert project.sentences[0].singer_id == singers_by_group["harmony"].id
    assert all(
        character.singer_id == singers_by_group["harmony"].id
        for character in project.sentences[0].characters
    )
    assert project.sentences[1].characters[1].singer_id == singers_by_group[
        "secondary"
    ].id
    assert project.sentences[1].characters[0].singer_id == project.singers[0].id
    assert reports[0]["voice_role"] == "harmony"
    assert reports[1]["character_voice_roles"] == {"1": "secondary"}


def test_build_project_persists_explicit_role_colors_without_changing_main_color():
    line = LyricLine(0, 1, "和声", 1_000, 2_000, "audio-duration")

    project, _ = build_project(
        TEST_SONG,
        3_000,
        [line],
        aligned_words={},
        alignment_meta={"status": "skipped"},
        singer_color="#E19E84",
        role_colors={"harmony": "#123456"},
        timing_overrides={"0": {"voice_role": "harmony"}},
    )

    main = project.get_default_singer()
    harmony = next(singer for singer in project.singers if singer.group == "harmony")
    assert main.color == "#E19E84"
    assert harmony.color == "#123456"
    assert harmony.color != main.color


def test_project_signature_contains_persisted_singers_and_character_ownership():
    lines = [
        LyricLine(0, 1, "甲乙", 1_000, 2_000, "audio-duration"),
    ]
    project, _ = build_project(
        TEST_SONG,
        3_000,
        lines,
        aligned_words={},
        alignment_meta={"status": "skipped"},
        timing_overrides={"0": {"character_voice_roles": {"1": "secondary"}}},
    )

    signature = project_signature(project)
    singers_by_id = {singer["id"]: singer for singer in signature["singers"]}
    sentence = signature["sentences"][0]
    assert sentence["singer_id"] in singers_by_id
    assert any(
        singer["group"] == "secondary" and singer["color"] != "#FF6B6B"
        for singer in signature["singers"]
    )
    assert (
        sentence["chars"][0]["singer_id"]
        == project.sentences[0].characters[0].singer_id
    )
    assert (
        sentence["chars"][1]["singer_id"]
        == project.sentences[0].characters[1].singer_id
    )


def test_legacy_or_user_reported_status_without_actual_ab_is_not_a_gate_pass():
    assert not _line_is_machine_reviewed(
        {"review_status": "dual-audio-machine-reviewed"}
    )
    assert not _line_is_machine_reviewed(
        {"review_status": "user-reported-machine-reviewed"}
    )
    assert _line_is_machine_reviewed(
        {
            "review_status": "dual-audio-machine-reviewed",
            "review_gate": {
                "ok": True,
                "actual_dual_audio": True,
            },
            "candidate_dispositions": {"0": "accepted-threshold"},
        }
    )


def test_phrase_gap_is_reassigned_to_the_preceding_held_syllable():
    line = LyricLine(
        3,
        7,
        "前半保持 後半確認です",
        58_610,
        66_650,
        "empty-marker",
    )
    words = [
        {"word": " 前半", "start": 58.61, "end": 59.25, "probability": 0.39},
        {"word": "保", "start": 59.25, "end": 59.67, "probability": 0.81},
        {"word": "持", "start": 59.67, "end": 61.01, "probability": 0.99},
        {"word": " 後", "start": 61.01, "end": 63.21, "probability": 0.85},
        {"word": "半", "start": 63.21, "end": 63.51, "probability": 0.99},
        {"word": "確", "start": 63.51, "end": 63.93, "probability": 0.65},
        {"word": "認", "start": 63.93, "end": 64.51, "probability": 0.42},
        {"word": "です", "start": 64.97, "end": 65.77, "probability": 0.26},
    ]

    timing, diagnostics = derive_line_timing(line, ReadingHelper(), words)

    second_phrase_index = line.text.index("後")
    assert timing[second_phrase_index] >= 62_300
    assert timing[second_phrase_index] > 61_010
    assert diagnostics["phrase_gap_correction_count"] == 1
    correction = diagnostics["phrase_gap_corrections"][0]
    assert correction["character_index"] == second_phrase_index
    assert correction["reassigned_gap_ms"] >= 1_300


def test_reviewed_character_override_replaces_only_the_selected_onset():
    line = LyricLine(0, 1, "第一行", 1_000, 3_000, "next-lyric")
    words = [
        {"word": " 第一", "start": 1.1, "end": 1.8, "probability": 0.9},
        {"word": "行", "start": 2.0, "end": 2.7, "probability": 0.8},
    ]
    project, reports = build_project(
        TEST_SONG,
        3_000,
        [line],
        aligned_words={0: words},
        alignment_meta={"status": "ok"},
        timing_overrides={
            "0": {
                "review_status": "acoustically-reviewed",
                "reason": "test evidence",
                "character_overrides_ms": {"0": 1_250},
                "evidence": ["vocal", "mix"],
                "release_override_ms": 2_900,
            }
        },
    )

    assert project.sentences[0].characters[0].timestamps == [1_250]
    assert reports[0]["review_status"] == "acoustically-reviewed"
    assert reports[0]["review_evidence"] == ["vocal", "mix"]
    assert reports[0]["timing_overrides"][0]["previous_ms"] == 1_100
    assert project.sentences[0].characters[-1].sentence_end_ts == 2_900
    assert reports[0]["release_source"] == "timing-override:dual-audio-mms-tail"


def test_project_signature_and_srt_legalization(tmp_path: Path):
    line = LyricLine(0, 1, "第一行", 1_000, 2_000, "audio-duration")
    project, _ = build_project(
        TEST_SONG,
        3_000,
        [line],
        aligned_words={},
        alignment_meta={"status": "skipped"},
    )
    assert validate_project(project, 3_000)["ok"]

    sug_path = tmp_path / "roundtrip.sug"
    SugProjectParser.save(project, str(sug_path))
    assert project_signature(SugProjectParser.load(str(sug_path))) == project_signature(
        project
    )

    ass_path = tmp_path / "burn-ready.ass"
    ASSDirectExporter().export(project, str(ass_path))
    ass_result = legalize_ass(ass_path, 3_000, "HarmonyOS Sans SC", project)
    assert ass_result["burn_ready_ass"]["ok"] is True
    ass_text = ass_path.read_text(encoding="utf-8")
    assert "HarmonyOS Sans SC,58," in ass_text
    assert "&H000000FF,&H00FFFFFF" in ass_text
    assert r"{\k20}{\kf" in ass_text
    assert ",1,0,0,0,100,100,0,0,1,3,0,2,980,80,100,1" in ass_text
    assert "|<" not in ass_text
    assert "#|" not in ass_text
    assert r"\sing_" not in ass_text

    srt_path = tmp_path / "test.srt"
    srt_path.write_text(
        "1\n00:00:01,000 --> 00:00:08,000\n第一行\n",
        encoding="utf-8",
    )
    result = legalize_srt(srt_path, 1_000)
    assert result["ok"] is False  # the source interval has no legal media overlap
    assert srt_path.read_text(encoding="utf-8") == ""


def test_burn_ready_visual_order_splits_equal_morae_and_punctuation():
    sentence = karaoke_timing.Sentence.from_text(
        "\u30cd\u30c3\u30fb\u30fc\u3002",
        "singer",
    )
    sentence.characters[0].add_timestamp(1_000)
    sentence.characters[1].add_timestamp(1_000)
    sentence.characters[3].add_timestamp(1_300)

    onsets = karaoke_timing._burn_ready_character_onsets(
        sentence.characters,
        1_800,
    )

    assert onsets == [1_000, 1_100, 1_200, 1_300, 1_550]
    assert all(
        right // 10 > left // 10
        for left, right in zip(onsets, onsets[1:], strict=False)
    )


def test_release_may_overlap_the_next_line_without_reversing_the_onset_axis():
    lines = [
        LyricLine(0, 1, "第一項", 1_000, 2_000, "next-lyric"),
        LyricLine(1, 2, "空", 2_100, 3_000, "audio-duration"),
    ]
    project, _ = build_project(
        TEST_SONG,
        4_000,
        lines,
        aligned_words={},
        alignment_meta={"status": "skipped"},
        timing_overrides={
            "0": {
                "character_overrides_ms": {"1": 2_200},
                "release_override_ms": 2_300,
            }
        },
    )

    validation = validate_project(project, 4_000)
    assert validation["ok"] is True
    assert validation["onset_axis_non_decreasing"] is True
    assert validation["release_overlap_count"] == 1
    assert validation["release_overlaps"][0]["overlap_ms"] == 200
    assert validation["cross_line_onset_overlap_count"] == 1
    assert validation["cross_line_onset_overlaps"][0]["overlap_ms"] == 100
