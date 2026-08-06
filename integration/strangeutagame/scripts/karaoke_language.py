"""Shared language policy for the reproducible karaoke production chain.

The album manifest stores short language codes so every downstream stage can
carry one identity from source lyrics through stable-ts, SUG, MMS and reports.
The default is deliberately Japanese for compatibility with older manifests.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "ja"
BUNDLED_LANGUAGE_PROFILES = frozenset({"ja"})
# Compatibility name used by older integration scripts.  This is the set of
# profiles bundled here, not a claim that the workflow itself is language-bound.
SUPPORTED_LANGUAGES = BUNDLED_LANGUAGE_PROFILES

LANGUAGE_NAMES = {"ja": "Japanese"}

LANGUAGE_ALIASES = {
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "ja-jp": "ja",
    "ja_jp": "ja",
}


def normalize_language(
    value: Any,
    *,
    default: str = DEFAULT_LANGUAGE,
) -> str:
    """Return a bundled language-profile code, defaulting to Japanese."""

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
            f"no validated bundled language profile for {value!r}; "
            "the default profile is 'ja' and other languages require a "
            "separately validated project adapter"
        )
    return language


def stable_ts_language(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the human-readable language name expected by stable-ts."""

    return LANGUAGE_NAMES[normalize_language(language)]


def uses_ruby(language: Any = DEFAULT_LANGUAGE) -> bool:
    """Whether the production chain may generate Japanese contextual ruby."""

    return normalize_language(language) == "ja"


def timing_granularity(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the acoustic unit defined by the selected bundled profile."""

    normalize_language(language)
    return "mora-character"


def mms_granularity(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the MMS source-unit policy defined by the selected profile."""

    normalize_language(language)
    return "mora"


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
        "ruby_policy": "japanese-contextual-only",
    }
