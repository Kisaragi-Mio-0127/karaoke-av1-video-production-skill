"""Canonical repository-local paths for karaoke runtime model weights."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = ROOT / "models"
MMS_MODEL_PATH = MODELS_ROOT / "mms" / "model.pt"
NEXTFIRE_JA_LATN_MODEL_RELATIVE_DIR = Path("hf") / "nextfire-mms-ja-latn"
NEXTFIRE_JA_LATN_MODEL_DIR = MODELS_ROOT / NEXTFIRE_JA_LATN_MODEL_RELATIVE_DIR
NEXTFIRE_JA_LATN_MODEL_PATH = NEXTFIRE_JA_LATN_MODEL_DIR / "model.safetensors"
NEXTFIRE_JA_LATN_REPOSITORY = (
    "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"
)
NEXTFIRE_JA_LATN_REVISION = "a5bc320991c4b97a887a0b7784a5652d4a22fd2a"
NEXTFIRE_JA_LATN_PROVENANCE = "MODEL_PROVENANCE.json"
WHISPER_MODEL_DIR = MODELS_ROOT / "whisper"
DEMUX_MODEL_PATH = MODELS_ROOT / "demucs" / "955717e8-8726e21a.th"

MMS_BACKEND_LOCAL = "local-mms-fa"
MMS_BACKEND_NEXTFIRE_JA_LATN = "nextfire-ja-latn"
MMS_BACKENDS = (MMS_BACKEND_LOCAL, MMS_BACKEND_NEXTFIRE_JA_LATN)

_NEXTFIRE_REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "processor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    NEXTFIRE_JA_LATN_PROVENANCE,
)


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


def resolve_nextfire_ja_latn_model_path(explicit: Path | None = None) -> Path:
    """Resolve a complete local NextFire snapshot without network fallback.

    ``explicit`` is primarily an internal/test hook. Production wrappers select
    the dedicated repository-local directory and expose only the backend choice.
    """

    candidate = (
        Path(explicit).expanduser().resolve()
        if explicit is not None
        else NEXTFIRE_JA_LATN_MODEL_PATH.resolve()
    )
    model_dir = candidate if candidate.is_dir() else candidate.parent
    missing = [
        name
        for name in _NEXTFIRE_REQUIRED_FILES
        if not (model_dir / name).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "NextFire Japanese Latn model snapshot is incomplete at "
            f"{model_dir}; missing: {', '.join(missing)}"
        )
    provenance_path = model_dir / NEXTFIRE_JA_LATN_PROVENANCE
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(
            f"NextFire model provenance is unreadable: {provenance_path}"
        ) from error
    expected = {
        "repository": NEXTFIRE_JA_LATN_REPOSITORY,
        "revision": NEXTFIRE_JA_LATN_REVISION,
        "model_license": "AGPL-3.0",
        "base_model_license": "CC-BY-NC-4.0",
    }
    mismatches = {
        key: provenance.get(key)
        for key, value in expected.items()
        if provenance.get(key) != value
    }
    if mismatches:
        raise ValueError(
            "NextFire model provenance does not match the pinned experimental "
            f"snapshot: {mismatches}"
        )
    return (model_dir / "model.safetensors").resolve()


def resolve_alignment_model_path(
    backend: str,
    *,
    explicit_mms_model: Path | None = None,
) -> Path:
    """Resolve one alignment backend while preserving local MMS_FA as default."""

    if backend == MMS_BACKEND_LOCAL:
        return resolve_mms_model_path(explicit_mms_model)
    if backend == MMS_BACKEND_NEXTFIRE_JA_LATN:
        if explicit_mms_model is not None:
            raise ValueError(
                "--mms-model-path applies only to local-mms-fa; the NextFire "
                "backend uses its fixed models/hf/nextfire-mms-ja-latn directory"
            )
        return resolve_nextfire_ja_latn_model_path()
    raise ValueError(f"unsupported MMS backend: {backend!r}")
