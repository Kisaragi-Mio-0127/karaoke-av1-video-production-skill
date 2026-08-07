import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import karaoke_cover_palette
from scripts.karaoke_color_plan import (
    KaraokeColorPlanError,
    apply_color_plan,
    load_composition_palette,
    parse_singer_color_overrides,
    resolve_color_plan,
    singer_first_appearance_order,
)

PALETTE_COLORS = [
    "#AA0000",
    "#00AA00",
    "#0000AA",
    "#AAAA00",
    "#AA00AA",
    "#00AAAA",
    "#AA5500",
    "#0055AA",
    "#5500AA",
]


def _project() -> SimpleNamespace:
    singers = [
        SimpleNamespace(id="a", name="A", color="#111111", is_default=True),
        SimpleNamespace(id="b", name="B", color="#222222", is_default=False),
        SimpleNamespace(id="unused", name="U", color="#333333", is_default=False),
    ]
    sentences = [
        SimpleNamespace(
            singer_id="b",
            characters=[SimpleNamespace(char="B", singer_id="b")],
        ),
        SimpleNamespace(
            singer_id="a",
            characters=[SimpleNamespace(char="A", singer_id="a")],
        ),
    ]
    return SimpleNamespace(singers=singers, sentences=sentences)


def _composition(tmp_path: Path, *, color_count: int = 8) -> Path:
    composition = tmp_path / "composition.png"
    composition.write_bytes(b"png")
    composition.with_suffix(".json").write_text(
        json.dumps(
            {
                "cover": {"sha256": "0" * 64},
                "cover_palette": {
                    "schema_version": "karaoke-cover-palette/v1",
                    "cover_sha256": "0" * 64,
                    "generator_sha256": hashlib.sha256(
                        Path(karaoke_cover_palette.__file__).resolve().read_bytes()
                    ).hexdigest(),
                    "method": "test",
                    "sample": {},
                    "candidates": [],
                    "colors": PALETTE_COLORS[:color_count],
                    "primary": "#AA0000",
                    "secondary": "#00AA00",
                    "fallback_used": False,
                    "adjustments": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return composition


@pytest.mark.parametrize("color_count", [2, 3, 7, 9])
def test_composition_palette_rejects_any_count_other_than_eight(
    tmp_path: Path,
    color_count: int,
):
    with pytest.raises(KaraokeColorPlanError, match="unexpected color count"):
        load_composition_palette(_composition(tmp_path, color_count=color_count))


def test_composition_palette_accepts_exactly_eight_colors(tmp_path: Path):
    palette = load_composition_palette(_composition(tmp_path))

    assert palette["colors"] == PALETTE_COLORS[:8]


def test_cover_plan_assigns_active_singers_in_first_appearance_order(tmp_path: Path):
    project = _project()
    assert singer_first_appearance_order(project) == ["b", "a"]

    plan = resolve_color_plan(project, composition_path=_composition(tmp_path))

    assert plan["singer_order"] == ["b", "a"]
    assert [item["resolved_color"] for item in plan["singers"]] == [
        "#AA0000",
        "#00AA00",
    ]
    assert plan["visual"] == {
        "spectrum_color": "#AA0000",
        "progress_color": "#00AA00",
    }
    assert len(plan["color_plan_sha256"]) == 64

    apply_color_plan(project, plan)
    assert project.singers[1].color == "#AA0000"
    assert project.singers[0].color == "#00AA00"
    assert project.singers[2].color == "#333333"


def test_third_active_singer_gets_third_slot_and_inactive_singer_gets_none(
    tmp_path: Path,
):
    project = _project()
    project.singers.insert(
        2,
        SimpleNamespace(id="c", name="C", color="#444444", is_default=False),
    )
    project.sentences.append(
        SimpleNamespace(
            singer_id="unused",
            characters=[SimpleNamespace(char="C", singer_id="c")],
        )
    )

    plan = resolve_color_plan(project, composition_path=_composition(tmp_path))

    assert plan["singer_order"] == ["b", "a", "c"]
    assert [item["resolved_color"] for item in plan["singers"]] == [
        "#AA0000",
        "#00AA00",
        "#0000AA",
    ]
    assert [item["role"] for item in plan["singers"]] == [
        "primary",
        "secondary",
        "accent-3",
    ]
    assert "unused" not in plan["singer_order"]


def test_empty_and_non_display_sentences_do_not_consume_singer_slots():
    project = _project()
    project.sentences[:0] = [
        SimpleNamespace(singer_id="unused", characters=[]),
        SimpleNamespace(
            singer_id="unused",
            characters=[
                SimpleNamespace(char="", singer_id="unused"),
                SimpleNamespace(char=" \t\n", singer_id="unused"),
                SimpleNamespace(char="\u200b", singer_id="unused"),
            ],
        ),
    ]

    assert singer_first_appearance_order(project) == ["b", "a"]


def test_project_with_no_display_characters_has_no_active_singer_slots():
    project = _project()
    project.sentences = [
        SimpleNamespace(singer_id="unused", characters=[]),
        SimpleNamespace(
            singer_id="unused",
            characters=[SimpleNamespace(char=" ", singer_id="unused")],
        ),
    ]

    assert singer_first_appearance_order(project) == []


def test_explicit_singer_override_beats_primary_slot_override(tmp_path: Path):
    plan = resolve_color_plan(
        _project(),
        composition_path=_composition(tmp_path),
        singer_overrides={"b": "#123456"},
        spectrum_color="#654321",
        progress_color="#FEDCBA",
    )
    assert plan["singers"][0]["resolved_color"] == "#123456"
    assert plan["singers"][0]["source"] == "explicit-singer-override"
    assert plan["singers"][1]["resolved_color"] == "#FEDCBA"
    assert plan["visual"]["spectrum_color"] == "#123456"


def test_project_policy_preserves_sug_colors(tmp_path: Path):
    plan = resolve_color_plan(
        _project(),
        composition_path=_composition(tmp_path),
        policy="project",
    )
    assert [item["resolved_color"] for item in plan["singers"]] == [
        "#222222",
        "#111111",
    ]


def test_singer_override_parser_rejects_duplicates_and_bad_colors():
    assert parse_singer_color_overrides(["a=#abcdef"]) == {"a": "#ABCDEF"}
    with pytest.raises(KaraokeColorPlanError, match="duplicate"):
        parse_singer_color_overrides(["a=#ABCDEF", "a=#123456"])
    with pytest.raises(KaraokeColorPlanError, match="invalid"):
        parse_singer_color_overrides(["a=red"])
