#!/usr/bin/env python3
"""Build reproducible StrangeUtaGame karaoke timing deliverables.

The builder deliberately keeps all domain data inside the repository's public
``Project``/``Character``/``SugProjectParser`` model.  It obtains the coarse
line timing from NetEase's LRC, optionally refines character onsets with
stable-ts ``model.align_words``, and always has a deterministic mora-weighted
fallback.

The script is intentionally self-contained so a future run can use the frozen
``netease_lyrics.json`` without network access::

    uv run --no-sync python scripts\\karaoke_timing.py

Use ``--refresh-source`` to fetch NetEase again, or ``--alignment
deterministic`` to rebuild without the optional alignment process.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any
from uuid import NAMESPACE_URL, uuid5

try:
    from .karaoke_album import (
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
        project_relative,
    )
    from .karaoke_common.device import (
        DEFAULT_DEVICE,
        add_device_argument,
        normalize_device,
        resolve_device,
    )
    from .karaoke_common.ffmpeg_tools import prepend_ffmpeg_to_path
    from .karaoke_language import (
        DEFAULT_LANGUAGE,
        english_word_spans,
        language_identity,
        mms_granularity,
        normalize_language,
        stable_ts_language,
        timing_granularity,
        uses_ruby,
    )
    from .karaoke_model_paths import WHISPER_MODEL_DIR
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_album import (  # type: ignore[no-redef]
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
        project_relative,
    )
    from karaoke_common.device import (  # type: ignore[no-redef]
        DEFAULT_DEVICE,
        add_device_argument,
        normalize_device,
        resolve_device,
    )
    from karaoke_common.ffmpeg_tools import (  # type: ignore[no-redef]
        prepend_ffmpeg_to_path,
    )
    from karaoke_language import (  # type: ignore[no-redef]
        DEFAULT_LANGUAGE,
        english_word_spans,
        language_identity,
        mms_granularity,
        normalize_language,
        stable_ts_language,
        timing_granularity,
        uses_ruby,
    )
    from karaoke_model_paths import WHISPER_MODEL_DIR  # type: ignore[no-redef]

try:
    from .sug_ruby import (
        fill_missing_project_ruby,
        is_pure_katakana,
        sug_hash,
        timing_fingerprint,
        write_review_sidecar,
    )
except ImportError:  # pragma: no cover - direct script execution
    from sug_ruby import (  # type: ignore[no-redef]
        fill_missing_project_ruby,
        is_pure_katakana,
        sug_hash,
        timing_fingerprint,
        write_review_sidecar,
    )

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from strange_uta_game.backend.application.auto_check_service import (  # noqa: E402
    AutoCheckService,
)
from strange_uta_game.backend.domain import (  # noqa: E402
    Character,
    Project,
    ProjectMetadata,
    Ruby,
    RubyPart,
    Sentence,
    Singer,
)
from strange_uta_game.backend.infrastructure.exporters import (  # noqa: E402
    ASSDirectExporter,
    LRCExporter,
    SRTExporter,
)
from strange_uta_game.backend.infrastructure.persistence.sug_io import (  # noqa: E402
    SugProjectParser,
)

DEFAULT_MODEL_CACHE = WHISPER_MODEL_DIR
DEFAULT_VOCAL_STEMS_DIR = ROOT / ".cache" / "msst-vocals"
SHARED_FONT_DIR = ROOT / "assets" / "fonts" / "HarmonyOS-Sans"
DEFAULT_FONT_FILE = SHARED_FONT_DIR / "HarmonyOS_Sans_SC_Regular.ttf"
NETEASE_ENDPOINT = "https://music.163.com/api/song/lyric"
SUG_VERSION = "0.3.0"
DEFAULT_FONT_NAME = "HarmonyOS Sans SC"
VOICE_ROLES = ("opera", "harmony", "secondary")
# Role colours are persisted on the role Singer itself.  Keep deterministic
# defaults so the timing builder never silently collapses every secondary
# singer onto the project default colour, while still allowing reviewed
# per-role colours to be supplied by the existing JSON override document.
DEFAULT_ROLE_SINGER_COLORS = {
    "opera": "#4ECDC4",
    "harmony": "#45B7D1",
    "secondary": "#C9B1FF",
}
ROLE_SINGER_COLOR_PALETTE = (
    "#4ECDC4",
    "#45B7D1",
    "#FFA07A",
    "#98D8C8",
    "#C9B1FF",
    "#F7DC6F",
    "#82E0AA",
    "#F1948A",
    "#85C1E9",
)

# These are evidence-contract labels, not claims that either forced aligner is
# an independent recognizer.  Keep them in the timing report so downstream
# gates cannot accidentally describe display interpolation as phoneme timing.
ALIGNMENT_EVIDENCE_CONTRACT = {
    "stable_ts": {
        "kind": "known-lyrics-forced-alignment",
        "independent_recognition": False,
        "description": (
            "stable-ts aligns the supplied lyric text; it is not an independent "
            "ASR recognition result."
        ),
    },
    "mms_fa": {
        "kind": "known-token-forced-alignment",
        "independent_recognition": False,
        "units": {
            "zh": "pinyin-character",
            "en": "word",
            "ja": "mora",
        },
        "description": (
            "MMS_FA aligns supplied pinyin-character/word/mora tokens; it is not "
            "independent phoneme recognition."
        ),
    },
    "visual_interpolation": {
        "kind": "display-timing-interpolation",
        "independent_recognition": False,
        "phoneme_alignment": False,
        "description": (
            "Per-character visual interpolation only orders display sweeps; it "
            "does not create phoneme-level acoustic evidence."
        ),
    },
}


def _normalize_voice_role(value: Any) -> str | None:
    """Validate a line/character voice role without changing default behavior."""

    if value is None:
        return None
    role = str(value).strip().lower()
    if not role or role == "default":
        return None
    if role not in VOICE_ROLES:
        raise ValueError(
            f"unsupported voice_role {value!r}; expected default or "
            f"one of {', '.join(VOICE_ROLES)}"
        )
    return role


def _normalize_hex_color(value: Any, *, field_name: str) -> str:
    color = str(value).strip().upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", color):
        raise ValueError(f"invalid {field_name}: {value!r}")
    return color


def _normalize_role_colors(
    role_colors: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Normalize optional persisted colour choices for known voice roles."""

    if role_colors is None:
        return {}
    if not isinstance(role_colors, Mapping):
        raise ValueError("role_colors must be an object")

    normalized: dict[str, str] = {}
    for raw_role, raw_color in role_colors.items():
        role = _normalize_voice_role(raw_role)
        if role is None:
            raise ValueError(
                f"role_colors contains unsupported role {raw_role!r}"
            )
        normalized[role] = _normalize_hex_color(
            raw_color,
            field_name=f"role color for {role}",
        )
    return normalized
HARMONYOS_FONT_URL = (
    "https://developer.huawei.com/images/download/general/HarmonyOS-Sans.zip"
)


@dataclass(frozen=True)
class SongSpec:
    song_id: str
    title: str
    artist: str
    audio_name: str
    slug: str
    expected_duration_ms: int
    sha256_hint: str
    expected_cues: int | None = None
    audio_path: Path | None = None
    deliverable_dir: Path | None = None
    album_title: str = ""
    project_root: Path | None = None
    language: str = DEFAULT_LANGUAGE


def song_spec_from_track(track: AlbumTrack, album: AlbumManifest) -> SongSpec:
    """Derive timing input metadata from one manifest track."""

    return SongSpec(
        song_id=track.song_id,
        title=track.title,
        artist=track.artist,
        audio_name=track.audio_file,
        slug=track.artifact_slug,
        expected_duration_ms=track.expected_duration_ms,
        sha256_hint=track.audio_sha256,
        expected_cues=track.expected_cues,
        audio_path=track.audio_path,
        deliverable_dir=album.deliverable_dir,
        album_title=album.title,
        project_root=album.project_root,
        language=track.language,
    )


def song_specs_from_manifest(
    path: Path | str,
) -> tuple[SongSpec, ...]:
    """Load the manifest and derive the complete timing song collection."""

    album = load_album_manifest(path)
    return tuple(song_spec_from_track(track, album) for track in album.tracks)


_LRC_TAG_RE = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,4}))?\]")
_ASS_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")
_SRT_CLOCK_RE = re.compile(r"^(\d+):(\d{2}):(\d{2}),(\d{3})$")
_SRT_TIME_RE = re.compile(
    r"^(\d+):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d+):(\d{2}):(\d{2}),(\d{3})$"
)
_END_MARKER_RE = re.compile(r"【\s*(?:おわり|完)\s*】", re.IGNORECASE)
_SMALL_KANA = set("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ")
_MORA_JOINING_SMALL_KANA = _SMALL_KANA - {"っ", "ッ"}
_EXTRA_PUNCTUATION = set('。、，．！？!?・:;；：—…~～（）()[]【】「」『』♪、"')
_PHRASE_GAP_MIN_EXCESS_MS = 600
_PHRASE_GAP_MIN_RATIO = 1.6
_LOCAL_MORA_MIN_MS = 180
_LOCAL_MORA_MAX_MS = 450
_LOCAL_MORA_DEFAULT_MS = 320
_PHRASE_TOKEN_MAX_EXPECTED_MS = 1_200


@dataclass(frozen=True)
class LrcEntry:
    timestamp_ms: int
    text: str
    source_line: int
    raw: str


@dataclass(frozen=True)
class LyricLine:
    line_index: int
    source_line: int
    text: str
    start_ms: int
    end_ms: int
    end_source: str


class ReadingHelper:
    """Best-effort pykakasi readings used only for weights and optional ruby."""

    def __init__(self) -> None:
        self._converter = None
        try:
            from pykakasi import kakasi

            self._converter = kakasi()
        except Exception:
            # The deterministic fallback remains valid without pykakasi.
            self._converter = None

    def reading(self, text: str) -> str:
        if not text:
            return ""
        if self._converter is None:
            return text
        try:
            converted = self._converter.convert(text)
            if converted:
                return str(converted[0].get("hira") or text)
        except Exception:
            pass
        return text

    def weight(self, char: str) -> float:
        if not is_timed_character(char):
            return 0.0
        # Small kana are part of the preceding mora.  Sokuon is itself a
        # mora, so it is explicitly kept as a timing unit.
        if char in _MORA_JOINING_SMALL_KANA:
            return 0.0
        reading = self.reading(char)
        moras = split_moras(reading)
        return float(max(1, len(moras)))

    def ruby(self, char: str, language: str = DEFAULT_LANGUAGE) -> Ruby | None:
        if not uses_ruby(language):
            return None
        if not is_timed_character(char):
            return None
        if is_pure_katakana(char):
            return None
        reading = self.reading(char)
        if not reading or reading == char:
            return None
        # Avoid adding a nonsensical ruby for Latin/digits.  A derived ruby
        # is useful for ASS preview but is explicitly reported as derived,
        # never presented as a NetEase romalrc transcription.
        if not any("ぁ" <= c <= "ゖ" or "ァ" <= c <= "ヺ" for c in reading):
            return None
        return Ruby(parts=[RubyPart(text=reading)])


def create_project_ruby_service() -> AutoCheckService:
    """Create the normal sentence analyzer with the project's reading dictionary."""

    dictionary_path = ROOT / "dictionary.json"
    user_dictionary: list[dict[str, Any]] = []
    if dictionary_path.is_file():
        loaded = json.loads(dictionary_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError(f"project dictionary must be a list: {dictionary_path}")
        user_dictionary = loaded
    return AutoCheckService(user_dictionary=user_dictionary)


def split_moras(text: str) -> list[str]:
    """Split Japanese kana into deterministic mora-like units."""

    result: list[str] = []
    for char in text:
        if char in _MORA_JOINING_SMALL_KANA and result:
            result[-1] += char
        else:
            result.append(char)
    return result


def is_timed_character(char: str) -> bool:
    """Return whether a character receives a normal Character checkpoint."""

    if not char or char.isspace() or char in _EXTRA_PUNCTUATION:
        return False
    category = unicodedata.category(char)
    return not (category.startswith(("P", "S")) and char not in {"ー", "―"})


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_audio(spec: SongSpec) -> Path:
    if spec.audio_path is not None:
        audio_path = spec.audio_path.resolve()
        if audio_path.is_file():
            return audio_path
    raise FileNotFoundError(f"audio not found for {spec.song_id}: {spec.audio_name}")


def read_mutagen_duration(path: Path) -> tuple[float, int]:
    """Read the authoritative media duration with mutagen, not catalog data."""

    from mutagen import File as MutagenFile

    media = MutagenFile(str(path))
    if media is None or getattr(media, "info", None) is None:
        raise RuntimeError(f"mutagen could not read audio metadata: {path}")
    seconds = float(media.info.length)
    if seconds <= 0:
        raise RuntimeError(f"audio duration is not positive: {path}")
    return seconds, int(round(seconds * 1000.0))


def fetch_netease_song(song_id: str) -> dict[str, Any]:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    query = urlencode({"id": song_id, "lv": 1, "kv": 1, "tv": -1})
    url = f"{NETEASE_ENDPOINT}?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": "StrangeUtaGame-karaoke-timing/1",
            "Referer": "https://music.163.com/",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("code") not in (None, 200):
        raise RuntimeError(
            f"NetEase lyric API returned an invalid response for {song_id}"
        )
    return payload


def _source_song_record(spec: SongSpec, payload: dict[str, Any]) -> dict[str, Any]:
    lrc = payload.get("lrc") or {}
    romalrc = payload.get("romalrc") or {}
    return {
        "song_id": spec.song_id,
        "title": spec.title,
        "artist": spec.artist,
        "audio_file": spec.audio_name,
        "lrc": str(lrc.get("lyric") or ""),
        "lrc_version": lrc.get("version"),
        "romalrc": str(romalrc.get("lyric") or ""),
        "romalrc_version": romalrc.get("version"),
        "api_code": payload.get("code"),
    }


def load_or_fetch_source(
    path: Path,
    refresh: bool,
    specs: Sequence[SongSpec],
    source_ids: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], str]:
    if path.exists() and not refresh:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("songs"), dict):
            raise ValueError(f"invalid frozen lyric source: {path}")
        for spec in specs:
            if spec.song_id not in data["songs"]:
                raise ValueError(f"frozen lyric source misses song {spec.song_id}")
        return data, "frozen-cache"
    if not refresh:
        raise FileNotFoundError(
            f"frozen lyric source is missing: {path}; "
            "pass --refresh-source to authorize a network refresh"
        )

    song_records: dict[str, Any] = {}
    for spec in specs:
        source_id = (source_ids or {}).get(spec.song_id, spec.song_id)
        song_records[spec.song_id] = _source_song_record(
            spec, fetch_netease_song(source_id)
        )
        song_records[spec.song_id]["netease_song_id"] = source_id
    data = {
        "schema_version": "netease-lyrics-source/v1",
        "endpoint": NETEASE_ENDPOINT,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "songs": song_records,
    }
    _json_dump(path, data)
    return data, "netease-api"


