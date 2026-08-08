from __future__ import annotations

import argparse
import json
import sys
import types
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import audit_karaoke_asr_recognition as asr_audit
from scripts import audit_karaoke_mms_alignment as mms_audit
from scripts import karaoke_timing
from scripts.karaoke_common.device import (
    DEFAULT_DEVICE,
    DeviceResolutionError,
    add_device_argument,
    resolve_device,
)


class _FakeCuda:
    def __init__(self, available: bool):
        self.available = available

    def is_available(self) -> bool:
        return self.available


class _FakeTorch:
    def __init__(self, available: bool):
        self.cuda = _FakeCuda(available)


def test_public_default_is_auto_and_explicit_cuda_is_strict():
    assert DEFAULT_DEVICE == "auto"
    assert resolve_device(None, torch_module=_FakeTorch(True)).resolved == "cuda"
    assert resolve_device("auto", torch_module=_FakeTorch(False)).resolved == "cpu"
    assert resolve_device("cpu", torch_module=_FakeTorch(False)).resolved == "cpu"
    with pytest.raises(DeviceResolutionError, match="--device cpu"):
        resolve_device("cuda", torch_module=_FakeTorch(False))


def test_public_ml_clis_default_to_auto_and_accept_explicit_overrides():
    cli = argparse.ArgumentParser()
    add_device_argument(cli)
    assert cli.parse_args([]).device == "auto"
    assert cli.parse_args(["--device", "cpu"]).device == "cpu"
    assert cli.parse_args(["--device", "cuda"]).device == "cuda"

    assert karaoke_timing.parse_args(["--manifest", "album.json"]).device == "auto"
    assert asr_audit.build_parser().parse_args([]).device == "auto"
    assert mms_audit.build_parser().parse_args(["--manifest", "album.json"]).device == "auto"


def test_stable_whisper_worker_receives_cpu_override_and_reports_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    captured: dict[str, object] = {}

    class FakeModel:
        def align_words(self, *_args, **_kwargs):
            return SimpleNamespace(segments=[])

    def load_model(path: str, *, device: str):
        captured.update(path=path, device=device)
        return FakeModel()

    stable_whisper = types.ModuleType("stable_whisper")
    stable_whisper.load_model = load_model
    monkeypatch.setitem(sys.modules, "stable_whisper", stable_whisper)

    request = tmp_path / "alignment-request.json"
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "base.pt").write_bytes(b"model")
    request.write_text(
        json.dumps(
            {
                "audio": str(tmp_path / "audio.wav"),
                "language": "en",
                "lines": [],
            }
        ),
        encoding="utf-8",
    )
    karaoke_timing.run_alignment_worker(
        request,
        "base",
        model_dir,
        device="cpu",
    )

    output = capsys.readouterr().out.strip()
    payload = json.loads(output.removeprefix("ALIGNMENT_JSON:"))
    assert captured["device"] == "cpu"
    assert payload["requested_device"] == "cpu"
    assert payload["resolved_device"] == "cpu"


def test_whisper_loader_receives_resolved_device_and_reports_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    model_path = tmp_path / "base.pt"
    model_path.write_bytes(b"model")
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            return {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 0.5,
                        "words": [
                            {
                                "word": "hello",
                                "start": 0.0,
                                "end": 0.5,
                                "probability": 0.9,
                            }
                        ],
                    }
                ]
            }

    stable_whisper = types.ModuleType("stable_whisper")
    stable_whisper.load_model = lambda path, *, device: (
        captured.update(path=path, device=device) or FakeModel()
    )
    monkeypatch.setitem(sys.modules, "stable_whisper", stable_whisper)

    report = asr_audit.run_recognition_audit(
        audio_path=audio,
        lyric_lines=[
            {"line_index": 0, "text": "hello", "start_ms": 0, "end_ms": 500}
        ],
        language="en",
        model_path=model_path,
        cache_dir=None,
        audio_loader=lambda _path: (np.zeros(8, dtype=np.float32), 16_000),
        device="cpu",
    )

    assert captured["device"] == "cpu"
    assert report["requested_device"] == "cpu"
    assert report["resolved_device"] == "cpu"
    assert report["songs"][0]["recognition_provenance"]["resolved_device"] == "cpu"


