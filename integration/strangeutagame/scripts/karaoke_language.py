"""Shared language policy for the reproducible karaoke production chain.

The album manifest stores short language codes so every downstream stage can
carry one identity from source lyrics through stable-ts, SUG, MMS and reports.
The default is deliberately Japanese for compatibility with older manifests.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from typing import Any

DEFAULT_LANGUAGE = "ja"
SUPPORTED_LANGUAGES = frozenset({"ja", "zh", "en"})

LANGUAGE_NAMES = {
    "ja": "Japanese",
    "zh": "Chinese",
    "en": "English",
}

LANGUAGE_ALIASES = {
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "ja-jp": "ja",
    "ja_jp": "ja",
    "zh": "zh",
    "chi": "zh",
    "zho": "zh",
    "chinese": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "zh-hans": "zh",
    "zh_hans": "zh",
    "en": "en",
    "eng": "en",
    "english": "en",
    "en-us": "en",
    "en_us": "en",
}

_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_ENGLISH_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*")


def normalize_language(
    value: Any,
    *,
    default: str = DEFAULT_LANGUAGE,
) -> str:
    """Return a supported short language code.

    Missing and blank values use ``default`` so manifests written before the
    language field was introduced remain Japanese.  Non-empty unknown values
    fail early instead of silently selecting the wrong stable-ts model.
    """

    fallback = str(default or DEFAULT_LANGUAGE).strip().lower().replace("_", "-")
    fallback = LANGUAGE_ALIASES.get(fallback, fallback)
    if fallback not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported default karaoke language: {default!r}")
    if value is None or (isinstance(value, str) and not value.strip()):
        return fallback
    raw = str(value).strip().lower().replace("_", "-")
    language = LANGUAGE_ALIASES.get(raw, raw)
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"unsupported karaoke language {value!r}; "
            f"expected one of {sorted(SUPPORTED_LANGUAGES)}"
        )
    return language


def stable_ts_language(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the human-readable language name expected by stable-ts."""

    return LANGUAGE_NAMES[normalize_language(language)]


def uses_ruby(language: Any = DEFAULT_LANGUAGE) -> bool:
    """Whether the production chain may generate Japanese contextual ruby."""

    return normalize_language(language) == "ja"


def timing_granularity(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the acoustic unit used for fallback timing diagnostics."""

    return {
        "ja": "mora-character",
        "zh": "character",
        "en": "word-character",
    }[normalize_language(language)]


def mms_granularity(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the MMS source-unit policy for one language."""

    return {
        "ja": "mora",
        "zh": "pypinyin-character",
        "en": "word",
    }[normalize_language(language)]


def language_identity(language: Any = DEFAULT_LANGUAGE) -> dict[str, Any]:
    """Return a stable report-shaped identity shared by all pipeline stages."""

    code = normalize_language(language)
    return {
        "code": code,
        "name": LANGUAGE_NAMES[code],
        "stable_ts_language": LANGUAGE_NAMES[code],
        "timing_granularity": timing_granularity(code),
        "mms_granularity": mms_granularity(code),
        "ruby_enabled": uses_ruby(code),
        "ruby_policy": "japanese-contextual-only" if code == "ja" else "disabled",
    }


def is_chinese_character(character: str) -> bool:
    """Return whether one character is a Han ideograph usable by pypinyin."""

    if not character:
        return False
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def is_english_word_character(character: str) -> bool:
    """Return whether a character belongs to an ASCII English word."""

    return len(character) == 1 and (
        ("A" <= character <= "Z")
        or ("a" <= character <= "z")
        or ("0" <= character <= "9")
    )


def english_word_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    """Return ``(start, end, word)`` spans for English word-level timing."""

    return tuple(
        (match.start(), match.end(), match.group(0))
        for match in _ENGLISH_WORD_RE.finditer(text)
    )


def iter_non_space_characters(texts: Iterable[str]) -> Iterator[str]:
    """Yield unique visual characters in stable first-seen order."""

    seen: set[str] = set()
    for text in texts:
        for character in str(text):
            if character.isspace() or character in seen:
                continue
            seen.add(character)
            yield character


def pinyin_for_character(character: str) -> str:
    """Return tone-free pinyin for one Chinese character.

    ``pypinyin`` is imported lazily so Japanese-only timing and report tests do
    not pay its import cost.  The returned value is ASCII and suitable for the
    MMS_FA character tokenizer; ``Style.NORMAL`` intentionally omits tone marks.
    """

    from pypinyin import Style, lazy_pinyin

    values = lazy_pinyin(character, style=Style.NORMAL, errors="default")
    value = "".join(str(item or "") for item in values).lower()
    value = value.replace("ü", "v")
    value = "".join(char for char in unicodedata.normalize("NFKD", value) if ord(char) < 128)
    return value
