import json
from pathlib import Path

import pytest

from scripts import karaoke_model_paths


def test_model_path_contract_uses_models_directory() -> None:
    assert karaoke_model_paths.MODELS_ROOT == karaoke_model_paths.ROOT / "models"
    assert karaoke_model_paths.MMS_MODEL_PATH == (
        karaoke_model_paths.MODELS_ROOT / "mms" / "model.pt"
    )
    assert karaoke_model_paths.WHISPER_MODEL_DIR == (
        karaoke_model_paths.MODELS_ROOT / "whisper"
    )
    assert karaoke_model_paths.DEMUX_MODEL_PATH == (
        karaoke_model_paths.MODELS_ROOT
        / "demucs"
        / "955717e8-8726e21a.th"
    )
    assert karaoke_model_paths.NEXTFIRE_JA_LATN_MODEL_DIR == (
        karaoke_model_paths.MODELS_ROOT / "hf" / "nextfire-mms-ja-latn"
    )
    assert karaoke_model_paths.NEXTFIRE_JA_LATN_REVISION == (
        "a5bc320991c4b97a887a0b7784a5652d4a22fd2a"
    )


def test_mms_resolver_accepts_only_existing_explicit_file(tmp_path: Path) -> None:
    checkpoint = tmp_path / "explicit.pt"
    checkpoint.write_bytes(b"checkpoint")

    assert karaoke_model_paths.resolve_mms_model_path(checkpoint) == checkpoint.resolve()
    with pytest.raises(FileNotFoundError):
        karaoke_model_paths.resolve_mms_model_path(tmp_path / "missing.pt")


def test_mms_resolver_uses_only_canonical_default(tmp_path: Path, monkeypatch) -> None:
    canonical = tmp_path / "models" / "mms" / "model.pt"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical")
    cache_copy = tmp_path / ".cache" / "torch" / "hub" / "checkpoints" / "model.pt"
    cache_copy.parent.mkdir(parents=True)
    cache_copy.write_bytes(b"cache")
    monkeypatch.setattr(karaoke_model_paths, "MMS_MODEL_PATH", canonical)

    assert karaoke_model_paths.resolve_mms_model_path(None) == canonical.resolve()
    canonical.unlink()
    with pytest.raises(FileNotFoundError):
        karaoke_model_paths.resolve_mms_model_path(None)


def _nextfire_snapshot(root: Path, *, revision: str | None = None) -> Path:
    root.mkdir(parents=True)
    for name in (
        "config.json",
        "model.safetensors",
        "processor_config.json",
        "tokenizer_config.json",
        "vocab.json",
    ):
        (root / name).write_bytes(b"model-data")
    (root / karaoke_model_paths.NEXTFIRE_JA_LATN_PROVENANCE).write_text(
        json.dumps(
            {
                "repository": karaoke_model_paths.NEXTFIRE_JA_LATN_REPOSITORY,
                "revision": revision or karaoke_model_paths.NEXTFIRE_JA_LATN_REVISION,
                "model_license": "AGPL-3.0",
                "base_model_license": "CC-BY-NC-4.0",
            }
        ),
        encoding="utf-8",
    )
    return root / "model.safetensors"


def test_nextfire_resolver_requires_complete_pinned_local_snapshot(tmp_path: Path) -> None:
    checkpoint = _nextfire_snapshot(tmp_path / "models" / "hf" / "nextfire")
    assert (
        karaoke_model_paths.resolve_nextfire_ja_latn_model_path(checkpoint)
        == checkpoint.resolve()
    )

    bad = _nextfire_snapshot(tmp_path / "bad", revision="moving-main")
    with pytest.raises(ValueError, match="pinned experimental snapshot"):
        karaoke_model_paths.resolve_nextfire_ja_latn_model_path(bad)


def test_nextfire_backend_rejects_mms_checkpoint_override(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="applies only to local-mms-fa"):
        karaoke_model_paths.resolve_alignment_model_path(
            karaoke_model_paths.MMS_BACKEND_NEXTFIRE_JA_LATN,
            explicit_mms_model=tmp_path / "model.safetensors",
        )
