#!/usr/bin/env python3
"""Verify a StrangeUtaGame checkout and selected SUG files without saving them.

Run this script with the target checkout's project-local Python. It imports the
real parser, checks the application and storage-format versions, and opens each
provided project read-only. No migration result is written back to disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    """Hash a project before and after parser loading to prove no disk write."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_key(value: str) -> tuple[int, ...]:
    """Return a numeric comparison key for ordinary StrangeUtaGame versions."""

    numbers = tuple(int(item) for item in re.findall(r"\d+", str(value)))
    return numbers or (0,)


def inspect_checkout(
    repo: Path,
    projects: list[Path],
    *,
    minimum_app_version: str,
    expected_sug_version: str,
) -> dict[str, Any]:
    """Import the target parser and load projects without modifying them."""

    if not projects:
        raise ValueError("at least one representative --project is required")
    repo = repo.expanduser().resolve()
    source_root = repo / "src"
    if not (source_root / "strange_uta_game").is_dir():
        raise SystemExit(f"Not a StrangeUtaGame checkout: {repo}")
    sys.path.insert(0, str(source_root))
    try:
        from strange_uta_game.__version__ import __version__
        from strange_uta_game.backend.infrastructure.persistence.sug_io import (
            SugMigrator,
            SugProjectParser,
        )
    finally:
        # Imported modules remain available; remove only the temporary path
        # entry so callers do not inherit an unexpected import search order.
        sys.path.remove(str(source_root))

    parser_version = str(SugMigrator.CURRENT_VERSION)
    records: list[dict[str, Any]] = []
    for requested in projects:
        project_path = requested.expanduser().resolve()
        before = sha256(project_path)
        raw = json.loads(project_path.read_text(encoding="utf-8"))
        loaded = SugProjectParser.load(str(project_path))
        after = sha256(project_path)
        records.append(
            {
                "path": str(project_path),
                "raw_version": str(raw.get("version", "1.0")),
                "sentence_count": len(getattr(loaded, "sentences", []) or []),
                "sha256_before": before,
                "sha256_after": after,
                "unchanged": before == after,
            }
        )

    checks = {
        "application_at_least_minimum": version_key(__version__)
        >= version_key(minimum_app_version),
        "parser_format_matches_expected": parser_version == expected_sug_version,
        "all_projects_loaded": len(records) == len(projects),
        "all_projects_unchanged": all(item["unchanged"] for item in records),
    }
    return {
        "schema_version": "karaoke-sug-compatibility/v1",
        "application_version": __version__,
        "minimum_application_version": minimum_app_version,
        "sug_format_version": parser_version,
        "expected_sug_format_version": expected_sug_version,
        "projects": records,
        "checks": checks,
        "ok": all(checks.values()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project", type=Path, action="append", default=[])
    parser.add_argument("--minimum-app-version", default="1.4.5")
    parser.add_argument("--expected-sug-version", default="0.3.0")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    # Windows may otherwise choose a legacy console code page that cannot
    # represent Japanese album names in the JSON report.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = inspect_checkout(
        args.repo,
        args.project,
        minimum_app_version=args.minimum_app_version,
        expected_sug_version=args.expected_sug_version,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