def test_missing_whisper_checkpoint_fails_closed_before_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    stable_whisper = types.ModuleType("stable_whisper")
    stable_whisper.load_model = lambda *_args, **_kwargs: pytest.fail(
        "missing local checkpoint must not reach stable-whisper"
    )
    monkeypatch.setitem(sys.modules, "stable_whisper", stable_whisper)

    with pytest.raises(FileNotFoundError, match="Whisper model checkpoint"):
        asr_audit.run_recognition_audit(
            audio_path=audio,
            lyric_lines=[],
            language="en",
            model_path=tmp_path / "missing.pt",
            cache_dir=None,
            audio_loader=lambda _path: (np.zeros(8, dtype=np.float32), 16_000),
            device="cpu",
        )


class _FakeDevice:
    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FakeDevice) and self.name == other.name


class _FakeTensor:
    def __init__(self, device: _FakeDevice | None = None):
        self.device = device or _FakeDevice("cpu")

    def unsqueeze(self, _dimension: int) -> _FakeTensor:
        return self

    def to(self, device: _FakeDevice) -> _FakeTensor:
        self.device = device
        return self

    def detach(self) -> _FakeTensor:
        return self

    def cpu(self) -> _FakeTensor:
        self.device = _FakeDevice("cpu")
        return self

    def size(self, _dimension: int) -> int:
        return 1


class _FakeMmsModel:
    def __init__(self):
        self.device: _FakeDevice | None = None

    def to(self, device: _FakeDevice) -> _FakeMmsModel:
        self.device = device
        return self

    def eval(self) -> _FakeMmsModel:
        return self

    def __call__(self, tensor: _FakeTensor):
        assert tensor.device == self.device
        return [[_FakeTensor(self.device)]]


class _FakeAudio:
    samplerate = 16_000

    def seek(self, _offset: int) -> None:
        return None

    def read(self, frame_count: int, **_kwargs):
        return np.zeros((frame_count, 1), dtype=np.float32)


def test_mms_model_and_input_share_resolved_device_and_emission_returns_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    model_path = tmp_path / "mms-model.pt"
    model_path.write_bytes(b"model")
    model = _FakeMmsModel()
    captured: dict[str, object] = {}

    class FakeAligner:
        def __call__(self, emission: _FakeTensor, tokens: list[str]):
            assert str(emission.device) == "cpu"
            return [[SimpleNamespace(start=0, end=1, score=0.9)] for _token in tokens]

    aligner = FakeAligner()
    torch = types.ModuleType("torch")
    torch.cuda = _FakeCuda(True)
    torch.device = _FakeDevice
    torch.from_numpy = lambda _waveform: _FakeTensor()
    torch.inference_mode = nullcontext
    torchaudio = types.ModuleType("torchaudio")
    torchaudio.functional = SimpleNamespace(
        resample=lambda tensor, _source, _target: tensor
    )
    pipelines = types.ModuleType("torchaudio.pipelines")
    pipelines.MMS_FA = SimpleNamespace(
        sample_rate=16_000,
        get_model=lambda *, dl_kwargs: captured.update(dl_kwargs=dl_kwargs) or model,
        get_tokenizer=lambda: lambda tokens: tokens,
        get_aligner=lambda: aligner,
        get_dict=lambda: ["hello"],
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)
    monkeypatch.setitem(sys.modules, "torchaudio.pipelines", pipelines)

    runtime = mms_audit.load_mms_runtime(
        model_path=model_path,
        device="cuda",
    )
    result = mms_audit.align_audio_units(
        _FakeAudio(),
        0,
        1_000,
        [{"unit": "hello", "character_index": 0}],
        runtime,
    )

    assert runtime.requested_device == "cuda"
    assert runtime.resolved_device == "cuda"
    assert model.device == runtime.device
    assert result[0]["unit"] == "hello"
    assert captured["dl_kwargs"] == {
        "model_dir": str(model_path.parent),
        "file_name": model_path.name,
    }
