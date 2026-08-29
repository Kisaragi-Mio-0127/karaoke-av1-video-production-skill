"""Tests for the formal lossless-source gate in complete-mix pitch shifting."""

from pathlib import Path

from scripts import pitch_shift_audio


def test_imports_bundled_pitch_shift_module() -> None:
    root = Path(__file__).resolve().parents[3]
    expected = root / "integration" / "strangeutagame" / "scripts" / "pitch_shift_audio.py"

    assert Path(pitch_shift_audio.__file__).resolve() == expected.resolve()


def test_formal_lossless_source_accepts_flac_and_pcm() -> None:
    assert pitch_shift_audio.is_formal_lossless_source({"codec_name": "flac"})
    assert pitch_shift_audio.is_formal_lossless_source({"codec_name": "pcm_f32le"})
    assert pitch_shift_audio.is_formal_lossless_source({"codec_name": "pcm_s24le"})


def test_formal_lossless_source_rejects_lossy_and_missing_codecs() -> None:
    assert not pitch_shift_audio.is_formal_lossless_source({"codec_name": "mp3"})
    assert not pitch_shift_audio.is_formal_lossless_source({"codec_name": "aac"})
    assert not pitch_shift_audio.is_formal_lossless_source({})


def test_inherited_metadata_uses_safe_aliases_and_omits_technical_tags() -> None:
    metadata = pitch_shift_audio.inherited_metadata(
        {
            "format": {
                "tags": {
                    "TITLE": "Song",
                    "artist": "Singer",
                    "ALBUM": "Record",
                    "ALBUMARTIST": "Various",
                    "YEAR": "2026",
                    "TRACKNUMBER": "3/12",
                    "DISCNUMBER": "1/2",
                    "COMMENT": "Source note",
                    "encoder": "source encoder",
                    "replaygain_track_gain": "-4 dB",
                }
            }
        }
    )

    assert metadata == {
        "title": "Song",
        "artist": "Singer",
        "album": "Record",
        "album_artist": "Various",
        "date": "2026",
        "track": "3/12",
        "disc": "1/2",
        "comment": "Source note",
    }


def test_flac_encode_maps_shifted_audio_and_only_attached_pictures() -> None:
    command, metadata, pictures = pitch_shift_audio.build_encode_command(
        Path("ffmpeg"),
        Path("shifted.wav"),
        Path("source.flac"),
        Path("candidate.flac"),
        Path("output.flac"),
        -0.25,
        {
            "format": {"tags": {"title": "Song"}},
            "streams": [
                {"index": 0, "codec_type": "audio", "disposition": {}},
                {"index": 1, "codec_type": "video", "disposition": {"attached_pic": 1}},
                {"index": 2, "codec_type": "video", "disposition": {"attached_pic": 0}},
            ],
        },
    )

    maps = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "-map"]
    assert maps == ["0:a:0", "1:1"]
    assert "1:0" not in command
    assert "1:2" not in command
    assert command[command.index("-c:v") : command.index("-c:v") + 2] == ["-c:v", "copy"]
    assert metadata == {"title": "Song"}
    assert pictures == [1]


def test_wav_encode_never_adds_source_as_media_input() -> None:
    command, _, pictures = pitch_shift_audio.build_encode_command(
        Path("ffmpeg"),
        Path("shifted.wav"),
        Path("source.flac"),
        Path("candidate.wav"),
        Path("output.wav"),
        0.0,
        {
            "format": {"tags": {"artist": "Singer"}},
            "streams": [
                {"index": 0, "codec_type": "audio", "disposition": {}},
                {"index": 1, "codec_type": "video", "disposition": {"attached_pic": 1}},
            ],
        },
    )

    assert command.count("-i") == 1
    assert [command[index + 1] for index, value in enumerate(command[:-1]) if value == "-map"] == ["0:a:0"]
    assert pictures == []


def test_wav_id3_metadata_carries_album_artist_and_disc(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF" + (4).to_bytes(4, "little") + b"WAVE")

    pitch_shift_audio.write_wav_id3_metadata(
        wav,
        {"album_artist": "Various", "disc": "1/2", "comment": "Note"},
    )

    data = wav.read_bytes()
    assert data[4:8] == (len(data) - 8).to_bytes(4, "little")
    assert b"id3 " in data
    assert b"TPE2" in data
    assert b"TPOS" in data
    assert b"COMM" in data


def test_executable_accepts_explicit_rubberband(tmp_path: Path) -> None:
    rubberband = tmp_path / "rubberband.exe"
    rubberband.write_bytes(b"stub")

    assert (
        pitch_shift_audio.executable(rubberband, "RUBBERBAND", "rubberband")
        == rubberband.resolve()
    )
