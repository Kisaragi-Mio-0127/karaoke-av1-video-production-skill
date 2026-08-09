#!/usr/bin/env python3
"""Read NetEase identity metadata, with optional explicit album lookup."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

NETEASE_COMMENT_PREFIX = "163 key(Don't modify):"
NETEASE_AES_KEY = b"#14ljk_!\\]&0U<'("
NETEASE_ALBUM_ENDPOINT = "https://music.163.com/api/v1/album/{album_id}"
MAX_ALBUM_RESPONSE_BYTES = 2 * 1024 * 1024


class NeteaseMetadataError(ValueError):
    """Raised when an audio file has no usable NetEase song id."""


def _gf_multiply(left: int, right: int) -> int:
    result = 0
    while right:
        if right & 1:
            result ^= left
        left = ((left << 1) ^ (0x11B if left & 0x80 else 0)) & 0xFF
        right >>= 1
    return result


def _gf_power(value: int, power: int) -> int:
    result = 1
    while power:
        if power & 1:
            result = _gf_multiply(result, value)
        value = _gf_multiply(value, value)
        power >>= 1
    return result


def _rotate_left(value: int, count: int) -> int:
    return ((value << count) | (value >> (8 - count))) & 0xFF


def _sbox_value(value: int) -> int:
    inverse = _gf_power(value, 254) if value else 0
    return (
        inverse
        ^ _rotate_left(inverse, 1)
        ^ _rotate_left(inverse, 2)
        ^ _rotate_left(inverse, 3)
        ^ _rotate_left(inverse, 4)
        ^ 0x63
    )


_SBOX = tuple(_sbox_value(value) for value in range(256))
_INVERSE_SBOX = tuple(_SBOX.index(value) for value in range(256))


def _expand_aes128_key(key: bytes) -> tuple[bytes, ...]:
    if len(key) != 16:
        raise ValueError("AES-128 requires a 16-byte key")
    expanded = list(key)
    round_constant = 1
    while len(expanded) < 176:
        temporary = expanded[-4:]
        if len(expanded) % 16 == 0:
            temporary = temporary[1:] + temporary[:1]
            temporary = [_SBOX[value] for value in temporary]
            temporary[0] ^= round_constant
            round_constant = _gf_multiply(round_constant, 2)
        for value in temporary:
            expanded.append(expanded[len(expanded) - 16] ^ value)
    return tuple(bytes(expanded[index : index + 16]) for index in range(0, 176, 16))


def _add_round_key(state: list[int], round_key: bytes) -> None:
    for index, value in enumerate(round_key):
        state[index] ^= value


def _inverse_shift_rows(state: list[int]) -> None:
    for row_index in range(1, 4):
        row = [state[row_index + 4 * column] for column in range(4)]
        row = row[-row_index:] + row[:-row_index]
        for column, value in enumerate(row):
            state[row_index + 4 * column] = value


def _inverse_mix_columns(state: list[int]) -> None:
    for column in range(4):
        start = column * 4
        first, second, third, fourth = state[start : start + 4]
        state[start : start + 4] = [
            _gf_multiply(first, 14)
            ^ _gf_multiply(second, 11)
            ^ _gf_multiply(third, 13)
            ^ _gf_multiply(fourth, 9),
            _gf_multiply(first, 9)
            ^ _gf_multiply(second, 14)
            ^ _gf_multiply(third, 11)
            ^ _gf_multiply(fourth, 13),
            _gf_multiply(first, 13)
            ^ _gf_multiply(second, 9)
            ^ _gf_multiply(third, 14)
            ^ _gf_multiply(fourth, 11),
            _gf_multiply(first, 11)
            ^ _gf_multiply(second, 13)
            ^ _gf_multiply(third, 9)
            ^ _gf_multiply(fourth, 14),
        ]


def _aes128_decrypt_block(block: bytes, key: bytes) -> bytes:
    if len(block) != 16:
        raise ValueError("AES block must contain 16 bytes")
    round_keys = _expand_aes128_key(key)
    state = list(block)
    _add_round_key(state, round_keys[10])
    for round_index in range(9, 0, -1):
        _inverse_shift_rows(state)
        state[:] = [_INVERSE_SBOX[value] for value in state]
        _add_round_key(state, round_keys[round_index])
        _inverse_mix_columns(state)
    _inverse_shift_rows(state)
    state[:] = [_INVERSE_SBOX[value] for value in state]
    _add_round_key(state, round_keys[0])
    return bytes(state)


def decode_163_key_comment(value: str) -> dict[str, Any]:
    """Decode the encrypted JSON stored in NetEase's ``163 key`` comment."""

    prefix_index = value.find(NETEASE_COMMENT_PREFIX)
    if prefix_index < 0:
        raise NeteaseMetadataError("metadata value has no NetEase 163 key prefix")
    encoded = value[prefix_index + len(NETEASE_COMMENT_PREFIX) :].strip()
    try:
        encrypted = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise NeteaseMetadataError("NetEase 163 key is not valid base64") from error
    if not encrypted or len(encrypted) % 16:
        raise NeteaseMetadataError("NetEase 163 key has an invalid AES payload length")
    decrypted = b"".join(
        _aes128_decrypt_block(encrypted[index : index + 16], NETEASE_AES_KEY)
        for index in range(0, len(encrypted), 16)
    )
    padding = decrypted[-1]
    if padding < 1 or padding > 16 or decrypted[-padding:] != bytes([padding]) * padding:
        raise NeteaseMetadataError("NetEase 163 key has invalid PKCS#7 padding")
    plaintext = decrypted[:-padding]
    if plaintext.startswith(b"music:"):
        plaintext = plaintext[len(b"music:") :]
    try:
        document = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NeteaseMetadataError("NetEase 163 key does not contain valid JSON") from error
    if not isinstance(document, dict):
        raise NeteaseMetadataError("NetEase 163 key JSON root must be an object")
    return document


