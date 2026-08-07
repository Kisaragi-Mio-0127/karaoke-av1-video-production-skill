#!/usr/bin/env python3
"""Audit independent ASR recognition against frozen karaoke lyrics.

This report is deliberately separate from stable-ts and MMS_FA.  Those tools
are forced aligners: they receive the known lyric/token sequence.  This script
transcribes a mix or vocal stem without supplying the lyrics as a prompt, then
records recognized token text, time, and confidence before doing an ordered,
fuzzy comparison with the frozen lyric source.  Recognition can support or
veto an acoustic candidate, but it never edits or proposes replacement lyrics.
"""

from __future__ import annotations

import argparse
import difflib
import functools
import hashlib
import json
import math
import re
import sys
import unicodedata
import wave
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from scripts.karaoke_album import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    load_album_manifest,
    project_relative,
    sha256_file,
)
from scripts.karaoke_language import (  # noqa: E402
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    is_chinese_character,
    language_identity,
    normalize_language,
)
from scripts.karaoke_model_paths import WHISPER_MODEL_DIR  # noqa: E402

SCHEMA_VERSION = "karaoke-asr-recognition-audit/v2"
MATCHER_VERSION = "line-window-bounded/v2"
SUPPORTED_AUDIO_KINDS = frozenset({"mix", "stem"})
DEFAULT_MODEL = "base"
DEFAULT_CACHE_DIR = ROOT / ".cache" / "asr-recognition"
TARGET_SAMPLE_RATE = 16_000
DEFAULT_WINDOW_TOLERANCE_MS = 250
MAX_WINDOW_TOLERANCE_MS = 250
MATCH_THRESHOLD = 0.72
EXACT_MATCH_THRESHOLD = 0.995
SUPPORT_CONFIDENCE_THRESHOLD = 0.55
VETO_CONFIDENCE_THRESHOLD = 0.75
VETO_SIMILARITY_THRESHOLD = 0.45
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*")
_TRADITIONAL_FALLBACK = str.maketrans(
    {
        "風": "风",
        "聽": "听",
        "見": "见",
        "說": "说",
        "話": "话",
        "夢": "梦",
        "愛": "爱",
        "來": "来",
        "時": "时",
        "間": "间",
        "無": "无",
        "與": "与",
        "為": "为",
        "裏": "里",
        "裡": "里",
        "這": "这",
        "個": "个",
        "們": "们",
        "會": "会",
        "還": "还",
        "過": "过",
        "從": "从",
        "後": "后",
        "開": "开",
        "關": "关",
        "長": "长",
        "聲": "声",
        "樂": "乐",
        "葉": "叶",
        "雲": "云",
        "萬": "万",
        "國": "国",
        "點": "点",
        "歸": "归",
        "當": "当",
        "歲": "岁",
        "離": "离",
        "別": "别",
        "飛": "飞",
        "尋": "寻",
        "盡": "尽",
        "頭": "头",
        "邊": "边",
        "遠": "远",
        "處": "处",
        "隻": "只",
        "雙": "双",
        "體": "体",
        "書": "书",
        "畫": "画",
        "門": "门",
        "問": "问",
        "記": "记",
        "讓": "让",
        "對": "对",
        "發": "发",
        "現": "现",
        "轉": "转",
        "變": "变",
    }
)

EVIDENCE_CONTRACT = {
    "stable_ts": {
        "kind": "known-lyrics-forced-alignment",
        "independent_recognition": False,
    },
    "mms_fa": {
        "kind": "known-token-forced-alignment",
        "independent_recognition": False,
    },
    "asr_recognition": {
        "kind": "independent-recognition-audit",
        "independent_recognition": True,
        "lyrics_are_not_model_input": True,
        "can_support_or_veto": True,
    },
    "visual_interpolation": {
        "kind": "display-timing-interpolation",
        "phoneme_alignment": False,
    },
}


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _confidence(item: Any, fallback: Any = None) -> float | None:
    for key in ("confidence", "probability", "prob", "score"):
        value = _finite_float(_value(item, key))
        if value is not None:
            return max(0.0, min(1.0, value))
    fallback_value = _finite_float(fallback)
    if fallback_value is not None:
        return max(0.0, min(1.0, fallback_value))
    logprob = _finite_float(_value(item, "avg_logprob", fallback))
    if logprob is not None:
        return max(0.0, min(1.0, math.exp(logprob)))
    no_speech = _finite_float(_value(item, "no_speech_prob"))
    if no_speech is not None:
        return max(0.0, min(1.0, 1.0 - no_speech))
    return None


