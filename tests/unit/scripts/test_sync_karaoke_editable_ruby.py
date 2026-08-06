"""Focused tests for explicit SUG review-patch synchronization."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "integration" / "strangeutagame" / "scripts"
SUG_SOURCE = SCRIPT_DIR / "sug_ruby.py"
SYNC_SOURCE = SCRIPT_DIR / "sync_karaoke_editable_ruby.py"


def load_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fixture() -> dict[str, object]:
    return {
        "metadata": {"language": "ja"},
        "sentences": [
            {
                "characters": [
                    {
                        "char": "\u597d",
                        "check_count": 1,
                        "timestamps": [100],
                        "sentence_end_ts": 700,
                        "linked_to_next": False,
                        "is_line_end": True,
                        "is_sentence_end": True,
                        "ruby": None,
                    }
                ],
            }
        ],
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def patch_payload() -> list[dict[str, object]]:
    return [
        {
            "sentence_id": "sentence:0",
            "start": 0,
            "end": 1,
            "surface": "\u597d",
            "reading": "\u3059",
            "review_status": "agent-reviewed",
            "confidence": 0.99,
            "source": "agent-review",
        }
    ]


def load_sync():
    names = (
        "scripts",
        "scripts.sug_ruby",
        "scripts.karaoke_timing",
        "public_sync_karaoke_editable_ruby",
    )
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in names}
    package = types.ModuleType("scripts")
    package.__path__ = [str(SCRIPT_DIR)]
    timing = types.ModuleType("scripts.karaoke_timing")
    timing.SONGS = ()
    sys.modules["scripts"] = package
    sug = load_from_path("scripts.sug_ruby", SUG_SOURCE)
    timing_module = sys.modules["scripts.karaoke_timing"] = timing
    try:
        return load_from_path("public_sync_karaoke_editable_ruby", SYNC_SOURCE)
    except Exception:
        del sys.modules["public_sync_karaoke_editable_ruby"]
        raise
    finally:
        del sug, timing_module
        for name, value in saved.items():
            if value is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class SyncRubyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sync = load_sync()

    def test_no_patch_path_is_read_only_structural_audit(self) -> None:
        source = fixture()
        before = copy.deepcopy(source)
        with tempfile.TemporaryDirectory() as temporary:
            sidecar_path = Path(temporary) / "review.json"
            changes, unresolved = self.sync.synchronize_document(
                source,
                sidecar_path=sidecar_path,
            )
            self.assertFalse(sidecar_path.exists())
        self.assertEqual(changes, [])
        self.assertEqual(unresolved, [])
        self.assertEqual(source, before)

    def test_cli_contract_requires_explicit_patch_input_for_writeback(self) -> None:
        source = SYNC_SOURCE.read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--patches", type=Path)', source)
        self.assertIn("patches = _load_patches(patches_path)", source)
        self.assertIn("write_sidecar=not check", source)
        self.assertIn("os.replace(sug_temporary, sug_path)", source)
        self.assertIn("os.replace(sidecar_temporary, sidecar_path)", source)

    def test_explicit_agent_patch_updates_sug_and_sidecar(self) -> None:
        source = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sug_path = directory / "fixture.sug"
            sidecar_path = directory / "fixture.ruby-review.json"
            write_json(sug_path, source)
            changes, unresolved = self.sync.synchronize_document(
                source,
                patch_payload(),
                sidecar_path=sidecar_path,
                sug_path=sug_path,
                model_prompt_version="fixture-v1",
            )
            self.assertTrue(changes)
            self.assertEqual(unresolved, [])
            self.assertEqual(
                source["sentences"][0]["characters"][0]["ruby"]["parts"][0][
                    "text"
                ],
                "\u3059",
            )
            self.assertTrue(sidecar_path.is_file())
            self.assertEqual(
                sidecar_path.read_text(encoding="utf-8").count("fixture-v1"),
                1,
            )

    def test_approved_patch_without_reading_change_publishes_new_sidecar(self) -> None:
        source = fixture()
        patch = patch_payload()[0]
        patch["reading"] = ""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sug_path = directory / "fixture.sug"
            sidecar_path = directory / "fixture.ruby-review.json"
            patches_path = directory / "patches.json"
            write_json(sug_path, source)
            write_json(patches_path, [patch])

            changes, unresolved = self.sync.sync_file(
                sug_path,
                check=False,
                patches_path=patches_path,
            )

            self.assertEqual(changes, 0)
            self.assertEqual(unresolved, [])
            published = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(len(published["records"]), 1)
            self.assertEqual(
                published["sug_hash_before"], published["sug_hash_after"]
            )

    def test_check_mode_performs_zero_writes(self) -> None:
        source = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sug_path = directory / "fixture.sug"
            sidecar_path = directory / "fixture.ruby-review.json"
            patches_path = directory / "patches.json"
            write_json(sug_path, source)
            write_json(patches_path, patch_payload())
            before_sug = sug_path.read_bytes()
            before_names = sorted(path.name for path in directory.iterdir())

            changes, unresolved = self.sync.sync_file(
                sug_path,
                check=True,
                patches_path=patches_path,
            )

            self.assertEqual(changes, 1)
            self.assertEqual(unresolved, [])
            self.assertEqual(sug_path.read_bytes(), before_sug)
            self.assertFalse(sidecar_path.exists())
            self.assertEqual(
                sorted(path.name for path in directory.iterdir()), before_names
            )

    def test_sug_replace_failure_does_not_publish_sidecar(self) -> None:
        source = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sug_path = directory / "fixture.sug"
            sidecar_path = directory / "fixture.ruby-review.json"
            patches_path = directory / "patches.json"
            write_json(sug_path, source)
            write_json(patches_path, patch_payload())
            before_sug = sug_path.read_bytes()
            calls: list[Path] = []
            original_replace = self.sync.os.replace

            def fail_sug_replace(source_path, destination):
                calls.append(Path(destination))
                if Path(destination) == sug_path:
                    raise OSError("injected SUG replace failure")
                return original_replace(source_path, destination)

            with mock.patch.object(
                self.sync.os, "replace", side_effect=fail_sug_replace
            ), self.assertRaisesRegex(OSError, "injected SUG replace failure"):
                self.sync.sync_file(
                    sug_path,
                    check=False,
                    patches_path=patches_path,
                )

            self.assertEqual(calls, [sug_path])
            self.assertEqual(sug_path.read_bytes(), before_sug)
            self.assertFalse(sidecar_path.exists())
            self.assertFalse(list(directory.glob(".*.tmp")))

    def test_sidecar_replace_failure_leaves_updated_sug_and_old_sidecar(self) -> None:
        source = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sug_path = directory / "fixture.sug"
            sidecar_path = directory / "fixture.ruby-review.json"
            patches_path = directory / "patches.json"
            write_json(sug_path, source)
            self.sync.write_review_sidecar(
                sidecar_path,
                sug_hash_before="old",
                sug_hash_after="old",
                records=[],
            )
            write_json(patches_path, patch_payload())
            before_sug = sug_path.read_bytes()
            before_sidecar = sidecar_path.read_bytes()
            calls: list[Path] = []
            original_replace = self.sync.os.replace

            def fail_sidecar_replace(source_path, destination):
                calls.append(Path(destination))
                if Path(destination) == sidecar_path:
                    raise OSError("injected sidecar replace failure")
                return original_replace(source_path, destination)

            with mock.patch.object(
                self.sync.os, "replace", side_effect=fail_sidecar_replace
            ), self.assertRaisesRegex(OSError, "injected sidecar replace failure"):
                self.sync.sync_file(
                    sug_path,
                    check=False,
                    patches_path=patches_path,
                )

            self.assertEqual(calls, [sug_path, sidecar_path])
            self.assertNotEqual(sug_path.read_bytes(), before_sug)
            self.assertEqual(sidecar_path.read_bytes(), before_sidecar)
            self.assertFalse(list(directory.glob(".*.tmp")))

    def test_album_default_fails_closed_without_private_manifest(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "pass canonical SUG paths"):
            self.sync.album_sug_paths()


if __name__ == "__main__":
    unittest.main()
