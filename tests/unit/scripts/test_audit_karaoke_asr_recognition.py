import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import audit_karaoke_asr_recognition as asr_audit
from scripts.audit_karaoke_asr_recognition import (
    extract_recognition_tokens,
    load_audio_numpy,
    match_known_lyrics,
    normalize_token_text,
    run_recognition_audit,
)


def test_japanese_normalization_does_not_simplify_kanji() -> None:
    assert normalize_token_text("後來", "ja") == "後來"


def test_direct_parser_accepts_japanese_asr_audit() -> None:
    args = asr_audit.build_parser().parse_args(
        ["--audio", "song.flac", "--lyrics", "song.lrc", "--language", "ja"]
    )

    assert args.language == "ja"


def test_manifest_mode_requires_an_explicit_manifest() -> None:
    with pytest.raises(SystemExit, match="--manifest is required"):
        asr_audit.main([])


def test_direct_mode_does_not_require_a_manifest() -> None:
    args = asr_audit.build_parser().parse_args(
        ["--audio", "song.flac", "--lyrics", "song.lrc", "--language", "ja"]
    )

    assert args.manifest is None


def test_import_has_no_private_default_manifest_binding() -> None:
    assert "DEFAULT_MANIFEST_PATH" not in vars(asr_audit)


def test_low_similarity_candidate_is_not_consumed_before_later_match() -> None:
    report = match_known_lyrics(
        [{"line_index": 0, "text": "x hello", "start_ms": 0, "end_ms": 2_000}],
        [{"token": "hello", "start_ms": 500, "end_ms": 1_000, "confidence": 0.9}],
        "en",
    )[0]

    assert report["matches"][0]["status"] == "unmatched"
    assert report["matches"][1]["status"] == "matched"
    assert report["consumed_recognized_indices"] == [0]


def test_cli_fails_closed_for_unresolved_asr(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        asr_audit,
        "run_recognition_audit",
        lambda **_kwargs: {
            "disposition": "unresolved",
            "structural_gate_ok": True,
            "support_gate_ok": False,
        },
    )
    monkeypatch.setattr(asr_audit, "_load_direct_lines", lambda _path: [])

    result = asr_audit.main(
        ["--audio", "song.flac", "--lyrics", "song.lrc", "--language", "ja"]
    )

    assert result == 2
    assert '"status": "fail"' in capsys.readouterr().out


def test_extract_recognition_tokens_keeps_time_and_probability():
    tokens = extract_recognition_tokens(
        {
            "segments": [
                {
                    "start": 1.0,
                    "end": 2.0,
                    "words": [
                        {"word": "你", "start": 1.0, "end": 1.4, "probability": 0.91},
                        {"word": "好", "start": 1.4, "end": 2.0, "probability": 0.83},
                    ],
                }
            ]
        },
        "zh",
    )

    assert [token["token"] for token in tokens] == ["你", "好"]
    assert [token["start_ms"] for token in tokens] == [1_000, 1_400]
    assert [token["confidence"] for token in tokens] == [0.91, 0.83]


def test_known_lyrics_matching_is_ordered_and_does_not_rewrite_lyrics():
    lyric_lines = [
        {"line_index": 4, "text": "你好", "start_ms": 1_000, "end_ms": 2_000}
    ]
    recognized = [
        {"token": "你", "start_ms": 1_000, "end_ms": 1_400, "confidence": 0.9},
        {"token": "好", "start_ms": 1_400, "end_ms": 2_000, "confidence": 0.8},
    ]

    result = match_known_lyrics(lyric_lines, recognized, "zh")

    assert result[0]["disposition"] == "support"
    assert result[0]["recognized_text"] == "你好"
    assert lyric_lines[0]["text"] == "你好"


def test_high_confidence_misrecognition_is_a_veto():
    result = match_known_lyrics(
        [{"line_index": 0, "text": "你好", "start_ms": 0, "end_ms": 1_000}],
        [{"token": "坏", "start_ms": 0, "end_ms": 500, "confidence": 0.97}],
        "zh",
    )

    assert result[0]["disposition"] == "veto"
    assert result[0]["matches"][0]["status"] == "unmatched"