@functools.lru_cache(maxsize=4096)
def _simplify_chinese(value: str) -> str:
    """Canonicalize traditional glyphs without making lyrics model input."""

    if not value:
        return value
    try:
        from opencc import OpenCC

        return str(OpenCC("t2s").convert(value))
    except ImportError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            simplified_flag = 0x02000000
            required = ctypes.windll.kernel32.LCMapStringEx(
                "zh-CN",
                simplified_flag,
                value,
                len(value),
                None,
                0,
                None,
                None,
                0,
            )
            if required:
                destination = ctypes.create_unicode_buffer(required)
                written = ctypes.windll.kernel32.LCMapStringEx(
                    "zh-CN",
                    simplified_flag,
                    value,
                    len(value),
                    destination,
                    required,
                    None,
                    None,
                    0,
                )
                if written:
                    return destination.value
        except (AttributeError, OSError):
            pass
    return value.translate(_TRADITIONAL_FALLBACK)


def normalize_token_text(text: Any, language: str = DEFAULT_LANGUAGE) -> str:
    """Normalize comparable text without changing the frozen lyric source."""

    language = normalize_language(language)
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = value.replace("’", "'")
    if language == "en":
        return "".join(_WORD_RE.findall(value)).replace(" ", "")
    comparable = "".join(
        char
        for char in value
        if not char.isspace() and (is_chinese_character(char) or char.isalnum())
    )
    # Simplifying Han glyphs is a Chinese comparison policy. Applying it to
    # Japanese would silently rewrite kanji and could create false evidence.
    return _simplify_chinese(comparable) if language == "zh" else comparable


def lyric_token_units(text: str, language: str = DEFAULT_LANGUAGE) -> list[dict[str, Any]]:
    """Split known lyrics into match units while preserving source indices."""

    language = normalize_language(language)
    if language == "en":
        return [
            {
                "token": match.group(0),
                "source_start": match.start(),
                "source_end": match.end(),
            }
            for match in _WORD_RE.finditer(str(text))
        ]
    units: list[dict[str, Any]] = []
    for index, char in enumerate(str(text)):
        if char.isspace() or not (is_chinese_character(char) or char.isalnum()):
            continue
        units.append({"token": char, "source_start": index, "source_end": index + 1})
    return units


def _split_recognized_text(
    text: str,
    start_ms: int,
    end_ms: int,
    language: str,
) -> list[tuple[str, int, int]]:
    language = normalize_language(language)
    if language == "en":
        values = [match.group(0) for match in _WORD_RE.finditer(text)]
    else:
        values = [char for char in text if is_chinese_character(char) or char.isalnum()]
    if not values:
        return []
    duration = max(0, end_ms - start_ms)
    return [
        (
            value,
            start_ms + round(duration * index / len(values)),
            start_ms + round(duration * (index + 1) / len(values)),
        )
        for index, value in enumerate(values)
    ]


def extract_recognition_tokens(
    result: Any,
    language: str = DEFAULT_LANGUAGE,
) -> list[dict[str, Any]]:
    """Extract stable token/time/confidence records from stable-whisper output."""

    language = normalize_language(language)
    segments = _value(result, "segments", []) or []
    tokens: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(segments):
        segment_start = _finite_float(_value(segment, "start", 0.0)) or 0.0
        segment_end = _finite_float(_value(segment, "end", segment_start)) or segment_start
        segment_start_ms = max(0, round(segment_start * 1000))
        segment_end_ms = max(segment_start_ms, round(segment_end * 1000))
        segment_confidence = _confidence(segment)
        words = _value(segment, "words") or []
        if words:
            for word_index, word in enumerate(words):
                text = str(_value(word, "word", _value(word, "text", "")) or "")
                start = _finite_float(_value(word, "start", segment_start))
                end = _finite_float(_value(word, "end", start or segment_end))
                if start is None or end is None:
                    continue
                start_ms = max(0, round(start * 1000))
                end_ms = max(start_ms, round(end * 1000))
                for split_index, (token, split_start, split_end) in enumerate(
                    _split_recognized_text(text, start_ms, end_ms, language)
                ):
                    tokens.append(
                        {
                            "token": token,
                            "text": token,
                            "start_ms": split_start,
                            "end_ms": split_end,
                            "confidence": _confidence(word, segment_confidence),
                            "segment_index": segment_index,
                            "word_index": word_index,
                            "split_index": split_index,
                        }
                    )
            continue
        text = str(_value(segment, "text", "") or "")
        for split_index, (token, start_ms, end_ms) in enumerate(
            _split_recognized_text(text, segment_start_ms, segment_end_ms, language)
        ):
            tokens.append(
                {
                    "token": token,
                    "text": token,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "confidence": segment_confidence,
                    "segment_index": segment_index,
                    "word_index": None,
                    "split_index": split_index,
                }
            )
    return tokens


def _similarity(left: str, right: str, language: str) -> float:
    left_normalized = normalize_token_text(left, language)
    right_normalized = normalize_token_text(right, language)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    return difflib.SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _recognized_text(record: Mapping[str, Any]) -> str:
    return str(record.get("token", record.get("text", "")) or "")


def _line_view(line: Any, index: int) -> dict[str, Any]:
    if isinstance(line, str):
        return {"line_index": index, "text": line}
    return {
        "line_index": int(_value(line, "line_index", index)),
        "text": str(_value(line, "text", "") or ""),
        "start_ms": _value(line, "start_ms"),
        "end_ms": _value(line, "end_ms"),
    }


