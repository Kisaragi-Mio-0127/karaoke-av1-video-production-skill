"""Static contract tests for the SUG-first shared ruby boundary."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "integration" / "strangeutagame" / "scripts" / "sug_ruby.py"


class SugRubyContractTests(unittest.TestCase):
    def test_shared_module_exposes_canonical_sug_gates(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SOURCE))
        self.assertIsNotNone(ast.get_docstring(tree))
        for symbol in (
            "apply_review_patches",
            "fill_missing_project_ruby",
            "timing_fingerprint",
            "write_review_sidecar",
        ):
            with self.subTest(symbol=symbol):
                self.assertIn(f"def {symbol}", source)
        self.assertIn("before_sug_hash", source)
        self.assertIn("after_sug_hash", source)
        self.assertIn("candidate_ruby_tokens", source)

    def test_optional_candidates_are_not_a_top_level_renderer_dependency(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SOURCE))
        top_level_imports = [
            ast.unparse(node)
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertFalse(any("pykakasi" in node for node in top_level_imports))
        self.assertNotRegex(source, r"(?i)[A-Za-z]:[\\/]")


if __name__ == "__main__":
    unittest.main()
