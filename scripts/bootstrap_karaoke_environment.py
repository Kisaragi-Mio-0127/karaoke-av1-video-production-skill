#!/usr/bin/env python3
"""Explicitly bootstrap or dry-run a public Japanese/general karaoke runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from karaoke_bootstrap import DEFAULT_MANIFEST, bootstrap, redact_report_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True, help="StrangeUtaGame checkout")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--allow-custom-manifest",
        action="store_true",
        help="Authorize a non-built-in manifest; URL hosts remain strictly allowlisted",
    )
    parser.add_argument(
        "--accept-mms-cc-by-nc-4-0",
        action="store_true",
        help=(
            "Permit MMS_FA download under CC BY-NC 4.0: attribution is required "
            "and use is non-commercial only"
        ),
    )
    parser.add_argument(
        "--allow-python-download",
        action="store_true",
        help="Allow uv to download a managed Python when no suitable local interpreter exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Deep-verify and plan only; never write or actively initiate network requests",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Pass uv offline mode and block model/Python downloads",
    )
    parser.add_argument(
        "--redact-paths",
        action="store_true",
        help="Replace absolute local paths in the JSON report",
    )
    args = parser.parse_args()
    report = bootstrap(
        args.target,
        manifest_path=args.manifest,
        dry_run=args.dry_run,
        offline=args.offline,
        allow_custom_manifest=args.allow_custom_manifest,
        accept_mms_cc_by_nc_4_0=args.accept_mms_cc_by_nc_4_0,
        allow_python_download=args.allow_python_download,
    )
    if args.redact_paths:
        report = redact_report_paths(report)
        report["paths_redacted"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
