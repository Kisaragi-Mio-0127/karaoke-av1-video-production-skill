from __future__ import annotations

import json
from types import SimpleNamespace

import mutagen
import pytest

from scripts import karaoke_netease_metadata as metadata


def test_aes128_decrypt_matches_standard_vector():
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ciphertext = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")

    assert metadata._aes128_decrypt_block(ciphertext, key) == bytes.fromhex(
        "00112233445566778899aabbccddeeff"
    )


def test_decode_163_key_comment_reads_music_id():
    encrypted = (
        "L64FU3W4YxX3ZFTmbZ+8/exta/CrXWx6pOeEsdGym5kqClbDXA3nEzk95Ub0n5f"
        "NJTfJWv1SvBj3WAKfHvsVyw=="
    )

    decoded = metadata.decode_163_key_comment(
        metadata.NETEASE_COMMENT_PREFIX + encrypted
    )

    assert decoded["musicId"] == 559880
    assert decoded["musicName"] == "アマオト"


def test_normalize_identity_includes_album_and_artist_ids():
    identity = metadata.normalize_netease_identity(
        {
            "musicId": "559880",
            "musicName": "アマオト",
            "artist": [["Duca", "16260"]],
            "albumId": "52408",
            "album": "Original Sound Track",
            "duration": 256000,
        }
    )

    assert identity == {
        "song": {
            "id": "559880",
            "title": "アマオト",
            "artists": [{"name": "Duca", "id": "16260"}],
        },
        "album": {"id": "52408", "title": "Original Sound Track"},
        "duration_ms": 256000,
    }


def test_read_metadata_expands_vorbis_style_string_lists(tmp_path, monkeypatch):
    audio = tmp_path / "tagged.flac"
    audio.write_bytes(b"fixture")
    monkeypatch.setattr(
        mutagen,
        "File",
        lambda _path: SimpleNamespace(tags={"musicId": ["559880"]}),
    )

    assert metadata.read_netease_song_id(audio) == "559880"


def test_normalize_album_detail_includes_album_artist():
    album = metadata.normalize_netease_album_detail(
        {
            "album": {
                "id": 52408,
                "name": "Original Sound Track",
                "artist": {"id": 16133, "name": "安瀬聖"},
                "artists": [{"id": 16133, "name": "安瀬聖"}],
                "size": 19,
            }
        }
    )

    assert album == {
        "id": "52408",
        "title": "Original Sound Track",
        "artists": [{"name": "安瀬聖", "id": "16133"}],
        "size": 19,
    }


def test_fetch_album_detail_uses_explicit_album_endpoint(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=-1):
            return json.dumps(
                {"code": 200, "album": {"id": 52408, "name": "OST"}}
            ).encode()

    def fake_urlopen(request, *, timeout):
        calls.append((request.full_url, request.headers, timeout))
        return Response()

    monkeypatch.setattr(metadata, "urlopen", fake_urlopen)

    document = metadata.fetch_netease_album_detail("52408", timeout_seconds=3.0)

    assert document["album"]["id"] == 52408
    assert calls[0][0] == "https://music.163.com/api/v1/album/52408"
    assert calls[0][2] == 3.0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"code": -462, "message": "rate limited"}, "code -462: rate limited"),
        ({"code": 200, "album": {"id": 1}}, "album id 1 for requested 52408"),
    ],
)
def test_fetch_album_detail_reports_api_and_identity_errors(
    monkeypatch, payload, message
):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit=-1):
            assert limit == metadata.MAX_ALBUM_RESPONSE_BYTES + 1
            return json.dumps(payload).encode()

    monkeypatch.setattr(metadata, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(metadata.NeteaseMetadataError, match=message):
        metadata.fetch_netease_album_detail("52408")


def test_fetch_album_detail_rejects_oversized_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=-1):
            return b"x" * (metadata.MAX_ALBUM_RESPONSE_BYTES + 1)

    monkeypatch.setattr(metadata, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(metadata.NeteaseMetadataError, match="response exceeded"):
        metadata.fetch_netease_album_detail("52408")


def test_fetch_album_detail_rejects_non_numeric_id():
    with pytest.raises(metadata.NeteaseMetadataError, match="digits only"):
        metadata.fetch_netease_album_detail("album-52408")