def _tag_strings(value: Any) -> Iterable[str]:
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _tag_strings(item)
        return
    if isinstance(value, str):
        yield value
        return
    text = getattr(value, "text", None)
    if isinstance(text, str):
        yield text
    elif isinstance(text, (list, tuple)):
        yield from (item for item in text if isinstance(item, str))
    rendered = str(value)
    if rendered:
        yield rendered


def read_netease_metadata(audio_path: str | Path) -> dict[str, Any]:
    """Return decoded NetEase metadata from an MP3/FLAC tag collection."""

    from mutagen import File as MutagenFile
    from mutagen import MutagenError

    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise NeteaseMetadataError(f"audio file does not exist: {path}")
    try:
        media = MutagenFile(str(path))
    except (MutagenError, OSError) as error:
        raise NeteaseMetadataError(
            f"audio metadata could not be read: {path}: {error}"
        ) from error
    tags = getattr(media, "tags", None) if media is not None else None
    if not isinstance(tags, Mapping) and not hasattr(tags, "items"):
        raise NeteaseMetadataError(f"audio file has no readable metadata tags: {path}")

    for key, value in tags.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if normalized_key in {"musicid", "neteasesongid"}:
            for candidate in _tag_strings(value):
                if candidate.strip().isdigit():
                    return {"musicId": int(candidate.strip())}

    errors: list[str] = []
    for _key, value in tags.items():
        for candidate in _tag_strings(value):
            if NETEASE_COMMENT_PREFIX not in candidate:
                continue
            try:
                return decode_163_key_comment(candidate)
            except NeteaseMetadataError as error:
                errors.append(str(error))
    detail = f" ({errors[-1]})" if errors else ""
    raise NeteaseMetadataError(f"audio metadata has no usable NetEase song id: {path}{detail}")


def read_netease_song_id(audio_path: str | Path) -> str:
    """Return a validated numeric NetEase song id from audio metadata."""

    metadata = read_netease_metadata(audio_path)
    value = metadata.get("musicId", metadata.get("music_id"))
    song_id = str(value).strip() if value is not None else ""
    if not song_id.isdigit():
        raise NeteaseMetadataError("NetEase metadata has no numeric musicId")
    return song_id