def _window_tolerance(value: Any) -> int:
    tolerance = _finite_float(value)
    if tolerance is None or tolerance < 0 or tolerance > MAX_WINDOW_TOLERANCE_MS:
        raise ValueError(
            "window_tolerance_ms must be between 0 and "
            f"{MAX_WINDOW_TOLERANCE_MS} milliseconds"
        )
    return round(tolerance)


def _time_interval(item: Mapping[str, Any]) -> tuple[int, int] | None:
    start = _finite_float(item.get("start_ms"))
    end = _finite_float(item.get("end_ms"))
    if start is None or end is None or start < 0 or end < start:
        return None
    return round(start), round(end)


def _intersects_window(
    interval: tuple[int, int] | None,
    window_start_ms: int,
    window_end_ms: int,
) -> bool:
    return bool(
        interval is not None
        and interval[1] >= window_start_ms
        and interval[0] <= window_end_ms
    )


def match_known_lyrics(
    lyric_lines: Sequence[Any],
    recognized_tokens: Sequence[Mapping[str, Any]],
    language: str = DEFAULT_LANGUAGE,
    *,
    lookahead: int = 8,
    window_tolerance_ms: int = DEFAULT_WINDOW_TOLERANCE_MS,
) -> list[dict[str, Any]]:
    """Match known lyrics using only monotonic tokens intersecting each line window."""

    language = normalize_language(language)
    tolerance_ms = _window_tolerance(window_tolerance_ms)
    cursor = 0
    consumed_indices: set[int] = set()
    last_consumed_start_ms = -1
    reports: list[dict[str, Any]] = []
    for line_position, raw_line in enumerate(lyric_lines):
        line = _line_view(raw_line, line_position)
        expected = lyric_token_units(line["text"], language)
        matches: list[dict[str, Any]] = []
        recognized_indices: list[int] = []
        line_errors: list[str] = []
        line_start_value = _finite_float(line.get("start_ms"))
        line_end_value = _finite_float(line.get("end_ms"))
        valid_window = bool(
            line_start_value is not None
            and line_end_value is not None
            and line_start_value >= 0
            and line_end_value >= line_start_value
        )
        line_start_ms = round(line_start_value) if line_start_value is not None else None
        line_end_ms = round(line_end_value) if line_end_value is not None else None
        if valid_window:
            assert line_start_ms is not None and line_end_ms is not None
            allowed_start_ms = max(0, line_start_ms - tolerance_ms)
            allowed_end_ms = line_end_ms + tolerance_ms
        else:
            allowed_start_ms = None
            allowed_end_ms = None
            line_errors.append("missing-or-invalid-line-window")

        available_indices = [
            index
            for index in range(cursor, len(recognized_tokens))
            if index not in consumed_indices
        ]
        invalid_timestamp_indices = [
            index
            for index in available_indices
            if _time_interval(recognized_tokens[index]) is None
        ]
        if valid_window:
            assert allowed_start_ms is not None and allowed_end_ms is not None
            in_window_indices = [
                index
                for index in available_indices
                if _intersects_window(
                    _time_interval(recognized_tokens[index]),
                    allowed_start_ms,
                    allowed_end_ms,
                )
            ]
            out_of_window_indices = [
                index
                for index in available_indices
                if _time_interval(recognized_tokens[index]) is not None
                and index not in in_window_indices
            ]
        else:
            in_window_indices = []
            out_of_window_indices = [
                index
                for index in available_indices
                if index not in invalid_timestamp_indices
            ]

        for expected_index, unit in enumerate(expected):
            candidate_indices = [
                index
                for index in in_window_indices
                if index >= cursor
                and index not in consumed_indices
                and (_time_interval(recognized_tokens[index]) or (-1, -1))[0]
                >= last_consumed_start_ms
            ][: max(1, lookahead)]
            if not candidate_indices:
                matches.append(
                    {
                        "expected_token": unit["token"],
                        "expected_index": expected_index,
                        "status": "unmatched",
                        "similarity": 0.0,
                    }
                )
                continue
            best_index = max(
                candidate_indices,
                key=lambda index: _similarity(
                    unit["token"], _recognized_text(recognized_tokens[index]), language
                ),
            )
            recognized = recognized_tokens[best_index]
            interval = _time_interval(recognized)
            if (
                allowed_start_ms is None
                or allowed_end_ms is None
                or not _intersects_window(interval, allowed_start_ms, allowed_end_ms)
            ):
                raise RuntimeError(
                    "matched token escaped lyric line window: "
                    f"line={line['line_index']} token={best_index} "
                    f"interval={interval} allowed={allowed_start_ms}..{allowed_end_ms}"
                )
            similarity = _similarity(
                unit["token"], _recognized_text(recognized), language
            )
            matched = similarity >= MATCH_THRESHOLD
            record = {
                "expected_token": unit["token"],
                "expected_index": expected_index,
                "expected_source_start": unit["source_start"],
                "expected_source_end": unit["source_end"],
                "recognized_index": best_index,
                "recognized_token": _recognized_text(recognized),
                "recognized_start_ms": recognized.get("start_ms"),
                "recognized_end_ms": recognized.get("end_ms"),
                "confidence": recognized.get("confidence"),
                "similarity": round(similarity, 6),
                "status": "matched" if matched else "unmatched",
                "window_intersects": True,
                "window_start_ms": line_start_ms,
                "window_end_ms": line_end_ms,
                "window_tolerance_ms": tolerance_ms,
            }
            if matched:
                # Only a real match may advance the monotonic cursor. Consuming
                # a low-similarity guess would hide a token that may correctly
                # belong to the next expected unit and cause cascading errors.
                consumed_indices.add(best_index)
                cursor = best_index + 1
                assert interval is not None
                last_consumed_start_ms = interval[0]
                recognized_indices.append(best_index)
            matches.append(record)

        matched_count = sum(item["status"] == "matched" for item in matches)
        confidences = [
            float(item["confidence"])
            for item in matches
            if item["status"] == "matched" and item.get("confidence") is not None
        ]
        similarities = [float(item["similarity"]) for item in matches]
        strong_mismatches = [
            item
            for item in matches
            if item["status"] == "unmatched"
            and item.get("confidence") is not None
            and float(item["confidence"]) >= VETO_CONFIDENCE_THRESHOLD
            and float(item["similarity"]) < VETO_SIMILARITY_THRESHOLD
        ]
        coverage = matched_count / len(expected) if expected else 0.0
        order_ok = recognized_indices == sorted(recognized_indices)
        if (
            expected
            and matched_count == len(expected)
            and order_ok
            and len(confidences) == matched_count
            and min(confidences) >= SUPPORT_CONFIDENCE_THRESHOLD
        ):
            disposition = "support"
        elif strong_mismatches:
            disposition = "veto"
        else:
            disposition = "unresolved"
        gate_ok = not line_errors and all(
            item.get("status") != "matched" or item.get("window_intersects") is True
            for item in matches
        )
        reports.append(
            {
                "line_index": line["line_index"],
                "text": line["text"],
                "expected_tokens": [unit["token"] for unit in expected],
                "expected_token_count": len(expected),
                "matched_token_count": matched_count,
                "coverage": round(coverage, 6),
                "mean_confidence": round(sum(confidences) / len(confidences), 6)
                if confidences
                else None,
                "min_confidence": min(confidences) if confidences else None,
                "mean_similarity": round(sum(similarities) / len(similarities), 6)
                if similarities
                else 0.0,
                "order_ok": order_ok and gate_ok,
                "disposition": disposition,
                "matches": matches,
                "recognized_text": "".join(
                    str(item.get("recognized_token", "")) for item in matches
                ),
                "strong_mismatches": strong_mismatches,
                "start_ms": line.get("start_ms"),
                "end_ms": line.get("end_ms"),
                "window_start_ms": line_start_ms,
                "window_end_ms": line_end_ms,
                "window_tolerance_ms": tolerance_ms,
                "allowed_window_start_ms": allowed_start_ms,
                "allowed_window_end_ms": allowed_end_ms,
                "in_window_token_count": len(in_window_indices),
                "out_of_window_count": len(out_of_window_indices),
                "invalid_timestamp_count": len(invalid_timestamp_indices),
                "consumed_recognized_indices": [
                    item["recognized_index"]
                    for item in matches
                    if item.get("status") == "matched"
                    and "recognized_index" in item
                ],
                "gate_ok": gate_ok,
                "structural_gate_ok": gate_ok,
                "support_gate_ok": gate_ok and disposition == "support",
                "errors": line_errors,
            }
        )
    return reports


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _lyrics_hash(lyric_lines: Sequence[Any], language: str) -> str:
    payload = [
        _line_view(line, index)
        for index, line in enumerate(lyric_lines)
    ]
    return _sha256_bytes(
        json.dumps(
            {"language": normalize_language(language), "lines": payload},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )


def load_audio_numpy(
    path: Path,
    *,
    target_sample_rate: int = TARGET_SAMPLE_RATE,
) -> tuple[Any, int]:
    """Load audio into numpy without asking stable-whisper to invoke ffmpeg."""

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment dependency
        raise RuntimeError("numpy is required for the independent ASR audit") from exc
    try:
        import soundfile as sf

        data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
        waveform = np.asarray(data, dtype=np.float32).mean(axis=1)
    except Exception as soundfile_error:
        try:
            with wave.open(str(path), "rb") as source:
                sample_rate = int(source.getframerate())
                channels = int(source.getnchannels())
                sample_width = int(source.getsampwidth())
                frames = source.readframes(source.getnframes())
            if sample_width not in {1, 2, 4}:
                raise RuntimeError(
                    f"unsupported PCM sample width {sample_width} for numpy loader"
                )
            dtype = {1: "u1", 2: "<i2", 4: "<i4"}[sample_width]
            waveform = np.frombuffer(frames, dtype=dtype).astype(np.float32)
            if sample_width == 1:
                waveform = (waveform - 128.0) / 128.0
            else:
                waveform /= float(2 ** (sample_width * 8 - 1))
            waveform = waveform.reshape(-1, channels).mean(axis=1)
        except Exception as wave_error:
            raise RuntimeError(
                f"numpy audio loading failed for {path}: {soundfile_error}; {wave_error}"
            ) from wave_error
    if int(sample_rate) != int(target_sample_rate):
        source_length = len(waveform)
        target_length = max(1, round(source_length * target_sample_rate / sample_rate))
        source_axis = np.linspace(0.0, 1.0, source_length, endpoint=False)
        target_axis = np.linspace(0.0, 1.0, target_length, endpoint=False)
        waveform = np.interp(target_axis, source_axis, waveform).astype(np.float32)
        sample_rate = int(target_sample_rate)
    return waveform, int(sample_rate)


def _atomic_write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _model_sha256(model_path: Path | None) -> str | None:
    return sha256_file(model_path) if model_path is not None and model_path.is_file() else None


def _resolve_model_path(
    model_path: Path | None,
    model_cache: Path | None,
    model_name: str,
) -> Path | None:
    if model_path is not None:
        return Path(model_path).expanduser().resolve()
    root = (
        Path(model_cache).expanduser().resolve()
        if model_cache is not None
        else WHISPER_MODEL_DIR.resolve()
    )
    candidate = root / f"{model_name}.pt"
    return candidate if candidate.is_file() else None


def _recognition_cache_key(
    *,
    audio_hash: str,
    audio_kind: str,
    language: str,
    lyric_hash: str,
    model_name: str,
    model_hash: str | None,
    tolerance_ms: int,
) -> str:
    return _sha256_bytes(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "matcher_version": MATCHER_VERSION,
                "audio_sha256": audio_hash,
                "audio_kind": audio_kind,
                "language": language,
                "lyrics_sha256": lyric_hash,
                "model": model_name,
                "model_sha256": model_hash,
                "window_tolerance_ms": tolerance_ms,
            },
            sort_keys=True,
        ).encode("utf-8")
    )


