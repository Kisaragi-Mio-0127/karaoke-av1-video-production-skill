"""Focused tests for the StrangeUtaGame compatibility version gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_checker():
    path = ROOT / "scripts" / "check_sug_compatibility.py"
    spec = importlib.util.spec_from_file_location("sug_compatibility_checker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_checkout(root: Path) -> tuple[Path, Path]:
    repo = root / "StrangeUtaGame"
    package = repo / "src" / "strange_uta_game"
    persistence = package / "backend" / "infrastructure" / "persistence"
    persistence.mkdir(parents=True)
    for directory in (
        package,
        package / "backend",
        package / "backend" / "infrastructure",
        persistence,
    ):
        (directory / "__init__.py").write_text("", encoding="utf-8")
    (package / "__version__.py").write_text(
        '__version__ = "1.6.2"\n', encoding="utf-8"
    )
    (persistence / "sug_io.py").write_text(
        "class SugMigrator:\n"
        '    CURRENT_VERSION = "0.3.0"\n\n'
        "class SugProjectParser:\n"
        "    @staticmethod\n"
        "    def load(path):\n"
        "        return type('Project', (), {'sentences': []})()\n",
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "strange-uta-game"\nversion = "1.2.6"\n',
        encoding="utf-8",
    )
    project = repo / "representative.sug"
    project.write_text(json.dumps({"version": "0.3.0"}), encoding="utf-8")
    return repo, project


def test_checker_defaults_to_strangeutagame_1_6_2_and_sug_0_3_0() -> None:
    args = _load_checker().build_parser().parse_args(
        ["--repo", ".", "--project", "representative.sug"]
    )

    assert args.expected_app_version == "1.6.2"
    assert args.expected_sug_version == "0.3.0"


def test_official_package_metadata_mismatch_is_diagnostic_only(tmp_path: Path) -> None:
    checker = _load_checker()
    repo, project = _fake_checkout(tmp_path)
    prior_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "strange_uta_game" or name.startswith("strange_uta_game.")
    }
    for name in prior_modules:
        sys.modules.pop(name)
    try:
        report = checker.inspect_checkout(
            repo,
            [project],
            expected_app_version="1.6.2",
            expected_sug_version="0.3.0",
        )
        wrong_version_report = checker.inspect_checkout(
            repo,
            [project],
            expected_app_version="1.5.0",
            expected_sug_version="0.3.0",
        )
    finally:
        for name in list(sys.modules):
            if name == "strange_uta_game" or name.startswith("strange_uta_game."):
                sys.modules.pop(name)
        sys.modules.update(prior_modules)

    assert report["application_version"] == "1.6.2"
    assert report["package_version"] == "1.2.6"
    assert report["schema_version"] == "karaoke-sug-compatibility/v2"
    assert report["diagnostics"]["package_version_matches_application"] is False
    assert "package_version_matches_application" not in report["checks"]
    assert "required_checks" not in report
    assert all(report["checks"].values())
    assert report["ok"] is True
    assert wrong_version_report["checks"]["application_version_matches_expected"] is False
    assert wrong_version_report["ok"] is False
