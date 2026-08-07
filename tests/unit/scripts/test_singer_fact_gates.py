"""Synthetic Japanese/general regression tests for singer fact-chain gates."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "integration" / "strangeutagame" / "scripts"


def load_functions(path: Path, names: set[str], namespace: dict):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    exec(compile(ast.Module(selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def test_eight_digit_ass_color_keeps_alpha_and_bgr_order() -> None:
    namespace = load_functions(
        SCRIPTS / "render_vinyl_karaoke.py",
        {"_parse_ass_color"},
        {"re": re},
    )
    parsed = namespace["_parse_ass_color"]("&H50C3B2A1&")
    assert parsed == {
        "ass": "&H50C3B2A1",
        "alpha": "50",
        "bgr": "C3B2A1",
        "rgb": "#A1B2C3",
    }


def test_workflow_parity_hashes_singer_secondary_and_ruby_facts() -> None:
    class KaraokeWorkflowError(RuntimeError):
        pass

    namespace = load_functions(
        SCRIPTS / "karaoke_workflow.py",
        {"_critical_ass_report_facts", "validate_ass_report_parity"},
        {
            "Any": object,
            "hashlib": hashlib,
            "json": json,
            "KaraokeWorkflowError": KaraokeWorkflowError,
        },
    )
    facts = {
        "ass": {
            "singer_color_mapping": [{"singer_id": "s1", "effective_color": "#A1B2C3"}],
            "sug_hash": "a" * 64,
            "ruby_consistency_gate": {"status": "pass"},
            "lines": [{"line_index": 0, "effective_singer_ids": ["s1"], "ruby": []}],
            "secondary_lines": [{"secondary_line_index": 0, "voice_role": "harmony"}],
        }
    }
    result = namespace["validate_ass_report_parity"](facts, facts)
    assert result["status"] == "ok"
    assert len(result["critical_facts_sha256"]) == 64
    changed = json.loads(json.dumps(facts))
    changed["ass"]["secondary_lines"][0]["voice_role"] = "opera"
    with pytest.raises(KaraokeWorkflowError):
        namespace["validate_ass_report_parity"](facts, changed)


def test_direct_album_requires_current_singer_secondary_ruby_generation() -> None:
    source = (SCRIPTS / "render_karaoke_direct_av1_420_album.py").read_text(
        encoding="utf-8"
    )
    assert "def validate_ass_report_generation" in source
    assert "ASS report sug_hash is stale" in source
    assert "ASS report singer role mapping is inconsistent" in source
    assert "ruby_identity(task, report)" in source
    assert "validate_ass_report_generation(" in source


def test_timing_signature_persists_independent_role_singer_facts() -> None:
    source = (SCRIPTS / "karaoke_timing.py").read_text(encoding="utf-8")
    assert "DEFAULT_ROLE_SINGER_COLORS" in source
    assert "role_colors=song_overrides.get(\"role_colors\")" in source
    assert '"singers": [' in source
    assert '"singer_id": character.singer_id' in source
