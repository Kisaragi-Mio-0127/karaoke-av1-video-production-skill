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
INTERNAL_MODULES = {"sug_ruby.py"}


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
        script_paths = {record["path"] for record in records}
        shared_records = manifest.get("shared_modules", [])
        shared_paths = {record["path"] for record in shared_records}
        package_records = manifest.get("package_files", [])
        package_paths = {record["path"] for record in package_records}
        recorded_paths = script_paths | shared_paths | package_paths
        bundled_paths = {
            path.relative_to(SCRIPTS).as_posix()
            for path in SCRIPTS.rglob("*.py")
            if "__pycache__" not in path.parts
        }
        self.assertEqual(recorded_paths, bundled_paths)
        self.assertEqual(len(script_paths), 22)
        self.assertEqual(len(package_paths), 5)
        self.assertEqual(shared_paths, INTERNAL_MODULES)
        self.assertTrue(
            all(
                record.get("category") == "shared-internal-module"
                and record.get("reason", "").strip()
                for record in shared_records
            )
        )
        self.assertTrue(
            all(
                record.get("category") == "package-file"
                and record.get("reason", "").strip()
                for record in package_records
            )
        )
        self.assertTrue((SCRIPTS / "sug_ruby.py").is_file())
        self.assertTrue(
            all(
                record.get("reason", "").strip()
                for record in records + shared_records + package_records
            ),
            "every dependency record must explain its classification",
        )

        actual_direct_imports: set[str] = set()
        for relative in sorted(script_paths):
            path = SCRIPTS / Path(*relative.split("/"))
            if relative in INTERNAL_MODULES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(
                isinstance(node, ast.ImportFrom)
                and bool(node.module)
                and node.module.startswith("strange_uta_game")
                for node in ast.walk(tree)
            ):
                actual_direct_imports.add(relative)
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
            for path in sorted(recorded_paths | INTERNAL_MODULES):
                with self.subTest(readme=readme_name, script=path):
                    self.assertIn(f"`{path}`", readme)

    def test_all_bundled_python_parses(self) -> None:
        files = sorted(
            path
            for path in SCRIPTS.rglob("*.py")
            if "__pycache__" not in path.parts
        )
        self.assertEqual(len(files), 28)
        for path in files:
            with self.subTest(path=path.relative_to(SCRIPTS).as_posix()):
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
        document_pairs = [(ROOT / "README.md", ROOT / "README.zh-CN.md")]
        for english in english_references:
            chinese = english.with_name(f"{english.stem}.zh-CN.md")
            with self.subTest(reference=english.name):
                self.assertTrue(chinese.is_file())
                self.assertIn(chinese.name, english.read_text(encoding="utf-8"))
                self.assertIn(english.name, chinese.read_text(encoding="utf-8"))
            document_pairs.append((english, chinese))

        for english, chinese in document_pairs:
            english_text = english.read_text(encoding="utf-8")
            chinese_text = chinese.read_text(encoding="utf-8")

            def structure(text: str) -> tuple[list[int], int, int, int]:
                lines = text.splitlines()
                heading_levels = [
                    len(match.group(1))
                    for line in lines
                    if (match := re.match(r"^(#{1,6})\s+", line))
                ]
                bullet_count = sum(line.startswith("- ") for line in lines)
                numbered_count = sum(bool(re.match(r"^\d+\.\s+", line)) for line in lines)
                fence_count = sum(line.startswith("```") for line in lines)
                return heading_levels, bullet_count, numbered_count, fence_count

            with self.subTest(document_pair=english.name):
                self.assertEqual(structure(english_text), structure(chinese_text))

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
                prose_lines: list[str] = []
                in_fence = False
                for line in text.splitlines():
                    if line.lstrip().startswith("```"):
                        in_fence = not in_fence
                        continue
                    if not in_fence:
                        prose_lines.append(line)
                prose = "\n".join(prose_lines)
                self.assertIsNone(
                    re.search(
                        r"[一-龯][，。；：！？、）】》]?\n[ \t]*[一-龯]",
                        prose,
                    ),
                    "Chinese prose must not create rendered spaces with soft line wraps",
                )

    def test_documentation_omits_internal_packaging_change_log(self) -> None:
        markdown = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(ROOT.rglob("*.md"))
            if ".git" not in path.parts
        )
        for phrase in (
            "Changes made for this repository through",
            "source refresh and public cover retrieval require explicit opt-in",
            "刷新歌词源和获取公开封面都必须显式授权",
            "不要把真实清单",
            "sanitized snapshot",
            "公开快照",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, markdown)

    def test_public_language_specific_implementations_are_absent(self) -> None:
        source = (SCRIPTS / "audit_karaoke_asr_recognition.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "_simplify_chinese",
            "is_chinese_character",
            "english_word_spans",
            "pinyin_for_character",
            "OpenCC",
            "pypinyin",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        preview_source = (SCRIPTS / "karaoke_review_preview.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('choices=("vinyl", "spectrum")', preview_source)
        self.assertIn('COMPATIBILITY_AUDIO_BITRATE = "320k"', preview_source)
        self.assertIn('"av1_preset":', preview_source)
        self.assertNotIn("wide-zh", preview_source)
        self.assertNotIn("wide-en", preview_source)
        self.assertNotIn("scripts.karaoke_zh_en", preview_source)
        self.assertNotIn("_DISPLAY_PHRASE_OVERRIDES", preview_source)
        self.assertNotIn("_SINGLE_TRACK_DISPLAY_PHRASE_OVERRIDES", preview_source)
        self.assertNotIn("_split_sentence_by_display_override", preview_source)
        self.assertIn("--pronunciation-validation", preview_source)
        self.assertIn("--vinyl-motion", preview_source)

        vinyl_source = (SCRIPTS / "render_vinyl_karaoke.py").read_text(
            encoding="utf-8"
        )
        vinyl_tree = ast.parse(vinyl_source)
        vinyl_strings = {
            node.value
            for node in ast.walk(vinyl_tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertTrue(
            {"wide-bottom-zh", "wide-bottom-en"}.isdisjoint(vinyl_strings)
        )
        for forbidden in (
            "natural_advance",
            "english_wide",
            "letter_spacing",
            "fsp_pattern",
            "fscx_pattern",
        ):
            with self.subTest(vinyl_forbidden=forbidden):
                self.assertNotIn(forbidden, vinyl_source)

    def test_bundled_workflow_entry_and_pronunciation_policy(self) -> None:
        sys.path.insert(0, str(BUNDLE))
        try:
            pronunciation = load_module(
                "public_karaoke_pronunciation_policy",
                SCRIPTS / "karaoke_common" / "pronunciation.py",
            )
        finally:
            sys.path.remove(str(BUNDLE))
        self.assertEqual(
            pronunciation.PRONUNCIATION_VALIDATION_MODES,
            ("optional", "required", "off"),
        )
        self.assertTrue((SCRIPTS / "run_karaoke_japanese_workflow.py").is_file())
        workflow_source = (SCRIPTS / "karaoke_workflow.py").read_text(encoding="utf-8")
        self.assertIn("cover_source_audio", workflow_source)
        self.assertIn("regenerate-current-vinyl", workflow_source)
        self.assertIn('"vinyl_motion": "rotate"', workflow_source)
        self.assertIn("--lossless-companion", workflow_source)
        self.assertIn("--lossless-output", workflow_source)
        self.assertIn('else "not-requested"', workflow_source)
        japanese_source = (SCRIPTS / "run_karaoke_japanese_workflow.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('default="optional"', japanese_source)
        common_layout = (SCRIPTS / "karaoke_common" / "layout.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Lane(x=32, main_y=660", common_layout)
        self.assertIn("Lane(x=1888, main_y=870", common_layout)
        vinyl_source = (SCRIPTS / "render_vinyl_karaoke.py").read_text(encoding="utf-8")
        self.assertIn(
            'VINYL_STYLE_VERSION = "direction-neutral-concentric-grooves/v3/backplate-absent"',
            vinyl_source,
        )
        self.assertIn('"vinyl_sha256": vinyl_sha256', vinyl_source)

    def test_current_workflow_direct_and_vinyl_contracts_are_merged(self) -> None:
        workflow = (SCRIPTS / "karaoke_workflow.py").read_text(encoding="utf-8")
        direct = (
            SCRIPTS / "render_karaoke_direct_av1_420_album.py"
        ).read_text(encoding="utf-8")
        vinyl = (SCRIPTS / "render_vinyl_karaoke.py").read_text(encoding="utf-8")

        for token in (
            "full_decode: bool = False",
            "validate_workflow_composition(config)",
            'if config.full_decode:',
            '"requested": requested',
            '"required": False',
            '"recommended": False',
        ):
            with self.subTest(workflow_token=token):
                self.assertIn(token, workflow)

        for token in (
            'default="optional"',
            'choices=PRONUNCIATION_VALIDATION_MODES',
            '"--lossless-companion",',
            '"--full-decode", action="store_true"',
            'default="rotate"',
            'ass["ass"] = str(task.ass_output.resolve())',
            'def refresh_existing_reports(',
            '"--refresh-existing-reports",',
            '"--refresh-existing-reports requires --report-only"',
            '"--refresh-existing-reports does not permit --full-decode"',
            '"lossless-companion-not-requested"',
            '"wide_compositions": wide_compositions',
        ):
            with self.subTest(direct_token=token):
                self.assertIn(token, direct)

        for token in (
            'if encoder == "av1_nvenc":',
            '"-profile:a",',
            '"aac_low",',
            'choices=("static", "rotate")',
            'default="rotate"',
            '_vinyl_filter(vinyl_motion=vinyl_motion',
        ):
            with self.subTest(vinyl_token=token):
                self.assertIn(token, vinyl)

        self.assertNotIn("music.126.net", vinyl)
        self.assertIn('DEFAULT_COVER_URL = ""', vinyl)
        self.assertIn("allow_network: bool = False", vinyl)
        self.assertIn("MAX_NETWORK_COVER_BYTES = 25 * 1024 * 1024", vinyl)

    def test_wide_layout_v5_has_no_vinyl_backplate(self) -> None:
        builder_source = (SCRIPTS / "build_karaoke_wide_artwork.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('WIDE_LAYOUT_VERSION = "wide-layout-v5/no-right-panels"', builder_source)
        self.assertIn('"vinyl": (40, 30, 340, 402)', builder_source)
        self.assertIn("BOTTOM_PANEL = (20, 576, 1900, 1050)", builder_source)
        self.assertIn("SLEEVE_BOTTOM_PADDING = 12", builder_source)
        self.assertNotIn("RIGHT_PANEL =", builder_source)
        self.assertNotIn("rounded_rectangle(RIGHT_PANEL", builder_source)
        self.assertIn('"right_panel": None', builder_source)
        self.assertIn('"right_panel_visible": False', builder_source)
        self.assertIn('"outer_right_panel": None', builder_source)
        self.assertIn('"outer_right_panel_visible": False', builder_source)
        self.assertIn('"vinyl_backplate": None', builder_source)
        self.assertIn('"vinyl_backplate_present": False', builder_source)
        self.assertIn('"vinyl_backplate_preserved": False', builder_source)
        direct_renderer_source = (
            SCRIPTS / "render_karaoke_direct_av1_420_album.py"
        ).read_text(encoding="utf-8")
        self.assertIn("validate_current_wide_compositions", direct_renderer_source)
        self.assertIn('"outer_right_panel_removed"', direct_renderer_source)
        self.assertIn('"vinyl_backplate_absent"', direct_renderer_source)
        self.assertIn('"wide_compositions": wide_compositions', direct_renderer_source)

        preview_source = (SCRIPTS / "karaoke_review_preview.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"spectrum_glow_top_padding_px": 56', preview_source)
        self.assertIn('"spectrum_clip_safe_geometry":', preview_source)

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
        transcoder = (SCRIPTS / "transcode_karaoke_av1.py").read_text(
            encoding="utf-8"
        )
        preview = (SCRIPTS / "karaoke_review_preview.py").read_text(
            encoding="utf-8"
        )
        for source in (renderer, transcoder, preview):
            self.assertIn("DEFAULT_AV1_CQ = 38", source)
            self.assertIn('DEFAULT_AV1_PRESET = "p7"', source)
        self.assertIn('"audio_bitrate": "320k"', renderer)
        self.assertIn('"audio_codec": "flac"', renderer)
        self.assertIn("lossless-output", renderer)
        self.assertIn("full_decode: bool = False", renderer)
        self.assertIn('"--full-decode"', renderer)
        self.assertIn('"full_decode_gate"', renderer)
        self.assertIn('"required": False', renderer)

    def test_sug_checker_requires_a_representative_project(self) -> None:
        checker = load_module(
            "public_check_sug_compatibility",
            ROOT / "scripts" / "check_sug_compatibility.py",
        )
        with self.assertRaisesRegex(ValueError, "at least one representative"):
            checker.inspect_checkout(
                ROOT,
                [],
                expected_app_version="1.4.5",
                expected_sug_version="0.3.0",
            )

    def test_sensitive_literals_are_absent(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for root in (SCRIPTS, ROOT / "tests")
            for path in sorted(root.rglob("*.py"))
            if "__pycache__" not in path.parts
        )
        forbidden_patterns = (
            r"(?i)\b[A-Za-z]:[\\/][A-Za-z0-9_.-]+[\\/][A-Za-z0-9_.-]+",
            r"karaoke_sources[\\/](?!album\.json)[^\r\n]+[\\/]album\.json",
            r"DEFAULT_COVER_URL\s*=\s*[\"']https?://",
            r"(?i)(?:api[_-]?key|password|access[_-]?token)\s*[:=]\s*[\"'][^\"']+",
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))

        preview_path = SCRIPTS / "karaoke_review_preview.py"
        preview_tree = ast.parse(
            preview_path.read_text(encoding="utf-8"), filename=str(preview_path)
        )
        for node in ast.walk(preview_tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            with self.subTest(path=preview_path.relative_to(ROOT), line=node.lineno):
                self.assertIsNone(
                    re.search(r"[ぁ-んァ-ヶ一-龯]{12,}", node.value),
                    "the generic preview renderer must not embed lyric-like literals",
                )
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
            manifest = json.loads(DEPENDENCY_MANIFEST.read_text(encoding="utf-8"))
            expected_python = {
                record["path"]
                for section in ("scripts", "shared_modules", "package_files")
                for record in manifest[section]
            }
            installed_python = {
                path.relative_to(target / "scripts").as_posix()
                for path in (target / "scripts").rglob("*.py")
                if "__pycache__" not in path.parts
            }
            self.assertEqual(installed_python, expected_python)
            for relative in sorted(expected_python):
                with self.subTest(installed_path=relative):
                    self.assertEqual(
                        (target / "scripts" / Path(*relative.split("/"))).read_bytes(),
                        (SCRIPTS / Path(*relative.split("/"))).read_bytes(),
                    )
            self.assertTrue(
                (target / "scripts" / "karaoke_common" / "pronunciation.py").is_file()
            )
            self.assertTrue(
                (target / "scripts" / "run_karaoke_japanese_workflow.py").is_file()
            )
            self.assertFalse((target / "scripts" / "karaoke_zh_en").exists())
            self.assertFalse(
                (target / "scripts" / "run_karaoke_zh_en_workflow.py").exists()
            )
            self.assertFalse(
                (target / "scripts" / "convert_english_sug_word_tokens.py").exists()
            )
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
        tree = ast.parse(timing)
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        namespace = {
            "SONGS": (),
            "json": json,
            "datetime": __import__("datetime").datetime,
            "timezone": __import__("datetime").timezone,
            "NETEASE_ENDPOINT": "https://example.invalid/lyrics",
            "fetch_netease_song": mock.Mock(
                return_value={"code": 200, "lrc": {"lyric": ""}}
            ),
            "_json_dump": lambda path, value: path.write_text(
                json.dumps(value), encoding="utf-8"
            ),
        }
        module = ast.Module(
            body=[
                functions["_source_song_record"],
                functions["load_or_fetch_source"],
            ],
            type_ignores=[],
        )
        exec(  # noqa: S102 - execute only selected, parsed local function definitions
            compile(module, str(SCRIPTS / "karaoke_timing.py"), "exec"), namespace
        )

        class Spec:
            song_id = "fixture"
            title = "fixture"
            artist = "fixture"
            audio_name = "fixture.flac"

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            source_path = Path(temporary) / "frozen.json"
            with self.assertRaises(FileNotFoundError):
                namespace["load_or_fetch_source"](source_path, False, [Spec()])
            namespace["fetch_netease_song"].assert_not_called()
            namespace["load_or_fetch_source"](
                source_path,
                False,
                [Spec()],
                allow_network=True,
            )
            namespace["fetch_netease_song"].assert_called_once_with("fixture")

        self.assertIn("--refresh-source", timing)
        self.assertIn('"--allow-network"', timing)
        self.assertIn("allow_network=args.allow_network", timing)
        self.assertIn('"--allow-network"', renderer)
        self.assertIn("allow_network=args.allow_network", renderer)
        self.assertNotIn("not args.no_network", renderer)

    def test_language_specific_zh_en_files_are_excluded(self) -> None:
        forbidden = {
            "convert_english_sug_word_tokens.py",
            "run_karaoke_zh_en_workflow.py",
            "karaoke_zh_en/__init__.py",
            "karaoke_zh_en/layout.py",
        }
        bundled = {
            path.relative_to(SCRIPTS).as_posix()
            for path in SCRIPTS.rglob("*.py")
            if "__pycache__" not in path.parts
        }
        self.assertTrue(forbidden.isdisjoint(bundled))
        manifest = json.loads(DEPENDENCY_MANIFEST.read_text(encoding="utf-8"))
        manifested = {
            record["path"]
            for section in ("scripts", "shared_modules", "package_files")
            for record in manifest[section]
        }
        self.assertTrue(forbidden.isdisjoint(manifested))
        workflow_closure = "\n".join(
            (SCRIPTS / relative).read_text(encoding="utf-8")
            for relative in manifested
        )
        self.assertNotIn("scripts.karaoke_zh_en", workflow_closure)

        guarded_modules = (
            "karaoke_language.py",
            "audit_karaoke_asr_recognition.py",
            "audit_karaoke_mms_alignment.py",
            "karaoke_timing.py",
            "karaoke_review_preview.py",
            "karaoke_workflow.py",
            "karaoke_common/pronunciation.py",
            "render_karaoke_direct_av1_420_album.py",
        )
        forbidden_name_fragments = (
            "chinese",
            "english",
            "hanzi",
            "mandarin",
            "pinyin",
        )
        for relative in guarded_modules:
            source = (SCRIPTS / relative).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
            names = {
                node.id.casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.Name)
            } | {
                node.name.casefold()
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            } | {
                node.attr.casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
            }
            exact_string_constants = {
                node.value.casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            with self.subTest(generic_module=relative):
                self.assertFalse(
                    any(
                        fragment in name
                        for name in names
                        for fragment in forbidden_name_fragments
                    )
                )
                self.assertTrue({"zh", "en"}.isdisjoint(exact_string_constants))
                self.assertNotRegex(source, r"(?i)\b(?:pypinyin|opencc)\b")

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
