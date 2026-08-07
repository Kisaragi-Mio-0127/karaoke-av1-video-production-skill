#!/usr/bin/env python3
"""Japanese-only private full-auto karaoke entry point."""

from __future__ import annotations

try:
    from .karaoke_full_auto import main as full_auto_main
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_full_auto import main as full_auto_main  # type: ignore[no-redef]


def main(argv=None) -> int:
    return full_auto_main(argv, allowed_languages=frozenset({"ja"}))


if __name__ == "__main__":
    raise SystemExit(main())