def normalize_netease_identity(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable song, artist, and album identity fields from a 163 key."""

    artists: list[dict[str, str | None]] = []
    raw_artists = metadata.get("artist")
    if isinstance(raw_artists, list):
        for value in raw_artists:
            if not isinstance(value, (list, tuple)) or not value:
                continue
            name = str(value[0]).strip()
            artist_id = str(value[1]).strip() if len(value) > 1 else ""
            if name:
                artists.append({"name": name, "id": artist_id or None})
    return {
        "song": {
            "id": str(metadata.get("musicId") or "").strip() or None,
            "title": str(metadata.get("musicName") or "").strip() or None,
            "artists": artists,
        },
        "album": {
            "id": str(metadata.get("albumId") or "").strip() or None,
            "title": str(metadata.get("album") or "").strip() or None,
        },
        "duration_ms": metadata.get("duration"),
    }


def fetch_netease_album_detail(
    album_id: str | int,
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Fetch one album document after an explicit caller network choice."""

    normalized_id = str(album_id).strip()
    if not normalized_id.isdigit():
        raise NeteaseMetadataError("NetEase album id must contain digits only")
    request = Request(
        NETEASE_ALBUM_ENDPOINT.format(album_id=normalized_id),
        headers={
            "Accept": "application/json",
            "Referer": "https://music.163.com/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_ALBUM_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise NeteaseMetadataError(
            f"NetEase album API request failed for {normalized_id}: {error}"
        ) from error
    if len(payload) > MAX_ALBUM_RESPONSE_BYTES:
        raise NeteaseMetadataError(
            f"NetEase album API response exceeded {MAX_ALBUM_RESPONSE_BYTES} bytes"
        )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NeteaseMetadataError(
            f"NetEase album API returned invalid JSON for {normalized_id}"
        ) from error
    if not isinstance(document, dict):
        raise NeteaseMetadataError(
            f"NetEase album API returned an invalid response for {normalized_id}"
        )
    if document.get("code") != 200:
        detail = str(document.get("message") or "unknown API error").strip()
        raise NeteaseMetadataError(
            "NetEase album API rejected "
            f"{normalized_id} with code {document.get('code')}: {detail}"
        )
    album = document.get("album")
    if not isinstance(album, dict):
        raise NeteaseMetadataError(
            f"NetEase album API response has no album for {normalized_id}"
        )
    response_album_id = str(album.get("id") or "").strip()
    if response_album_id != normalized_id:
        raise NeteaseMetadataError(
            "NetEase album API returned album id "
            f"{response_album_id or '<missing>'} for requested {normalized_id}"
        )
    return document


def normalize_netease_album_detail(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable album identity and album-artist fields from an API document."""

    raw_album = document.get("album")
    if not isinstance(raw_album, Mapping):
        raise NeteaseMetadataError("NetEase album document has no album object")
    artists: list[dict[str, str | None]] = []
    raw_artists = raw_album.get("artists")
    if isinstance(raw_artists, list):
        for value in raw_artists:
            if not isinstance(value, Mapping):
                continue
            name = str(value.get("name") or "").strip()
            artist_id = str(value.get("id") or "").strip()
            if name and not any(item["name"] == name for item in artists):
                artists.append({"name": name, "id": artist_id or None})
    raw_artist = raw_album.get("artist")
    if not artists and isinstance(raw_artist, Mapping):
        name = str(raw_artist.get("name") or "").strip()
        artist_id = str(raw_artist.get("id") or "").strip()
        if name:
            artists.append({"name": name, "id": artist_id or None})
    size = raw_album.get("size")
    return {
        "id": str(raw_album.get("id") or "").strip() or None,
        "title": str(raw_album.get("name") or "").strip() or None,
        "artists": artists,
        "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
    }


def read_netease_identity(
    audio_path: str | Path,
    *,
    fetch_album: bool = False,
) -> dict[str, Any]:
    """Return normalized local identity, optionally enriched from the album API."""

    identity = normalize_netease_identity(read_netease_metadata(audio_path))
    if fetch_album:
        album_id = identity["album"]["id"]
        if not isinstance(album_id, str) or not album_id.isdigit():
            raise NeteaseMetadataError(
                "audio metadata has no numeric NetEase album id for --fetch-album"
            )
        identity["album"] = normalize_netease_album_detail(
            fetch_netease_album_detail(album_id)
        )
    return identity


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit raw decoded metadata JSON")
    output.add_argument(
        "--identity",
        action="store_true",
        help="emit normalized song, artist, album, and duration metadata JSON",
    )
    parser.add_argument(
        "--fetch-album",
        action="store_true",
        help=(
            "explicitly query the NetEase album API and add album artists; "
            "requires --identity"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.fetch_album and not args.identity:
        print("error: --fetch-album requires --identity", file=sys.stderr)
        return 2
    try:
        metadata = read_netease_metadata(args.audio)
        song_id = read_netease_song_id(args.audio)
        identity = (
            read_netease_identity(args.audio, fetch_album=args.fetch_album)
            if args.identity
            else None
        )
    except NeteaseMetadataError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        output: str | dict[str, Any] = metadata
    elif args.identity:
        if identity is None:  # Defensive; argparse keeps this branch consistent.
            raise AssertionError("identity output was not prepared")
        output = identity
    else:
        output = song_id
    print(
        json.dumps(output, ensure_ascii=False)
        if isinstance(output, dict)
        else output
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
