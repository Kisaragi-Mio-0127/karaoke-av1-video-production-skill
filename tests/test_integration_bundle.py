"""Regression tests for the sanitized public integration bundle."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "integration" / "strangeutagame"
SCRIPTS = BUNDLE / "scripts"
DEPENDENCY_MANIFEST = BUNDLE / "dependency-manifest.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_fake_target(parent: Path) -> Path:
    target = parent / "StrangeUtaGame"
    (target / "src" / "strange_uta_game").mkdir(parents=True)
    (target / "scripts").mkdir()
    (target / "pyproject.toml").write_text(
        '[project]\nname = "strange-uta-game"\n', encoding="utf-8"
    )
    return target


class IntegrationBundleTests(unittest.TestCase):
    def test_dependency_manifest_covers_bundle_and_direct_imports(self) -> None:
        manifest = json.loads(DEPENDENCY_MANIFEST.read_text(encoding="utf-8"))
        records = manifest["scripts"]
        recorded_paths = {record["path"] for record in records}
        bundled_paths = {path.name for path in SCRIPTS.glob("*.py")}
        self.assertEqual(recorded_paths, bundled_paths)
        self.assertTrue(
            all(record.get("reason", "").strip() for record in records),
            "every dependency record must explain its classification",
        )

        actual_direct_imports: set[str] = set()
        for path in SCRIPTS.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(
                isinstance(node, ast.ImportFrom)
                and bool(node.module)
                and node.module.startswith("strange_uta_game")
                for node in ast.walk(tree)
            ):
                actual_direct_imports.add(path.name)
        recorded_direct_imports = {
            record["path"]
            for record in records
            if record["category"] == "direct-upstream-import"
        }
        self.assertEqual(recorded_direct_imports, actual_direct_imports)

        for record in manifest["support_tools"]:
            with self.subTest(support_tool=record["path"]):
                self.assertTrue((ROOT / record["path"]).is_file())
                self.assertTrue(record.get("reason", "").strip())

        for readme_name in ("README.md", "README.zh-CN.md"):
            readme = (ROOT / readme_name).read_text(encoding="utf-8")
            for path in recorded_paths:
                with self.subTest(readme=readme_name, script=path):
                    self.assertIn(f"`{path}`", readme)

    def test_all_bundled_python_parses(self) -> None:
        files = sorted(SCRIPTS.glob("*.py"))
        self.assertGreaterEqual(len(files), 15)
        for path in files:
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                self.assertIsNotNone(
                    ast.get_docstring(tree),
                    "bundled scripts need a module comment explaining their purpose",
                )

    def test_documentation_has_bilingual_pairs_without_cjk_word_spacing(self) -> None:
        references = ROOT / "references"
        english_references = sorted(
            path for path in references.glob("*.md") if not path.name.endswith(".zh-CN.md")
        )
        self.assertGreaterEqual(len(english_references), 6)
        for english in english_references:
            chinese = english.with_name(f"{english.stem}.zh-CN.md")
            with self.subTest(reference=english.name):
                self.assertTrue(chinese.is_file())
                self.assertIn(chinese.name, english.read_text(encoding="utf-8"))
                self.assertIn(english.name, chinese.read_text(encoding="utf-8"))

        chinese_documents = [
            ROOT / "README.zh-CN.md",
            ROOT / "NOTICE.md",
            ROOT / "THIRD_PARTY_NOTICES.md",
            ROOT / "SKILL.md",
            *(path.with_name(f"{path.stem}.zh-CN.md") for path in english_references),
        ]
        for path in chinese_documents:
            with self.subTest(chinese_document=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, r"[一-龯]")
                self.assertIsNone(
                    re.search(r"[一-龯] [一-龯]", text),
                    "Chinese prose must not insert spaces between CJK characters",
                )

    def test_asr_uses_explicit_language_and_fails_closed(self) -> None:
        source = (SCRIPTS / "audit_karaoke_asr_recognition.py").read_text(
            encoding="utf-8"
        )
        language_source = (SCRIPTS / "karaoke_language.py").read_text(encoding="utf-8")
        self.assertIn('SUPPORTED_LANGUAGES = frozenset({"ja", "zh", "en"})', language_source)
        self.assertIn('if language == "zh" else comparable', source)
        self.assertIn("--language ja, zh, or en is required with direct audio", source)
        self.assertIn("one manifest ASR run must select tracks in exactly one language", source)
        self.assertIn('"--allow-unresolved"', source)
        self.assertIn('report.get("support_gate_ok") is True', source)

    def test_pitch_tool_and_dual_audio_contract_are_bundled(self) -> None:
        top_level = ROOT / "scripts" / "pitch_shift_audio.py"
        installed = SCRIPTS / "pitch_shift_audio.py"
        self.assertEqual(top_level.read_bytes(), installed.read_bytes())
        pitch_source = top_level.read_text(encoding="utf-8")
        for option in ("--semitones", "--no-formant", "--rubberband"):
            self.assertIn(option, pitch_source)
        pitch = load_module("public_pitch_shift_audio", top_level)
        self.assertTrue(pitch.is_formal_lossless_source({"codec_name": "flac"}))
        self.assertTrue(pitch.is_formal_lossless_source({"codec_name": "pcm_f32le"}))
        self.assertFalse(pitch.is_formal_lossless_source({"codec_name": "mp3"}))
        self.assertFalse(pitch.is_formal_lossless_source({"codec_name": "aac"}))

        renderer = (SCRIPTS / "render_karaoke_direct_av1_420_album.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"audio_bitrate": "320k"', renderer)
        self.assertIn('"audio_codec": "flac"', renderer)
        self.assertIn("lossless-output", renderer)

    def test_sug_checker_requires_a_representative_project(self) -> None:
        checker = load_module(
            "public_check_sug_compatibility",
            ROOT / "scripts" / "check_sug_compatibility.py",
        )
        with self.assertRaisesRegex(ValueError, "at least one representative"):
            checker.inspect_checkout(
                ROOT,
                [],
                minimum_app_version="1.4.5",
                expected_sug_version="0.3.0",
            )

    def test_sensitive_literals_are_absent(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(SCRIPTS.glob("*.py"))
        )
        forbidden_patterns = (
            r"[A-Za-z]:\\(?:Users|ProgramData)\\",
            r"karaoke_sources[\\/](?!album\.json)[^\r\n]+[\\/]album\.json",
            r"DEFAULT_COVER_URL\s*=\s*[\"']https?://",
            r"(?i)(?:api[_-]?key|password|access[_-]?token)\s*[:=]\s*[\"'][^\"']+",
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))
    def test_example_manifest_loads_without_private_media(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        try:
            album = load_module("public_karaoke_album", SCRIPTS / "karaoke_album.py")
            manifest = album.load_album_manifest(
                ROOT / "examples" / "album.example.json",
                require_five_tracks=False,
            )
        finally:
            sys.path.remove(str(SCRIPTS))
        self.assertEqual(manifest.title, "Example Karaoke Album")
        self.assertEqual(len(manifest.tracks), 1)

    def test_installer_is_guarded_and_reproducible(self) -> None:
        installer = load_module(
            "karaoke_skill_installer",
            ROOT / "scripts" / "install_strangeutagame_integration.py",
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            target = make_fake_target(Path(temporary))
            dry = installer.install(target, force=False, dry_run=True)
            self.assertTrue(dry["dry_run"])
            self.assertFalse((target / "scripts" / "karaoke_album.py").exists())
            first = installer.install(target, force=False, dry_run=False)
            self.assertTrue((target / "scripts" / "karaoke_album.py").is_file())
            second = installer.install(target, force=False, dry_run=False)
            self.assertTrue(all(item["action"] == "unchanged" for item in second["files"]))
            self.assertEqual(len(first["files"]), len(second["files"]))
            modified = target / "scripts" / "karaoke_album.py"
            modified.write_text("# local change\n", encoding="utf-8")
            preview = installer.install(target, force=False, dry_run=True)
            self.assertIn("scripts/karaoke_album.py", preview["conflicts"])
            with self.assertRaises(SystemExit):
                installer.install(target, force=False, dry_run=False)
            replaced = installer.install(target, force=True, dry_run=False)
            self.assertTrue(replaced["backups"])
            self.assertNotEqual(modified.read_text(encoding="utf-8"), "# local change\n")

    def test_installer_rejects_directory_and_link_destinations(self) -> None:
        installer = load_module(
            "karaoke_skill_installer_safety",
            ROOT / "scripts" / "install_strangeutagame_integration.py",
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            parent = Path(temporary)
            target = make_fake_target(parent)
            collision = target / "scripts" / "karaoke_album.py"
            collision.mkdir()
            with self.assertRaises(SystemExit):
                installer.install(target, force=True, dry_run=True)

            collision.rmdir()
            outside = parent / "outside.py"
            outside.write_text("# outside\n", encoding="utf-8")
            try:
                collision.symlink_to(outside)
            except OSError:
                with mock.patch.object(
                    installer,
                    "_is_reparse_point",
                    side_effect=lambda path: Path(path) == collision,
                ):
                    with self.assertRaises(SystemExit):
                        installer.install(target, force=True, dry_run=True)
            else:
                with self.assertRaises(SystemExit):
                    installer.install(target, force=True, dry_run=True)
            self.assertEqual(outside.read_text(encoding="utf-8"), "# outside\n")

    def test_installer_rolls_back_mid_commit_failure(self) -> None:
        installer = load_module(
            "karaoke_skill_installer_rollback",
            ROOT / "scripts" / "install_strangeutagame_integration.py",
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            target = make_fake_target(Path(temporary))
            installer.install(target, force=False, dry_run=False)
            first = target / "scripts" / "karaoke_album.py"
            second = target / "scripts" / "karaoke_language.py"
            first.write_text("# first local change\n", encoding="utf-8")
            second.write_text("# second local change\n", encoding="utf-8")

            real_replace = installer.os.replace
            stage_replacements = 0

            def fail_second_stage_replace(source, destination):
                nonlocal stage_replacements
                if ".karaoke-skill-stage-" in str(source):
                    stage_replacements += 1
                    if stage_replacements == 2:
                        raise OSError("injected commit failure")
                return real_replace(source, destination)

            with mock.patch.object(
                installer.os, "replace", side_effect=fail_second_stage_replace
            ):
                with self.assertRaises(RuntimeError):
                    installer.install(target, force=True, dry_run=False)
            self.assertEqual(first.read_text(encoding="utf-8"), "# first local change\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "# second local change\n")
            self.assertFalse(list(target.glob(".karaoke-skill-stage-*")))

    def test_network_access_is_explicit_opt_in(self) -> None:
        timing = (SCRIPTS / "karaoke_timing.py").read_text(encoding="utf-8")
        renderer = (SCRIPTS / "render_vinyl_karaoke.py").read_text(encoding="utf-8")
        self.assertIn("if not refresh:", timing)
        self.assertIn("--refresh-source", timing)
        self.assertIn('"--allow-network"', renderer)
        self.assertIn("allow_network=args.allow_network", renderer)
        self.assertNotIn("not args.no_network", renderer)

    def test_english_converter_supports_direct_script_execution(self) -> None:
        converter = (SCRIPTS / "convert_english_sug_word_tokens.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from .karaoke_language import", converter)
        self.assertIn("from karaoke_language import", converter)
        self.assertNotIn("from scripts.karaoke_language import", converter)

    def test_cover_network_guard_rejects_unsafe_redirects_and_oversize(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        try:
            renderer = load_module(
                "public_render_vinyl_karaoke",
                SCRIPTS / "render_vinyl_karaoke.py",
            )
        finally:
            sys.path.remove(str(SCRIPTS))
        with self.assertRaises(ValueError):
            renderer.fetch_cover("http://example.com/cover.jpg")
        with self.assertRaises(ValueError):
            renderer.fetch_cover("https://localhost/cover.jpg")

        public_address = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        redirect = urllib.error.HTTPError(
            "https://example.com/cover.jpg", 302, "redirect", {}, None
        )
        redirect_opener = mock.MagicMock()
        redirect_opener.open.side_effect = redirect
        with mock.patch.object(
            renderer.socket, "getaddrinfo", return_value=public_address
        ), mock.patch.object(
            renderer.urllib.request, "build_opener", return_value=redirect_opener
        ) as build_opener:
            with self.assertRaises(urllib.error.HTTPError):
                renderer.fetch_cover("https://example.com/cover.jpg")
            handler = build_opener.call_args.args[0]
            self.assertTrue(
                issubclass(handler, renderer.urllib.request.HTTPRedirectHandler)
            )

        class Headers:
            @staticmethod
            def get_content_type() -> str:
                return "image/jpeg"

        class OversizeResponse:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read(_limit: int) -> bytes:
                return b"123456789"

        oversize_opener = mock.MagicMock()
        oversize_opener.open.return_value = OversizeResponse()
        with mock.patch.object(renderer, "MAX_NETWORK_COVER_BYTES", 8), mock.patch.object(
            renderer.socket, "getaddrinfo", return_value=public_address
        ), mock.patch.object(
            renderer.urllib.request, "build_opener", return_value=oversize_opener
        ):
            with self.assertRaises(RuntimeError):
                renderer.fetch_cover("https://example.com/cover.jpg")

    def test_private_override_examples_match_loader_shapes(self) -> None:
        display = json.loads(
            (ROOT / "examples" / "display-overrides.example.json").read_text(
                encoding="utf-8"
            )
        )
        ruby = json.loads(
            (ROOT / "examples" / "ruby-group-overrides.example.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(display, {"overrides": {}})
        self.assertEqual(
            set(ruby),
            {"reading_overrides", "span_splits", "multi_kanji_splits", "linked_spans"},
        )
        timing = json.loads(
            (ROOT / "examples" / "timing-reading-overrides.example.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(timing, {"reading_overrides": {}})


if __name__ == "__main__":
    unittest.main()
