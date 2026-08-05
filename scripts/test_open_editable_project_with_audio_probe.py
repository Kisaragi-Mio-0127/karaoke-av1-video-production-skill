from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("open_editable_project_with_audio_probe.py")
SPEC = importlib.util.spec_from_file_location("audio_probe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class DirtyClassificationTests(unittest.TestCase):
    def test_clean_is_accepted(self) -> None:
        disposition = PROBE.classify_dirty(None, False, 0, True)
        self.assertEqual(disposition, "clean")
        self.assertTrue(PROBE.dirty_disposition_is_accepted(disposition))

    def test_one_ms_duration_only_is_accepted(self) -> None:
        sync = {
            "changed": True,
            "non_duration_state_unchanged": True,
            "before": {"dirty": False},
            "after": {"dirty": True},
        }
        disposition = PROBE.classify_dirty(sync, True, -1, True)
        self.assertEqual(disposition, "do-not-save-duration-normalization")
        self.assertTrue(PROBE.dirty_disposition_is_accepted(disposition))

    def test_large_or_unrelated_dirty_requires_review(self) -> None:
        sync = {
            "changed": True,
            "non_duration_state_unchanged": True,
            "before": {"dirty": False},
            "after": {"dirty": True},
        }
        self.assertEqual(
            PROBE.classify_dirty(sync, True, 2, True),
            "review-required",
        )
        sync["non_duration_state_unchanged"] = False
        self.assertEqual(
            PROBE.classify_dirty(sync, True, 1, True),
            "review-required",
        )

    def test_full_callback_change_rejects_one_ms_dirty(self) -> None:
        sync = {
            "changed": True,
            "non_duration_state_unchanged": True,
            "before": {"dirty": False},
            "after": {"dirty": True},
        }
        self.assertEqual(
            PROBE.classify_dirty(sync, True, 1, False),
            "review-required",
        )

    def test_full_callback_change_rejects_false_clean_flag(self) -> None:
        self.assertEqual(
            PROBE.classify_dirty(None, False, 0, False),
            "review-required",
        )


class EvidenceTests(unittest.TestCase):
    def test_video_extensions_come_from_application_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            repo = Path(raw_root)
            source = (
                repo
                / "src/strange_uta_game/backend/infrastructure/audio/video_converter.py"
            )
            source.parent.mkdir(parents=True)
            source.write_text(
                'VIDEO_EXTENSIONS = {".mp4", ".3gp", ".dts"}\n',
                encoding="utf-8",
            )
            suffixes = PROBE.load_video_suffixes(repo)
            self.assertEqual(suffixes, {".mp4", ".3gp", ".dts"})

    def test_final_gate_requires_every_guard(self) -> None:
        runtime = {
            "graceful_exit": True,
            "callback_gate_pass": True,
            "audio_callback_verified": True,
            "dirty_disposition_accepted": True,
            "full_callback_non_duration_unchanged": True,
            "canonical_project_unchanged": True,
            "adjacent_recovery_unchanged_after_exit": True,
        }
        self.assertTrue(PROBE.final_gate_pass(runtime))
        for key in tuple(runtime):
            rejected = dict(runtime)
            rejected[key] = False
            self.assertFalse(PROBE.final_gate_pass(rejected), key)

    def test_all_destructive_cleanup_methods_are_declared(self) -> None:
        self.assertEqual(
            PROBE.DESTRUCTIVE_CLEANUP_METHODS,
            {
                "cleanup_temp_files",
                "_cleanup_temp_for_path",
                "delete_crash_recovery",
            },
        )

    def test_adjacent_recovery_snapshot_detects_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            project = Path(raw_root) / "song.sug"
            project.write_text("{}", encoding="utf-8")
            paths = PROBE.adjacent_recovery_paths(project)
            before = PROBE.snapshot_paths(paths)
            paths[1].write_text("recovery", encoding="utf-8")
            after = PROBE.snapshot_paths(paths)
            self.assertNotEqual(before, after)

    def test_preflight_failure_writes_fail_closed_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            status = root / "status.json"
            code = PROBE.main(
                [
                    "--repo",
                    str(root / "missing-repo"),
                    "--project",
                    str(root / "missing.sug"),
                    "--status",
                    str(status),
                    "--preflight-only",
                ]
            )
            self.assertEqual(code, 1)
            report = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "preflight-failed")
            self.assertIn("error", report)


if __name__ == "__main__":
    unittest.main()
