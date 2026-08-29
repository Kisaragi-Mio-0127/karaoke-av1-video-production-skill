"""Prepare one-run wide artwork without introducing another layout source."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

try:
    from scripts.build_karaoke_wide_artwork import build_wide_composition
    from scripts.render_vinyl_karaoke import (
        VINYL_STYLE_VERSION,
        build_background,
        build_vinyl,
        embedded_cover,
        inspect_font_dir,
    )
except ImportError:  # pragma: no cover - direct script entry point
    from build_karaoke_wide_artwork import (
        build_wide_composition,  # type: ignore[no-redef]
    )
    from render_vinyl_karaoke import (  # type: ignore[no-redef]
        VINYL_STYLE_VERSION,
        build_background,
        build_vinyl,
        embedded_cover,
        inspect_font_dir,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _load_cover(
    *,
    cover_path: Path | None,
    audio_path: Path,
) -> tuple[Image.Image, dict[str, Any]]:
    if cover_path is not None:
        resolved = cover_path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"explicit cover does not exist: {resolved}")
        try:
            cover = ImageOps.exif_transpose(Image.open(resolved)).convert("RGB")
        except Exception as error:
            raise RuntimeError(f"explicit cover is not a readable image: {resolved}") from error
        return cover, {"selection": "explicit-cover", **_identity(resolved)}

    resolved_audio = audio_path.expanduser().resolve()
    cover_bytes, embedded = embedded_cover(resolved_audio)
    if cover_bytes is None:
        raise RuntimeError(
            "automatic artwork needs --cover or an embedded cover in "
            f"the selected cover audio: {resolved_audio}"
        )
    try:
        cover = ImageOps.exif_transpose(Image.open(io.BytesIO(cover_bytes))).convert("RGB")
    except Exception as error:
        raise RuntimeError("embedded cover is not a readable image") from error
    return cover, {
        "selection": "audio-embedded-cover",
        "audio": _identity(resolved_audio),
        "embedded": embedded,
        "sha256": hashlib.sha256(cover_bytes).hexdigest(),
    }


def prepare_auto_artwork(
    *,
    output_dir: Path,
    audio_path: Path,
    cover_source_audio: Path | None,
    cover_path: Path | None,
    background_path: Path | None,
    composition_override: Path | None,
    fonts_dir: Path,
    title: str,
    artist: str,
    album_title: str,
    album_artist: str,
    visual_style: str,
) -> dict[str, Any]:
    """Resolve or build a gated composition and optionally a fresh vinyl."""

    if visual_style not in {
        "vinyl",
        "spectrum",
        "spectrum-line",
        "spectrum-mirror",
        "spectrum-dots",
        "spectrum-ribbon",
    }:
        raise ValueError(f"unsupported visual style: {visual_style}")

    artwork_dir = output_dir.resolve() / "artwork-current"
    needs_cover = composition_override is None or visual_style == "vinyl"
    cover: Image.Image | None = None
    cover_identity: dict[str, Any] | None = None
    normalized_cover: Path | None = None
    if needs_cover:
        cover, cover_identity = _load_cover(
            cover_path=cover_path,
            audio_path=cover_source_audio or audio_path,
        )
        artwork_dir.mkdir(parents=True, exist_ok=True)
        normalized_cover = artwork_dir / "cover.png"
        cover.save(normalized_cover, format="PNG", optimize=True)

    layout_report: dict[str, Any] | None = None
    if composition_override is not None:
        composition = composition_override.expanduser().resolve()
        if not composition.is_file():
            raise FileNotFoundError(f"explicit composition does not exist: {composition}")
        layout_selection = "explicit-advanced-override"
    else:
        assert cover is not None and normalized_cover is not None
        artwork_dir.mkdir(parents=True, exist_ok=True)
        background = artwork_dir / "background.png"
        if background_path is not None:
            explicit_background = background_path.expanduser().resolve()
            if not explicit_background.is_file():
                raise FileNotFoundError(
                    f"explicit background does not exist: {explicit_background}"
                )
            try:
                background_image = ImageOps.exif_transpose(
                    Image.open(explicit_background)
                ).convert("RGB")
            except Exception as error:
                raise RuntimeError(
                    f"explicit background is not a readable image: {explicit_background}"
                ) from error
            background_image.save(background, format="PNG", optimize=True)
            background_identity = {
                "selection": "explicit-background",
                **_identity(explicit_background),
            }
        else:
            build_background(cover).convert("RGB").save(
                background, format="PNG", optimize=True
            )
            background_identity = {
                "selection": "cover-derived",
                **_identity(background),
            }
        font_info = inspect_font_dir(fonts_dir)
        composition = artwork_dir / "composition.png"
        layout_report = build_wide_composition(
            background_path=background,
            cover_path=normalized_cover,
            regular_font=Path(font_info["regular"]["path"]),
            bold_font=Path(font_info["bold"]["path"]),
            title=title,
            artist=artist,
            album_title=album_title,
            album_artist=album_artist,
            output_path=composition,
            visual_style=visual_style,
        )
        layout_report["background_source"] = background_identity
        layout_selection = "automatic-wide-layout-v7"

    vinyl_path: Path | None = None
    vinyl_metadata: dict[str, Any] | None = None
    if visual_style == "vinyl":
        assert cover is not None
        vinyl_path = artwork_dir / "vinyl.png"
        build_vinyl(cover).save(vinyl_path, format="PNG", optimize=True)
        generator = Path(__file__).resolve().parents[1] / "render_vinyl_karaoke.py"
        vinyl_metadata = {
            "vinyl_style_version": VINYL_STYLE_VERSION,
            "vinyl_generator_sha256": _sha256(generator),
            "render_vinyl_karaoke_sha256": _sha256(generator),
            "vinyl_sha256": _sha256(vinyl_path),
            "vinyl_backplate": None,
            "vinyl_backplate_present": False,
            "vinyl_backplate_preserved": False,
            "vinyl_motion_contract": {
                "default": "rotate",
                "allowed": ["static", "rotate"],
                "rotation_period_seconds": 8.0,
            },
        }

    report = {
        "schema_version": "karaoke-auto-artwork/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": layout_selection,
        "visual_style": visual_style,
        "composition": _identity(composition),
        "layout": (
            {
                "layout_version": layout_report["layout_version"],
                "layout_generator": layout_report["layout_generator"],
                "layout_generator_sha256": layout_report["layout_generator_sha256"],
                "visual_style": layout_report["visual_style"],
            }
            if layout_report is not None
            else json.loads(composition.with_suffix(".json").read_text(encoding="utf-8"))
        ),
        "cover_source": cover_identity,
        "vinyl": _identity(vinyl_path) if vinyl_path is not None else None,
    }
    if vinyl_metadata is not None:
        report.update(vinyl_metadata)
    if artwork_dir.is_dir():
        (artwork_dir / "artwork.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "composition": composition,
        "vinyl": vinyl_path,
        "report": report,
        "metadata": vinyl_metadata,
    }
