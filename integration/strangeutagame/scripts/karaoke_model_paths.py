"""Canonical repository-local paths for karaoke runtime model weights."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = ROOT / "models"
MMS_MODEL_PATH = MODELS_ROOT / "mms" / "model.pt"
WHISPER_MODEL_DIR = MODELS_ROOT / "whisper"
DEMUX_MODEL_PATH = MODELS_ROOT / "demucs" / "955717e8-8726e21a.th"


def resolve_mms_model_path(explicit: Path | None) -> Path:
    """Resolve an explicit MMS checkpoint or the canonical local default.

    The resolver deliberately does not inspect Torch or project cache folders.
    Missing checkpoints fail closed so model loading cannot trigger a download.
    """

    candidate = (
        Path(explicit).expanduser().resolve()
        if explicit is not None
        else MMS_MODEL_PATH.resolve()
    )
    if not candidate.is_file():
        raise FileNotFoundError(f"MMS model checkpoint does not exist: {candidate}")
    return candidate
