"""Japanese language policy for the public karaoke production chain.

The bundled integration intentionally exposes only its validated Japanese
profile.  Additional languages belong in separately distributed adapters and
must not be implemented in this shared module.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

DEFAULT_LANGUAGE = "ja"
SUPPORTED_LANGUAGES = frozenset({DEFAULT_LANGUAGE})

_JAPANESE_ALIASES = frozenset(
    {"ja", "jp", "jpn", "japanese", "ja-jp"}
)


def normalize_language(
    value: Any,
    *,
    default: str = DEFAULT_LANGUAGE,
) -> str:
    """Return ``ja`` or fail closed for an unbundled language adapter."""

    fallback = str(default or DEFAULT_LANGUAGE).strip().lower().replace("_", "-")
    if fallback not in _JAPANESE_ALIASES:
        raise ValueError(
            f"unsupported default karaoke language: {default!r}; "
            "install a separately validated project adapter"
        )
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_LANGUAGE
    raw = str(value).strip().lower().replace("_", "-")
    if raw not in _JAPANESE_ALIASES:
        raise ValueError(
            f"unsupported karaoke language {value!r}; "
            "the public integration bundles only Japanese"
        )
    return DEFAULT_LANGUAGE


def stable_ts_language(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the validated stable-ts language name."""

    normalize_language(language)
    return "Japanese"


def uses_ruby(language: Any = DEFAULT_LANGUAGE) -> bool:
    """Return whether the validated public profile uses reviewed ruby."""

    normalize_language(language)
    return True


def timing_granularity(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the validated fallback timing unit."""

    normalize_language(language)
    return "mora-character"


def mms_granularity(language: Any = DEFAULT_LANGUAGE) -> str:
    """Return the validated MMS alignment unit."""

    normalize_language(language)
    return "mora"


def language_identity(language: Any = DEFAULT_LANGUAGE) -> dict[str, Any]:
    """Return the stable Japanese identity shared by pipeline reports."""

    code = normalize_language(language)
    return {
        "code": code,
        "name": "Japanese",
        "stable_ts_language": "Japanese",
        "timing_granularity": timing_granularity(code),
        "mms_granularity": mms_granularity(code),
        "ruby_enabled": True,
        "ruby_policy": "japanese-contextual-only",
    }


def iter_non_space_characters(texts: Iterable[str]) -> Iterator[str]:
    """Yield unique visual characters in stable first-seen order."""

    seen: set[str] = set()
    for text in texts:
        for character in str(text):
            if character.isspace() or character in seen:
                continue
            seen.add(character)
            yield character
