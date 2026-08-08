#!/usr/bin/env python3
"""Check local karaoke state without actively initiating network requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from karaoke_bootstrap import DEFAULT_MANIFEST, check, redact_report_paths


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
        "--deep-verify",
        action="store_true",
        help="Read each complete model and verify SHA-256 (default checks exact size only)",
    )
    parser.add_argument(
        "--redact-paths",
        action="store_true",
        help="Replace absolute local paths in the JSON report",
    )
    args = parser.parse_args()
    report = check(
        args.target,
        args.manifest,
        deep_verify=args.deep_verify,
        allow_custom_manifest=args.allow_custom_manifest,
    )
    if args.redact_paths:
        report = redact_report_paths(report)
        report["paths_redacted"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["core_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