def _lrc_fraction_ms(raw_fraction: str | None) -> int:
    if not raw_fraction:
        return 0
    fraction = (raw_fraction + "000")[:3]
    return int(fraction)


def parse_lrc(raw_lrc: str) -> list[LrcEntry]:
    entries: list[LrcEntry] = []
    for line_number, raw_line in enumerate(raw_lrc.splitlines(), start=1):
        matches = list(_LRC_TAG_RE.finditer(raw_line))
        if not matches:
            continue
        text = raw_line[matches[-1].end() :].strip()
        for match in matches:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            timestamp_ms = (
                minutes * 60_000 + seconds * 1_000 + _lrc_fraction_ms(match.group(3))
            )
            entries.append(
                LrcEntry(
                    timestamp_ms=timestamp_ms,
                    text=text,
                    source_line=line_number,
                    raw=raw_line,
                )
            )
    return entries


def apply_lyric_corrections(
    raw_lrc: str,
    corrections: Sequence[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Apply acoustically evidenced text fixes while preserving the frozen LRC."""

    corrected = raw_lrc
    applied: list[dict[str, Any]] = []
    for index, correction in enumerate(corrections):
        source_text = str(correction.get("source_text") or "")
        corrected_text = str(correction.get("corrected_text") or "")
        if not source_text or not corrected_text:
            raise ValueError(
                f"lyric correction {index} requires source_text and corrected_text"
            )
        occurrences = corrected.count(source_text)
        if occurrences != 1:
            raise ValueError(
                f"lyric correction {index} expected one occurrence of {source_text!r}; "
                f"found {occurrences}"
            )
        corrected = corrected.replace(source_text, corrected_text, 1)
        applied.append(
            {
                "source_text": source_text,
                "corrected_text": corrected_text,
                "review_status": correction.get("review_status"),
                "evidence": list(correction.get("evidence") or []),
            }
        )
    return corrected, applied


def exclusion_reason(text: str) -> str | None:
    normalized = re.sub(r"\s+", "", text)
    if not normalized:
        return "empty"
    if any(
        marker in normalized
        for marker in ("作词", "作曲", "作詞", "編曲", "编曲")
    ):
        return "composer-credit"
    if _END_MARKER_RE.search(text):
        return "end-marker"
    return None


def make_lyric_lines(
    entries: Sequence[LrcEntry], duration_ms: int
) -> tuple[list[LyricLine], dict[str, Any]]:
    valid: list[tuple[int, LrcEntry]] = []
    excluded: dict[str, int] = {}
    for index, entry in enumerate(entries):
        reason = exclusion_reason(entry.text)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            valid.append((index, entry))

    lines: list[LyricLine] = []
    for valid_pos, (entry_index, entry) in enumerate(valid):
        if entry.timestamp_ms >= duration_ms:
            excluded["after-media-end"] = excluded.get("after-media-end", 0) + 1
            continue
        next_lyric_start: int | None = None
        for _, candidate in valid[valid_pos + 1 :]:
            if candidate.timestamp_ms > entry.timestamp_ms:
                next_lyric_start = candidate.timestamp_ms
                break

        next_empty_start: int | None = None
        for candidate in entries[entry_index + 1 :]:
            if (
                candidate.timestamp_ms > entry.timestamp_ms
                and not candidate.text.strip()
            ):
                next_empty_start = candidate.timestamp_ms
                break

        candidates: list[tuple[int, str]] = []
        if next_lyric_start is not None:
            candidates.append((next_lyric_start, "next-lyric"))
        if next_empty_start is not None:
            candidates.append((next_empty_start, "empty-marker"))
        if candidates:
            end_ms = min(candidates, key=lambda item: item[0])[0]
            chosen_sources = "+".join(
                source for value, source in candidates if value == end_ms
            )
        else:
            end_ms = duration_ms
            chosen_sources = "audio-duration"

        end_ms = min(max(end_ms, entry.timestamp_ms), duration_ms)
        if end_ms == entry.timestamp_ms and entry.timestamp_ms < duration_ms:
            # A same-time duplicate is still a valid line interval; keep a
            # deterministic one-millisecond floor for interpolation.
            end_ms = min(duration_ms, entry.timestamp_ms + 1)
        lines.append(
            LyricLine(
                line_index=len(lines),
                source_line=entry.source_line,
                text=entry.text,
                start_ms=max(0, entry.timestamp_ms),
                end_ms=end_ms,
                end_source=chosen_sources,
            )
        )

    return lines, {
        "raw_timestamped_entries": len(entries),
        "valid_lyric_lines": len(lines),
        "excluded": excluded,
    }


def weighted_onsets(
    start_ms: int,
    end_ms: int,
    indices: Sequence[int],
    weights: dict[int, float],
) -> dict[int, int]:
    """Return deterministic weighted onset timestamps in [start_ms, end_ms]."""

    if not indices:
        return {}
    span = max(0, end_ms - start_ms)
    effective_weights = [max(0.0, float(weights.get(index, 1.0))) for index in indices]
    if sum(effective_weights) <= 0:
        effective_weights = [1.0 for _ in indices]
    total = sum(effective_weights)
    result: dict[int, int] = {}
    cumulative = 0.0
    for index, weight in zip(indices, effective_weights):
        value = start_ms + span * cumulative / total
        result[index] = min(end_ms, max(start_ms, int(round(value))))
        cumulative += weight
    return result


def _clean_alignment_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return "".join(char for char in normalized if not char.isspace())


def _line_char_map(text: str) -> tuple[str, list[int]]:
    clean_chars: list[str] = []
    source_indices: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        normalized = unicodedata.normalize("NFKC", char)
        if len(normalized) == 1:
            clean_chars.append(normalized)
            source_indices.append(index)
        else:
            # The target lyrics are Japanese/ASCII; retaining the original
            # character is safer than losing its source index on NFKC.
            clean_chars.append(char)
            source_indices.append(index)
    return "".join(clean_chars), source_indices


def _is_kana(char: str) -> bool:
    return bool(char) and ("ぁ" <= char <= "ゖ" or "ァ" <= char <= "ヺ" or char == "ー")


def contextual_mora_weights(
    text: str,
    helper: ReadingHelper,
    language: str = DEFAULT_LANGUAGE,
) -> dict[int, float]:
    """Estimate per-character mora weights from whole-line readings.

    Per-character lookup can lose context-sensitive readings. Whole-line
    conversion also lets phrase-gap repair reserve a realistic amount of sung
    time for the first token after a visible space.
    """

    language = normalize_language(language)
    weights = {
        index: helper.weight(char)
        for index, char in enumerate(text)
        if is_timed_character(char)
    }
    if language != "ja":
        # Chinese is one checkpoint per displayed character.  English is
        # grouped into words by the caller, so every character starts with a
        # neutral unit weight and inherits its word onset later.
        return {
            index: 1.0
            for index, char in enumerate(text)
            if is_timed_character(char)
        }
    converter = helper._converter
    if converter is None:
        return weights
    try:
        converted = converter.convert(text)
    except Exception:
        return weights

    cursor = 0
    for item in converted:
        original = str(item.get("orig") or "")
        if not original:
            continue
        start = text.find(original, cursor)
        if start < 0:
            continue
        cursor = start + len(original)
        indices = [
            start + offset
            for offset, char in enumerate(original)
            if is_timed_character(char)
        ]
        if not indices:
            continue
        reading = str(item.get("hira") or original)
        total_moras = max(1.0, float(len(split_moras(reading))))
        kana_indices = [index for index in indices if _is_kana(text[index])]
        fixed_kana = sum(max(0.0, weights.get(index, 1.0)) for index in kana_indices)
        flexible = [index for index in indices if index not in kana_indices]
        remaining = max(0.0, total_moras - fixed_kana)
        if flexible:
            baseline = sum(max(0.1, weights.get(index, 1.0)) for index in flexible)
            for index in flexible:
                weights[index] = (
                    remaining * max(0.1, weights.get(index, 1.0)) / baseline
                )
        elif fixed_kana > 0:
            scale = total_moras / fixed_kana
            for index in kana_indices:
                weights[index] = max(0.0, weights.get(index, 1.0)) * scale
    return weights


def _clamp_ms(value: float, start_ms: int, end_ms: int) -> int:
    if not math.isfinite(value):
        return start_ms
    return min(end_ms, max(start_ms, int(round(value))))


def _language_timing_groups(
    text: str,
    timed_indices: Sequence[int],
    language: str,
) -> list[list[int]]:
    """Return fallback timing groups for the requested language.

    Japanese and Chinese keep one group per timed character.  English uses one
    group per contiguous word so all letters share the same acoustic onset;
    visual rendering may still spread equal onsets on the ASS axis.
    """

    if normalize_language(language) != "en":
        return [[index] for index in timed_indices]
    timed = set(timed_indices)
    groups: list[list[int]] = []
    for start, end, _word in english_word_spans(text):
        group = [index for index in range(start, end) if index in timed]
        if group:
            groups.append(group)
            timed.difference_update(group)
    groups.extend([[index] for index in timed_indices if index in timed])
    groups.sort(key=lambda group: group[0])
    return groups


def _fallback_language_onsets(
    line: LyricLine,
    text: str,
    helper: ReadingHelper,
    language: str,
) -> dict[int, int]:
    timed_indices = [
        index for index, char in enumerate(text) if is_timed_character(char)
    ]
    groups = _language_timing_groups(text, timed_indices, language)
    if normalize_language(language) != "en":
        weights = contextual_mora_weights(text, helper, language)
        return weighted_onsets(line.start_ms, line.end_ms, timed_indices, weights)
    group_indices = [group[0] for group in groups]
    group_onsets = weighted_onsets(
        line.start_ms,
        line.end_ms,
        group_indices,
        dict.fromkeys(group_indices, 1.0),
    )
    return {
        index: group_onsets[group[0]]
        for group in groups
        for index in group
    }


def interpolate_from_anchors(
    line: LyricLine,
    text: str,
    helper: ReadingHelper,
    anchors: dict[int, int],
    language: str = DEFAULT_LANGUAGE,
) -> dict[int, int]:
    timed_indices = [
        index for index, char in enumerate(text) if is_timed_character(char)
    ]
    language = normalize_language(language)
    weights = {
        index: helper.weight(text[index])
        for index in timed_indices
    }
    fallback = _fallback_language_onsets(line, text, helper, language)
    if not anchors:
        return fallback

    ordered_anchors: dict[int, int] = {}
    previous = line.start_ms
    for index in timed_indices:
        if index not in anchors:
            continue
        value = max(previous, min(line.end_ms, int(anchors[index])))
        ordered_anchors[index] = value
        previous = value

    result: dict[int, int] = dict(ordered_anchors)
    anchor_positions = [
        position
        for position, index in enumerate(timed_indices)
        if index in ordered_anchors
    ]
    if not anchor_positions:
        return fallback

    first_position = anchor_positions[0]
    if first_position:
        result.update(
            weighted_onsets(
                line.start_ms,
                ordered_anchors[timed_indices[first_position]],
                timed_indices[:first_position],
                weights,
            )
        )

    for left_position, right_position in zip(anchor_positions, anchor_positions[1:]):
        if right_position - left_position > 1:
            result.update(
                weighted_onsets(
                    ordered_anchors[timed_indices[left_position]],
                    ordered_anchors[timed_indices[right_position]],
                    timed_indices[left_position + 1 : right_position],
                    weights,
                )
            )

    last_position = anchor_positions[-1]
    if last_position < len(timed_indices) - 1:
        result.update(
            weighted_onsets(
                ordered_anchors[timed_indices[last_position]],
                line.end_ms,
                timed_indices[last_position + 1 :],
                weights,
            )
        )

    # Final monotonicity closeout also protects against model timestamps that
    # were rounded to the same centisecond boundary.
    previous = line.start_ms
    for index in timed_indices:
        value = max(previous, min(line.end_ms, int(result.get(index, fallback[index]))))
        result[index] = value
        previous = value
    return result


def derive_line_timing(
    line: LyricLine,
    helper: ReadingHelper,
    aligned_words: Sequence[dict[str, Any]] | None,
    alignment_error: str | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> tuple[dict[int, int], dict[str, Any]]:
    language = normalize_language(language)
    fallback_method = (
        "deterministic-mora-interpolation"
        if language == "ja"
        else f"deterministic-{timing_granularity(language)}-interpolation"
    )
    text = line.text
    timed_indices = [
        index for index, char in enumerate(text) if is_timed_character(char)
    ]
    weights = contextual_mora_weights(text, helper, language)
    fallback = _fallback_language_onsets(line, text, helper, language)

    diagnostics: dict[str, Any] = {
        "line_index": line.line_index,
        "source_line": line.source_line,
        "start_ms": line.start_ms,
        "end_ms": line.end_ms,
        "end_source": line.end_source,
        "timed_characters": len(timed_indices),
        "aligned_words": 0,
        "matched_characters": 0,
        "coverage": 0.0,
        "mean_probability": None,
        "min_probability": None,
        "low_probability_words": 0,
        "acoustic_start_ms": None,
        "acoustic_end_ms": None,
        "alignment_error": alignment_error,
        "language": language,
        "language_name": stable_ts_language(language),
        "timing_granularity": timing_granularity(language),
        "word_granularity": language == "en",
    }
    if not aligned_words or not timed_indices:
        diagnostics.update(
            {
                "method": fallback_method,
                "confidence": "low",
                "confidence_reason": alignment_error or "no alignment words",
            }
        )
        return fallback, diagnostics

    target, source_indices = _line_char_map(text)
    search_target = target.casefold() if language == "en" else target
    cursor = 0
    anchors: dict[int, int] = {}
    probabilities: list[float] = []
    matched_word_starts: list[int] = []
    matched_word_ends: list[int] = []
    aligned_word_count = 0
    matched_records: list[dict[str, Any]] = []
    previous_source_end = -1
    for word in aligned_words:
        raw_word = str(word.get("word") or "")
        clean_word = _clean_alignment_text(raw_word)
        if not clean_word:
            continue
        search_word = clean_word.casefold() if language == "en" else clean_word
        position = search_target.find(search_word, cursor)
        if position < 0:
            # A tokenizer can normalize a full-width symbol.  Try a
            # normalized target before giving up on this token.
            normalized_word = unicodedata.normalize("NFKC", search_word)
            position = search_target.find(normalized_word, cursor)
        if position < 0:
            continue
        span_indices = source_indices[position : position + len(clean_word)]
        cursor = position + len(clean_word)
        if not span_indices:
            continue
        source_gap = text[previous_source_end + 1 : span_indices[0]]
        phrase_start = previous_source_end >= 0 and any(
            char.isspace() for char in source_gap
        )
        previous_source_end = span_indices[-1]
        word_start = float(word.get("start", line.start_ms / 1000.0)) * 1000.0
        word_end = float(word.get("end", word_start / 1000.0)) * 1000.0
        if word_end < word_start:
            word_start, word_end = word_end, word_start
        word_start_ms = _clamp_ms(word_start, line.start_ms, line.end_ms)
        word_end_ms = _clamp_ms(word_end, word_start_ms, line.end_ms)
        word_timed_indices = [index for index in span_indices if index in weights]
        if word_timed_indices:
            word_weights = [max(0.0, weights[index]) for index in word_timed_indices]
            if sum(word_weights) <= 0:
                word_weights = [1.0 for _ in word_timed_indices]
            matched_records.append(
                {
                    "raw_word": raw_word,
                    "indices": word_timed_indices,
                    "weights": word_weights,
                    "start_ms": word_start_ms,
                    "end_ms": word_end_ms,
                    "phrase_start": phrase_start,
                    "probability": word.get("probability"),
                }
            )

    mora_rates = []
    for record in matched_records:
        total_weight = sum(record["weights"])
        duration_ms = record["end_ms"] - record["start_ms"]
        if total_weight <= 0 or record["phrase_start"]:
            continue
        rate = duration_ms / total_weight
        if 80 <= rate <= 800:
            mora_rates.append(rate)
    local_mora_ms = int(
        round(
            min(
                _LOCAL_MORA_MAX_MS,
                max(
                    _LOCAL_MORA_MIN_MS,
                    median(mora_rates) if mora_rates else _LOCAL_MORA_DEFAULT_MS,
                ),
            )
        )
    )
    phrase_gap_corrections: list[dict[str, Any]] = []
    for record in matched_records:
        word_start_ms = record["start_ms"]
        word_end_ms = record["end_ms"]
        total_weight = sum(record["weights"])
        expected_ms = min(
            _PHRASE_TOKEN_MAX_EXPECTED_MS,
            max(_LOCAL_MORA_MIN_MS, int(round(total_weight * local_mora_ms))),
        )
        duration_ms = word_end_ms - word_start_ms
        if (
            language == "ja"
            and
            record["phrase_start"]
            and duration_ms - expected_ms >= _PHRASE_GAP_MIN_EXCESS_MS
            and duration_ms >= expected_ms * _PHRASE_GAP_MIN_RATIO
        ):
            corrected_start_ms = max(word_start_ms, word_end_ms - expected_ms)
            phrase_gap_corrections.append(
                {
                    "word": record["raw_word"].strip(),
                    "character_index": record["indices"][0],
                    "original_start_ms": word_start_ms,
                    "corrected_start_ms": corrected_start_ms,
                    "word_end_ms": word_end_ms,
                    "reassigned_gap_ms": corrected_start_ms - word_start_ms,
                }
            )
            word_start_ms = corrected_start_ms

        aligned_word_count += 1
        matched_word_starts.append(word_start_ms)
        matched_word_ends.append(word_end_ms)
        total = sum(record["weights"])
        record_indices = record["indices"]
        if language == "en":
            # MMS/stable-ts returns one word interval.  Preserve that word
            # interval for every displayed letter; the visual renderer will
            # perform any strictly ordered ASS sweep independently.
            for index in record_indices:
                anchors[index] = word_start_ms
            total = 0.0
        cumulative = 0.0
        if language != "en":
            for index, weight in zip(record_indices, record["weights"]):
                if index not in anchors:
                    anchors[index] = _clamp_ms(
                        word_start_ms + (word_end_ms - word_start_ms) * cumulative / total,
                        line.start_ms,
                        line.end_ms,
                    )
                cumulative += weight
        probability = record["probability"]
        if probability is not None:
            try:
                probability_value = float(probability)
                if math.isfinite(probability_value):
                    probabilities.append(probability_value)
            except (TypeError, ValueError):
                pass

    result = interpolate_from_anchors(line, text, helper, anchors, language)
    coverage = len(anchors) / len(timed_indices) if timed_indices else 1.0
    diagnostics.update(
        {
            "method": (
                "stable-ts-constrained+phrase-gap-reassignment"
                if phrase_gap_corrections
                else "stable-ts-constrained"
                if coverage >= 0.99
                else "stable-ts-partial+"
                + fallback_method.removesuffix("-interpolation")
                if anchors
                else fallback_method
            ),
            "confidence": (
                "high"
                if coverage >= 0.99
                and probabilities
                and sum(probabilities) / len(probabilities) >= 0.60
                else "medium"
                if anchors and coverage >= 0.50
                else "low"
            ),
            "confidence_reason": (
                "acoustic token probabilities; long phrase-leading token gaps "
                "are reassigned to the preceding held syllable"
            ),
            "aligned_words": aligned_word_count,
            "matched_characters": len(anchors),
            "coverage": round(coverage, 4),
            "mean_probability": round(sum(probabilities) / len(probabilities), 6)
            if probabilities
            else None,
            "min_probability": round(min(probabilities), 6) if probabilities else None,
            "low_probability_words": sum(1 for value in probabilities if value < 0.25),
            "acoustic_start_ms": min(matched_word_starts)
            if matched_word_starts
            else None,
            "acoustic_end_ms": max(matched_word_ends) if matched_word_ends else None,
            "local_mora_ms": local_mora_ms,
            "phrase_gap_correction_count": len(phrase_gap_corrections),
            "phrase_gap_corrections": phrase_gap_corrections,
        "mms_granularity": mms_granularity(language),
        }
    )
    return result, diagnostics


def _ffmpeg_environment() -> tuple[dict[str, str], str | None]:
    try:
        env, ffmpeg = prepend_ffmpeg_to_path(root=ROOT)
        return env, str(ffmpeg)
    except RuntimeError:
        return os.environ.copy(), None


def model_cache_path(model_name: str, model_cache: Path) -> Path:
    return model_cache / f"{model_name}.pt"


def run_alignment_worker(
    request_path: Path,
    model_name: str,
    model_cache: Path,
    device: str = DEFAULT_DEVICE,
) -> None:
    """Worker entry point; stdout contains one ASCII-safe JSON marker."""

    response: dict[str, Any] = {
        "status": "ok",
        "segments": [],
        "error": None,
        "language": DEFAULT_LANGUAGE,
        "stable_ts_language": stable_ts_language(DEFAULT_LANGUAGE),
        "requested_device": normalize_device(device),
        "resolved_device": None,
    }
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        requested_device = normalize_device(request.get("device", device))
        response["requested_device"] = requested_device
        selection = resolve_device(requested_device)
        response.update(selection.as_report())
        language = normalize_language(request.get("language"), default=DEFAULT_LANGUAGE)
        response["language"] = language
        response["stable_ts_language"] = stable_ts_language(language)
        import stable_whisper

        model_path = model_cache_path(model_name, model_cache)
        if not model_path.is_file():
            raise FileNotFoundError(
                f"Whisper model checkpoint does not exist: {model_path}"
            )
        model = stable_whisper.load_model(
            str(model_path),
            device=selection.resolved,
        )
        audio = str(request["audio"])
        line_inputs = list(request["lines"])
        result = model.align_words(
            audio,
            [
                {
                    "start": float(line["start_ms"]) / 1000.0,
                    "end": float(line["end_ms"]) / 1000.0,
                    "text": line["text"],
                }
                for line in line_inputs
            ],
            stable_ts_language(language),
            normalize_text=True,
            suppress_silence=True,
            regroup=False,
            verbose=None,
        )
        for segment in getattr(result, "segments", []) or []:
            words: list[dict[str, Any]] = []
            for word in getattr(segment, "words", []) or []:
                start = getattr(word, "start", None)
                end = getattr(word, "end", None)
                if start is None or end is None:
                    continue
                probability = getattr(word, "probability", None)
                words.append(
                    {
                        "word": str(getattr(word, "word", "") or ""),
                        "start": float(start),
                        "end": float(end),
                        "probability": float(probability)
                        if probability is not None
                        else None,
                    }
                )
            response["segments"].append(
                {
                    "start": float(getattr(segment, "start", 0.0)),
                    "end": float(getattr(segment, "end", 0.0)),
                    "text": str(getattr(segment, "text", "") or ""),
                    "words": words,
                }
            )
    except Exception as exc:
        response["status"] = "error"
        response["error"] = f"{type(exc).__name__}: {exc}"
    marker = "ALIGNMENT_JSON:" + json.dumps(response, ensure_ascii=True, sort_keys=True)
    sys.stdout.buffer.write(marker.encode("ascii") + b"\n")


def run_forced_alignment(
    audio_path: Path,
    lines: Sequence[LyricLine],
    model_name: str,
    model_cache: Path,
    timeout_seconds: float,
    language: str = DEFAULT_LANGUAGE,
    device: str = DEFAULT_DEVICE,
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    """Run one batch alignment in a killable subprocess.

    Word tokens are assigned to their coarse line by timestamp in the parent,
    so stable-ts segment regrouping cannot change our line model.
    """

    language = normalize_language(language)
    selection = resolve_device(device)
    device_evidence = selection.as_report()
    empty: dict[int, list[dict[str, Any]]] = {}
    if importlib.util.find_spec("stable_whisper") is None:
        return empty, {
            "attempted": False,
            "status": "unavailable",
            "error": "stable_whisper is not importable in the active Python",
            "language": language,
            "stable_ts_language": stable_ts_language(language),
            **device_evidence,
        }
    model_file = model_cache_path(model_name, model_cache)
    if not model_file.is_file():
        return empty, {
            "attempted": False,
            "status": "model-missing",
            "model": model_name,
            "model_path": str(model_file),
            "language": language,
            "stable_ts_language": stable_ts_language(language),
            **device_evidence,
        }

    request = {
        "audio": str(audio_path),
        "lines": [
            {
                "line_index": line.line_index,
                "start_ms": line.start_ms,
                "end_ms": line.end_ms,
                "text": line.text,
            }
            for line in lines
        ],
        "language": language,
        "device": selection.requested,
    }
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    ) as request_file:
        request_path = Path(request_file.name)
        json.dump(request, request_file, ensure_ascii=False)
    try:
        env, ffmpeg_path = _ffmpeg_environment()
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--alignment-worker",
            "--request",
            str(request_path),
            "--model",
            model_name,
            "--model-cache",
            str(model_cache),
            "--device",
            selection.requested,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return empty, {
                "attempted": True,
                "status": "timeout",
                "model": model_name,
                "timeout_seconds": timeout_seconds,
                "ffmpeg_path": ffmpeg_path,
                "error": f"alignment worker exceeded {timeout_seconds:g}s",
                "stderr_tail": _decode_bytes(exc.stderr)[-1000:] if exc.stderr else "",
                "language": language,
                "stable_ts_language": stable_ts_language(language),
                **device_evidence,
            }
        stdout = _decode_bytes(completed.stdout)
        stderr = _decode_bytes(completed.stderr)
        marker = next(
            (
                line
                for line in reversed(stdout.splitlines())
                if line.startswith("ALIGNMENT_JSON:")
            ),
            None,
        )
        if not marker:
            return empty, {
                "attempted": True,
                "status": "worker-error",
                "model": model_name,
                "returncode": completed.returncode,
                "ffmpeg_path": ffmpeg_path,
                "error": "alignment worker did not return JSON",
                "stderr_tail": stderr[-1000:],
                "language": language,
                "stable_ts_language": stable_ts_language(language),
                **device_evidence,
            }
        payload = json.loads(marker[len("ALIGNMENT_JSON:") :])
        if payload.get("status") != "ok":
            return empty, {
                "attempted": True,
                "status": "alignment-error",
                "model": model_name,
                "returncode": completed.returncode,
                "ffmpeg_path": ffmpeg_path,
                "error": payload.get("error") or "unknown alignment error",
                "stderr_tail": stderr[-1000:],
                "language": language,
                "stable_ts_language": stable_ts_language(language),
                "requested_device": payload.get(
                    "requested_device", selection.requested
                ),
                "resolved_device": payload.get(
                    "resolved_device", selection.resolved
                ),
            }

        line_words: dict[int, list[dict[str, Any]]] = {
            line.line_index: [] for line in lines
        }
        for segment in payload.get("segments", []):
            for word in segment.get("words", []):
                try:
                    word_start_ms = float(word["start"]) * 1000.0
                except (KeyError, TypeError, ValueError):
                    continue
                candidate = 0
                for index, line in enumerate(lines):
                    if line.start_ms <= word_start_ms < line.end_ms:
                        candidate = index
                        break
                    if word_start_ms >= line.start_ms:
                        candidate = index
                target_line = lines[candidate]
                line_words[target_line.line_index].append(word)
        return line_words, {
            "attempted": True,
            "status": "ok",
            "model": model_name,
            "returncode": completed.returncode,
            "ffmpeg_path": ffmpeg_path,
            "language": language,
            "stable_ts_language": stable_ts_language(language),
            **device_evidence,
            "segment_count": len(payload.get("segments", [])),
            "word_count": sum(len(words) for words in line_words.values()),
            "model_sha256": sha256_file(model_file),
        }
    finally:
        with contextlib.suppress(OSError):
            request_path.unlink()


def _decode_bytes(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _font_table(data: bytes, tag: bytes) -> tuple[int, int] | None:
    if len(data) < 12:
        return None
    table_count = int.from_bytes(data[4:6], "big")
    for index in range(table_count):
        record = 12 + index * 16
        if record + 16 > len(data):
            break
        if data[record : record + 4] == tag:
            offset = int.from_bytes(data[record + 8 : record + 12], "big")
            length = int.from_bytes(data[record + 12 : record + 16], "big")
            if offset + length <= len(data):
                return offset, length
    return None


def _font_names(data: bytes) -> dict[int, list[str]]:
    table = _font_table(data, b"name")
    if table is None:
        return {}
    offset, length = table
    name_data = data[offset : offset + length]
    if len(name_data) < 6:
        return {}
    count = int.from_bytes(name_data[2:4], "big")
    string_offset = int.from_bytes(name_data[4:6], "big")
    result: dict[int, list[str]] = {}
    for index in range(count):
        record = 6 + index * 12
        if record + 12 > len(name_data):
            break
        platform = int.from_bytes(name_data[record : record + 2], "big")
        name_id = int.from_bytes(name_data[record + 6 : record + 8], "big")
        string_length = int.from_bytes(name_data[record + 8 : record + 10], "big")
        string_start = string_offset + int.from_bytes(
            name_data[record + 10 : record + 12], "big"
        )
        raw = name_data[string_start : string_start + string_length]
        try:
            value = raw.decode("utf-16-be" if platform in (0, 3) else "mac_roman")
        except UnicodeDecodeError:
            value = raw.decode("utf-8", errors="replace")
        if value and value not in result.setdefault(name_id, []):
            result[name_id].append(value)
    return result


def _font_has_glyphs(data: bytes, codepoints: Iterable[int]) -> list[int]:
    table = _font_table(data, b"cmap")
    if table is None:
        return list(codepoints)
    offset, length = table
    cmap = data[offset : offset + length]
    if len(cmap) < 4:
        return list(codepoints)
    target = set(codepoints)
    found: set[int] = set()
    tables = int.from_bytes(cmap[2:4], "big")
    for index in range(tables):
        record = 4 + index * 8
        if record + 8 > len(cmap):
            break
        sub_offset = int.from_bytes(cmap[record + 4 : record + 8], "big")
        if sub_offset + 2 > len(cmap):
            continue
        fmt = int.from_bytes(cmap[sub_offset : sub_offset + 2], "big")
        if fmt == 4 and sub_offset + 16 <= len(cmap):
            seg_count = (
                int.from_bytes(cmap[sub_offset + 6 : sub_offset + 8], "big") // 2
            )
            end_start = sub_offset + 14
            start_start = end_start + seg_count * 2 + 2
            delta_start = start_start + seg_count * 2
            range_start = delta_start + seg_count * 2
            if range_start > len(cmap):
                continue
            for codepoint in target - found:
                if codepoint > 0xFFFF:
                    continue
                for segment in range(seg_count):
                    end_code = int.from_bytes(
                        cmap[end_start + segment * 2 : end_start + segment * 2 + 2],
                        "big",
                    )
                    start_code = int.from_bytes(
                        cmap[start_start + segment * 2 : start_start + segment * 2 + 2],
                        "big",
                    )
                    if not start_code <= codepoint <= end_code:
                        continue
                    delta = int.from_bytes(
                        cmap[delta_start + segment * 2 : delta_start + segment * 2 + 2],
                        "big",
                        signed=True,
                    )
                    range_offset = int.from_bytes(
                        cmap[range_start + segment * 2 : range_start + segment * 2 + 2],
                        "big",
                    )
                    if range_offset == 0:
                        glyph = (codepoint + delta) & 0xFFFF
                    else:
                        glyph_address = (
                            range_start
                            + segment * 2
                            + range_offset
                            + 2 * (codepoint - start_code)
                        )
                        if glyph_address + 2 > len(cmap):
                            glyph = 0
                        else:
                            glyph = int.from_bytes(
                                cmap[glyph_address : glyph_address + 2], "big"
                            )
                            if glyph:
                                glyph = (glyph + delta) & 0xFFFF
                    if glyph:
                        found.add(codepoint)
                    break
        elif fmt == 12 and sub_offset + 16 <= len(cmap):
            groups = int.from_bytes(cmap[sub_offset + 12 : sub_offset + 16], "big")
            group_start = sub_offset + 16
            for group in range(groups):
                row = group_start + group * 12
                if row + 12 > len(cmap):
                    break
                first = int.from_bytes(cmap[row : row + 4], "big")
                last = int.from_bytes(cmap[row + 4 : row + 8], "big")
                for codepoint in target - found:
                    if first <= codepoint <= last:
                        glyph = (
                            int.from_bytes(cmap[row + 8 : row + 12], "big")
                            + codepoint
                            - first
                        )
                        if glyph:
                            found.add(codepoint)
    return sorted(target - found)


def verify_font(
    font_name: str,
    font_path: Path,
    lyric_texts: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Verify family coverage for representative and actual lyric glyphs."""

    result: dict[str, Any] = {
        "requested_family": font_name,
        "font_source": HARMONYOS_FONT_URL
        if font_name.startswith("HarmonyOS Sans")
        else None,
        "font_path": str(font_path),
        "status": "missing",
        "ok": False,
    }
    if not font_path.exists():
        result["error"] = (
            "font file not found; ASS still carries the requested family name"
        )
        return result
    try:
        data = font_path.read_bytes()
        names = _font_names(data)
        family_names = names.get(1, [])
        glyph_text = "あいうえお漢字歌詞"
        representative_missing = _font_has_glyphs(
            data,
            (ord(char) for char in glyph_text),
        )
        actual_characters = "".join(
            dict.fromkeys(
                char
                for text in (lyric_texts or ())
                for char in str(text)
                if not char.isspace()
            )
        )
        actual_missing = _font_has_glyphs(
            data,
            (ord(char) for char in actual_characters),
        )
        result.update(
            {
                "status": "verified",
                "internal_family_names": family_names,
                "full_names": names.get(4, []),
                "postscript_names": names.get(6, []),
                "family_matches": font_name in family_names,
                "representative_japanese_glyphs": glyph_text,
                "missing_representative_glyphs": [
                    chr(codepoint) for codepoint in representative_missing
                ],
                "actual_lyric_characters": actual_characters,
                "actual_lyric_glyph_check": {
                    "provided": lyric_texts is not None,
                    "required_character_count": len(actual_characters),
                    "missing_glyphs": [
                        chr(codepoint) for codepoint in actual_missing
                    ],
                    "ok": not actual_missing,
                },
                "ok": font_name in family_names
                and not representative_missing
                and not actual_missing,
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def stable_id(kind: str, song_id: str, index: int = 0) -> str:
    return str(uuid5(NAMESPACE_URL, f"strange-uta-game:{kind}:{song_id}:{index}"))


def _voice_role_assignments(
    line: LyricLine,
    line_override: Mapping[str, Any],
) -> tuple[str | None, dict[int, str]]:
    """Return a validated line role and optional character role map.

    ``voice_role`` is the compact line-level form.  For mixed lines callers may
    use ``character_voice_roles`` (or the older-friendly ``voice_roles`` alias)
    with character indices as keys.  The source override remains the audit
    record; only the resulting singer IDs are written to SUG.
    """

    raw_line_role = line_override.get("voice_role")
    character_roles: Any = line_override.get(
        "character_voice_roles", line_override.get("voice_roles", {})
    )
    if isinstance(raw_line_role, Mapping):
        if character_roles:
            raise ValueError(
                f"line {line.line_index} cannot combine mapping voice_role "
                "with character_voice_roles"
            )
        character_roles = raw_line_role
        raw_line_role = None
    line_role = _normalize_voice_role(raw_line_role)
    if character_roles is None:
        character_roles = {}
    if not isinstance(character_roles, Mapping):
        raise ValueError(
            f"line {line.line_index} character_voice_roles must be an object"
        )
    normalized: dict[int, str] = {}
    for raw_index, raw_role in character_roles.items():
        try:
            character_index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"line {line.line_index} voice role index is not an integer: "
                f"{raw_index!r}"
            ) from exc
        if not 0 <= character_index < len(line.text):
            raise ValueError(
                f"line {line.line_index} voice role index {character_index} "
                "is outside the lyric text"
            )
        role = _normalize_voice_role(raw_role)
        if role is None:
            continue
        normalized[character_index] = role
    return line_role, normalized


def collapse_english_sentence_to_word_tokens(sentence: Sentence) -> Sentence:
    """Collapse an English editable sentence to one checkpoint per word.

    StrangeUtaGame permits ``Character.char`` to contain more than one visible
    codepoint.  English projects use that capability so the timing editor shows
    one adjustable checkpoint for each non-space token.  Spaces remain explicit
    untimed tokens, preserving the source text exactly.  Final renderers may
    interpolate a visual sweep inside each word without persisting letter-level
    checkpoints back into the editable project.
    """

    if not sentence.characters:
        return sentence
    if any(len(character.char) != 1 for character in sentence.characters):
        return sentence

    source_text = sentence.text
    collapsed: list[Character] = []
    for match in re.finditer(r"\s+|\S+", source_text):
        members = sentence.characters[match.start() : match.end()]
        if not members:
            continue
        singer_ids = {member.singer_id for member in members if member.singer_id}
        if len(singer_ids) > 1:
            raise ValueError(
                "cannot collapse one English word across multiple singers: "
                f"{match.group(0)!r}"
            )
        timestamps = [
            int(timestamp)
            for member in members
            for timestamp in member.timestamps
        ]
        first_timestamp = min(timestamps) if timestamps else None
        last = members[-1]
        token = Character(
            char=match.group(0),
            check_count=1 if first_timestamp is not None else 0,
            timestamps=[] if first_timestamp is None else [first_timestamp],
            sentence_end_ts=(
                last.sentence_end_ts if last.is_sentence_end else None
            ),
            linked_to_next=False,
            is_line_end=last.is_line_end,
            is_sentence_end=last.is_sentence_end,
            is_rest=all(member.is_rest for member in members),
            singer_id=next(iter(singer_ids), sentence.singer_id),
            needs_guide=any(member.needs_guide for member in members),
            is_guide=all(member.is_guide for member in members),
            force_singer_tag=any(
                member.force_singer_tag for member in members
            ),
        )
        collapsed.append(token)

    if "".join(token.char for token in collapsed) != source_text:
        raise ValueError("English word-token collapse changed the source text")
    sentence.characters = collapsed
    return sentence


def build_project(
    spec: SongSpec,
    duration_ms: int,
    lines: Sequence[LyricLine],
    aligned_words: dict[int, list[dict[str, Any]]],
    alignment_meta: dict[str, Any],
    timing_overrides: dict[str, Any] | None = None,
    singer_color: str = "#FF6B6B",
    role_colors: Mapping[str, Any] | None = None,
) -> tuple[Project, list[dict[str, Any]]]:
    language = normalize_language(spec.language)
    helper = ReadingHelper()
    singer_id = stable_id("singer", spec.song_id)
    singer_color = _normalize_hex_color(
        singer_color,
        field_name="singer highlight color",
    )
    normalized_role_colors = _normalize_role_colors(role_colors)
    allocated_role_colors = set(normalized_role_colors.values())
    singer = Singer(
        id=singer_id,
        name=spec.artist,
        color=singer_color,
        is_default=True,
        is_placeholder=False,
        display_priority=0,
        backend_number=1,
    )
    role_singers: dict[str, Singer] = {}

    def singer_for_role(role: str) -> Singer:
        existing = role_singers.get(role)
        if existing is not None:
            return existing

        role_color = normalized_role_colors.get(role)
        if role_color is None:
            candidates = (
                DEFAULT_ROLE_SINGER_COLORS.get(role),
                *ROLE_SINGER_COLOR_PALETTE,
            )
            role_color = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate
                    and candidate != singer_color
                    and candidate not in allocated_role_colors
                ),
                None,
            )
            if role_color is None:
                raise ValueError(
                    f"no independent colour available for voice role {role!r}"
                )
        allocated_role_colors.add(role_color)
        role_singer = Singer(
            id=stable_id("singer", f"{spec.song_id}:{role}"),
            name=role,
            color=role_color,
            is_default=False,
            is_placeholder=False,
            display_priority=1 + VOICE_ROLES.index(role),
            backend_number=2 + VOICE_ROLES.index(role),
            group=role,
        )
        role_singers[role] = role_singer
        return role_singer

    sentences: list[Sentence] = []
    line_reports: list[dict[str, Any]] = []
    timing_overrides = timing_overrides or {}
    for line in lines:
        line_override = timing_overrides.get(str(line.line_index), {})
        if not isinstance(line_override, Mapping):
            raise ValueError(f"line {line.line_index} override must be an object")
        line_role, character_roles = _voice_role_assignments(line, line_override)
        if line_role is not None:
            singer_for_role(line_role)
        for role in character_roles.values():
            singer_for_role(role)
        line_singer_id = (
            role_singers[line_role].id if line_role is not None else singer_id
        )
        characters: list[Character] = []
        for index, char in enumerate(line.text):
            timed = is_timed_character(char)
            character_role = character_roles.get(index, line_role)
            character_singer_id = (
                role_singers[character_role].id
                if character_role is not None
                else singer_id
            )
            character = Character(
                char=char,
                # Ruby is filled once, after timing is canonicalized and just
                # before SUG serialization.  Keeping construction ruby-free
                # prevents a candidate generator from overwriting existing
                # editable facts during rebuilds.
                ruby=None,
                check_count=1 if timed else 0,
                is_line_end=index == len(line.text) - 1,
                is_sentence_end=index == len(line.text) - 1,
                singer_id=character_singer_id,
            )
            characters.append(character)
        sentence = Sentence(
            id=stable_id("sentence", spec.song_id, line.line_index),
            singer_id=line_singer_id,
            characters=characters,
        )
        timing, diagnostics = derive_line_timing(
            line,
            helper,
            aligned_words.get(line.line_index),
            alignment_error=None
            if alignment_meta.get("status") == "ok"
            else alignment_meta.get("error"),
            language=language,
        )
        character_window_end_ms = max(
            line.end_ms,
            int(line_override.get("release_override_ms", line.end_ms)),
        )
        applied_overrides: list[dict[str, Any]] = []
        for raw_index, raw_value in line_override.get(
            "character_overrides_ms", {}
        ).items():
            character_index = int(raw_index)
            if character_index not in timing:
                raise ValueError(
                    f"line {line.line_index} character {character_index} is not timed"
                )
            override_ms = int(raw_value)
            if not line.start_ms <= override_ms <= character_window_end_ms:
                raise ValueError(
                    f"line {line.line_index} override {override_ms}ms is outside "
                    f"its {line.start_ms}..{character_window_end_ms}ms window"
                )
            previous_ms = timing[character_index]
            timing[character_index] = override_ms
            applied_overrides.append(
                {
                    "character_index": character_index,
                    "character": line.text[character_index],
                    "previous_ms": previous_ms,
                    "override_ms": override_ms,
                    "reason": line_override.get("reason"),
                    "review_status": line_override.get("review_status"),
                }
            )
        if applied_overrides:
            ordered_indices = sorted(timing)
            previous_ms = line.start_ms
            for character_index in ordered_indices:
                timing[character_index] = max(previous_ms, timing[character_index])
                previous_ms = timing[character_index]
        diagnostics["review_status"] = line_override.get("review_status")
        diagnostics["review_evidence"] = line_override.get("evidence", [])
        diagnostics["timing_overrides"] = applied_overrides
        diagnostics["voice_role"] = line_role
        diagnostics["character_voice_roles"] = {
            str(index): role for index, role in sorted(character_roles.items())
        }
        visual_release_overrides: dict[int, int] = {}
        raw_visual_releases = line_override.get("visual_release_overrides_ms", {})
        if not isinstance(raw_visual_releases, dict):
            raise ValueError(
                f"line {line.line_index} visual release overrides must be an object"
            )
        ordered_timing_indices = sorted(timing)
        for character_index_text, release_value in raw_visual_releases.items():
            character_index = int(character_index_text)
            if character_index not in timing:
                raise ValueError(
                    f"line {line.line_index} visual release index "
                    f"{character_index} has no character onset"
                )
            next_indices = [
                index for index in ordered_timing_indices if index > character_index
            ]
            if not next_indices:
                raise ValueError(
                    f"line {line.line_index} visual release index "
                    f"{character_index} has no following character"
                )
            release_value = int(release_value)
            next_onset = int(timing[next_indices[0]])
            if not int(timing[character_index]) + 10 <= release_value <= next_onset:
                raise ValueError(
                    f"line {line.line_index} visual release override "
                    f"{character_index}={release_value}ms is outside "
                    f"{int(timing[character_index]) + 10}..{next_onset}ms"
                )
            visual_release_overrides[character_index] = release_value
        diagnostics["visual_release_overrides_ms"] = {
            str(index): value
            for index, value in sorted(visual_release_overrides.items())
        }
        for index, timestamp_ms in timing.items():
            sentence.characters[index].add_timestamp(timestamp_ms, checkpoint_idx=0)
        if sentence.characters:
            last_onset_ms = max(timing.values(), default=line.start_ms)
            acoustic_end_ms = diagnostics.get("acoustic_end_ms")
            # The LRC line axis is the safe visual release boundary.  Stable-ts
            # word ends often stop at the consonant/vowel alignment boundary
            # and can precede a sung tail by hundreds of milliseconds.  Keep
            # the completed line until the explicit empty marker or the next
            # lyric starts so held final notes never disappear early.
            release_override = line_override.get("release_override_ms")
            if release_override is None:
                release_ms = max(last_onset_ms, line.end_ms)
                release_source = f"lrc-line-axis:{line.end_source}"
            else:
                release_ms = int(release_override)
                if not last_onset_ms <= release_ms <= duration_ms:
                    raise ValueError(
                        f"line {line.line_index} release override {release_ms}ms "
                        f"is outside {last_onset_ms}..{duration_ms}ms"
                    )
                release_source = "timing-override:dual-audio-mms-tail"
            sentence.characters[-1].set_sentence_end_ts(release_ms)
            diagnostics["release_ms"] = release_ms
            diagnostics["release_source"] = release_source
            diagnostics["release_hold_after_last_onset_ms"] = release_ms - last_onset_ms
            diagnostics["release_extension_after_acoustic_end_ms"] = (
                release_ms - int(acoustic_end_ms)
                if acoustic_end_ms is not None
                else None
            )
        if language == "en":
            letter_character_count = len(sentence.characters)
            collapse_english_sentence_to_word_tokens(sentence)
            diagnostics["editable_timing_unit"] = "word"
            diagnostics["editable_token_count"] = len(sentence.characters)
            diagnostics["editable_timing_point_count"] = sum(
                character.check_count for character in sentence.characters
            )
            diagnostics["render_sweep_unit"] = "interpolated-visible-letter"
            diagnostics["collapsed_letter_character_count"] = (
                letter_character_count
            )
        sentences.append(sentence)
        line_reports.append(diagnostics)

    metadata = ProjectMetadata(
        title=spec.title,
        artist=spec.artist,
        album=spec.album_title or "karaoke-album",
        language=language,
        # Fixed metadata keeps .sug bytes reproducible across invocations.
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    project = Project(
        id=stable_id("project", spec.song_id),
        sentences=sentences,
        singers=[singer, *[role_singers[role] for role in VOICE_ROLES if role in role_singers]],
        metadata=metadata,
        audio_duration_ms=duration_ms,
    )
    return project, line_reports


def project_signature(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "duration": project.audio_duration_ms,
        "title": project.metadata.title,
        "language": normalize_language(project.metadata.language),
        "singers": [
            {
                "id": singer.id,
                "name": singer.name,
                "color": singer.color,
                "complement_color": singer.complement_color,
                "color_mode": singer.color_mode,
                "split_colors": list(singer.split_colors),
                "backend_number": singer.backend_number,
                "is_default": singer.is_default,
                "is_placeholder": singer.is_placeholder,
                "display_priority": singer.display_priority,
                "enabled": singer.enabled,
                "group": singer.group,
            }
            for singer in project.singers
        ],
        "sentences": [
            {
                "id": sentence.id,
                "singer_id": sentence.singer_id,
                "text": sentence.text,
                "chars": [
                    {
                        "char": character.char,
                        "singer_id": character.singer_id,
                        "check_count": character.check_count,
                        "timestamps": list(character.timestamps),
                        "sentence_end_ts": character.sentence_end_ts,
                        "linked_to_next": character.linked_to_next,
                        "is_line_end": character.is_line_end,
                        "is_sentence_end": character.is_sentence_end,
                        "ruby": [part.text for part in character.ruby.parts]
                        if character.ruby
                        else None,
                    }
                    for character in sentence.characters
                ],
            }
            for sentence in project.sentences
        ],
    }


def validate_project(project: Project, duration_ms: int) -> dict[str, Any]:
    errors: list[str] = []
    all_timestamps: list[int] = []
    line_first_onsets: list[int | None] = []
    line_last_onsets: list[int | None] = []
    line_releases: list[int | None] = []
    line_onset_axes_non_decreasing = True
    release_count = 0
    for line_index, sentence in enumerate(project.sentences):
        if not sentence.characters:
            errors.append(f"line {line_index}: empty sentence")
            line_first_onsets.append(None)
            line_last_onsets.append(None)
            line_releases.append(None)
            continue
        sentence_onsets: list[int] = []
        sentence_release: int | None = None
        for char_index, character in enumerate(sentence.characters):
            previous_char_timestamp: int | None = None
            for timestamp in character.timestamps:
                if timestamp < 0 or timestamp > duration_ms:
                    errors.append(
                        f"line {line_index} char {char_index}: timestamp outside media"
                    )
                if (
                    previous_char_timestamp is not None
                    and timestamp < previous_char_timestamp
                ):
                    errors.append(
                        f"line {line_index} char {char_index}: timestamps decrease"
                    )
                previous_char_timestamp = timestamp
                sentence_onsets.append(timestamp)
                all_timestamps.append(timestamp)
            if character.is_sentence_end:
                release_count += 1
                if character.sentence_end_ts is None:
                    errors.append(f"line {line_index}: missing sentence release")
                else:
                    if (
                        character.sentence_end_ts < 0
                        or character.sentence_end_ts > duration_ms
                    ):
                        errors.append(f"line {line_index}: release outside media")
                    if (
                        character.timestamps
                        and character.sentence_end_ts < character.timestamps[-1]
                    ):
                        errors.append(
                            f"line {line_index}: release precedes final onset"
                        )
                    sentence_release = character.sentence_end_ts
                    all_timestamps.append(character.sentence_end_ts)
        if not sentence.is_fully_timed():
            errors.append(f"line {line_index}: not fully timed")
        sentence_onsets_non_decreasing = all(
            left <= right for left, right in zip(sentence_onsets, sentence_onsets[1:])
        )
        if not sentence_onsets_non_decreasing:
            errors.append(f"line {line_index}: character onset sequence decreases")
            line_onset_axes_non_decreasing = False
        line_first_onsets.append(min(sentence_onsets) if sentence_onsets else None)
        line_last_onsets.append(max(sentence_onsets) if sentence_onsets else None)
        line_releases.append(sentence_release)

    present_line_starts = [
        onset_ms for onset_ms in line_first_onsets if onset_ms is not None
    ]
    line_start_axis_non_decreasing = all(
        left <= right
        for left, right in zip(present_line_starts, present_line_starts[1:])
    )
    if not line_start_axis_non_decreasing:
        errors.append("line-start onset sequence decreases")
    onset_axis_non_decreasing = (
        line_onset_axes_non_decreasing and line_start_axis_non_decreasing
    )
    if any(timestamp < 0 or timestamp > duration_ms for timestamp in all_timestamps):
        errors.append("global timestamp sequence exceeds media bounds")
    release_overlaps = [
        {
            "line_index": line_index,
            "release_ms": release_ms,
            "next_line_first_onset_ms": next_onset_ms,
            "overlap_ms": release_ms - next_onset_ms,
        }
        for line_index, (release_ms, next_onset_ms) in enumerate(
            zip(line_releases, line_first_onsets[1:])
        )
        if release_ms is not None
        and next_onset_ms is not None
        and release_ms > next_onset_ms
    ]
    cross_line_onset_overlaps = [
        {
            "line_index": line_index,
            "last_onset_ms": last_onset_ms,
            "next_line_first_onset_ms": next_onset_ms,
            "overlap_ms": last_onset_ms - next_onset_ms,
        }
        for line_index, (last_onset_ms, next_onset_ms) in enumerate(
            zip(line_last_onsets, line_first_onsets[1:])
        )
        if last_onset_ms is not None
        and next_onset_ms is not None
        and last_onset_ms > next_onset_ms
    ]
    errors.extend(project.validate())
    return {
        "ok": not errors,
        "errors": errors,
        "timestamp_count": len(all_timestamps),
        "release_point_count": release_count,
        "global_non_decreasing": onset_axis_non_decreasing,
        "onset_axis_non_decreasing": onset_axis_non_decreasing,
        "line_onset_axes_non_decreasing": line_onset_axes_non_decreasing,
        "line_start_axis_non_decreasing": line_start_axis_non_decreasing,
        "cross_line_onset_overlap_count": len(cross_line_onset_overlaps),
        "cross_line_onset_overlaps": cross_line_onset_overlaps,
        "release_overlap_count": len(release_overlaps),
        "release_overlaps": release_overlaps,
        "all_timestamps_within_media": all(
            0 <= timestamp <= duration_ms for timestamp in all_timestamps
        ),
        "project_valid": project.is_valid(),
    }


def ass_time_to_ms(value: str) -> int:
    match = _ASS_TIME_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid ASS timestamp: {value}")
    return (
        int(match.group(1)) * 3_600_000
        + int(match.group(2)) * 60_000
        + int(match.group(3)) * 1_000
        + int(match.group(4)) * 10
    )


def ms_to_ass_time(timestamp_ms: int) -> str:
    timestamp_ms = max(0, int(timestamp_ms))
    hours, remaining = divmod(timestamp_ms, 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    seconds, milliseconds = divmod(remaining, 1_000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds // 10:02d}"


def _burn_ready_character_onsets(
    chars: Sequence[Any],
    line_end_ms: int,
) -> list[int]:
    """Return one strictly ordered libass onset per displayed character.

    This is intentionally a visual-only transformation. Source checkpoints
    stay unchanged while equal timestamps and untimed punctuation/spaces are
    spread before the next acoustic anchor or line release.
    """

    raw: list[int | None] = [
        int(char.global_timestamps[0]) if char.global_timestamps else None
        for char in chars
    ]
    anchors = [value for value in raw if value is not None]
    if not anchors:
        return []
    first_anchor_index = next(
        index for index, value in enumerate(raw) if value is not None
    )
    first_anchor = int(raw[first_anchor_index])
    result = [first_anchor] * len(raw)
    current = first_anchor
    for index, value in enumerate(raw):
        if value is not None:
            current = int(value)
        result[index] = current
    for index in range(first_anchor_index - 1, -1, -1):
        result[index] = max(0, result[index + 1] - 10)

    terminal = max(int(line_end_ms), result[-1] + 10)
    index = first_anchor_index
    while index < len(result):
        end = index + 1
        while end < len(result) and result[end] == result[index]:
            end += 1
        if end - index > 1 or any(raw[pos] is None for pos in range(index, end)):
            boundary = result[end] if end < len(result) else terminal
            boundary = max(boundary, result[index] + 10 * (end - index))
            start = result[index]
            span = boundary - start
            count = end - index
            for position in range(index, end):
                result[position] = start + round(
                    span * (position - index) / count
                )
        index = end
    for index in range(1, len(result)):
        if result[index] // 10 <= result[index - 1] // 10:
            result[index] = (result[index - 1] // 10 + 1) * 10
    return result


def _burn_ready_ass_text(
    sentence: Sentence,
    line_end_ms: int,
    event_start_ms: int,
) -> str:
    """Render a libass-safe karaoke line from the domain sentence.

    The official ASS exporter intentionally emits Aegisub karaoke-template
    ruby syntax (``|<``/``#|``) for preview/template workflows.  libass does
    not execute that template language, so the delivered ASS must be a plain
    original-text line with ordinary ``\\k`` tags.  A character can have
    multiple domain checkpoints for a multi-mora ruby; those checkpoints are
    deliberately collapsed into the one visible character's interval here.
    The complete ruby/checkpoint data remains in the SUG project.
    """

    chars = sentence.characters
    if not chars:
        return "{\\k0}{\\k0}"

    escape = ASSDirectExporter._escape_ass_text
    visual_onsets = _burn_ready_character_onsets(chars, line_end_ms)
    if not visual_onsets:
        plain_text = "".join(escape(char.char) for char in chars)
        return "{\\k0}" + plain_text + "{\\k0}"

    first_start_ms = visual_onsets[0]
    lead_in_cs = max(0, int(first_start_ms) - int(event_start_ms)) // 10
    # The source exporter starts the event before the vocal onset so the
    # complete line is readable in advance.  This empty karaoke syllable is
    # essential: without it libass colours the first visible glyph at the
    # event start (200 ms early with the current exporter pre-roll).
    parts: list[str] = [f"{{\\k{lead_in_cs}}}"]
    effective_end_ms = max(int(line_end_ms), visual_onsets[-1] + 10)
    for index, (char, start_ms) in enumerate(
        zip(chars, visual_onsets, strict=True)
    ):
        next_start_ms = (
            visual_onsets[index + 1]
            if index + 1 < len(visual_onsets)
            else effective_end_ms
        )
        duration_cs = max(1, int(round((next_start_ms - start_ms) / 10)))
        parts.append(f"{{\\kf{duration_cs}}}{escape(char.char)}")

    parts.append("{\\k0}")
    return "".join(parts)


def _ass_visible_text(text: str) -> str:
    """Remove ASS override blocks for a plain-text comparison."""

    return re.sub(r"\{[^}]*\}", "", text).replace(r"\N", "\n")


def _ass_style_metrics(lines: Sequence[str], font_name: str) -> dict[str, Any]:
    required = {
        "font_name": font_name,
        "font_size": "58",
        "bold": "1",
        "outline": "3",
        "alignment": "2",
        "margin_l": "980",
        "margin_r": "80",
        "margin_v": "100",
    }
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    for line in lines:
        if not line.startswith("Style:"):
            continue
        fields = line.split(",")
        if len(fields) < 22:
            errors.append("malformed style line")
            continue
        rows.append(
            {
                "name": fields[0][len("Style: ") :],
                "font_name": fields[1],
                "font_size": fields[2],
                "bold": fields[7],
                "outline": fields[16],
                "alignment": fields[18],
                "margin_l": fields[19],
                "margin_r": fields[20],
                "margin_v": fields[21],
            }
        )
    checks = {
        key: bool(rows) and all(row.get(key) == value for row in rows)
        for key, value in required.items()
    }
    checks["styles_present"] = bool(rows)
    checks["all_required_values"] = all(checks.values())
    return {"checks": checks, "styles": rows, "errors": errors}


def legalize_ass(
    path: Path,
    duration_ms: int,
    font_name: str,
    project: Project | None = None,
) -> dict[str, Any]:
    source_lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    inserted_resolution = False
    dialogue_count = 0
    dialogue_errors: list[str] = []
    event_times: list[tuple[int, int]] = []
    dialogue_texts: list[str] = []
    burn_sentences = (
        [sentence for sentence in project.sentences if sentence.has_timetags]
        if project is not None
        else []
    )
    sentence_cursor = 0
    style_values = {
        2: "58",  # Fontsize: approximately 58 px at 1920x1080
        3: "&H000000FF",  # Primary: sung red (ASS stores BGR)
        4: "&H00FFFFFF",  # Secondary: unsung white
        7: "1",  # Bold
        16: "3",  # Outline
        18: "2",  # Alignment: bottom-center
        19: "980",  # MarginL: right-side vinyl panel offset
        20: "80",  # MarginR
        21: "100",  # MarginV
    }
    for line in source_lines:
        if line.startswith("ScriptType:") and not inserted_resolution:
            output.append(line)
            output.append("PlayResX: 1920")
            output.append("PlayResY: 1080")
            inserted_resolution = True
            continue
        if line.startswith("Style:"):
            fields = line.split(",")
            if len(fields) >= 22:
                fields[1] = font_name
                for index, value in style_values.items():
                    fields[index] = value
                line = ",".join(fields)
        if line.startswith("Dialogue:"):
            fields = line.split(",", 9)
            if len(fields) == 10:
                try:
                    start_ms = min(duration_ms, max(0, ass_time_to_ms(fields[1])))
                    end_ms = min(duration_ms, max(0, ass_time_to_ms(fields[2])))
                    if end_ms < start_ms:
                        end_ms = start_ms
                    fields[1] = ms_to_ass_time(start_ms)
                    fields[2] = ms_to_ass_time(end_ms)
                    if sentence_cursor < len(burn_sentences):
                        sentence = burn_sentences[sentence_cursor]
                        line_end_ms = ASSDirectExporter()._compute_line_end_ms(sentence)
                        fields[9] = _burn_ready_ass_text(
                            sentence,
                            line_end_ms,
                            start_ms,
                        )
                        sentence_cursor += 1
                    line = ",".join(fields)
                    event_times.append((start_ms, end_ms))
                    dialogue_texts.append(fields[9])
                    dialogue_count += 1
                except ValueError as exc:
                    dialogue_errors.append(str(exc))
            else:
                dialogue_errors.append("malformed Dialogue line")
        output.append(line)
    if not inserted_resolution:
        output.insert(1, "PlayResX: 1920")
        output.insert(2, "PlayResY: 1080")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    content = "\n".join(output) + "\n"
    forbidden_tokens = [token for token in ("|<", "#|", r"\sing_") if token in content]
    visible_texts = [_ass_visible_text(text) for text in dialogue_texts]
    expected_texts = [
        sentence.text for sentence in burn_sentences[: len(dialogue_texts)]
    ]
    visible_texts_match = bool(project is not None) and visible_texts == expected_texts
    style_report = _ass_style_metrics(output, font_name)
    burn_ready_checks = {
        "original_text_only": not forbidden_tokens and visible_texts_match,
        "no_template_ruby_or_singer_tokens": not forbidden_tokens,
        "visible_texts_match_project": visible_texts_match,
        "has_k_tags": bool(dialogue_texts)
        and all(r"\k" in text for text in dialogue_texts),
        "style": style_report["checks"],
        "style_errors": style_report["errors"],
        "forbidden_tokens": forbidden_tokens,
        "dialogue_count_matches_project": bool(project is not None)
        and dialogue_count == len(burn_sentences),
    }
    burn_ready_ok = (
        not dialogue_errors
        and all(0 <= start <= end <= duration_ms for start, end in event_times)
        and all(
            value
            for key, value in burn_ready_checks.items()
            if key not in {"style", "style_errors", "forbidden_tokens"}
        )
        and not style_report["errors"]
        and style_report["checks"].get("all_required_values", False)
        and not forbidden_tokens
    )
    return {
        "ok": burn_ready_ok,
        "dialogue_count": dialogue_count,
        "play_res": [1920, 1080],
        "font": font_name,
        "font_source": HARMONYOS_FONT_URL
        if font_name.startswith("HarmonyOS Sans")
        else None,
        "errors": dialogue_errors,
        "within_media": all(
            0 <= start <= end <= duration_ms for start, end in event_times
        ),
        "has_k_tags": bool(re.search(r"\\k\d+", content)),
        "burn_ready_ass": {
            "ok": burn_ready_ok,
            "checks": burn_ready_checks,
            "style": style_report,
        },
    }


def _srt_timestamp_to_ms(value: str) -> int:
    match = _SRT_CLOCK_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid SRT timing: {value}")
    return (
        int(match.group(1)) * 3_600_000
        + int(match.group(2)) * 60_000
        + int(match.group(3)) * 1_000
        + int(match.group(4))
    )


def ms_to_srt_time(timestamp_ms: int) -> str:
    timestamp_ms = max(0, int(timestamp_ms))
    hours, remaining = divmod(timestamp_ms, 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    seconds, millis = divmod(remaining, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def legalize_srt(path: Path, duration_ms: int) -> dict[str, Any]:
    blocks = [
        block
        for block in path.read_text(encoding="utf-8").split("\n\n")
        if block.strip()
    ]
    output: list[str] = []
    previous_end = 0
    errors: list[str] = []
    intervals: list[tuple[int, int]] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            errors.append("malformed SRT block")
            continue
        try:
            start_text, end_text = lines[1].split(" --> ", 1)
            start_ms = min(
                duration_ms, max(previous_end, _srt_timestamp_to_ms(start_text))
            )
            end_ms = min(duration_ms, max(0, _srt_timestamp_to_ms(end_text)))
        except (ValueError, IndexError) as exc:
            errors.append(str(exc))
            continue
        if end_ms <= start_ms:
            errors.append(f"non-positive SRT interval at block {lines[0]}")
            continue
        lines[1] = f"{ms_to_srt_time(start_ms)} --> {ms_to_srt_time(end_ms)}"
        output.append("\n".join(lines))
        intervals.append((start_ms, end_ms))
        previous_end = end_ms
    path.write_text("\n\n".join(output) + ("\n" if output else ""), encoding="utf-8")
    return {
        "ok": not errors
        and all(
            0 <= start < end <= duration_ms
            and (index == 0 or intervals[index - 1][1] <= start)
            for index, (start, end) in enumerate(intervals)
        ),
        "block_count": len(intervals),
        "non_overlapping": all(
            intervals[index - 1][1] <= intervals[index][0]
            for index in range(1, len(intervals))
        ),
        "within_media": all(
            0 <= start < end <= duration_ms for start, end in intervals
        ),
        "errors": errors,
    }


def validate_lrc(path: Path, duration_ms: int) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    timestamps: list[int] = []
    pattern = re.compile(r"[\[<](\d+):(\d{2})\.(\d{2,3})[\]>]")
    for match in pattern.finditer(content):
        fraction = (match.group(3) + "00")[:3]
        timestamps.append(
            int(match.group(1)) * 60_000 + int(match.group(2)) * 1_000 + int(fraction)
        )
    return {
        "ok": all(0 <= value <= duration_ms for value in timestamps),
        "timestamp_count": len(timestamps),
        "within_media": all(0 <= value <= duration_ms for value in timestamps),
        "non_decreasing_in_file": all(
            left <= right for left, right in zip(timestamps, timestamps[1:])
        ),
    }


def export_song(
    spec: SongSpec,
    audio_path: Path,
    duration_ms: int,
    project: Project,
    font_name: str,
) -> dict[str, Any]:
    timing_dir = (spec.deliverable_dir or ROOT / "deliverables") / "timing"
    timing_dir.mkdir(parents=True, exist_ok=True)
    base = timing_dir / f"{spec.song_id}_{spec.slug}"
    sug_path = base.with_suffix(".sug")
    ass_path = base.with_suffix(".ass")
    lrc_path = base.with_suffix(".lrc")
    srt_path = base.with_suffix(".srt")
    ruby_review_path = base.with_suffix(".ruby-review.json")

    relative_audio = os.path.relpath(audio_path, sug_path.parent)
    timing_before_ruby = timing_fingerprint(project)
    sug_hash_before_ruby = sug_hash(project)
    ruby_fill_records = fill_missing_project_ruby(
        project, create_project_ruby_service()
    )
    if timing_fingerprint(project) != timing_before_ruby:
        raise ValueError("ruby fill changed canonical timing fields")
    sug_hash_after_ruby = sug_hash(project)
    SugProjectParser.save(project, str(sug_path), media_path=relative_audio)
    ruby_sidecar = write_review_sidecar(
        ruby_review_path,
        sug_hash_before=sug_hash_before_ruby,
        sug_hash_after=sug_hash_after_ruby,
        records=ruby_fill_records,
    )
    LRCExporter().export(project, str(lrc_path))
    ASSDirectExporter().export(project, str(ass_path))
    SRTExporter().export(project, str(srt_path))

    ass_validation = legalize_ass(ass_path, duration_ms, font_name, project)
    srt_validation = legalize_srt(srt_path, duration_ms)
    lrc_validation = validate_lrc(lrc_path, duration_ms)
    loaded = SugProjectParser.load(str(sug_path))
    roundtrip = project_signature(project) == project_signature(loaded)
    raw_sug = json.loads(sug_path.read_text(encoding="utf-8"))
    sug_metadata = raw_sug.get("metadata") if isinstance(raw_sug, dict) else None
    sug_language = normalize_language(
        sug_metadata.get("language") if isinstance(sug_metadata, dict) else None,
        default=project.metadata.language,
    )
    if sug_language != normalize_language(project.metadata.language):
        raise ValueError(
            f"SUG metadata language mismatch: {sug_language!r} != "
            f"{project.metadata.language!r}"
        )
    project_root = spec.project_root or ROOT
    return {
        "paths": {
            "sug": project_relative(sug_path, project_root),
            "ass": project_relative(ass_path, project_root),
            "lrc": project_relative(lrc_path, project_root),
            "srt": project_relative(srt_path, project_root),
        },
        "sug_version": raw_sug.get("version"),
        "ruby_review": {
            "path": project_relative(ruby_review_path, project_root),
            "sug_hash_before": ruby_sidecar["sug_hash_before"],
            "sug_hash_after": ruby_sidecar["sug_hash_after"],
            "record_count": len(ruby_sidecar["records"]),
            "source": "canonical-sug",
        },
        "sug_metadata": {
            "language": sug_language,
            "language_identity": language_identity(sug_language),
        },
        "sug_roundtrip": {
            "ok": roundtrip,
            "same_domain_signature": roundtrip,
            "loaded_line_count": len(loaded.sentences),
        },
        "ass": ass_validation,
        "burn_ready_ass": ass_validation["burn_ready_ass"],
        "lrc": lrc_validation,
        "srt": srt_validation,
    }


def _override_gate_reasons(line_override: Mapping[str, Any]) -> list[str]:
    """Return explicit reasons why an override cannot satisfy the release gate."""

    reasons: list[str] = []
    status = str(line_override.get("review_status") or "")
    unresolved_indices = line_override.get("unresolved_character_indices") or []
    if unresolved_indices:
        reasons.append(
            "unresolved-character-indices:" + ",".join(map(str, unresolved_indices))
        )
    review_gate = line_override.get("review_gate")
    actual_ab_evidence = line_override.get(
        "actual_ab_evidence", line_override.get("actual_dual_audio")
    )
    if isinstance(review_gate, Mapping):
        if actual_ab_evidence is None:
            actual_ab_evidence = review_gate.get("actual_dual_audio")
        if review_gate.get("ok") is False:
            reasons.extend(str(item) for item in review_gate.get("reasons", []))
        if status == "dual-audio-machine-reviewed" and review_gate.get("ok") is not True:
            reasons.append("machine-review-gate-not-passed")
        if actual_ab_evidence is not True and status in {
            "acoustically-reviewed",
            "human-reviewed",
            "dual-audio-machine-reviewed",
            "user-reported-machine-reviewed",
            "user-reported-dual-audio-reviewed",
        }:
            reasons.append("missing-actual-dual-audio-evidence")
    elif status in {
        "dual-audio-machine-reviewed",
        "user-reported-machine-reviewed",
        "user-reported-dual-audio-reviewed",
    }:
        # Old generated overrides only carried a status string.  Do not trust
        # that string after the gate contract became candidate-based.
        reasons.append("legacy-review-status-without-candidate-gate")
    elif status in {"acoustically-reviewed", "human-reviewed"}:
        if actual_ab_evidence is not True:
            reasons.append("missing-actual-dual-audio-evidence")
    candidate_dispositions = line_override.get("candidate_dispositions")
    if isinstance(candidate_dispositions, Mapping):
        accepted = {"accepted-threshold", "inherited-accepted-threshold"}
        bad = [
            str(index)
            for index, disposition in candidate_dispositions.items()
            if str(disposition) not in accepted
        ]
        if bad:
            reasons.append("candidate-dispositions-unresolved:" + ",".join(bad))
    if status == "unresolved":
        reasons.append("line-review-status-unresolved")
    return list(dict.fromkeys(reasons))


def _line_is_machine_reviewed(line_override: Mapping[str, Any]) -> bool:
    """Require the new candidate gate before honoring machine-review status."""

    return (
        line_override.get("review_status") == "dual-audio-machine-reviewed"
        and not _override_gate_reasons(line_override)
    )


def build_song(
    spec: SongSpec,
    source_song: dict[str, Any],
    alignment_mode: str,
    model_name: str,
    model_cache: Path,
    alignment_timeout: float,
    font_name: str,
    vocal_stems_dir: Path | None = None,
    audit_original_mix: bool = True,
    timing_overrides: dict[str, Any] | None = None,
    singer_color: str = "#FF6B6B",
    role_colors: Mapping[str, Any] | None = None,
    device: str = DEFAULT_DEVICE,
) -> dict[str, Any]:
    language = normalize_language(spec.language)
    requested_device = normalize_device(device)
    fallback_method = (
        "deterministic-mora-interpolation"
        if language == "ja"
        else f"deterministic-{timing_granularity(language)}-interpolation"
    )
    audio_path = find_audio(spec)
    duration_seconds, duration_ms = read_mutagen_duration(audio_path)
    digest = sha256_file(audio_path)
    entries = parse_lrc(str(source_song.get("lrc") or ""))
    lyric_lines, parse_report = make_lyric_lines(entries, duration_ms)

    aligned_words: dict[int, list[dict[str, Any]]] = {}
    alignment_audio_path = audio_path
    alignment_audio_kind = "original-mix"
    stem_candidates = (
        [
            vocal_stems_dir / audio_path.stem / "Vocals.wav",
            vocal_stems_dir / audio_path.stem / "vocals.wav",
        ]
        if vocal_stems_dir is not None
        else []
    )
    stem_candidate = next((path for path in stem_candidates if path.is_file()), None)
    if stem_candidate is not None:
        alignment_audio_path = stem_candidate
        alignment_audio_kind = "msst-karaoke-vocals"
    if alignment_mode == "deterministic":
        alignment_meta: dict[str, Any] = {
            "attempted": False,
            "status": "skipped",
            "method": fallback_method,
            "language": language,
            "stable_ts_language": stable_ts_language(language),
            "requested_device": requested_device,
            "resolved_device": None,
        }
    else:
        aligned_words, alignment_meta = run_forced_alignment(
            alignment_audio_path,
            lyric_lines,
            model_name,
            model_cache,
            alignment_timeout,
            language=language,
            device=requested_device,
        )
    alignment_meta["audio_kind"] = alignment_audio_kind
    project_root = spec.project_root or ROOT
    alignment_meta["audio_path"] = project_relative(alignment_audio_path, project_root)
    if alignment_audio_path.is_file():
        alignment_meta["audio_sha256"] = sha256_file(alignment_audio_path)
    alignment_meta["evidence_contract"] = ALIGNMENT_EVIDENCE_CONTRACT
    if (
        audit_original_mix
        and alignment_mode != "deterministic"
        and alignment_audio_kind == "msst-karaoke-vocals"
    ):
        mix_words, mix_meta = run_forced_alignment(
            audio_path,
            lyric_lines,
            model_name,
            model_cache,
            alignment_timeout,
            language=language,
            device=requested_device,
        )
        helper = ReadingHelper()
        comparisons: list[dict[str, Any]] = []
        for line in lyric_lines:
            vocal_timing, vocal_diagnostics = derive_line_timing(
                line,
                helper,
                aligned_words.get(line.line_index),
                alignment_error=(
                    None
                    if alignment_meta.get("status") == "ok"
                    else alignment_meta.get("error")
                ),
                language=language,
            )
            mix_timing, mix_diagnostics = derive_line_timing(
                line,
                helper,
                mix_words.get(line.line_index),
                alignment_error=(
                    None if mix_meta.get("status") == "ok" else mix_meta.get("error")
                ),
                language=language,
            )
            common_indices = sorted(set(vocal_timing) & set(mix_timing))
            deltas = [
                vocal_timing[index] - mix_timing[index] for index in common_indices
            ]
            absolute_deltas = [abs(value) for value in deltas]
            delta_records = [
                {
                    "character_index": index,
                    "character": line.text[index],
                    "vocal_onset_ms": vocal_timing[index],
                    "mix_onset_ms": mix_timing[index],
                    "vocal_minus_mix_ms": delta,
                }
                for index, delta in zip(common_indices, deltas, strict=True)
            ]
            maximum_delta = (
                max(delta_records, key=lambda item: abs(item["vocal_minus_mix_ms"]))
                if delta_records
                else None
            )
            comparisons.append(
                {
                    "line_index": line.line_index,
                    "text": line.text,
                    "vocal_method": vocal_diagnostics.get("method"),
                    "mix_method": mix_diagnostics.get("method"),
                    "vocal_mean_probability": vocal_diagnostics.get("mean_probability"),
                    "mix_mean_probability": mix_diagnostics.get("mean_probability"),
                    "mean_absolute_delta_ms": (
                        int(round(sum(absolute_deltas) / len(absolute_deltas)))
                        if absolute_deltas
                        else None
                    ),
                    "max_absolute_delta_ms": max(absolute_deltas)
                    if absolute_deltas
                    else None,
                    "vocal_minus_mix_first_onset_ms": deltas[0] if deltas else None,
                    "maximum_delta_character": maximum_delta,
                    "notable_character_deltas": [
                        item
                        for item in delta_records
                        if abs(item["vocal_minus_mix_ms"]) >= 200
                    ],
                    "vocal_phrase_gap_corrections": vocal_diagnostics.get(
                        "phrase_gap_corrections", []
                    ),
                    "mix_phrase_gap_corrections": mix_diagnostics.get(
                        "phrase_gap_corrections", []
                    ),
                }
            )
        review_threshold_ms = 450
        flagged = [
            item
            for item in comparisons
            if (item.get("max_absolute_delta_ms") or 0) >= review_threshold_ms
        ]
        override_reasons: dict[int, list[str]] = {}
        for raw_line_index, raw_override in (timing_overrides or {}).items():
            if not isinstance(raw_override, Mapping):
                override_reasons[int(raw_line_index)] = ["override-is-not-an-object"]
                continue
            reasons = _override_gate_reasons(raw_override)
            if reasons:
                override_reasons[int(raw_line_index)] = reasons

        def reviewed_override(line_index: int) -> bool:
            override = (timing_overrides or {}).get(str(line_index), {})
            if not isinstance(override, Mapping):
                return False
            status = str(override.get("review_status") or "")
            return status in {"acoustically-reviewed", "human-reviewed"} and not (
                _override_gate_reasons(override)
            ) or _line_is_machine_reviewed(override)

        reviewed_flagged = [
            item["line_index"]
            for item in flagged
            if reviewed_override(item["line_index"])
        ]
        unreviewed_flagged = [
            item["line_index"]
            for item in flagged
            if item["line_index"] not in reviewed_flagged
        ]
        unresolved = [
            {
                "line_index": line_index,
                "reasons": reasons,
            }
            for line_index, reasons in sorted(override_reasons.items())
        ]
        for item in flagged:
            line_index = int(item["line_index"])
            if line_index in reviewed_flagged:
                continue
            unresolved.append(
                {
                    "line_index": line_index,
                    "reasons": ["dual-audio-delta-at-or-above-review-threshold"],
                }
            )
        unresolved_by_line: dict[int, dict[str, Any]] = {}
        for item in unresolved:
            line_index = int(item["line_index"])
            entry = unresolved_by_line.setdefault(
                line_index, {"line_index": line_index, "reasons": []}
            )
            entry["reasons"] = list(
                dict.fromkeys([*entry["reasons"], *item["reasons"]])
            )
        alignment_meta["original_mix_cross_check"] = {
            "status": mix_meta.get("status"),
            "model": model_name,
            "review_threshold_ms": review_threshold_ms,
            "flagged_line_count": len(flagged),
            "flagged_line_indices": [item["line_index"] for item in flagged],
            "reviewed_flagged_line_indices": reviewed_flagged,
            "unreviewed_flagged_line_indices": unreviewed_flagged,
            "unresolved": list(unresolved_by_line.values()),
            "unresolved_count": len(unresolved_by_line),
            "mix_alignment": mix_meta,
            "lines": comparisons,
        }
    cross_check = alignment_meta.get("original_mix_cross_check", {})
    unresolved = list(cross_check.get("unresolved") or [])
    if alignment_meta.get("status") != "ok":
        unresolved.append(
            {
                "line_index": None,
                "reasons": ["stable-ts-alignment-status:" + str(alignment_meta.get("status"))],
            }
        )
    if not cross_check:
        unresolved.append(
            {
                "line_index": None,
                "reasons": ["missing-original-mix-cross-check"],
            }
        )
    elif cross_check.get("status") != "ok":
        unresolved.append(
            {
                "line_index": None,
                "reasons": ["original-mix-cross-check-status:" + str(cross_check.get("status"))],
            }
        )
    alignment_meta["unresolved"] = unresolved
    alignment_meta["unresolved_count"] = len(unresolved)
    alignment_meta["gate_ok"] = bool(
        alignment_meta.get("status") == "ok"
        and alignment_audio_kind == "msst-karaoke-vocals"
        and cross_check.get("status") == "ok"
        and not unresolved
    )
    project, line_reports = build_project(
        spec,
        duration_ms,
        lyric_lines,
        aligned_words,
        alignment_meta,
        timing_overrides,
        singer_color,
        role_colors,
    )
    project_validation = validate_project(project, duration_ms)
    exports = export_song(spec, audio_path, duration_ms, project, font_name)
    all_line_methods = {report["method"] for report in line_reports}
    if len(all_line_methods) == 1 and next(iter(all_line_methods)).startswith(
        "deterministic-"
    ):
        overall_method = next(iter(all_line_methods))
    elif all_line_methods == {"stable-ts-constrained"}:
        overall_method = "stable-ts-constrained"
    elif any("stable-ts" in method for method in all_line_methods):
        overall_method = "stable-ts-constrained-with-deterministic-fallback"
    else:
        overall_method = fallback_method

    source_hash_match = digest.startswith(
        spec.sha256_hint.split("...")[0]
    ) and digest.endswith(spec.sha256_hint.split("...")[-1])
    flagged_line_indices = list(
        alignment_meta.get("original_mix_cross_check", {}).get(
            "flagged_line_indices", []
        )
    )
    flagged_acoustic_reviewed = all(
        isinstance((timing_overrides or {}).get(str(line_index)), Mapping)
        and (timing_overrides or {}).get(str(line_index), {}).get("review_status")
        in {"acoustically-reviewed", "human-reviewed"}
        and not _override_gate_reasons((timing_overrides or {}).get(str(line_index), {}))
        for line_index in flagged_line_indices
    )
    flagged_machine_reviewed = all(
        isinstance((timing_overrides or {}).get(str(line_index)), Mapping)
        and _line_is_machine_reviewed((timing_overrides or {}).get(str(line_index), {}))
        for line_index in flagged_line_indices
    )
    return {
        "song_id": spec.song_id,
        "title": spec.title,
        "artist": spec.artist,
        "language": language,
        "language_identity": language_identity(language),
        "audio": {
            "path": project_relative(audio_path, project_root),
            "sha256": digest,
            "sha256_hint": spec.sha256_hint,
            "sha256_matches_hint": source_hash_match,
            "duration_source": "mutagen.File.info.length",
            "duration_seconds": duration_seconds,
            "duration_ms": duration_ms,
            "expected_duration_ms": spec.expected_duration_ms,
            "duration_delta_ms": duration_ms - spec.expected_duration_ms,
        },
        "source": {
            "language": language,
            "language_identity": language_identity(language),
            "lyric_texts": [line.text for line in lyric_lines],
            "lrc_version": source_song.get("lrc_version"),
            "romalrc_version": source_song.get("romalrc_version"),
            "romalrc_available": bool(str(source_song.get("romalrc") or "").strip()),
            "parse": parse_report,
            "line_axis": "start to nearest later empty LRC timestamp or next lyric timestamp; final fallback is mutagen media duration",
        },
        "alignment": {
            **alignment_meta,
            "language": language,
            "stable_ts_language": stable_ts_language(language),
            "requested_mode": alignment_mode,
            "model": model_name if alignment_mode != "deterministic" else None,
            "overall_method": overall_method,
            "human_reviewed": False,
            "flagged_lines_acoustically_reviewed": flagged_acoustic_reviewed,
            "flagged_lines_machine_reviewed": flagged_machine_reviewed,
            "unresolved": unresolved,
            "unresolved_count": len(unresolved),
            "confidence_is_acoustic_probability_only": True,
            "visual_interpolation_is_not_phoneme_alignment": True,
            "lines": line_reports,
        },
        "project_validation": project_validation,
        "exports": exports,
    }


def build_report(
    source_path: Path,
    source_mode: str,
    songs: Sequence[dict[str, Any]],
    args: argparse.Namespace,
    font_verification: dict[str, Any],
) -> dict[str, Any]:
    project_root = getattr(args, "project_root", ROOT)
    resolved_source = source_path.resolve()
    report_source = project_relative(resolved_source, project_root)
    normalized_font_verification = dict(font_verification)
    if normalized_font_verification.get("font_path"):
        normalized_font_verification["font_path"] = project_relative(
            normalized_font_verification["font_path"], project_root
        )
    language_identities = {
        song["song_id"]: song.get(
            "language_identity",
            language_identity(song.get("language", DEFAULT_LANGUAGE)),
        )
        for song in songs
    }
    language_codes = {
        song_id: identity["code"]
        for song_id, identity in language_identities.items()
    }
    requested_device = normalize_device(
        getattr(args, "device", DEFAULT_DEVICE)
    )
    resolved_devices = {
        song.get("alignment", {}).get("resolved_device")
        for song in songs
        if song.get("alignment", {}).get("resolved_device")
    }
    resolved_device = next(iter(resolved_devices)) if len(resolved_devices) == 1 else None
    return {
        "schema_version": "karaoke-timing-report/v1",
        "evidence_contract": ALIGNMENT_EVIDENCE_CONTRACT,
        "builder": {
            "script": "scripts/karaoke_timing.py",
            "sug_format_version": SUG_VERSION,
            "language_default": DEFAULT_LANGUAGE,
            "language_identities": language_identities,
            "source_mode": source_mode,
            "alignment_mode": args.alignment,
            "model": args.model,
            "model_cache": project_relative(args.model_cache, project_root),
            "requested_device": requested_device,
            "resolved_device": resolved_device,
            "fixed_ass": {
                "play_res_x": 1920,
                "play_res_y": 1080,
                "font": args.font_name,
                "font_size": 58,
                "bold": True,
                "outline_px": 3,
                "alignment": 2,
                "margin_l": 980,
                "margin_r": 80,
                "margin_v": 100,
                "font_source": HARMONYOS_FONT_URL
                if args.font_name.startswith("HarmonyOS Sans")
                else None,
                "font_verification": normalized_font_verification,
            },
        },
        "source_path": report_source,
        "requested_device": requested_device,
        "resolved_device": resolved_device,
        "font_verification": normalized_font_verification,
        "language_codes": language_codes,
        "language_identities": language_identities,
        "unresolved": [
            {
                "song_id": song["song_id"],
                "items": song.get("alignment", {}).get("unresolved", []),
            }
            for song in songs
            if song.get("alignment", {}).get("unresolved")
        ],
        "songs": list(songs),
        "ok": all(
            (
                args.alignment == "deterministic"
                or song["alignment"].get("gate_ok", False)
            )
            and song["project_validation"]["ok"]
            and song["exports"]["sug_roundtrip"]["ok"]
            and song["exports"]["ass"]["ok"]
            and song["exports"]["burn_ready_ass"]["ok"]
            and song["exports"]["lrc"]["ok"]
            and song["exports"]["srt"]["ok"]
            for song in songs
        )
        and normalized_font_verification.get("ok", False),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="album.json manifest that owns the complete track collection",
    )
    parser.add_argument(
        "--allow-partial-manifest",
        action="store_true",
        help="allow an explicitly supplied manifest with fewer than five tracks",
    )
    parser.add_argument(
        "--song-id",
        help="build exactly one manifest track (requires --output-root)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help=(
            "new private output root below <project>/.render-work; timing and "
            "validation artifacts are written here instead of canonical deliverables"
        ),
    )
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--refresh-source", action="store_true")
    parser.add_argument(
        "--netease-song-id",
        help="NetEase numeric song id for a selected single-song refresh",
    )
    parser.add_argument(
        "--alignment",
        choices=("auto", "forced", "deterministic"),
        default="auto",
        help="auto/forced tries stable-ts; deterministic skips acoustic alignment",
    )
    parser.add_argument(
        "--timing-overrides",
        type=Path,
        default=None,
        help="reviewed per-character timing overrides and flagged-line dispositions",
    )
    parser.add_argument(
        "--lyric-corrections",
        type=Path,
        default=None,
        help="acoustically evidenced corrections applied to the frozen NetEase LRC",
    )
    parser.add_argument(
        "--skip-mix-cross-check",
        action="store_true",
        help="when a vocal stem is present, skip the secondary original-mix alignment",
    )
    parser.add_argument("--model", default="base")
    parser.add_argument("--model-cache", type=Path, default=DEFAULT_MODEL_CACHE)
    add_device_argument(parser)
    parser.add_argument("--alignment-timeout", type=float, default=180.0)
    parser.add_argument(
        "--vocal-stems-dir",
        type=Path,
        default=DEFAULT_VOCAL_STEMS_DIR,
        help=(
            "separator output root containing <audio stem>/Vocals.wav; "
            "falls back to the original mix when absent"
        ),
    )
    parser.add_argument("--font-name", default=DEFAULT_FONT_NAME)
    parser.add_argument("--font-file", type=Path, default=None)
    parser.add_argument(
        "--alignment-worker", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--request", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.alignment_worker:
        if args.request is None:
            raise SystemExit("--request is required in alignment worker mode")
        run_alignment_worker(args.request, args.model, args.model_cache, args.device)
        return 0

    album = load_album_manifest(
        args.manifest,
        require_five_tracks=not args.allow_partial_manifest,
    )
    if args.song_id and args.output_root is None:
        raise SystemExit("--song-id requires --output-root to prevent canonical overwrite")
    selected_tracks = tuple(
        track
        for track in album.tracks
        if args.song_id is None or str(track.song_id) == str(args.song_id)
    )
    if args.song_id is not None and len(selected_tracks) != 1:
        raise SystemExit(
            f"manifest must contain exactly one selected song-id: {args.song_id}"
        )
    if args.netease_song_id and not args.refresh_source:
        raise SystemExit("--netease-song-id requires --refresh-source")
    if args.netease_song_id and len(selected_tracks) != 1:
        raise SystemExit("--netease-song-id requires exactly one selected song")
    output_root = album.deliverable_dir.resolve()
    if args.output_root is not None:
        output_root = args.output_root.expanduser().resolve()
        private_root = (album.project_root / ".render-work").resolve()
        try:
            output_root.relative_to(private_root)
        except ValueError as error:
            raise SystemExit(
                f"--output-root must stay below the project private root: {private_root}"
            ) from error
        if output_root == private_root:
            raise SystemExit("--output-root must be a new child of .render-work")
        if output_root.exists():
            raise SystemExit(f"--output-root already exists: {output_root}")
    specs = tuple(
        replace(song_spec_from_track(track, album), deliverable_dir=output_root)
        for track in selected_tracks
    )
    args.project_root = album.project_root
    args.source = (
        args.source or album.deliverable_dir / "sources" / "netease_lyrics.json"
    )
    args.timing_overrides = (
        args.timing_overrides or args.source.parent / "timing_overrides.json"
    )
    args.lyric_corrections = (
        args.lyric_corrections or args.source.parent / "lyric_corrections.json"
    )
    args.font_file = args.font_file or (
        album.deliverable_dir / "artwork" / "fonts" / "HarmonyOS_Sans_SC_Regular.ttf"
    )
    source_ids = (
        {specs[0].song_id: args.netease_song_id}
        if args.netease_song_id
        else None
    )
    source, source_mode = load_or_fetch_source(
        args.source,
        args.refresh_source,
        specs,
        source_ids=source_ids,
    )
    overrides_document = (
        json.loads(args.timing_overrides.read_text(encoding="utf-8"))
        if args.timing_overrides.is_file()
        else {"songs": {}}
    )
    corrections_document = (
        json.loads(args.lyric_corrections.read_text(encoding="utf-8"))
        if args.lyric_corrections.is_file()
        else {"songs": {}}
    )
    song_reports: list[dict[str, Any]] = []
    for spec in specs:
        song_overrides = (
            overrides_document.get("songs", {}).get(spec.song_id, {})
        )
        source_song = dict(source["songs"][spec.song_id])
        corrected_lrc, applied_corrections = apply_lyric_corrections(
            str(source_song.get("lrc") or ""),
            corrections_document.get("songs", {}).get(spec.song_id, []),
        )
        source_song["lrc"] = corrected_lrc
        song_report = build_song(
            spec,
            source_song,
            args.alignment,
            args.model,
            args.model_cache,
            args.alignment_timeout,
            args.font_name,
            args.vocal_stems_dir,
            not args.skip_mix_cross_check,
            song_overrides.get("lines", {}),
            singer_color=song_overrides.get("highlight_color", "#FF6B6B"),
            role_colors=song_overrides.get("role_colors"),
            device=args.device,
        )
        song_report["source"]["lyric_corrections"] = applied_corrections
        song_reports.append(song_report)
    font_verification = verify_font(
        args.font_name,
        args.font_file,
        lyric_texts=[
            text
            for song in song_reports
            for text in song["source"].get("lyric_texts", [])
        ],
    )
    report = build_report(
        args.source, source_mode, song_reports, args, font_verification
    )
    report_path = output_root / "validation" / "timing_report.json"
    _json_dump(report_path, report)
    print("karaoke timing build complete")
    for song in song_reports:
        print(
            f"{song['song_id']}: {song['alignment']['overall_method']}; "
            f"duration={song['audio']['duration_ms']}ms; "
            f"roundtrip={song['exports']['sug_roundtrip']['ok']}"
        )
    print("report written")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