def test_low_confidence_recognition_remains_unresolved():
    result = match_known_lyrics(
        [{"line_index": 0, "text": "hello", "start_ms": 0, "end_ms": 1_000}],
        [{"token": "hello", "start_ms": 0, "end_ms": 500, "confidence": 0.2}],
        "en",
    )

    assert result[0]["coverage"] == 1.0
    assert result[0]["disposition"] == "unresolved"


def test_numpy_loader_and_report_cache_do_not_load_a_real_model(tmp_path: Path):
    audio = tmp_path / "stem.wav"
    samples = (np.zeros(800, dtype=np.int16)).tobytes()
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(samples)

    waveform, sample_rate = load_audio_numpy(audio)
    assert sample_rate == 16_000
    assert waveform.shape == (800,)

    def fake_transcribe(_waveform, _sample_rate, _language, _model, _model_path):
        return {
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "words": [
                        {"word": "hello", "start": 0.0, "end": 0.5, "probability": 0.9}
                    ],
                }
            ]
        }

    cache = tmp_path / "cache"
    model = tmp_path / "base.pt"
    model.write_bytes(b"fake-model-weights")
    first = run_recognition_audit(
        audio_path=audio,
        lyric_lines=[
            {"line_index": 0, "text": "hello", "start_ms": 0, "end_ms": 500}
        ],
        language="en",
        audio_kind="stem",
        model_path=model,
        cache_dir=cache,
        transcribe_fn=fake_transcribe,
    )
    second = run_recognition_audit(
        audio_path=audio,
        lyric_lines=[
            {"line_index": 0, "text": "hello", "start_ms": 0, "end_ms": 500}
        ],
        language="en",
        audio_kind="stem",
        model_path=model,
        cache_dir=cache,
        transcribe_fn=lambda *_args: (_ for _ in ()).throw(
            AssertionError("cache hit should not transcribe")
        ),
    )

    assert first["disposition"] == "support"
    assert first["audio_kind"] == "stem"
    assert first["audio"]["sha256"] == second["audio"]["sha256"]
    assert second["cache"]["hit"] is True
    assert second["lyrics_written"] is False
    assert first["recognized_token_count"] == 1
    provenance = first["songs"][0]["recognition_provenance"]
    assert provenance["audio_path"] == str(audio.resolve())
    assert provenance["audio_sha256"] == first["audio"]["sha256"]
    assert provenance["model_path"] == str(model.resolve())
    assert provenance["model_sha256"] == first["model_sha256"]
    assert provenance["model_sha256"] is not None
    assert provenance["recognized_tokens"] == first["recognized_tokens"]
    assert provenance["transcription_cache_key"] == first["cache"]["key"]


def test_chinese_line_cannot_match_identical_tokens_sixty_seconds_later():
    result = match_known_lyrics(
        [
            {
                "line_index": 0,
                "text": "测试歌词",
                "start_ms": 30_100,
                "end_ms": 37_026,
            }
        ],
        [
            {
                "token": token,
                "start_ms": 60_260 + index * 930,
                "end_ms": 61_190 + index * 930,
                "confidence": 0.98,
            }
            for index, token in enumerate("测试歌词")
        ],
        "zh",
    )

    line = result[0]
    assert line["matched_token_count"] == 0
    assert line["recognized_text"] == ""
    assert line["window_start_ms"] == 30_100
    assert line["window_end_ms"] == 37_026
    assert line["window_tolerance_ms"] == 250
    assert line["out_of_window_count"] == 4
    assert line["gate_ok"] is True
    assert all("recognized_index" not in match for match in line["matches"])


