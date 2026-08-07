#!/usr/bin/env python3
"""Deprecated compatibility entry point for direct HEVC 4:4:4 album rendering."""

from __future__ import annotations

import sys
from collections.abc import Sequence

try:
    from .render_karaoke_direct_hevc444_album import main as _hevc_main
except ImportError:  # pragma: no cover - direct script execution
    from render_karaoke_direct_hevc444_album import (  # type: ignore[no-redef]
        main as _hevc_main,
    )


DEPRECATION_MESSAGE = (
    "DEPRECATED: render_karaoke_direct_av1_album.py is the legacy name for the "
    "HEVC 4:4:4 album renderer; use render_karaoke_direct_hevc444_album.py."
)


def main(argv: Sequence[str] | None = None) -> int:
    """Preserve the legacy argv and exit-code contract while naming the real lane."""

    print(DEPRECATION_MESSAGE, file=sys.stderr)
    return _hevc_main(list(argv) if argv is not None else None)


if __name__ == "__main__":
    raise SystemExit(main())
