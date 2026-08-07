"""Focused tests for import-safe, canonical-SUG review rendering."""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "integration" / "strangeutagame" / "scripts"
SOURCE = SCRIPT_DIR / "karaoke_review_preview.py"
SUG_SOURCE = SCRIPT_DIR / "sug_ruby.py"


def load_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_renderer_without_optional_ruby():
    names = (
        "scripts",
        "scripts.karaoke_language",
        "scripts.karaoke_timing",
        "scripts.render_vinyl_karaoke",
        "scripts.sug_ruby",
        "strange_uta_game",
        "strange_uta_game.backend",
        "strange_uta_game.backend.domain",
        "strange_uta_game.backend.infrastructure",
        "strange_uta_game.backend.infrastructure.persistence",
        "strange_uta_game.backend.infrastructure.persistence.sug_io",
        "imageio_ffmpeg",
        "PIL",
        "PIL.ImageFont",
        "pykakasi",
        "public_renderer_import_without_pykakasi",
    )
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in names}

    scripts = types.ModuleType("scripts")
    scripts.__path__ = [str(SCRIPT_DIR)]
    language = types.ModuleType("scripts.karaoke_language")
    language.DEFAULT_LANGUAGE = "ja"
    language.normalize_language = lambda value, default="ja": (
        "ja"
        if str(value or default).lower() in {"ja", "jp", "jpn", "japanese", "ja-jp"}
        else (_ for _ in ()).throw(ValueError("project adapter"))
    )
    language.language_identity = lambda value: {"code": str(value or "ja")}
    language.uses_ruby = lambda value: True
    timing = types.ModuleType("scripts.karaoke_timing")
    timing.ms_to_ass_time = lambda value: str(value)
    timing.verify_font = lambda *args, **kwargs: None
    vinyl = types.ModuleType("scripts.render_vinyl_karaoke")
    vinyl.escape_filter_path = lambda value: str(value)
    imageio = types.ModuleType("imageio_ffmpeg")
    pil = types.ModuleType("PIL")
    image_font = types.ModuleType("PIL.ImageFont")
    pil.ImageFont = image_font
    domain = types.ModuleType("strange_uta_game.backend.domain")
    domain.Character = type("Character", (), {})
    domain.Sentence = type("Sentence", (), {})
    sug_io = types.ModuleType(
        "strange_uta_game.backend.infrastructure.persistence.sug_io"
    )
    sug_io.SugProjectParser = type("SugProjectParser", (), {})

    sys.modules.update(
        {
            "scripts": scripts,
            "scripts.karaoke_language": language,
            "scripts.karaoke_timing": timing,
            "scripts.render_vinyl_karaoke": vinyl,
            "imageio_ffmpeg": imageio,
            "PIL": pil,
            "PIL.ImageFont": image_font,
            "pykakasi": None,
            "strange_uta_game": types.ModuleType("strange_uta_game"),
            "strange_uta_game.backend": types.ModuleType("strange_uta_game.backend"),
            "strange_uta_game.backend.domain": domain,
            "strange_uta_game.backend.infrastructure": types.ModuleType(
                "strange_uta_game.backend.infrastructure"
            ),
            "strange_uta_game.backend.infrastructure.persistence": types.ModuleType(
                "strange_uta_game.backend.infrastructure.persistence"
            ),
            "strange_uta_game.backend.infrastructure.persistence.sug_io": sug_io,
        }
    )
    load_from_path("scripts.sug_ruby", SUG_SOURCE)
    try:
        return load_from_path("public_renderer_import_without_pykakasi", SOURCE)
    finally:
        for name, value in saved.items():
            if value is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class KaraokeReviewPreviewTests(unittest.TestCase):
    def test_synthetic_singer_resolution_and_final_secondary_geometry(self) -> None:
        renderer = load_renderer_without_optional_ruby()
        default = SimpleNamespace(id="default", color="#112233", is_default=True)
        alternate = SimpleNamespace(id="alternate", color="#A1B2C3", is_default=False)
        project = SimpleNamespace(
            singers=[alternate, default],
            get_default_singer=lambda: default,
            get_singer=lambda singer_id: {
                "default": default,
                "alternate": alternate,
            }.get(singer_id),
        )
        sentence = SimpleNamespace(singer_id="default", id="line")
        character = SimpleNamespace(char="あ", singer_id="alternate")
        empty = SimpleNamespace(char="い", singer_id="")
        invalid = SimpleNamespace(char="う", singer_id="missing")

        self.assertEqual(
            renderer._effective_singer(character, sentence, project)["singer_id"],
            "alternate",
        )
        fallback = renderer._effective_singer(empty, sentence, project)
        self.assertEqual(fallback["singer_id"], "default")
        self.assertEqual(fallback["resolution_source"], "sentence.singer_id")
        with self.assertRaisesRegex(ValueError, "unknown non-empty character.singer_id"):
            renderer._effective_singer(invalid, sentence, project)
        self.assertEqual(renderer.SECONDARY_FONT_SIZE, 60)
        self.assertEqual(renderer.SECONDARY_MIN_FONT_SIZE, 36)
        self.assertEqual(renderer.SECONDARY_TOP_Y, 12)
        self.assertEqual(
            (renderer.SECONDARY_TOP_SAFE_TOP_PX, renderer.SECONDARY_TOP_SAFE_BOTTOM_PX),
            (0, 96),
        )

    def test_singer_resolution_rejects_unknown_sentence_and_invalid_defaults(self) -> None:
        renderer = load_renderer_without_optional_ruby()
        default = SimpleNamespace(id="default", color="#112233", is_default=True)
        character = SimpleNamespace(char="a", singer_id="")

        with self.assertRaisesRegex(ValueError, "unknown non-empty sentence.singer_id"):
            renderer._effective_singer(
                character,
                SimpleNamespace(id="line", singer_id="missing"),
                SimpleNamespace(singers=[default]),
            )

        for singers, expected_count in (
            ([SimpleNamespace(id="only", color="#112233", is_default=False)], 0),
            (
                [
                    SimpleNamespace(id="first", color="#112233", is_default=True),
                    SimpleNamespace(id="second", color="#445566", is_default=True),
                ],
                2,
            ),
        ):
            with self.subTest(expected_count=expected_count), self.assertRaisesRegex(
                ValueError,
                f"exactly one explicit default singer: found={expected_count}",
            ):
                renderer._effective_singer(
                    character,
                    SimpleNamespace(id="line", singer_id=""),
                    SimpleNamespace(singers=singers),
                )

    def test_singer_resolution_rejects_duplicate_ids_and_invalid_color(self) -> None:
        renderer = load_renderer_without_optional_ruby()
        character = SimpleNamespace(char="a", singer_id="")
        sentence = SimpleNamespace(id="line", singer_id="")

        duplicate_singers = [
            SimpleNamespace(id="duplicate", color="#112233", is_default=True),
            SimpleNamespace(id="duplicate", color="#445566", is_default=False),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate singer_id: 'duplicate'"):
            renderer._effective_singer(
                character,
                sentence,
                SimpleNamespace(singers=duplicate_singers),
            )

        invalid_color = SimpleNamespace(
            id="default",
            color="not-a-color",
            is_default=True,
        )
        with self.assertRaisesRegex(ValueError, "invalid non-empty singer.color"):
            renderer._effective_singer(
                character,
                sentence,
                SimpleNamespace(singers=[invalid_color]),
            )

    def test_synthetic_ruby_cannot_cross_effective_singers(self) -> None:
        renderer = load_renderer_without_optional_ruby()
        first = SimpleNamespace(id="first", color="#112233", is_default=True)
        second = SimpleNamespace(id="second", color="#A1B2C3", is_default=False)
        project = SimpleNamespace(
            singers=[first, second],
            get_default_singer=lambda: first,
            get_singer=lambda singer_id: {"first": first, "second": second}.get(singer_id),
        )
        sentence = SimpleNamespace(
            id="line",
            singer_id="first",
            characters=[
                SimpleNamespace(char="あ", singer_id="first"),
                SimpleNamespace(char="い", singer_id="second"),
            ],
        )
        token = SimpleNamespace(start=0, end=2)
        with self.assertRaisesRegex(ValueError, "crosses effective singer boundary"):
            renderer._validate_ruby_singer_boundaries(sentence, [token], project)

        sentence.characters[1].singer_id = "unknown"
        with self.assertRaisesRegex(
            ValueError,
            "unknown non-empty character.singer_id",
        ):
            renderer._validate_ruby_singer_boundaries(sentence, [token], project)

    def test_renderer_has_only_canonical_sug_ruby_path(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SOURCE))
        top_level_imports = [
            ast.unparse(node)
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertFalse(any("pykakasi" in node for node in top_level_imports))
        self.assertNotIn("contextual_ruby_tokens", source)
        self.assertIn("canonical_ruby_tokens", source)
        self.assertIn("ruby_sidecar", source)
        self.assertIn("validate_review_sidecar", source)
        self.assertIn("_require_reviewed_canonical_ruby", source)
        self.assertIn("renderer requires canonical SUG ruby tokens; inference is disabled", source)
        self.assertIn('"ruby_source": "canonical-sug"', source)
        self.assertNotIn("scripts.karaoke_zh_en", source)
        self.assertNotIn('"wide-zh"', source)
        self.assertNotIn('"wide-en"', source)
        self.assertIn("PRONUNCIATION_VALIDATION_MODES", source)

    def test_renderer_imports_when_pykakasi_is_unavailable(self) -> None:
        renderer = load_renderer_without_optional_ruby()
        self.assertFalse(hasattr(renderer, "kakasi"))
        self.assertTrue(callable(renderer.canonical_ruby_tokens))
        self.assertTrue(callable(renderer.build_review_ass))

    def test_ruby_gate_is_fail_closed_and_allows_no_ruby(self) -> None:
        renderer = load_renderer_without_optional_ruby()
        source = object()
        span = object()

        with self.assertRaises(ValueError) as missing_error:
            renderer._require_reviewed_canonical_ruby(source, None, [span])
        self.assertIn("sidecar", str(missing_error.exception))

        for reason in ("stale SUG hash", "missing approved span", "machine-fill", "unresolved"):
            with self.subTest(reason=reason):
                renderer.validate_review_sidecar = lambda *_args, reason=reason: [reason]
                with self.assertRaises(ValueError) as rejected_error:
                    renderer._require_reviewed_canonical_ruby(
                        source,
                        {"records": []},
                        [span],
                    )
                self.assertIn(reason, str(rejected_error.exception))
                self.assertNotIn('"status": "pass"', str(rejected_error.exception))

        calls = []

        def approve(candidate_source, candidate_sidecar):
            calls.append((candidate_source, candidate_sidecar))
            return []

        renderer.validate_review_sidecar = approve
        sidecar = {"records": [{"review_status": "ai-approved"}]}
        renderer._require_reviewed_canonical_ruby(source, sidecar, [span])
        self.assertEqual(calls, [(source, sidecar)])

        def fail_if_called(*_args, **_kwargs):
            self.fail("review validator must not run without canonical ruby")

        renderer.validate_review_sidecar = fail_if_called
        renderer._require_reviewed_canonical_ruby(source, None, [])


if __name__ == "__main__":
    unittest.main()
