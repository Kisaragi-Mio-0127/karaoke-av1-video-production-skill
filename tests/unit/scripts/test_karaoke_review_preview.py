"""Focused tests for import-safe, canonical-SUG review rendering."""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path


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
        self.assertNotIn("wide-zh", source)
        self.assertNotIn("wide-en", source)

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