def test_english_line_cannot_match_later_chorus_tokens():
    result = match_known_lyrics(
        [
            {
                "line_index": 0,
                "text": "Don't Stop!",
                "start_ms": 23_339,
                "end_ms": 28_005,
            }
        ],
        [
            {
                "token": "DON’T",
                "start_ms": 36_440,
                "end_ms": 36_800,
                "confidence": 0.99,
            },
            {
                "token": "stop.",
                "start_ms": 36_800,
                "end_ms": 37_100,
                "confidence": 0.99,
            },
        ],
        "en",
    )

    line = result[0]
    assert line["matched_token_count"] == 0
    assert line["out_of_window_count"] == 2
    assert line["matches"][0]["status"] == "unmatched"
    assert line["matches"][1]["status"] == "unmatched"


def test_small_tolerance_is_reported_and_capped():
    lines = [{"line_index": 0, "text": "hello", "start_ms": 1_000, "end_ms": 2_000}]
    token = [{"token": "hello", "start_ms": 700, "end_ms": 760, "confidence": 0.9}]

    accepted = match_known_lyrics(lines, token, "en", window_tolerance_ms=250)
    rejected = match_known_lyrics(lines, token, "en", window_tolerance_ms=200)

    assert accepted[0]["matched_token_count"] == 1
    assert accepted[0]["allowed_window_start_ms"] == 750
    assert rejected[0]["matched_token_count"] == 0
    assert rejected[0]["out_of_window_count"] == 1

    with np.testing.assert_raises_regex(ValueError, "between 0 and 250"):
        match_known_lyrics(lines, token, "en", window_tolerance_ms=251)


def test_tokens_are_consumed_once_and_never_in_reverse_order():
    result = match_known_lyrics(
        [
            {"line_index": 0, "text": "hello", "start_ms": 1_000, "end_ms": 2_000},
            {"line_index": 1, "text": "world", "start_ms": 2_000, "end_ms": 3_000},
        ],
        [
            {"token": "hello", "start_ms": 1_500, "end_ms": 2_100, "confidence": 0.9},
            {"token": "world", "start_ms": 2_100, "end_ms": 2_500, "confidence": 0.9},
        ],
        "en",
    )

    assert result[0]["consumed_recognized_indices"] == [0]
    assert result[1]["consumed_recognized_indices"] == [1]
    assert result[0]["matches"][0]["recognized_index"] < result[1]["matches"][0][
        "recognized_index"
    ]

    reversed_tokens = match_known_lyrics(
        [
            {"line_index": 0, "text": "hello", "start_ms": 1_000, "end_ms": 2_000},
            {"line_index": 1, "text": "world", "start_ms": 2_000, "end_ms": 3_000},
        ],
        [
            {"token": "world", "start_ms": 2_300, "end_ms": 2_600, "confidence": 0.9},
            {"token": "hello", "start_ms": 1_400, "end_ms": 1_800, "confidence": 0.9},
        ],
        "en",
    )

    assert reversed_tokens[0]["consumed_recognized_indices"] == [1]
    assert reversed_tokens[1]["consumed_recognized_indices"] == []
    assert reversed_tokens[1]["disposition"] == "unresolved"


def test_normalization_equates_traditional_chinese_and_english_punctuation_case():
    assert normalize_token_text("測試歌詞", "zh") == normalize_token_text("测试歌词", "zh")
    assert normalize_token_text("DON’T,", "en") == normalize_token_text("don't", "en")

    chinese = match_known_lyrics(
        [{"line_index": 0, "text": "测试歌词", "start_ms": 1_000, "end_ms": 3_000}],
        [
            {
                "token": token,
                "start_ms": 1_000 + index * 400,
                "end_ms": 1_400 + index * 400,
                "confidence": 0.9,
            }
            for index, token in enumerate("測試歌詞")
        ],
        "zh",
    )
    english = match_known_lyrics(
        [
            {
                "line_index": 0,
                "text": "Don't stop!",
                "start_ms": 1_000,
                "end_ms": 2_000,
            }
        ],
        [
            {"token": "DON’T,", "start_ms": 1_000, "end_ms": 1_400, "confidence": 0.9},
            {"token": "STOP.", "start_ms": 1_400, "end_ms": 1_800, "confidence": 0.9},
        ],
        "en",
    )

    assert chinese[0]["disposition"] == "support"
    assert english[0]["disposition"] == "support"


