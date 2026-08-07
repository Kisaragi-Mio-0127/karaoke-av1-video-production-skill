"""Wide artwork must reserve the top secondary-vocal overlay geometrically."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "integration" / "strangeutagame" / "scripts"
ARTWORK_SOURCE = SCRIPTS / "build_karaoke_wide_artwork.py"
DIRECT_SOURCE = SCRIPTS / "render_karaoke_direct_av1_420_album.py"


def _load_artwork_module():
    spec = importlib.util.spec_from_file_location("public_wide_artwork", ARTWORK_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_composition_gate():
    tree = ast.parse(DIRECT_SOURCE.read_text(encoding="utf-8"), filename=str(DIRECT_SOURCE))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "validate_current_wide_compositions"
    ]

    class DirectAV1420RenderError(RuntimeError):
        pass

    namespace = {
        "Any": Any,
        "DirectAV1420RenderError": DirectAV1420RenderError,
        "Iterable": Iterable,
        "Mapping": Mapping,
        "Path": Path,
        "WIDE_ARTWORK_GENERATOR": ARTWORK_SOURCE,
        "json": json,
        "re": re,
        "render_core": SimpleNamespace(RenderTask=object),
        "sha256_file": _sha256_file,
    }
    exec(compile(ast.Module(selected, type_ignores=[]), str(DIRECT_SOURCE), "exec"), namespace)
    return namespace["validate_current_wide_compositions"], DirectAV1420RenderError


def _build(tmp_path: Path):
    artwork = _load_artwork_module()
    background = tmp_path / "background.png"
    cover = tmp_path / "cover.png"
    output = tmp_path / "composition.png"
    Image.new("RGB", artwork.CANVAS_SIZE, "#101820").save(background)
    Image.new("RGB", (400, 400), "#385878").save(cover)
    font_candidates = (
        Path.cwd()
        / "assets"
        / "fonts"
        / "HarmonyOS-Sans"
        / "HarmonyOS_Sans_SC_Regular.ttf",
        Path(os.environ.get("WINDIR", "")) / "Fonts" / "arial.ttf",
    )
    font_path = next((path for path in font_candidates if path.is_file()), None)
    if font_path is None:
        pytest.skip("no integration-test TrueType font is available")
    report = artwork.build_wide_composition(
        background_path=background,
        cover_path=cover,
        regular_font=font_path,
        bold_font=font_path,
        title="Collision-proof title",
        artist="Public integration test",
        album_title="Album",
        album_artist="Artist",
        output_path=output,
        visual_style="vinyl",
    )
    return artwork, output, report


def test_wide_title_is_below_secondary_outline_and_glow_reserve(tmp_path: Path):
    artwork, output, report = _build(tmp_path)
    assert artwork.TITLE_BLOCK_Y == {"label": 120, "title": 155, "artist": 220}
    assert report["secondary_overlay_contract"] == {
        "anchor_y": 12,
        "font_size_px": 60,
        "safe_bounds": (0, 0, 1920, 96),
        "outline_px": 3,
        "glow_px": 8,
        "reserved_bounds": (0, 0, 1920, 107),
    }
    assert report["title_bounds"][1] - report["secondary_reserved_bounds"][3] >= 16
    assert report["title_secondary_clearance_px"] == (
        report["title_bounds"][1] - report["secondary_reserved_bounds"][3]
    )
    assert report["title_secondary_collision"] is False

    gate, _ = _load_composition_gate()
    (result,) = gate(
        [SimpleNamespace(profile="wide", composition_path=output)],
        visual_style="vinyl",
    )
    assert result["status"] == "pass"
    assert result["title_bounds"][1] - result["secondary_reserved_bounds"][3] >= 16


def test_current_composition_gate_rejects_reported_title_collision(tmp_path: Path):
    _, output, _ = _build(tmp_path)
    metadata_path = output.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["title_bounds"][1] = metadata["secondary_reserved_bounds"][3]
    metadata["title_secondary_clearance_px"] = 0
    metadata["title_secondary_collision"] = True
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    gate, error_type = _load_composition_gate()
    with pytest.raises(error_type, match="title_secondary_no_collision"):
        gate([SimpleNamespace(profile="wide", composition_path=output)])


def test_current_composition_gate_rejects_15px_but_accepts_16px(tmp_path: Path):
    _, output, _ = _build(tmp_path)
    metadata_path = output.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    reserve_bottom = metadata["secondary_reserved_bounds"][3]
    gate, error_type = _load_composition_gate()

    metadata["title_bounds"][1] = reserve_bottom + 15
    metadata["title_secondary_clearance_px"] = 15
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(error_type, match="title_secondary_no_collision"):
        gate([SimpleNamespace(profile="wide", composition_path=output)])

    metadata["title_bounds"][1] = reserve_bottom + 16
    metadata["title_secondary_clearance_px"] = 16
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    (result,) = gate([SimpleNamespace(profile="wide", composition_path=output)])
    assert result["title_secondary_clearance_px"] == 16
