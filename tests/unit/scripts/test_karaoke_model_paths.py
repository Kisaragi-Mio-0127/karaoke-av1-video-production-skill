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
