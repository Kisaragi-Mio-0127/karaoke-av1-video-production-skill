#!/usr/bin/env python3
"""Run the isolated Japanese karaoke workflow with vinyl or spectrum visuals."""

from __future__ import annotations

import argparse

try:
    from scripts.karaoke_workflow import (
        add_common_arguments,
        config_from_args,
        print_result,
        run_workflow,
    )
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_workflow import (  # type: ignore[no-redef]
        add_common_arguments,
        config_from_args,
        print_result,
        run_workflow,
    )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument(
        "--pronunciation-validation",
        choices=("off", "optional", "required"),
        default="optional",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    config = config_from_args(
        args,
        language="ja",
        layout="wide",
        pronunciation_validation=args.pronunciation_validation,
    )
    return print_result(run_workflow(config))


if __name__ == "__main__":
    raise SystemExit(main())
