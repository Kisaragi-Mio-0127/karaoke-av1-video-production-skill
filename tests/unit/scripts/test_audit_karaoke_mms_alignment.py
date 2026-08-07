from __future__ import annotations

import sys
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[3] / "integration" / "strangeutagame"
if str(BUNDLE) not in sys.path:
    sys.path.insert(0, str(BUNDLE))

from scripts import karaoke_album as _karaoke_album  # noqa: E402

_karaoke_album.DEFAULT_MANIFEST_PATH = (
    BUNDLE.parents[1] / "examples" / "album.example.json"
)
_load_public_manifest = _karaoke_album.load_album_manifest


def _load_example_manifest(path, *, require_five_tracks=True):
    del require_five_tracks
    return _load_public_manifest(path, require_five_tracks=False)


_karaoke_album.load_album_manifest = _load_example_manifest

from scripts.audit_karaoke_mms_alignment import (  # noqa: E402
    _report_gate_ok,
    _validate_mms_model_access,
    line_units,
)


def test_mms_model_access_is_offline_and_fail_closed_by_default(tmp_path: Path):
    with pytest.raises(RuntimeError, match="offline by default"):
        _validate_mms_model_access(None, allow_network=False)

    missing = tmp_path / "missing-model.pt"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        _validate_mms_model_access(missing, allow_network=True)

    model = tmp_path / "local-model.pt"
    model.write_bytes(b"local checkpoint")
    assert _validate_mms_model_access(model, allow_network=False) == model.resolve()
    assert _validate_mms_model_access(None, allow_network=True) is None


def test_audit_gate_rejects_empty_or_vacuous_song_results():
    assert _report_gate_ok([], 0) is False
    assert _report_gate_ok([{"gate_ok": False}], 0) is False
    assert _report_gate_ok([{"gate_ok": True}], 1) is False
    assert _report_gate_ok([{"gate_ok": True}], 0) is True


def test_unbundled_language_adapter_is_rejected():
    with pytest.raises(ValueError, match="only Japanese"):
        line_units("Sing again", object(), language="en")