def run_recognition_audit(
    *,
    audio_path: Path,
    lyric_lines: Sequence[Any],
    language: str,
    audio_kind: str = "mix",
    model_name: str = DEFAULT_MODEL,
    model_cache: Path | None = None,
    model_path: Path | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    output_path: Path | None = None,
    force: bool = False,
    window_tolerance_ms: int = DEFAULT_WINDOW_TOLERANCE_MS,
    song_id: str = "direct",
    title: str = "",
    audio_loader: Callable[[Path], tuple[Any, int]] | None = None,
    transcribe_fn: Callable[[Any, int, str, str, Path | None], Any] | None = None,
    model_loader: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run or load one cached independent recognition report."""

    language = normalize_language(language)
    tolerance_ms = _window_tolerance(window_tolerance_ms)
    audio_kind = str(audio_kind).strip().lower()
    if audio_kind not in SUPPORTED_AUDIO_KINDS:
        raise ValueError(
            f"unsupported audio_kind {audio_kind!r}; expected mix or stem"
        )
    audio_path = Path(audio_path).expanduser().resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    audio_hash = sha256_file(audio_path)
    lyric_hash = _lyrics_hash(lyric_lines, language)
    inspect_model_artifact = (
        transcribe_fn is None or model_path is not None or model_cache is not None
    )
    resolved_model_path = (
        _resolve_model_path(model_path, model_cache, model_name)
        if inspect_model_artifact
        else None
    )
    model_hash = _model_sha256(resolved_model_path)
    cache_key = _recognition_cache_key(
        audio_hash=audio_hash,
        audio_kind=audio_kind,
        language=language,
        lyric_hash=lyric_hash,
        model_name=model_name,
        model_hash=model_hash,
        tolerance_ms=tolerance_ms,
    )
    cache_path = (
        Path(cache_dir).expanduser().resolve() / f"{cache_key}.json"
        if cache_dir is not None
        else None
    )
    if cache_path is not None and cache_path.is_file() and not force:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and cached.get("schema_version") == SCHEMA_VERSION
            and cached.get("matcher_version") == MATCHER_VERSION
        ):
            cached["cache"] = {
                "hit": True,
                "key": cache_key,
                "key_algorithm": "sha256",
                "path": str(cache_path),
            }
            if output_path is not None:
                _atomic_write(Path(output_path).resolve(), cached)
            return cached

    loader = audio_loader or load_audio_numpy
    waveform, sample_rate = loader(audio_path)
    if transcribe_fn is None:
        if resolved_model_path is None:
            expected = (
                Path(model_path).expanduser().resolve()
                if model_path is not None
                else (
                    Path(model_cache).expanduser().resolve()
                    if model_cache is not None
                    else WHISPER_MODEL_DIR.resolve()
                )
                / f"{model_name}.pt"
            )
            raise FileNotFoundError(f"Whisper model checkpoint does not exist: {expected}")
        try:
            import stable_whisper
        except ImportError as exc:  # pragma: no cover - environment dependency
            raise RuntimeError("stable_whisper is required for ASR recognition audit") from exc
        load = model_loader or stable_whisper.load_model
        model = load(str(resolved_model_path), device="cpu")
        result = model.transcribe(
            waveform,
            language=language,
            word_timestamps=True,
            condition_on_previous_text=False,
            verbose=False,
        )
    else:
        result = transcribe_fn(waveform, sample_rate, language, model_name, model_path)
    post_transcription_model_path = (
        _resolve_model_path(model_path, model_cache, model_name)
        if inspect_model_artifact
        else None
    )
    post_transcription_model_hash = _model_sha256(post_transcription_model_path)
    if post_transcription_model_hash != model_hash:
        resolved_model_path = post_transcription_model_path
        model_hash = post_transcription_model_hash
        cache_key = _recognition_cache_key(
            audio_hash=audio_hash,
            audio_kind=audio_kind,
            language=language,
            lyric_hash=lyric_hash,
            model_name=model_name,
            model_hash=model_hash,
            tolerance_ms=tolerance_ms,
        )
        cache_path = (
            Path(cache_dir).expanduser().resolve() / f"{cache_key}.json"
            if cache_dir is not None
            else None
        )
    tokens = extract_recognition_tokens(result, language)
    lines = match_known_lyrics(
        lyric_lines,
        tokens,
        language,
        window_tolerance_ms=tolerance_ms,
    )
    line_dispositions = [line["disposition"] for line in lines]
    if line_dispositions and any(item == "veto" for item in line_dispositions):
        disposition = "veto"
    elif line_dispositions and all(item == "support" for item in line_dispositions):
        disposition = "support"
    else:
        disposition = "unresolved"
    gate_errors = [
        {"line_index": line["line_index"], "error": error}
        for line in lines
        for error in line.get("errors", [])
    ]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "matcher_version": MATCHER_VERSION,
        "song_id": song_id,
        "title": title,
        "language": language,
        "language_identity": language_identity(language),
        "audio_kind": audio_kind,
        "audio": {
            "path": str(audio_path),
            "sha256": audio_hash,
            "sample_rate": sample_rate,
            "loader": "numpy-soundfile-or-wave",
        },
        "model": model_name,
        "model_path": (
            str(resolved_model_path) if resolved_model_path is not None else None
        ),
        "model_sha256": model_hash,
        "lyrics_sha256": lyric_hash,
        "window_tolerance_ms": tolerance_ms,
        "recognized_token_count": len(tokens),
        "recognized_tokens": tokens,
        "songs": [
            {
                "song_id": song_id,
                "title": title,
                "language": language,
                "lines": lines,
                "recognition_provenance": {
                    "audio_path": str(audio_path),
                    "audio_sha256": audio_hash,
                    "audio_kind": audio_kind,
                    "model": model_name,
                    "model_path": (
                        str(resolved_model_path)
                        if resolved_model_path is not None
                        else None
                    ),
                    "model_sha256": model_hash,
                    "recognized_token_count": len(tokens),
                    "recognized_tokens": tokens,
                    "transcription_cache_key": cache_key,
                    "transcription_cache_key_sha256": cache_key,
                    "transcription_cache_key_algorithm": "sha256",
                    "transcription_cache_path": (
                        str(cache_path) if cache_path is not None else None
                    ),
                },
            }
        ],
        "disposition": disposition,
        "gate_ok": all(line.get("gate_ok") is True for line in lines),
        "structural_gate_ok": all(
            line.get("structural_gate_ok", line.get("gate_ok")) is True
            for line in lines
        ),
        "support_gate_ok": bool(lines)
        and all(line.get("support_gate_ok") is True for line in lines),
        "errors": gate_errors,
        "human_reviewed": False,
        "lyrics_written": False,
        "evidence_contract": EVIDENCE_CONTRACT,
        "cache": {
            "hit": False,
            "key": cache_key,
            "key_algorithm": "sha256",
            "path": str(cache_path) if cache_path is not None else None,
        },
    }
    if cache_path is not None:
        _atomic_write(cache_path, report)
    if output_path is not None:
        _atomic_write(Path(output_path).resolve(), report)
    return report


def _manifest_lines(
    album: Any,
    track: Any,
    source_path: Path | None = None,
) -> list[dict[str, Any]]:
    from scripts.karaoke_timing import make_lyric_lines, parse_lrc

    source_dir = album.deliverable_dir / "sources"
    resolved_source = (
        Path(source_path).expanduser().resolve()
        if source_path is not None
        else (source_dir / "netease_lyrics.json").resolve()
    )
    source = json.loads(resolved_source.read_text(encoding="utf-8"))
    corrections_path = source_dir / "lyric_corrections.json"
    corrections = (
        json.loads(corrections_path.read_text(encoding="utf-8"))
        if corrections_path.is_file()
        else {"songs": {}}
    )
    from scripts.karaoke_timing import apply_lyric_corrections

    raw_lrc = str(source["songs"][track.song_id].get("lrc") or "")
    corrected_lrc, _ = apply_lyric_corrections(
        raw_lrc,
        corrections.get("songs", {}).get(track.song_id, []),
    )
    lines, _ = make_lyric_lines(
        parse_lrc(corrected_lrc), int(track.expected_duration_ms)
    )
    return [
        {
            "line_index": line.line_index,
            "text": line.text,
            "start_ms": line.start_ms,
            "end_ms": line.end_ms,
        }
        for line in lines
    ]


def _stem_path(track: Any, vocals_root: Path) -> Path:
    candidates = (
        vocals_root / Path(track.audio_file).stem / "Vocals.wav",
        vocals_root / Path(track.audio_file).stem / "vocals.wav",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"missing vocal stem for {track.song_id}: {candidates[0]}")


def run_manifest_audit(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    source_path: Path | None = None,
    song_ids: Sequence[str] | None = None,
    audio_kind: str = "mix",
    model_name: str = DEFAULT_MODEL,
    model_cache: Path | None = None,
    model_path: Path | None = None,
    vocals_root: Path | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    output_path: Path | None = None,
    force: bool = False,
    window_tolerance_ms: int = DEFAULT_WINDOW_TOLERANCE_MS,
    allow_partial_manifest: bool = False,
) -> dict[str, Any]:
    album = load_album_manifest(
        manifest_path, require_five_tracks=not allow_partial_manifest
    )
    requested = {str(item) for item in song_ids or []}
    tracks = [
        track
        for track in album.tracks
        if not requested or str(track.song_id) in requested
    ]
    if requested and {str(track.song_id) for track in tracks} != requested:
        raise ValueError("requested song ID is not present in the album manifest")
    selected_languages = {
        normalize_language(track.language) for track in tracks
    }
    if len(selected_languages) != 1:
        raise ValueError(
            "one manifest ASR run must select tracks in exactly one language; "
            "use --song-id to split ja, zh, and en into separate runs"
        )
    selected_language = next(iter(selected_languages))
    resolved_vocals_root = (
        Path(vocals_root).resolve()
        if vocals_root is not None
        else (album.project_root / ".cache" / "msst-vocals").resolve()
    )
    song_reports: list[dict[str, Any]] = []
    recognition_audits: list[dict[str, Any]] = []
    for track in tracks:
        audio_path = (
            track.audio_path
            if audio_kind == "mix"
            else _stem_path(track, resolved_vocals_root)
        )
        single = run_recognition_audit(
            audio_path=audio_path,
            lyric_lines=_manifest_lines(album, track, source_path),
            language=selected_language,
            audio_kind=audio_kind,
            model_name=model_name,
            model_cache=model_cache,
            model_path=model_path,
            cache_dir=cache_dir,
            force=force,
            window_tolerance_ms=window_tolerance_ms,
            song_id=track.song_id,
            title=track.title,
        )
        song_reports.extend(single["songs"])
        recognition_audits.append(
            {
                "song_id": track.song_id,
                "title": track.title,
                "audio_path": single["audio"]["path"],
                "audio_sha256": single["audio"]["sha256"],
                "audio_kind": single["audio_kind"],
                "model": single["model"],
                "model_path": single["model_path"],
                "model_sha256": single["model_sha256"],
                "recognized_token_count": single["recognized_token_count"],
                "recognized_tokens": single["recognized_tokens"],
                "transcription_cache_key": single["cache"]["key"],
                "transcription_cache_key_sha256": single["cache"]["key"],
                "transcription_cache_key_algorithm": "sha256",
                "transcription_cache_path": single["cache"]["path"],
            }
        )
    dispositions = [
        str(line.get("disposition") or "unresolved")
        for song in song_reports
        for line in song.get("lines", [])
    ]
    if dispositions and any(value == "veto" for value in dispositions):
        disposition = "veto"
    elif dispositions and all(value == "support" for value in dispositions):
        disposition = "support"
    else:
        disposition = "unresolved"
    structural_gate_ok = all(
        line.get("structural_gate_ok", line.get("gate_ok")) is True
        for song in song_reports
        for line in song.get("lines", [])
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "matcher_version": MATCHER_VERSION,
        "manifest_path": project_relative(Path(manifest_path).resolve(), album.project_root),
        "lyric_source_path": project_relative(
            (
                Path(source_path).expanduser().resolve()
                if source_path is not None
                else (album.deliverable_dir / "sources" / "netease_lyrics.json").resolve()
            ),
            album.project_root,
        ),
        "lyric_source_sha256": sha256_file(
            Path(source_path).expanduser().resolve()
            if source_path is not None
            else (album.deliverable_dir / "sources" / "netease_lyrics.json").resolve()
        ),
        "audio_kind": audio_kind,
        "language": selected_language,
        "language_identity": language_identity(selected_language),
        "model": model_name,
        "model_path": str(model_path.resolve()) if model_path is not None else None,
        "songs": song_reports,
        "recognition_audits": recognition_audits,
        "recognized_token_count": sum(
            item["recognized_token_count"] for item in recognition_audits
        ),
        "transcription_cache_keys": [
            item["transcription_cache_key"] for item in recognition_audits
        ],
        "window_tolerance_ms": _window_tolerance(window_tolerance_ms),
        "disposition": disposition,
        "gate_ok": structural_gate_ok,
        "structural_gate_ok": structural_gate_ok,
        "support_gate_ok": bool(dispositions)
        and structural_gate_ok
        and disposition == "support",
        "errors": [
            {"song_id": song["song_id"], "line_index": line["line_index"], "error": error}
            for song in song_reports
            for line in song.get("lines", [])
            for error in line.get("errors", [])
        ],
        "human_reviewed": False,
        "lyrics_written": False,
        "evidence_contract": EVIDENCE_CONTRACT,
    }
    if output_path is not None:
        _atomic_write(Path(output_path).resolve(), report)
    return report


def _load_direct_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("lines", data.get("songs", []))
        if not isinstance(data, list):
            raise ValueError("direct lyrics JSON must contain a lines array")
        return [_line_view(item, index) for index, item in enumerate(data)]
    from scripts.karaoke_timing import make_lyric_lines, parse_lrc

    entries = parse_lrc(path.read_text(encoding="utf-8"))
    lines, _ = make_lyric_lines(entries, 24 * 60 * 60 * 1000)
    return [
        {
            "line_index": line.line_index,
            "text": line.text,
            "start_ms": line.start_ms,
            "end_ms": line.end_ms,
        }
        for line in lines
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--source",
        type=Path,
        help="frozen local/net lyrics JSON used to build comparison windows",
    )
    parser.add_argument("--song-id", action="append", dest="song_ids")
    parser.add_argument("--audio", type=Path, help="direct mix/stem input")
    parser.add_argument("--lyrics", type=Path, help="direct frozen LRC/JSON input")
    parser.add_argument(
        "--language",
        choices=tuple(sorted(SUPPORTED_LANGUAGES)),
        default=None,
        help="language for direct audio/lyrics mode (ja, zh, or en)",
    )
    parser.add_argument("--audio-kind", choices=tuple(sorted(SUPPORTED_AUDIO_KINDS)), default="mix")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-cache", type=Path, default=WHISPER_MODEL_DIR)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--vocals-root", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--window-tolerance-ms",
        type=int,
        default=DEFAULT_WINDOW_TOLERANCE_MS,
        help=f"line-window onset/release tolerance (0..{MAX_WINDOW_TOLERANCE_MS} ms)",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-partial-manifest", action="store_true")
    parser.add_argument(
        "--allow-unresolved",
        action="store_true",
        help="return success for structurally valid unresolved evidence; veto still fails",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.audio) != bool(args.lyrics):
        raise SystemExit("--audio and --lyrics must be supplied together")
    if args.audio is not None:
        if args.language not in SUPPORTED_LANGUAGES:
            raise SystemExit("--language ja, zh, or en is required with direct audio")
        report = run_recognition_audit(
            audio_path=args.audio,
            lyric_lines=_load_direct_lines(args.lyrics),
            language=args.language,
            audio_kind=args.audio_kind,
            model_name=args.model,
            model_cache=args.model_cache,
            model_path=args.model_path,
            cache_dir=args.cache_dir,
            output_path=args.output,
            force=args.force,
            window_tolerance_ms=args.window_tolerance_ms,
        )
    else:
        report = run_manifest_audit(
            manifest_path=args.manifest,
            source_path=args.source,
            song_ids=args.song_ids,
            audio_kind=args.audio_kind,
            model_name=args.model,
            model_cache=args.model_cache,
            model_path=args.model_path,
            vocals_root=args.vocals_root,
            cache_dir=args.cache_dir,
            output_path=args.output,
            force=args.force,
            window_tolerance_ms=args.window_tolerance_ms,
            allow_partial_manifest=args.allow_partial_manifest,
        )
    disposition = str(report.get("disposition") or "unresolved")
    structural_ok = report.get("structural_gate_ok", report.get("gate_ok")) is True
    support_ok = report.get("support_gate_ok") is True
    accepted = support_ok or (
        args.allow_unresolved and structural_ok and disposition == "unresolved"
    )
    print(
        json.dumps(
            {
                "status": "pass" if accepted else "fail",
                "disposition": disposition,
                "structural_gate_ok": structural_ok,
                "support_gate_ok": support_ok,
                "output": str(args.output) if args.output else None,
            },
            ensure_ascii=False,
        )
    )
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
