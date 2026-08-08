"""Security and behavior coverage for the public Japanese/general bootstrap."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "karaoke_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("karaoke_bootstrap_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bootstrap_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap_module
SPEC.loader.exec_module(bootstrap_module)


def _target(root: Path) -> Path:
    target = root / "StrangeUtaGame"
    (target / "src" / "strange_uta_game").mkdir(parents=True)
    (target / "scripts").mkdir()
    (target / "pyproject.toml").write_text(
        '[project]\nname = "strange-uta-game"\n', encoding="utf-8"
    )
    return target


def _license(*, restricted: bool = False) -> dict[str, object]:
    return {
        "spdx": "CC-BY-NC-4.0" if restricted else "MIT",
        "url": (
            "https://creativecommons.org/licenses/by-nc/4.0/"
            if restricted
            else "https://github.com/openai/whisper/blob/main/LICENSE"
        ),
        "notice": (
            "Attribution required; non-commercial use only."
            if restricted
            else "MIT licensed model weights."
        ),
        "source_url": (
            "https://docs.pytorch.org/audio/stable/generated/torchaudio.pipelines.MMS_FA.html"
            if restricted
            else "https://github.com/openai/whisper"
        ),
        "requires_acceptance": restricted,
    }


def _model(
    name: str = "whisper-base",
    *,
    payload: bytes = b"public-model",
    restricted: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "destination": (
            "models/mms/model.pt" if restricted else "models/whisper/base.pt"
        ),
        "url": (
            bootstrap_module.MMS_MODEL_URL
            if restricted
            else "https://openaipublic.azureedge.net/main/whisper/base.pt"
        ),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "license": _license(restricted=restricted),
    }


def _manifest(
    root: Path,
    *,
    models: list[dict[str, object]] | None = None,
    requirements: str = "requirements/requirements-karaoke.pinned.txt",
) -> Path:
    bundle = root / "bundle"
    (bundle / "requirements").mkdir(parents=True)
    (bundle / "requirements" / "requirements-karaoke.pinned.txt").write_text(
        "# version pinned\n-e .\ntorch==2.10.0\ntorchaudio==2.10.0\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "karaoke-bootstrap/v1",
        "python": {"minimum": "3.11", "required_modules": ["example_module"]},
        "requirements": requirements,
        "models": models or [_model()],
    }
    path = bundle / "bootstrap-assets.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _commands_ok(
    name: str, _args: list[str], **_kwargs: object
) -> dict[str, object]:
    return {
        "ok": name != "nvidia-smi",
        "path": "tool" if name != "nvidia-smi" else None,
        "detail": "ok" if name != "nvidia-smi" else "not found on PATH",
    }


def _report(
    target: Path,
    *,
    cuda: bool = False,
    models: list[dict[str, object]] | None = None,
    environment_ok: bool = False,
) -> dict[str, object]:
    accelerator = "cuda" if cuda else "cpu"
    model_reports = models or [
        {
            "name": "whisper-base",
            "path": str(target / "models/whisper/base.pt"),
            "ok": False,
        }
    ]
    models_ok = all(bool(model["ok"]) for model in model_reports)
    return {
        "core_ok": environment_ok and models_ok,
        "backend_selection": {
            "accelerator": accelerator,
            "uv_torch_backend": "auto" if cuda else "cpu",
            "nvidia_detected": cuda,
        },
        "dependency_install": {
            "classification": "version-pinned",
            "reproducible_lock": False,
            "local_editable_project_install": True,
            "effective_local_source": str(target),
        },
        "runtime": {"ok": environment_ok, "accelerator": {"backend": accelerator}},
        "models": model_reports,
        "status": {
            "environment_ok": environment_ok,
            "models_ok": models_ok,
            "bootstrap_scope_ok": environment_ok and models_ok,
            "external_tools_ok": True,
        },
    }


def test_builtin_manifest_has_license_metadata_and_version_pinned_source() -> None:
    manifest = bootstrap_module.load_manifest()
    records = {record["name"]: record for record in manifest["models"]}

    assert records["mms-forced-alignment"]["license"] == {
        "spdx": "CC-BY-NC-4.0",
        "url": "https://creativecommons.org/licenses/by-nc/4.0/",
        "notice": (
            "Attribution is required and use is limited to non-commercial purposes "
            "under CC BY-NC 4.0."
        ),
        "source_url": (
            "https://docs.pytorch.org/audio/stable/generated/"
            "torchaudio.pipelines.MMS_FA.html"
        ),
        "requires_acceptance": True,
    }
    assert records["whisper-base"]["license"]["spdx"] == "MIT"
    assert manifest["requirements_validation"]["classification"] == "version-pinned"
    assert manifest["requirements_validation"]["reproducible_lock"] is False
    assert manifest["requirements_validation"]["local_editable_project_install"] is True
    assert manifest["requirements"].endswith("requirements-karaoke.pinned.txt")


def test_builtin_nextfire_profile_is_fixed_explicit_and_dual_licensed() -> None:
    manifest = bootstrap_module.load_manifest()
    selected_default, default_set = bootstrap_module._select_optional_models(
        manifest, include_nextfire_mms_ja_latn=False
    )
    selected, model_set = bootstrap_module._select_optional_models(
        manifest, include_nextfire_mms_ja_latn=True
    )

    assert default_set is None
    assert [record["name"] for record in selected_default["models"]] == [
        "mms-forced-alignment",
        "whisper-base",
    ]
    assert model_set["repository"] == bootstrap_module.NEXTFIRE_REPOSITORY
    assert model_set["revision"] == bootstrap_module.NEXTFIRE_REVISION
    assert model_set["destination"] == "models/hf/nextfire-mms-ja-latn"
    assert model_set["trust_remote_code"] is False
    assert model_set["licenses"]["model_card"]["spdx"] == "AGPL-3.0-only"
    assert model_set["licenses"]["base_model"]["spdx"] == "CC-BY-NC-4.0"
    assert {
        Path(record["destination"]).name for record in model_set["files"]
    } == bootstrap_module.NEXTFIRE_REQUIRED_FILES
    assert len(selected["models"]) == len(selected_default["models"]) + 5


def test_nextfire_download_plan_requires_both_license_confirmations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    nextfire_name = "nextfire-mms-ja-latn/config.json"
    before = _report(
        target,
        models=[
            {
                "name": nextfire_name,
                "path": str(target / "models/hf/nextfire-mms-ja-latn/config.json"),
                "ok": False,
            }
        ],
        environment_ok=True,
    )
    monkeypatch.setattr(bootstrap_module, "check", lambda *_args, **_kwargs: before)

    blocked = bootstrap_module.bootstrap(
        target,
        include_nextfire_mms_ja_latn=True,
        accept_nextfire_agpl_3_0=True,
        dry_run=True,
    )
    gate = blocked["blocked_actions"][0]
    assert gate["name"] == bootstrap_module.NEXTFIRE_MODEL_SET
    assert gate["required_flags"] == ["--accept-mms-cc-by-nc-4-0"]

    allowed = bootstrap_module.bootstrap(
        target,
        include_nextfire_mms_ja_latn=True,
        accept_nextfire_agpl_3_0=True,
        accept_mms_cc_by_nc_4_0=True,
        dry_run=True,
    )
    assert not allowed["blocked_actions"]
    assert any(
        action["kind"] == "download-model" and action["name"] == nextfire_name
        for action in allowed["actions"]
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update(license=_license(restricted=False)),
        lambda record: record.update(
            url="https://openaipublic.azureedge.net/main/whisper/base.pt"
        ),
        lambda record: record.update(destination="models/whisper/base.pt"),
        lambda record: record.update(name="whisper-base"),
    ],
)
def test_custom_manifest_cannot_weaken_known_mms_license_identity(
    mutate, tmp_path: Path
) -> None:
    record = _model(name="mms-forced-alignment", restricted=True)
    record["url"] = bootstrap_module.MMS_MODEL_URL
    mutate(record)
    manifest_path = _manifest(tmp_path, models=[record])

    with pytest.raises(
        ValueError,
        match="cannot be changed or weakened|restricted to mms-forced-alignment",
    ):
        bootstrap_module.load_manifest(
            manifest_path, allow_custom_manifest=True
        )


def test_check_makes_no_active_network_request_and_defaults_to_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    manifest_path = _manifest(tmp_path)
    model = target / "models" / "whisper" / "base.pt"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"public-model")
    monkeypatch.setattr(bootstrap_module, "_command", _commands_ok)
    monkeypatch.setattr(
        bootstrap_module,
        "_runtime_probe",
        lambda _python, _modules, _target_src: {
            "ok": True,
            "python_version": "3.11.9",
            "modules": {"example_module": True},
            "accelerator": {
                "backend": "cpu",
                "cuda_available": False,
                "pair_compatible": True,
            },
        },
    )
    monkeypatch.setattr(
        bootstrap_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: pytest.fail("check attempted DNS resolution"),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "_open_download",
        lambda *_args, **_kwargs: pytest.fail("check attempted model download"),
    )

    report = bootstrap_module.check(
        target, manifest_path, allow_custom_manifest=True
    )

    assert report["network_policy"] == "no-active-network-requests"
    assert report["model_verification"] == "size"
    assert report["models"][0]["checksum_verified"] is None
    assert "optional_models" not in report
    assert report["core_ok"] is True
    assert report["dependency_install"]["effective_local_source"] == str(target)
    assert "may execute" in report["dependency_install"]["behavior"]


def test_default_model_check_skips_hash_and_deep_verify_is_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    manifest = bootstrap_module.load_manifest(
        _manifest(tmp_path), allow_custom_manifest=True
    )
    model = target / "models" / "whisper" / "base.pt"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"public-model")
    original_sha256 = bootstrap_module.sha256
    monkeypatch.setattr(
        bootstrap_module,
        "sha256",
        lambda _path: pytest.fail("default check computed SHA-256"),
    )

    regular = bootstrap_module._model_report(target, manifest)
    assert regular[0]["ok"] is True

    monkeypatch.setattr(bootstrap_module, "sha256", original_sha256)
    deep = bootstrap_module._model_report(target, manifest, deep_verify=True)
    assert deep[0]["checksum_verified"] is True


def test_custom_manifest_requires_explicit_authorization(tmp_path: Path) -> None:
    custom = _manifest(tmp_path)
    with pytest.raises(ValueError, match="--allow-custom-manifest"):
        bootstrap_module.load_manifest(custom)
    assert bootstrap_module.load_manifest(custom, allow_custom_manifest=True)["models"]


@pytest.mark.parametrize(
    "requirements",
    [
        r"requirements\pinned.txt",
        "C:/requirements/pinned.txt",
        r"C:\requirements\pinned.txt",
        "//server/share/pinned.txt",
        r"\\?\C:\requirements\pinned.txt",
        r"\\.\GLOBALROOT\Device\pinned.txt",
    ],
)
def test_requirements_reject_windows_and_unc_escapes(
    requirements: str, tmp_path: Path
) -> None:
    manifest_path = _manifest(tmp_path, requirements=requirements)
    with pytest.raises(ValueError, match="Unsafe requirements path"):
        bootstrap_module.load_manifest(
            manifest_path, allow_custom_manifest=True
        )


def test_requirements_reject_reparse_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path = _manifest(tmp_path)
    requirement = manifest_path.parent / "requirements/requirements-karaoke.pinned.txt"
    original = bootstrap_module._is_reparse_point
    monkeypatch.setattr(
        bootstrap_module,
        "_is_reparse_point",
        lambda path: path == requirement or original(path),
    )
    with pytest.raises(ValueError, match="symlink or reparse point"):
        bootstrap_module.load_manifest(
            manifest_path, allow_custom_manifest=True
        )


def test_requirements_reject_remote_or_additional_editable_sources(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    requirement = manifest_path.parent / "requirements/requirements-karaoke.pinned.txt"
    requirement.write_text("-e .\n-r other.txt\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported requirements source"):
        bootstrap_module.load_manifest(
            manifest_path, allow_custom_manifest=True
        )


def test_model_url_exact_allowlist_rejects_metadata_and_custom_hosts(
    tmp_path: Path,
) -> None:
    for url in (
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://openaipublic.azureedge.net.evil.test/model.pt",
    ):
        model = _model()
        model["url"] = url
        manifest_path = _manifest(tmp_path / hashlib.sha256(url.encode()).hexdigest(), models=[model])
        with pytest.raises(ValueError, match="exact HTTPS host allowlist"):
            bootstrap_module.load_manifest(
                manifest_path, allow_custom_manifest=True
            )


def test_network_endpoint_rejects_private_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bootstrap_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(RuntimeError, match="forbidden address class"):
        bootstrap_module._validate_network_endpoint(
            "https://openaipublic.azureedge.net/main/whisper/base.pt"
        )


def test_download_rejects_redirect_even_if_opener_returns_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def geturl(self) -> str:
            return "https://example.test/redirected"

        def close(self) -> None:
            pass

    class Opener:
        def open(self, _request, timeout):
            assert timeout == 60
            return Response()

    monkeypatch.setattr(bootstrap_module, "_validate_network_endpoint", lambda _url: None)
    monkeypatch.setattr(bootstrap_module.urllib.request, "build_opener", lambda _handler: Opener())
    request = bootstrap_module.urllib.request.Request(
        "https://openaipublic.azureedge.net/main/whisper/base.pt"
    )
    with pytest.raises(RuntimeError, match="redirect was rejected"):
        bootstrap_module._open_download(request)


def test_target_venv_and_python_reparse_points_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    python = bootstrap_module.target_python(target)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    original = bootstrap_module._is_reparse_point
    monkeypatch.setattr(
        bootstrap_module,
        "_is_reparse_point",
        lambda path: path in {target / ".venv", python} or original(path),
    )
    with pytest.raises(ValueError, match="Target .venv"):
        bootstrap_module.validate_target_venv(target, require_python=True)


def test_runtime_probe_imports_target_src(tmp_path: Path) -> None:
    package = tmp_path / "src" / "strange_uta_game"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = bootstrap_module._runtime_probe(
        Path(sys.executable), ["strange_uta_game"], tmp_path / "src"
    )
    assert report["modules"]["strange_uta_game"] is True


def test_backend_selection_uses_uv_auto_for_nvidia_and_cpu_otherwise() -> None:
    commands = {"nvidia_smi_optional": {"ok": True}}
    assert bootstrap_module.select_torch_backend(commands)["uv_torch_backend"] == "auto"
    commands["nvidia_smi_optional"]["ok"] = False
    assert bootstrap_module.select_torch_backend(commands)["uv_torch_backend"] == "cpu"


def test_dry_run_deep_verifies_and_never_writes_or_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    manifest_path = _manifest(tmp_path)
    before = _report(target)
    deep_flags: list[bool] = []

    def fake_check(*_args, **kwargs):
        deep_flags.append(bool(kwargs["deep_verify"]))
        return before

    monkeypatch.setattr(bootstrap_module, "check", fake_check)
    monkeypatch.setattr(
        bootstrap_module, "_run", lambda *_args, **_kwargs: pytest.fail("dry-run executed uv")
    )
    monkeypatch.setattr(
        bootstrap_module,
        "_download",
        lambda *_args, **_kwargs: pytest.fail("dry-run downloaded"),
    )

    report = bootstrap_module.bootstrap(
        target,
        manifest_path=manifest_path,
        allow_custom_manifest=True,
        dry_run=True,
    )

    assert deep_flags == [True]
    assert report["network_allowed"] is False
    assert report["python_download_allowed"] is False
    assert not (target / ".venv").exists()
    assert not (target / "models").exists()


def test_bootstrap_deep_verifies_existing_model_before_skipping_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    manifest_path = _manifest(tmp_path)
    existing = _report(
        target,
        models=[
            {
                "name": "whisper-base",
                "path": str(target / "models/whisper/base.pt"),
                "ok": True,
            }
        ],
        environment_ok=True,
    )
    deep_flags: list[bool] = []

    def fake_check(*_args, **kwargs):
        deep_flags.append(bool(kwargs["deep_verify"]))
        return existing

    monkeypatch.setattr(bootstrap_module, "check", fake_check)
    report = bootstrap_module.bootstrap(
        target,
        manifest_path=manifest_path,
        allow_custom_manifest=True,
        dry_run=True,
    )
    assert deep_flags == [True]
    assert not any(action["kind"] == "download-model" for action in report["actions"])


def test_without_mms_acceptance_environment_and_whisper_continue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    python = bootstrap_module.target_python(target)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    models = [_model("mms-forced-alignment", restricted=True), _model()]
    manifest_path = _manifest(tmp_path, models=models)
    model_reports = [
        {
            "name": "mms-forced-alignment",
            "path": str(target / "models/mms/model.pt"),
            "ok": False,
        },
        {
            "name": "whisper-base",
            "path": str(target / "models/whisper/base.pt"),
            "ok": False,
        },
    ]
    before = _report(target, models=model_reports)
    after = _report(target, models=model_reports, environment_ok=True)
    reports = iter((before, after))
    commands: list[list[str]] = []
    downloads: list[str] = []
    monkeypatch.setattr(bootstrap_module, "check", lambda *_args, **_kwargs: next(reports))
    monkeypatch.setattr(bootstrap_module.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        bootstrap_module,
        "_run",
        lambda command, _cwd, *, offline: commands.append(command),
    )

    def fake_download(record, destination, *, license_accepted):
        downloads.append(record["name"])
        assert license_accepted is False
        return destination.with_name(destination.name + ".source-license.json")

    monkeypatch.setattr(bootstrap_module, "_download", fake_download)

    report = bootstrap_module.bootstrap(
        target,
        manifest_path=manifest_path,
        allow_custom_manifest=True,
    )

    assert commands and commands[0][0:4] == ["uv", "--no-config", "pip", "install"]
    assert commands[0][commands[0].index("--default-index") + 1] == (
        "https://pypi.org/simple"
    )
    assert downloads == ["whisper-base"]
    assert report["blocked_actions"][0]["required_flag"] == (
        "--accept-mms-cc-by-nc-4-0"
    )
    assert "non-commercial" in report["blocked_actions"][0]["notice"]


def test_mms_acceptance_allows_download_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    manifest_path = _manifest(
        tmp_path, models=[_model("mms-forced-alignment", restricted=True)]
    )
    before = _report(
        target,
        models=[
            {
                "name": "mms-forced-alignment",
                "path": str(target / "models/mms/model.pt"),
                "ok": False,
            }
        ],
    )
    monkeypatch.setattr(bootstrap_module, "check", lambda *_args, **_kwargs: before)

    report = bootstrap_module.bootstrap(
        target,
        manifest_path=manifest_path,
        allow_custom_manifest=True,
        accept_mms_cc_by_nc_4_0=True,
        dry_run=True,
    )

    downloads = [action for action in report["actions"] if action["kind"] == "download-model"]
    assert downloads[0]["name"] == "mms-forced-alignment"
    assert downloads[0]["license_accepted"] is True
    assert not report["blocked_actions"]


@pytest.mark.parametrize("allow_python_download", [False, True])
def test_new_environment_is_only_target_dot_venv_and_python_download_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    allow_python_download: bool,
) -> None:
    target = _target(tmp_path)
    manifest_path = _manifest(tmp_path)
    before = _report(
        target,
        models=[
            {
                "name": "whisper-base",
                "path": str(target / "models/whisper/base.pt"),
                "ok": True,
            }
        ],
    )
    ready = _report(
        target,
        models=before["models"],
        environment_ok=True,
    )
    reports = iter((before, ready))
    commands: list[list[str]] = []
    monkeypatch.setattr(bootstrap_module, "check", lambda *_args, **_kwargs: next(reports))
    monkeypatch.setattr(bootstrap_module.shutil, "which", lambda _name: "uv")

    def fake_run(command, _cwd, *, offline):
        commands.append(command)
        if command[2] == "venv":
            python = bootstrap_module.target_python(target)
            python.parent.mkdir(parents=True)
            python.write_bytes(b"")

    monkeypatch.setattr(bootstrap_module, "_run", fake_run)

    report = bootstrap_module.bootstrap(
        target,
        manifest_path=manifest_path,
        allow_custom_manifest=True,
        allow_python_download=allow_python_download,
    )

    assert commands[0][0:4] == [
        "uv",
        "--no-config",
        "venv",
        str(target / ".venv"),
    ]
    assert ("--no-python-downloads" in commands[0]) is (not allow_python_download)
    assert report["actions"][0]["python_download_allowed"] is allow_python_download
    assert all(
        any(str(argument).startswith(str(target / ".venv")) for argument in command)
        for command in commands
    )


def test_new_environment_is_revalidated_before_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _target(tmp_path)
    manifest_path = _manifest(tmp_path)
    before = _report(
        target,
        models=[
            {
                "name": "whisper-base",
                "path": str(target / "models/whisper/base.pt"),
                "ok": True,
            }
        ],
    )
    monkeypatch.setattr(bootstrap_module, "check", lambda *_args, **_kwargs: before)
    monkeypatch.setattr(bootstrap_module.shutil, "which", lambda _name: "uv")
    created = {"unsafe": False}
    original = bootstrap_module._is_reparse_point

    def fake_run(command, _cwd, *, offline):
        assert command[2] == "venv"
        python = bootstrap_module.target_python(target)
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        created["unsafe"] = True

    monkeypatch.setattr(bootstrap_module, "_run", fake_run)
    monkeypatch.setattr(
        bootstrap_module,
        "_is_reparse_point",
        lambda path: (
            created["unsafe"] and path == target / ".venv"
        )
        or original(path),
    )
    with pytest.raises(ValueError, match="Target .venv"):
        bootstrap_module.bootstrap(
            target,
            manifest_path=manifest_path,
            allow_custom_manifest=True,
        )


def test_uv_run_scrubs_uncontrolled_package_source_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setenv("UV_INDEX_URL", "https://evil.test/simple")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://evil.test/extra")

    def fake_run(_command, *, cwd, env, check):
        assert cwd == tmp_path
        assert check is True
        captured.update(env)

    monkeypatch.setattr(bootstrap_module.subprocess, "run", fake_run)
    bootstrap_module._run(["uv", "--version"], tmp_path, offline=False)
    assert "UV_INDEX_URL" not in captured
    assert "PIP_EXTRA_INDEX_URL" not in captured


def test_download_verifies_and_writes_source_license_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"verified-public-model"
    destination = tmp_path / "models" / "mms" / "model.pt"
    record = _model(
        "mms-forced-alignment", payload=payload, restricted=True
    )

    class Response:
        def __init__(self) -> None:
            self.remaining = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int) -> bytes:
            result, self.remaining = self.remaining, b""
            return result

    monkeypatch.setattr(bootstrap_module, "_open_download", lambda _request: Response())

    sidecar = bootstrap_module._download(
        record, destination, license_accepted=True
    )

    assert destination.read_bytes() == payload
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    assert metadata["source_url"] == record["url"]
    assert metadata["license"]["spdx"] == "CC-BY-NC-4.0"
    assert metadata["license_accepted_for_download"] is True
    assert not list(destination.parent.glob("*.partial"))


def test_download_checksum_failure_preserves_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "models" / "whisper" / "base.pt"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing")
    record = _model(payload=b"expected")
    record["size"] = len(b"corrupt!")

    class Response:
        remaining = b"corrupt!"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int) -> bytes:
            result, self.remaining = self.remaining, b""
            return result

    monkeypatch.setattr(bootstrap_module, "_open_download", lambda _request: Response())

    with pytest.raises(ValueError, match="failed size or SHA-256"):
        bootstrap_module._download(
            record, destination, license_accepted=False
        )
    assert destination.read_bytes() == b"existing"
    assert not destination.with_name("base.pt.source-license.json").exists()


def test_nextfire_download_uses_target_cache_then_publishes_without_file_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"pinned-nextfire-file"
    target = tmp_path / "StrangeUtaGame"
    destination = target / "models/hf/nextfire-mms-ja-latn/config.json"
    cache = target / ".cache/karaoke-bootstrap/nextfire-mms-ja-latn/cache-key"
    record = {
        "name": "nextfire-mms-ja-latn/config.json",
        "url": (
            "https://huggingface.co/NextFire/"
            "mms-300m-ForcedAligner-karaoke-ja-Latn/resolve/"
            f"{bootstrap_module.NEXTFIRE_REVISION}/config.json"
        ),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

    class Response:
        remaining = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int) -> bytes:
            result, self.remaining = self.remaining, b""
            return result

    monkeypatch.setattr(
        bootstrap_module,
        "_open_download",
        lambda _request, *, allow_redirects: Response(),
    )
    result = bootstrap_module._download(
        record,
        destination,
        license_accepted=True,
        cache_path=cache,
        allow_redirects=True,
        write_sidecar=False,
    )

    assert result is None
    assert cache.read_bytes() == payload
    assert destination.read_bytes() == payload
    assert not destination.with_name("config.json.source-license.json").exists()
    assert not list(cache.parent.glob("*.partial"))


def test_nextfire_provenance_is_written_and_checked_without_remote_code(
    tmp_path: Path,
) -> None:
    target = tmp_path / "StrangeUtaGame"
    target.mkdir()
    manifest = bootstrap_module.load_manifest()
    _, model_set = bootstrap_module._select_optional_models(
        manifest, include_nextfire_mms_ja_latn=True
    )

    path = bootstrap_module._write_nextfire_provenance(
        target,
        model_set,
        agpl_accepted=True,
        base_license_accepted=True,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    report = bootstrap_module._nextfire_provenance_report(target, model_set)

    assert path == target / "models/hf/nextfire-mms-ja-latn/MODEL_PROVENANCE.json"
    assert payload["revision"] == bootstrap_module.NEXTFIRE_REVISION
    assert payload["trust_remote_code"] is False
    assert payload["license_acceptance_for_download"] == {
        "nextfire_agpl_3_0": True,
        "facebook_mms_300m_cc_by_nc_4_0": True,
    }
    assert report["ok"] is True


def test_nextfire_redirect_host_allowlist_is_exact() -> None:
    with pytest.raises(ValueError, match="exact HTTPS host allowlist"):
        bootstrap_module._validate_network_endpoint(
            "https://us.aws.cdn.hf.co.evil.test/model", allow_redirect_host=True
        )


def test_redact_paths_removes_absolute_values(tmp_path: Path) -> None:
    report = {
        "target": str(tmp_path.resolve()),
        "url": "https://openaipublic.azureedge.net/model.pt",
        "nested": [str((tmp_path / "model.pt").resolve())],
    }
    redacted = bootstrap_module.redact_report_paths(report)
    assert redacted["target"] == "<redacted:absolute-path>"
    assert redacted["nested"] == ["<redacted:absolute-path>"]
    assert redacted["url"] == report["url"]
