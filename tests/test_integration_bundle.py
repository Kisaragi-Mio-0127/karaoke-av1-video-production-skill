"""Repository-level checks for the installable Japanese/general bundle."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "integration" / "strangeutagame"
SCRIPTS = BUNDLE / "scripts"
MANIFEST = BUNDLE / "dependency-manifest.json"


def _load_installer():
    path = ROOT / "scripts" / "install_strangeutagame_integration.py"
    spec = importlib.util.spec_from_file_location("skill_installer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_target(root: Path) -> Path:
    target = root / "StrangeUtaGame"
    (target / "src" / "strange_uta_game").mkdir(parents=True)
    (target / "scripts").mkdir()
    (target / "pyproject.toml").write_text(
        '[project]\nname = "strange-uta-game"\n', encoding="utf-8"
    )
    return target


def test_dependency_manifest_exactly_covers_python_bundle() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    records = [
        record
        for section in ("scripts", "shared_modules", "package_files")
        for record in manifest[section]
    ]
    declared = [record["path"] for record in records]
    actual = sorted(
        path.relative_to(SCRIPTS).as_posix()
        for path in SCRIPTS.rglob("*.py")
        if "__pycache__" not in path.parts
    )

    assert len(declared) == len(set(declared))
    assert sorted(declared) == actual
    assert "render_karaoke_direct_hevc444_album.py" not in declared
    assert "run_karaoke_auto_workflow.py" not in declared
    assert "run_karaoke_zh_en_full_auto.py" not in declared
    assert "run_karaoke_zh_en_workflow.py" not in declared
    assert "run_karaoke_zh_en_mms_workflow.py" not in declared
    assert {
        "karaoke_common/artwork.py",
        "karaoke_full_auto.py",
        "karaoke_mms_editable.py",
        "karaoke_model_paths.py",
        "run_karaoke_japanese_full_auto.py",
        "run_karaoke_japanese_workflow.py",
        "run_karaoke_japanese_mms_workflow.py",
    }.issubset(declared)
    records_by_path = {record["path"]: record for record in records}
    assert records_by_path["karaoke_full_auto.py"]["category"] == (
        "shared-internal-module"
    )
    assert records_by_path["karaoke_full_auto.py"]["upstream_dependency"] == (
        "transitive-runtime"
    )
    assert records_by_path["run_karaoke_japanese_full_auto.py"]["category"] == (
        "transitive-runtime"
    )
    assert all(record.get("reason", "").strip() for record in records)
    requirement_records = manifest["requirements"]
    assert {record["path"] for record in requirement_records} == {
        path.relative_to(BUNDLE).as_posix()
        for path in (BUNDLE / "requirements").iterdir()
        if path.is_file()
    }
    assert all(
        record.get("destination", "").strip() and record.get("reason", "").strip()
        for record in requirement_records
    )
    assert manifest["bootstrap_assets"] == [
        {
            "path": "bootstrap-assets.json",
            "category": "bootstrap-configuration",
            "reason": manifest["bootstrap_assets"][0]["reason"],
        }
    ]
    assert manifest["bootstrap_assets"][0]["reason"].strip()


def test_all_bundled_python_parses_and_has_module_documentation() -> None:
    for path in sorted(SCRIPTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert ast.get_docstring(tree), path.relative_to(SCRIPTS).as_posix()


def test_full_auto_keeps_zh_en_route_import_lazy() -> None:
    path = SCRIPTS / "karaoke_full_auto.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    top_level_imports = {
        alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not any("run_karaoke_zh_en" in name for name in top_level_imports)


def test_installer_dry_run_is_manifest_driven(tmp_path: Path) -> None:
    installer = _load_installer()
    target = _fake_target(tmp_path)

    report = installer.install(target, force=False, dry_run=True)
    destinations = {record["destination"] for record in report["files"]}
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = {
        f"scripts/{record['path']}"
        for section in ("scripts", "shared_modules", "package_files")
        for record in manifest[section]
    }
    declared.update(record["destination"] for record in manifest["requirements"])

    assert declared == destinations
    assert all(record["action"] == "install" for record in report["files"])
    assert not (target / ".karaoke-skill-backup").exists()
    assert not list(target.glob(".karaoke-skill-stage-*"))


@pytest.mark.parametrize(
    ("path", "destination"),
    [
        (r"requirements\pinned.txt", "safe.txt"),
        ("requirements/pinned.txt", r"..\escape.txt"),
        ("C:/requirements/pinned.txt", "safe.txt"),
        ("requirements/pinned.txt", r"\\?\C:\escape.txt"),
    ],
)
def test_installer_rejects_windows_requirement_mapping_escapes(
    monkeypatch, tmp_path: Path, path: str, destination: str
) -> None:
    installer = _load_installer()
    manifest_path = tmp_path / "dependency-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "requirements": [
                    {"path": path, "destination": destination}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(installer, "DEPENDENCY_MANIFEST", manifest_path)

    with pytest.raises(SystemExit, match="Unsafe requirement mapping"):
        installer._manifest_requirement_paths()


def test_documentation_is_bilingual_and_focuses_on_current_routes() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    for text in (english, chinese, skill):
        assert "run_karaoke_japanese_workflow.py" in text
        assert "run_karaoke_japanese_mms_workflow.py" in text
        assert "render_karaoke_direct_hevc444_album.py" not in text
        assert "--quality-policy" in text
        assert "auto-fallback" in text
    assert "current wide layout" in english
    assert "当前宽屏布局" in chinese
    assert re.search(r"[\u4e00-\u9fff]", chinese)
    assert not re.search(r"[\u4e00-\u9fff] [\u4e00-\u9fff]", chinese)


def test_network_and_output_expansion_require_explicit_flags() -> None:
    timing = (SCRIPTS / "karaoke_timing.py").read_text(encoding="utf-8")
    workflow = (SCRIPTS / "karaoke_workflow.py").read_text(encoding="utf-8")
    mms = (SCRIPTS / "run_karaoke_japanese_mms_workflow.py").read_text(
        encoding="utf-8"
    )
    direct = (SCRIPTS / "render_karaoke_direct_av1_420_album.py").read_text(
        encoding="utf-8"
    )

    assert "--refresh-source" in timing
    assert "--allow-network" in workflow
    assert "--allow-cover-network" in mms
    assert "--lossless-companion" in workflow and "action=\"store_true\"" in workflow
    assert "--full-decode" in workflow and "action=\"store_true\"" in workflow
    assert "--full-decode" in direct and "action=\"store_true\"" in direct


def test_bootstrap_license_and_network_boundaries_are_publicly_declared() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap_karaoke_environment.py").read_text(
        encoding="utf-8"
    )
    checker = (ROOT / "scripts" / "check_karaoke_environment.py").read_text(
        encoding="utf-8"
    )
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    manifest = json.loads((BUNDLE / "bootstrap-assets.json").read_text(encoding="utf-8"))

    for flag in (
        "--accept-mms-cc-by-nc-4-0",
        "--allow-custom-manifest",
        "--allow-python-download",
        "--redact-paths",
    ):
        assert flag in bootstrap
    assert "non-commercial only" in bootstrap
    assert "without actively initiating network requests" in checker
    assert "CC-BY-NC-4.0" in notices
    assert "OpenAI Whisper model weights" in notices
    assert "MIT License" in notices
    licenses = {record["name"]: record["license"] for record in manifest["models"]}
    assert licenses["mms-forced-alignment"]["requires_acceptance"] is True
    assert licenses["whisper-base"]["spdx"] == "MIT"


def test_bootstrap_dependencies_are_named_version_pinned_not_lock() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    requirement_names = {
        Path(record["path"]).name for record in manifest["requirements"]
    }
    assert "requirements-karaoke.pinned.txt" in requirement_names
    assert not any("lock" in name.casefold() for name in requirement_names)
    assert all("reproducible installation" not in record["reason"] for record in manifest["requirements"])


def test_manifest_driven_tools_have_no_import_time_private_manifest() -> None:
    for name in ("package_karaoke_numbered_archives.py", "transcode_karaoke_av1.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "DEFAULT_MANIFEST_PATH" not in text


def test_public_bundle_contains_no_track_specific_or_private_literals() -> None:
    forbidden = re.compile(
        r"((?<![A-Za-z0-9_])[A-Za-z]:[/\\]|/home/|private-album-name|"
        r"private-song-title|private-lyric-fragment)",
        re.IGNORECASE,
    )
    for path in sorted(
        [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "SKILL.md"]
        + list(SCRIPTS.rglob("*.py"))
    ):
        assert forbidden.search(path.read_text(encoding="utf-8")) is None, path


def test_skill_contains_no_runtime_or_test_artifacts() -> None:
    forbidden_names = {".venv", ".pytest_cache", ".ruff_cache", "__pycache__"}
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8"
    ).splitlines()
    assert not any(
        forbidden_names.intersection(Path(path).parts)
        or Path(path).suffix.lower() in {".mp4", ".mkv", ".wav"}
        for path in tracked
    )
