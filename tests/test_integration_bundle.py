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
    def test_all_bundled_python_parses(self) -> None:
        files = sorted(SCRIPTS.glob("*.py"))
        self.assertGreaterEqual(len(files), 15)
        for path in files:
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

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