def test_missing_line_window_is_an_explicit_gate_error():
    line = match_known_lyrics(
        [{"line_index": 0, "text": "hello"}],
        [{"token": "hello", "start_ms": 0, "end_ms": 500, "confidence": 0.9}],
        "en",
    )[0]

    assert line["gate_ok"] is False
    assert line["errors"] == ["missing-or-invalid-line-window"]
    assert line["matched_token_count"] == 0


def test_manifest_report_retains_raw_per_song_recognition_provenance(
    tmp_path: Path, monkeypatch
):
    audio = tmp_path / "mix.flac"
    deliverable_dir = tmp_path / "deliverable"
    source = deliverable_dir / "sources" / "netease_lyrics.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"songs": {}}', encoding="utf-8")
    track = SimpleNamespace(
        song_id="song-1",
        title="Song One",
        language="en",
        audio_path=audio,
    )
    album = SimpleNamespace(
        tracks=[track],
        project_root=tmp_path,
        deliverable_dir=deliverable_dir,
    )
    raw_tokens = [
        {
            "token": "hello",
            "start_ms": 1_000,
            "end_ms": 1_500,
            "confidence": 0.91,
        }
    ]
    single_report = {
        "audio": {"path": str(audio), "sha256": "audio-hash"},
        "audio_kind": "mix",
        "model": "base",
        "model_path": str(tmp_path / "base.pt"),
        "model_sha256": "model-hash",
        "recognized_token_count": 1,
        "recognized_tokens": raw_tokens,
        "cache": {"key": "cache-hash", "path": str(tmp_path / "cache.json")},
        "songs": [
            {
                "song_id": "song-1",
                "title": "Song One",
                "language": "en",
                "lines": [
                    {
                        "line_index": 0,
                        "gate_ok": True,
                        "errors": [],
                        "matches": [],
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(asr_audit, "load_album_manifest", lambda *_args, **_kwargs: album)
    monkeypatch.setattr(asr_audit, "_manifest_lines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        asr_audit,
        "run_recognition_audit",
        lambda **_kwargs: single_report,
    )

    report = asr_audit.run_manifest_audit(
        manifest_path=tmp_path / "manifest.json",
        cache_dir=None,
        allow_partial_manifest=True,
    )

    audit = report["recognition_audits"][0]
    assert audit["audio_path"] == str(audio)
    assert audit["audio_sha256"] == "audio-hash"
    assert audit["model_path"] == str(tmp_path / "base.pt")
    assert audit["model_sha256"] == "model-hash"
    assert audit["recognized_tokens"] == raw_tokens
    assert audit["transcription_cache_key_sha256"] == "cache-hash"
    assert report["recognized_token_count"] == 1
    assert report["language"] == "en"
    assert report["lyric_source_path"] == "deliverable/sources/netease_lyrics.json"
    assert report["lyric_source_sha256"] == asr_audit.sha256_file(source)


def test_manifest_audit_rejects_mixed_languages_before_transcription(
    tmp_path: Path, monkeypatch
):
    album = SimpleNamespace(
        tracks=[
            SimpleNamespace(song_id="ja-1", language="ja"),
            SimpleNamespace(song_id="zh-1", language="zh"),
        ],
        project_root=tmp_path,
        deliverable_dir=tmp_path / "deliverable",
    )
    monkeypatch.setattr(asr_audit, "load_album_manifest", lambda *_args, **_kwargs: album)

    with np.testing.assert_raises_regex(ValueError, "exactly one language"):
        asr_audit.run_manifest_audit(
            manifest_path=tmp_path / "manifest.json",
            cache_dir=None,
            allow_partial_manifest=True,
        )
