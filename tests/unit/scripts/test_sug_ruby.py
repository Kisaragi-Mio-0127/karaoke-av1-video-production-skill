"""Focused tests for canonical SUG ruby facts and candidate isolation."""

from __future__ import annotations

import ast
import copy
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "integration" / "strangeutagame" / "scripts" / "sug_ruby.py"


def load_sug():
    name = "public_sug_ruby_focused"
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def character(
    text: str,
    *,
    reading: str | None = None,
    linked_to_next: bool = False,
    index: int = 0,
) -> dict[str, object]:
    return {
        "char": text,
        "check_count": 1,
        "timestamps": [100 + index * 120],
        "sentence_end_ts": 800,
        "linked_to_next": linked_to_next,
        "is_line_end": not linked_to_next,
        "is_sentence_end": not linked_to_next,
        "ruby": (
            {"parts": [{"text": reading, "offset_ms": 0}]}
            if reading is not None
            else None
        ),
    }


def document(*, reading: str | None = None, language: str = "ja") -> dict[str, object]:
    return {
        "metadata": {
            "language": language,
        },
        "audio_duration_ms": 1200,
        "sentences": [
            {
                "characters": [character("語", reading=reading)],
            }
        ],
    }


def review_sidecar(
    sug_module: object,
    fixture: dict[str, object],
    *,
    review_status: str = "human-reviewed",
    source: str = "human-review",
    confidence: float | None = 1.0,
    surface: str | None = None,
    after_hash: str | None = None,
) -> dict[str, object]:
    span = sug_module.iter_sug_ruby_spans(fixture)[0]
    record = {
        "sentence_id": span.sentence_id,
        "start": span.start,
        "end": span.end,
        "surface": surface if surface is not None else span.surface,
        "source": source,
        "review_status": review_status,
        "confidence": confidence,
        "after_hash": (
            after_hash
            if after_hash is not None
            else sug_module.span_hash(fixture, 0, span.start, span.end)
        ),
    }
    return {
        "schema": sug_module.RUBY_REVIEW_SCHEMA,
        "sug_hash_after": sug_module.sug_hash(fixture),
        "records": [record],
    }


class SugRubyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sug = load_sug()

    def test_canonical_reads_are_stored_spans(self) -> None:
        source = self.sug
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        top_level_pykakasi = [
            node
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and "pykakasi" in ast.unparse(node)
        ]
        self.assertEqual(top_level_pykakasi, [])

        fixture = document(reading="ご")
        spans = source.iter_sug_ruby_spans(fixture)
        self.assertEqual([(span.surface, span.reading) for span in spans], [("語", "ご")])
        self.assertEqual(source.validate_sug_ruby(fixture, require_ruby=True), [])
        self.assertTrue(source.is_ruby_language(fixture))

    def test_linked_spans_and_hashes_ignore_candidate_inference(self) -> None:
        fixture = document(reading="ご")
        first = fixture["sentences"][0]["characters"][0]
        first["linked_to_next"] = True
        second = character("音", reading="おと", index=1)
        fixture["sentences"][0]["characters"].append(second)
        timing_before = self.sug.timing_fingerprint(fixture)
        spans = self.sug.iter_sug_ruby_spans(fixture)
        self.assertEqual([(span.surface, span.reading) for span in spans], [("語音", "ごおと")])
        self.assertEqual(len(spans[0].linked_to_next), 2)

        result = self.sug.apply_review_patches(
            fixture,
            [
                {
                    "sentence_id": "sentence:0",
                    "start": 0,
                    "end": 1,
                    "surface": "語",
                    "reading": "ご",
                    "review_status": "agent-reviewed",
                    "confidence": 0.99,
                    "source": "agent-review",
                }
            ],
        )
        self.assertEqual(result["unresolved"], [{"reason": "human-locked", "patch": mock.ANY}])
        self.assertEqual(self.sug.timing_fingerprint(fixture), timing_before)

    def test_patch_gate_rejects_low_confidence_without_mutation(self) -> None:
        source = self.sug
        fixture = document()
        before = copy.deepcopy(fixture)
        result = source.apply_review_patches(
            fixture,
            [
                {
                    "sentence_id": "sentence:0",
                    "start": 0,
                    "end": 1,
                    "surface": "語",
                    "reading": "ご",
                    "review_status": "ai-reviewed",
                    "confidence": 0.2,
                }
            ],
        )
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["unresolved"][0]["reason"], "low-confidence")
        self.assertEqual(fixture, before)

    def test_approved_patch_preserves_timing_and_writes_sidecar(self) -> None:
        source = self.sug
        fixture = document()
        timing_before = source.timing_fingerprint(fixture)
        sug_before = source.sug_hash(fixture)
        result = source.apply_review_patches(
            fixture,
            [
                {
                    "sentence_id": "sentence:0",
                    "start": 0,
                    "end": 1,
                    "surface": "語",
                    "reading": "ご",
                    "review_status": "agent-reviewed",
                    "confidence": 0.99,
                    "source": "agent-review",
                    "model_prompt_version": "fixture-v1",
                }
            ],
        )
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(fixture["sentences"][0]["characters"][0]["ruby"]["parts"][0]["text"], "ご")
        self.assertEqual(source.timing_fingerprint(fixture), timing_before)
        self.assertEqual(result["before_sug_hash"], sug_before)
        self.assertNotEqual(result["after_sug_hash"], sug_before)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.ruby-review.json"
            payload = source.write_review_sidecar(
                path,
                sug_hash_before=result["before_sug_hash"],
                sug_hash_after=result["after_sug_hash"],
                records=result["records"],
                model_prompt_version="fixture-v1",
            )
            loaded = source.load_review_sidecar(path)
        self.assertEqual(loaded, payload)
        self.assertEqual(loaded["records"][0]["review_status"], "ai-approved")

    def test_validate_review_sidecar_accepts_human_approval_states(self) -> None:
        fixture = document(reading="ご")
        for review_status in ("human-reviewed", "human-locked"):
            self.assertEqual(
                self.sug.validate_review_sidecar(
                    fixture,
                    review_sidecar(
                        self.sug,
                        fixture,
                        review_status=review_status,
                    ),
                ),
                [],
            )

    def test_validate_review_sidecar_fails_closed_for_missing_or_stale_sidecar(
        self,
    ) -> None:
        fixture = document(reading="ご")
        self.assertTrue(self.sug.validate_review_sidecar(fixture, None))

        missing_hash = review_sidecar(self.sug, fixture)
        missing_hash.pop("sug_hash_after")
        self.assertTrue(self.sug.validate_review_sidecar(fixture, missing_hash))

        stale = review_sidecar(self.sug, fixture)
        stale["sug_hash_after"] = "stale-sug-hash"
        self.assertTrue(self.sug.validate_review_sidecar(fixture, stale))

        wrong_schema = review_sidecar(self.sug, fixture)
        wrong_schema["schema"] = "strange-utagame-ruby-review/v0"
        self.assertTrue(self.sug.validate_review_sidecar(fixture, wrong_schema))

    def test_validate_review_sidecar_requires_latest_exact_span_and_hash(
        self,
    ) -> None:
        fixture = document(reading="ご")

        wrong_surface = review_sidecar(self.sug, fixture, surface="別")
        errors = self.sug.validate_review_sidecar(fixture, wrong_surface)
        self.assertTrue(any("surface mismatch" in error for error in errors))

        wrong_hash = review_sidecar(self.sug, fixture, after_hash="old-span-hash")
        errors = self.sug.validate_review_sidecar(fixture, wrong_hash)
        self.assertTrue(any("after_hash mismatch" in error for error in errors))

        latest_blocked = review_sidecar(self.sug, fixture)
        latest_blocked["records"].append(
            {**latest_blocked["records"][0], "review_status": "unresolved"}
        )
        errors = self.sug.validate_review_sidecar(fixture, latest_blocked)
        self.assertTrue(any("blocked" in error for error in errors))

    def test_validate_review_sidecar_rejects_machine_and_low_confidence_records(
        self,
    ) -> None:
        fixture = document(reading="ご")
        cases = [
            {"review_status": "machine-fill"},
            {"review_status": "blocked"},
            {"review_status": "low-confidence"},
            {"source": "machine-fill"},
            {"source": " machine-fill "},
            {"review_status": "ai-approved", "confidence": 0.20},
            {"review_status": "ai-approved", "confidence": True},
            {"review_status": "ai-approved", "confidence": False},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertTrue(
                    self.sug.validate_review_sidecar(
                        fixture,
                        review_sidecar(self.sug, fixture, **overrides),
                    )
                )

    def test_validate_review_sidecar_requires_source_and_confidence(self) -> None:
        fixture = document(reading="ご")
        for source, confidence in (
            (None, 1.0),
            ("", 1.0),
            ("   ", 1.0),
            ("human-review", None),
        ):
            with self.subTest(source=source, confidence=confidence):
                sidecar = review_sidecar(
                    self.sug,
                    fixture,
                    confidence=confidence,
                )
                if source is None:
                    sidecar["records"][0].pop("source")
                else:
                    sidecar["records"][0]["source"] = source
                self.assertTrue(
                    self.sug.validate_review_sidecar(fixture, sidecar)
                )

    def test_apply_review_patches_requires_explicit_source_and_numeric_confidence(
        self,
    ) -> None:
        missing_source_result = self.sug.apply_review_patches(
            document(),
            [
                {
                    "sentence_id": "sentence:0",
                    "start": 0,
                    "end": 1,
                    "surface": "語",
                    "reading": "ご",
                    "review_status": "ai-approved",
                    "confidence": 0.99,
                }
            ],
        )
        self.assertEqual(
            missing_source_result["unresolved"][0]["reason"],
            "invalid-review-source",
        )

        cases = [
            ({"source": None}, "invalid-review-source"),
            ({"source": " machine-fill "}, "invalid-review-source"),
            ({"confidence": True}, "invalid-confidence"),
            ({"confidence": False}, "invalid-confidence"),
        ]
        for overrides, expected_reason in cases:
            with self.subTest(overrides=overrides):
                fixture = document()
                patch = {
                    "sentence_id": "sentence:0",
                    "start": 0,
                    "end": 1,
                    "surface": "語",
                    "reading": "ご",
                    "review_status": "ai-approved",
                    "confidence": 0.99,
                    "source": "agent-review",
                    **overrides,
                }
                result = self.sug.apply_review_patches(fixture, [patch])
                self.assertEqual(result["changes"], [])
                self.assertEqual(
                    result["unresolved"][0]["reason"], expected_reason
                )

    def test_validate_review_sidecar_allows_historical_machine_fill_provenance(
        self,
    ) -> None:
        fixture = document(reading="ご")
        sidecar = review_sidecar(
            self.sug,
            fixture,
            review_status="ai-approved",
            source="agent-review",
            confidence=0.99,
        )
        current_record = sidecar["records"][0]
        sidecar["records"].insert(
            0,
            {
                **current_record,
                "review_status": "machine-fill",
                "source": "machine-fill",
            },
        )

        self.assertEqual(self.sug.validate_review_sidecar(fixture, sidecar), [])

    def test_missing_ruby_fill_is_optional_and_non_japanese_is_closed(self) -> None:
        source = self.sug
        fixture = document()

        class Helper:
            @staticmethod
            def ruby(text: str, *, language: str) -> dict[str, object] | None:
                if text == "語" and language == "ja":
                    return {"parts": [{"text": "ご", "offset_ms": 0}]}
                return None

        records = source.fill_missing_project_ruby(fixture, Helper())
        self.assertEqual(len(records), 1)
        self.assertEqual(source.iter_sug_ruby_spans(fixture)[0].reading, "ご")

        non_japanese = document(language="en")
        result = source.apply_review_patches(
            non_japanese,
            [{"sentence_id": "sentence:0", "start": 0, "end": 1, "reading": "x"}],
        )
        self.assertEqual(result["unresolved"][0]["reason"], "ruby-disabled-language")

    def test_candidate_helper_lazy_loads_optional_dependency(self) -> None:
        source = self.sug
        fake_pykakasi = types.ModuleType("pykakasi")

        class Converter:
            @staticmethod
            def convert(text: str) -> list[dict[str, str]]:
                return [{"orig": text, "hira": "ご"}]

        fake_pykakasi.kakasi = lambda: Converter()
        with mock.patch.dict(sys.modules, {"pykakasi": fake_pykakasi}):
            tokens = source.candidate_ruby_tokens("語")
        self.assertEqual([(token.text, token.reading) for token in tokens], [("語", "ご")])
        self.assertEqual(source.candidate_ruby_tokens("語", language="en"), [])


if __name__ == "__main__":
    unittest.main()
