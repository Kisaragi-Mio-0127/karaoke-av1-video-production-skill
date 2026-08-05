#!/usr/bin/env python3
"""Report core and optional karaoke production environment capabilities."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path


def command_check(name: str, args: list[str]) -> dict[str, object]:
    executable = shutil.which(name)
    if executable is None:
        return {"ok": False, "path": None, "detail": "not found on PATH"}
    completed = subprocess.run(
        [executable, *args], capture_output=True, text=True, errors="replace", check=False
    )
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return {
        "ok": completed.returncode == 0,
        "path": executable,
        "detail": output[0] if output else f"exit {completed.returncode}",
    }


def module_check(import_name: str) -> dict[str, object]:
    spec = importlib.util.find_spec(import_name)
    return {"ok": spec is not None, "module": import_name}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, help="StrangeUtaGame checkout")
    args = parser.parse_args()
    target = args.target.expanduser().resolve() if args.target else None
    if target:
        sys.path.insert(0, str(target / "src"))

    commands = {
        "git": command_check("git", ["--version"]),
        "uv": command_check("uv", ["--version"]),
        "ffmpeg": command_check("ffmpeg", ["-version"]),
        "ffprobe": command_check("ffprobe", ["-version"]),
        "rubberband_optional": command_check("rubberband", ["--version"]),
    }
    modules = {
        name: module_check(name)
        for name in (
            "strange_uta_game",
            "imageio_ffmpeg",
            "mutagen",
            "PIL",
            "numpy",
            "soundfile",
            "pykakasi",
            "stable_whisper",
            "torch",
            "torchaudio",
        )
    }
    core_names = ("git", "uv", "ffmpeg", "ffprobe")
    core_modules = (
        "strange_uta_game",
        "imageio_ffmpeg",
        "mutagen",
        "PIL",
        "numpy",
        "soundfile",
        "pykakasi",
    )
    core_ok = sys.version_info >= (3, 10) and all(
        bool(commands[name]["ok"]) for name in core_names
    ) and all(bool(modules[name]["ok"]) for name in core_modules)
    report = {
        "schema_version": "karaoke-environment-check/v1",
        "python": {
            "ok": sys.version_info >= (3, 10),
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "target": str(target) if target else None,
        "commands": commands,
        "modules": modules,
        "core_ok": core_ok,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if core_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
