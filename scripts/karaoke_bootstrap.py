"""No-active-network checks and explicit StrangeUtaGame bootstrap support."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse


SKILL_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = SKILL_ROOT / "integration" / "strangeutagame"
DEFAULT_MANIFEST = BUNDLE_ROOT / "bootstrap-assets.json"
MODEL_HOST_ALLOWLIST = frozenset(
    {"dl.fbaipublicfiles.com", "huggingface.co", "openaipublic.azureedge.net"}
)
MODEL_REDIRECT_HOST_ALLOWLIST = frozenset({"us.aws.cdn.hf.co"})
MMS_MODEL_NAME = "mms-forced-alignment"
MMS_MODEL_DESTINATION = "models/mms/model.pt"
MMS_MODEL_URL = (
    "https://dl.fbaipublicfiles.com/mms/torchaudio/"
    "ctc_alignment_mling_uroman/model.pt"
)
MMS_MODEL_LICENSE = "CC-BY-NC-4.0"
NEXTFIRE_MODEL_SET = "nextfire-mms-ja-latn"
NEXTFIRE_REPOSITORY = "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"
NEXTFIRE_REVISION = "a5bc320991c4b97a887a0b7784a5652d4a22fd2a"
NEXTFIRE_DESTINATION = "models/hf/nextfire-mms-ja-latn"
NEXTFIRE_REQUIRED_FILES = frozenset(
    {
        "config.json",
        "model.safetensors",
        "processor_config.json",
        "tokenizer_config.json",
        "vocab.json",
    }
)
PYPI_INDEX = "https://pypi.org/simple"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ABSOLUTE_TEXT_RE = re.compile(r"^(?:[A-Za-z]:[/\\]|[/\\]{2}|/)")
_PINNED_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+(?:\s*;\s*.+)?$"
)


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    if not os.path.lexists(path):
        return False
    if path.is_symlink():
        return True
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & _REPARSE_POINT)


def _assert_no_reparse_chain(root: Path, destination: Path, label: str) -> None:
    try:
        relative = destination.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escapes its allowed root: {destination}") from error
    current = root
    if _is_reparse_point(current):
        raise ValueError(f"{label} root is a symlink or reparse point: {current}")
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and _is_reparse_point(current):
            raise ValueError(f"{label} is a symlink or reparse point: {current}")


def validate_target(target: Path) -> Path:
    """Resolve a real, non-linked compatible StrangeUtaGame checkout."""

    requested = target.expanduser().absolute()
    if _is_reparse_point(requested):
        raise ValueError(f"Target checkout is a symlink or reparse point: {requested}")
    resolved = requested.resolve()
    if resolved != requested:
        raise ValueError(f"Target checkout resolves through a different path: {requested}")
    required = (
        resolved / "pyproject.toml",
        resolved / "src" / "strange_uta_game",
        resolved / "scripts",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError("Not a compatible StrangeUtaGame checkout; missing: " + ", ".join(missing))
    for path in required:
        _assert_no_reparse_chain(resolved, path, "Target checkout path")
    return resolved


def _safe_posix_relative(raw: object, label: str) -> PurePosixPath:
    text = str(raw)
    if (
        not text
        or "\\" in text
        or ":" in text
        or text.startswith("//")
        or _WINDOWS_DRIVE_RE.match(text)
    ):
        raise ValueError(f"Unsafe {label}: {text!r}")
    relative = PurePosixPath(text)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"Unsafe {label}: {text!r}")
    return relative


def _safe_regular_file(root: Path, relative: PurePosixPath, label: str) -> Path:
    root = root.resolve()
    candidate = root / Path(*relative.parts)
    _assert_no_reparse_chain(root, candidate, label)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError(f"{label} is missing or escapes its allowed root: {candidate}") from error
    if not candidate.is_file() or _is_reparse_point(candidate):
        raise ValueError(f"{label} is not a regular non-linked file: {candidate}")
    return candidate


def _validate_version_pinned_requirements(path: Path) -> dict[str, Any]:
    editable_count = 0
    package_count = 0
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "-e .":
            editable_count += 1
            continue
        if (
            line.startswith(("-r", "--requirement", "-c", "--constraint", "-e"))
            or "://" in line
            or " @ " in line
            or line.startswith((".", "/", "\\", "file:"))
            or not _PINNED_REQUIREMENT_RE.fullmatch(line)
        ):
            raise ValueError(f"Unsupported requirements source at {path}:{number}: {line!r}")
        package_count += 1
    if editable_count != 1:
        raise ValueError("Version-pinned requirements must contain exactly one '-e .' target install")
    return {
        "classification": "version-pinned",
        "reproducible_lock": False,
        "pinned_package_count": package_count,
        "local_editable_project_install": True,
    }


def _validate_model_url(raw_url: object, name: str) -> str:
    url = str(raw_url)
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or hostname not in MODEL_HOST_ALLOWLIST
        or parsed.fragment
    ):
        raise ValueError(f"Model URL is outside the exact HTTPS host allowlist: {name}")
    return url


def _validate_license(record: dict[str, Any]) -> dict[str, Any]:
    license_data = record.get("license")
    required = {"spdx", "url", "notice", "source_url", "requires_acceptance"}
    if not isinstance(license_data, dict) or not required.issubset(license_data):
        raise ValueError(f"Model license metadata is incomplete: {record.get('name')}")
    if not all(
        isinstance(license_data[key], str) and license_data[key].strip()
        for key in ("spdx", "url", "notice", "source_url")
    ):
        raise ValueError(f"Model license metadata has empty text: {record.get('name')}")
    if not isinstance(license_data["requires_acceptance"], bool):
        raise ValueError(f"Model license acceptance flag is invalid: {record.get('name')}")
    for key in ("url", "source_url"):
        parsed = urlparse(license_data[key])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"Model license {key} must use HTTPS: {record.get('name')}")
    if license_data["requires_acceptance"] and license_data["spdx"] != "CC-BY-NC-4.0":
        raise ValueError("Only the built-in MMS CC-BY-NC-4.0 acceptance contract is supported")
    if license_data["requires_acceptance"] and record.get("name") != "mms-forced-alignment":
        raise ValueError("CC BY-NC 4.0 acceptance is restricted to mms-forced-alignment")
    return license_data


def _validate_mms_license_identity(
    record: dict[str, Any], destination: PurePosixPath, license_data: dict[str, Any]
) -> None:
    """Prevent custom manifests from weakening the known MMS license gate."""

    identity = (
        record.get("name") == MMS_MODEL_NAME,
        destination.as_posix() == MMS_MODEL_DESTINATION,
        record.get("url") == MMS_MODEL_URL,
    )
    if not any(identity):
        return
    if not all(identity) or (
        license_data.get("spdx") != MMS_MODEL_LICENSE
        or license_data.get("requires_acceptance") is not True
    ):
        raise ValueError(
            "The known MMS model identity and CC-BY-NC-4.0 acceptance gate "
            "cannot be changed or weakened by a custom manifest"
        )


def _validate_nextfire_model_set(manifest: dict[str, Any]) -> None:
    optional_sets = manifest.get("optional_model_sets", {})
    if not isinstance(optional_sets, dict):
        raise ValueError("Bootstrap optional_model_sets must be an object")
    model_set = optional_sets.get(NEXTFIRE_MODEL_SET)
    if model_set is None:
        return
    if not isinstance(model_set, dict):
        raise ValueError("NextFire optional model set must be an object")
    if (
        model_set.get("repository") != NEXTFIRE_REPOSITORY
        or model_set.get("revision") != NEXTFIRE_REVISION
        or model_set.get("destination") != NEXTFIRE_DESTINATION
        or model_set.get("provenance") != "MODEL_PROVENANCE.json"
        or model_set.get("trust_remote_code") is not False
    ):
        raise ValueError("NextFire model identity, revision, destination, or trust policy changed")
    licenses = model_set.get("licenses")
    if not isinstance(licenses, dict):
        raise ValueError("NextFire model set has no license metadata")
    model_card = licenses.get("model_card", {})
    base_model = licenses.get("base_model", {})
    if (
        model_card.get("spdx") != "AGPL-3.0-only"
        or model_card.get("requires_acceptance") is not True
        or base_model.get("name") != "facebook/mms-300m"
        or base_model.get("spdx") != "CC-BY-NC-4.0"
        or base_model.get("requires_acceptance") is not True
    ):
        raise ValueError("NextFire AGPL-3.0 and base MMS CC-BY-NC-4.0 gates are mandatory")
    for license_data in (model_card, base_model):
        if not all(
            isinstance(license_data.get(key), str) and license_data[key].strip()
            for key in ("url", "notice")
        ):
            raise ValueError("NextFire license metadata is incomplete")
        parsed = urlparse(license_data["url"])
        if parsed.scheme != "https" or parsed.hostname != "huggingface.co":
            raise ValueError("NextFire license evidence must use huggingface.co HTTPS")

    files = model_set.get("files")
    if not isinstance(files, list) or len(files) != len(NEXTFIRE_REQUIRED_FILES):
        raise ValueError("NextFire model set must contain exactly the required five files")
    seen: set[str] = set()
    expected_url_prefix = f"https://huggingface.co/{NEXTFIRE_REPOSITORY}/resolve/{NEXTFIRE_REVISION}/"
    for record in files:
        if not isinstance(record, dict):
            raise ValueError("NextFire model file record is not an object")
        destination = _safe_posix_relative(record.get("destination", ""), "model destination")
        destination_text = destination.as_posix()
        prefix = f"{NEXTFIRE_DESTINATION}/"
        if not destination_text.startswith(prefix):
            raise ValueError("NextFire file escapes its fixed model directory")
        basename = destination_text.removeprefix(prefix)
        if basename not in NEXTFIRE_REQUIRED_FILES or "/" in basename or basename in seen:
            raise ValueError(f"Unexpected or duplicate NextFire model file: {basename}")
        url = _validate_model_url(record.get("url"), str(record.get("name")))
        if url != expected_url_prefix + basename:
            raise ValueError("NextFire model file URL is not pinned to the fixed revision")
        checksum = str(record.get("sha256", "")).lower()
        if not _SHA256_RE.fullmatch(checksum):
            raise ValueError(f"Invalid SHA-256 for NextFire model file: {basename}")
        if not isinstance(record.get("size"), int) or record["size"] <= 0:
            raise ValueError(f"Invalid size for NextFire model file: {basename}")
        record["sha256"] = checksum
        seen.add(basename)
    if seen != NEXTFIRE_REQUIRED_FILES:
        raise ValueError("NextFire model set is missing required files")


def _select_optional_models(
    manifest: dict[str, Any], *, include_nextfire_mms_ja_latn: bool
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not include_nextfire_mms_ja_latn:
        return manifest, None
    model_set = manifest.get("optional_model_sets", {}).get(NEXTFIRE_MODEL_SET)
    if model_set is None:
        raise ValueError("Selected NextFire model set is absent from the bootstrap manifest")
    combined_license = {
        "spdx": "AGPL-3.0-only AND CC-BY-NC-4.0",
        "url": model_set["licenses"]["model_card"]["url"],
        "notice": (
            model_set["licenses"]["model_card"]["notice"]
            + " "
            + model_set["licenses"]["base_model"]["notice"]
        ),
        "source_url": model_set["licenses"]["model_card"]["url"],
        "requires_acceptance": True,
    }
    selected = dict(manifest)
    selected["models"] = list(manifest["models"]) + [
        {**record, "license": combined_license, "optional_model_set": NEXTFIRE_MODEL_SET}
        for record in model_set["files"]
    ]
    return selected, model_set


def load_manifest(
    path: Path = DEFAULT_MANIFEST, *, allow_custom_manifest: bool = False
) -> dict[str, Any]:
    """Load the built-in manifest, or an explicitly authorized custom one."""

    requested = path.expanduser().absolute()
    if requested.resolve() != DEFAULT_MANIFEST.resolve() and not allow_custom_manifest:
        raise ValueError("Custom bootstrap manifest requires --allow-custom-manifest")
    if _is_reparse_point(requested) or not requested.is_file():
        raise ValueError(f"Bootstrap manifest is not a regular non-linked file: {requested}")
    manifest = json.loads(requested.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "karaoke-bootstrap/v1":
        raise ValueError("Unsupported bootstrap manifest schema")
    python = manifest.get("python")
    if not isinstance(python, dict) or not isinstance(python.get("required_modules"), list):
        raise ValueError("Bootstrap manifest has no required Python modules")
    _validate_nextfire_model_set(manifest)

    requirements_relative = _safe_posix_relative(
        manifest.get("requirements", ""), "requirements path"
    )
    requirements_path = _safe_regular_file(
        requested.parent, requirements_relative, "Requirements file"
    )
    manifest["requirements_validation"] = _validate_version_pinned_requirements(
        requirements_path
    )
    manifest["requirements_resolved"] = str(requirements_path)

    names: set[str] = set()
    destinations: set[str] = set()
    models = manifest.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("Bootstrap manifest has no models")
    for record in models:
        if not isinstance(record, dict):
            raise ValueError("Bootstrap model record is not an object")
        name = record.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError(f"Invalid or duplicate model name: {name!r}")
        destination = _safe_posix_relative(record.get("destination", ""), "model destination")
        if len(destination.parts) < 3 or destination.parts[0] != "models":
            raise ValueError(f"Unsafe model destination: {destination}")
        destination_key = destination.as_posix().casefold()
        if destination_key in destinations:
            raise ValueError(f"Duplicate model destination: {destination}")
        record["url"] = _validate_model_url(record.get("url"), name)
        checksum = str(record.get("sha256", "")).lower()
        if not _SHA256_RE.fullmatch(checksum):
            raise ValueError(f"Invalid SHA-256 for model: {name}")
        record["sha256"] = checksum
        if not isinstance(record.get("size"), int) or record["size"] <= 0:
            raise ValueError(f"Invalid size for model: {name}")
        license_data = _validate_license(record)
        _validate_mms_license_identity(record, destination, license_data)
        names.add(name)
        destinations.add(destination_key)
    return manifest


def target_python(target: Path) -> Path:
    """Return the only permitted uv virtual-environment Python path."""

    return target / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def validate_target_venv(target: Path, *, require_python: bool) -> Path:
    """Reject linked/junction venvs and Python executables that escape target."""

    venv = target / ".venv"
    python = target_python(target)
    if not os.path.lexists(venv):
        if require_python:
            raise ValueError(f"Target uv environment does not exist: {venv}")
        return python
    if not venv.is_dir() or _is_reparse_point(venv):
        raise ValueError(f"Target .venv is not a real directory: {venv}")
    _assert_no_reparse_chain(target, python, "Target .venv Python")
    try:
        venv.resolve().relative_to(target)
    except ValueError as error:
        raise ValueError(f"Target .venv escapes checkout: {venv}") from error
    if os.path.lexists(python):
        if not python.is_file() or _is_reparse_point(python):
            raise ValueError(f"Target .venv Python is not a regular non-linked file: {python}")
        try:
            python.resolve(strict=True).relative_to(venv.resolve())
        except (OSError, ValueError) as error:
            raise ValueError(f"Target .venv Python escapes target environment: {python}") from error
    elif require_python:
        raise ValueError(f"Target .venv Python does not exist: {python}")
    return python


def model_destination(target: Path, relative: str) -> Path:
    """Resolve a model path while preventing model-root and reparse escapes."""

    relative_path = _safe_posix_relative(relative, "model destination")
    models_root = target / "models"
    destination = target / Path(*relative_path.parts)
    _assert_no_reparse_chain(target, destination, "Model destination")
    resolved_root = models_root.resolve()
    try:
        resolved_root.relative_to(target)
        destination.resolve().relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"Model destination escapes project models directory: {destination}") from error
    return destination


def _command(
    name: str,
    args: list[str],
    *,
    preferred: Path | None = None,
) -> dict[str, Any]:
    executable = (
        str(preferred.resolve())
        if preferred and preferred.is_file()
        else shutil.which(name)
    )
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


def _runtime_probe(python: Path, modules: list[str], target_src: Path) -> dict[str, Any]:
    if not python.is_file():
        return {
            "ok": False,
            "python": str(python),
            "modules": {name: False for name in modules},
            "accelerator": {"backend": "unavailable", "cuda_available": False},
            "detail": "target uv environment does not exist",
        }
    code = (
        "import importlib.util,json,platform,sys\n"
        f"names={modules!r}\n"
        "mods={n:importlib.util.find_spec(n) is not None for n in names}\n"
        "acc={'backend':'cpu','cuda_available':False,'device_count':0}\n"
        "if mods.get('torch') and mods.get('torchaudio'):\n"
        " import torch,torchaudio\n"
        " acc={'backend':'cuda' if torch.cuda.is_available() else 'cpu',"
        "'cuda_available':bool(torch.cuda.is_available()),"
        "'device_count':int(torch.cuda.device_count()),"
        "'torch_version':str(torch.__version__),"
        "'torchaudio_version':str(torchaudio.__version__),"
        "'pair_compatible':str(torch.__version__).split('+')[0] == "
        "str(torchaudio.__version__).split('+')[0],"
        "'cuda_runtime':str(torch.version.cuda) if torch.version.cuda else None}\n"
        "print(json.dumps({'python_version':platform.python_version(),"
        "'executable':sys.executable,'modules':mods,'accelerator':acc}))\n"
    )
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(target_src) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    completed = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "python": str(python),
            "modules": {name: False for name in modules},
            "accelerator": {"backend": "unknown", "cuda_available": False},
            "detail": (completed.stderr or completed.stdout).strip(),
        }
    payload = json.loads(completed.stdout)
    payload["ok"] = all(payload["modules"].values()) and bool(
        payload["accelerator"].get("pair_compatible")
    )
    return payload


def select_torch_backend(commands: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Choose uv's official PyTorch backend from a local NVIDIA probe."""

    nvidia_detected = bool(commands["nvidia_smi_optional"]["ok"])
    return {
        "accelerator": "cuda" if nvidia_detected else "cpu",
        "uv_torch_backend": "auto" if nvidia_detected else "cpu",
        "nvidia_detected": nvidia_detected,
        "reason": (
            "NVIDIA device detected; uv auto selects an official driver-compatible CUDA index"
            if nvidia_detected
            else "No usable NVIDIA device detected; select the official CPU index"
        ),
    }


def _model_report(
    target: Path, manifest: dict[str, Any], *, deep_verify: bool = False
) -> list[dict[str, Any]]:
    result = []
    for record in manifest["models"]:
        destination = model_destination(target, record["destination"])
        exists = destination.is_file()
        actual_size = destination.stat().st_size if exists else None
        actual_sha256 = sha256(destination) if exists and deep_verify else None
        size_ok = exists and actual_size == record["size"]
        checksum_ok = actual_sha256 == record["sha256"] if deep_verify and exists else None
        result.append(
            {
                "name": record["name"],
                "path": str(destination),
                "ok": bool(size_ok and (checksum_ok if deep_verify else True)),
                "exists": exists,
                "verification": "sha256" if deep_verify else "size",
                "checksum_verified": checksum_ok,
                "size": actual_size,
                "expected_size": record["size"],
                "sha256": actual_sha256,
                "expected_sha256": record["sha256"],
                "license": record["license"],
                "sidecar": str(destination.with_name(destination.name + ".source-license.json")),
            }
        )
    return result


def _nextfire_provenance_payload(
    model_set: dict[str, Any], *, agpl_accepted: bool, base_license_accepted: bool
) -> dict[str, Any]:
    return {
        "schema_version": "karaoke-model-provenance/v1",
        "repository": model_set["repository"],
        "revision": model_set["revision"],
        "trust_remote_code": False,
        "licenses": model_set["licenses"],
        "license_acceptance_for_download": {
            "nextfire_agpl_3_0": agpl_accepted,
            "facebook_mms_300m_cc_by_nc_4_0": base_license_accepted,
        },
        "files": [
            {
                "name": PurePosixPath(record["destination"]).name,
                "sha256": record["sha256"],
                "size": record["size"],
                "source_url": record["url"],
            }
            for record in model_set["files"]
        ],
        "download_cache": f".cache/karaoke-bootstrap/{NEXTFIRE_MODEL_SET}",
    }


def _nextfire_provenance_report(
    target: Path, model_set: dict[str, Any]
) -> dict[str, Any]:
    path = model_destination(
        target, f"{model_set['destination']}/{model_set['provenance']}"
    )
    report: dict[str, Any] = {"path": str(path), "exists": path.is_file(), "ok": False}
    if not path.is_file():
        report["detail"] = "missing"
        return report
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        report["detail"] = f"unreadable: {error}"
        return report
    expected = _nextfire_provenance_payload(
        model_set, agpl_accepted=False, base_license_accepted=False
    )
    acceptance = payload.get("license_acceptance_for_download")
    static_keys_ok = all(
        payload.get(key) == expected[key]
        for key in (
            "schema_version",
            "repository",
            "revision",
            "trust_remote_code",
            "licenses",
            "files",
            "download_cache",
        )
    )
    acceptance_ok = isinstance(acceptance, dict) and all(
        isinstance(acceptance.get(key), bool)
        for key in ("nextfire_agpl_3_0", "facebook_mms_300m_cc_by_nc_4_0")
    )
    report["ok"] = bool(static_keys_ok and acceptance_ok)
    report["detail"] = "valid" if report["ok"] else "metadata mismatch"
    report["repository"] = payload.get("repository")
    report["revision"] = payload.get("revision")
    report["trust_remote_code"] = payload.get("trust_remote_code")
    return report


def _write_nextfire_provenance(
    target: Path,
    model_set: dict[str, Any],
    *,
    agpl_accepted: bool,
    base_license_accepted: bool,
) -> Path:
    path = model_destination(
        target, f"{model_set['destination']}/{model_set['provenance']}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _nextfire_provenance_payload(
        model_set,
        agpl_accepted=agpl_accepted,
        base_license_accepted=base_license_accepted,
    )
    payload["installed_at_utc"] = datetime.now(timezone.utc).isoformat()
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def check(
    target: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    deep_verify: bool = False,
    allow_custom_manifest: bool = False,
    include_nextfire_mms_ja_latn: bool = False,
) -> dict[str, Any]:
    """Check local state without actively initiating any network request."""

    target = validate_target(target)
    manifest = load_manifest(
        manifest_path, allow_custom_manifest=allow_custom_manifest
    )
    manifest, nextfire_model_set = _select_optional_models(
        manifest, include_nextfire_mms_ja_latn=include_nextfire_mms_ja_latn
    )
    python = validate_target_venv(target, require_python=False)
    suffix = ".exe" if os.name == "nt" else ""
    ffmpeg_bin = target / "tools" / "ffmpeg" / "bin"
    commands = {
        "git": _command("git", ["--version"]),
        "uv": _command("uv", ["--version"]),
        "ffmpeg": _command(
            "ffmpeg", ["-version"], preferred=ffmpeg_bin / f"ffmpeg{suffix}"
        ),
        "ffprobe": _command(
            "ffprobe", ["-version"], preferred=ffmpeg_bin / f"ffprobe{suffix}"
        ),
        "nvidia_smi_optional": _command(
            "nvidia-smi", ["--query-gpu=name,compute_cap", "--format=csv,noheader"]
        ),
    }
    backend_selection = select_torch_backend(commands)
    runtime = _runtime_probe(python, manifest["python"]["required_modules"], target / "src")
    if runtime["ok"]:
        minimum = tuple(int(part) for part in manifest["python"]["minimum"].split("."))
        actual = tuple(int(part) for part in runtime["python_version"].split(".")[: len(minimum)])
        runtime["minimum_version"] = manifest["python"]["minimum"]
        runtime["version_ok"] = actual >= minimum
        runtime["ok"] = runtime["ok"] and runtime["version_ok"]
    models = _model_report(target, manifest, deep_verify=deep_verify)
    runtime_backend_matches = (
        runtime["accelerator"].get("backend") == backend_selection["accelerator"]
    )
    runtime["selected_backend_matches"] = runtime_backend_matches
    environment_ok = bool(runtime["ok"] and runtime_backend_matches)
    nextfire_provenance = (
        _nextfire_provenance_report(target, nextfire_model_set)
        if nextfire_model_set is not None
        else None
    )
    models_ok = all(model["ok"] for model in models) and (
        nextfire_provenance is None or nextfire_provenance["ok"]
    )
    external_tools_ok = all(
        commands[name]["ok"] for name in ("git", "uv", "ffmpeg", "ffprobe")
    )
    external_missing = [
        name for name in ("git", "uv", "ffmpeg", "ffprobe") if not commands[name]["ok"]
    ]
    bootstrap_scope_ok = environment_ok and models_ok
    return {
        "schema_version": "karaoke-environment-check/v3",
        "network_policy": "no-active-network-requests",
        "model_verification": "sha256" if deep_verify else "size",
        "target": str(target),
        "commands": commands,
        "backend_selection": backend_selection,
        "runtime": runtime,
        "models": models,
        **(
            {
                "optional_models": {
                    NEXTFIRE_MODEL_SET: {
                        "selected": True,
                        "repository": NEXTFIRE_REPOSITORY,
                        "revision": NEXTFIRE_REVISION,
                        "trust_remote_code": False,
                        "provenance": nextfire_provenance,
                    }
                }
            }
            if nextfire_model_set is not None
            else {}
        ),
        "dependency_install": {
            **manifest["requirements_validation"],
            "requirements": manifest["requirements_resolved"],
            "effective_local_source": str(target),
            "package_sources": [
                PYPI_INDEX,
                "uv official PyTorch index selected by --torch-backend",
            ],
            "behavior": "uv installs the target checkout with '-e .'; its build/install code may execute",
        },
        "status": {
            "environment_ok": environment_ok,
            "models_ok": models_ok,
            "bootstrap_scope_ok": bootstrap_scope_ok,
            "external_tools_ok": external_tools_ok,
            "external_missing": external_missing,
            "bootstrap_manages": ["target/.venv Python packages", "models/* checkpoints"],
            "bootstrap_does_not_manage": ["git", "uv", "ffmpeg", "ffprobe", "GPU drivers"],
        },
        "core_ok": external_tools_ok and bootstrap_scope_ok,
    }


def _run(command: list[str], cwd: Path, *, offline: bool) -> None:
    environment = os.environ.copy()
    for name in (
        "UV_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS",
        "UV_CONSTRAINT",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_CONSTRAINT",
    ):
        environment.pop(name, None)
    if offline:
        environment["UV_OFFLINE"] = "1"
    subprocess.run(command, cwd=cwd, env=environment, check=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject every HTTP redirect; each configured model URL must be final."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


class _AllowlistedRedirect(urllib.request.HTTPRedirectHandler):
    """Follow only HTTPS redirects to the exact model/CDN host allowlists."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        _validate_network_endpoint(newurl, allow_redirect_host=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_network_endpoint(url: str, *, allow_redirect_host: bool = False) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if allow_redirect_host and hostname in MODEL_REDIRECT_HOST_ALLOWLIST:
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
        ):
            raise ValueError("Model redirect is outside the exact HTTPS host allowlist")
    else:
        _validate_model_url(url, "download")
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise RuntimeError(f"Cannot resolve model host: {hostname}") from error
    if not addresses:
        raise RuntimeError(f"Model host resolved to no addresses: {hostname}")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%")[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise RuntimeError(f"Model host resolved to a forbidden address class: {hostname}")


def _open_download(
    request: urllib.request.Request, *, allow_redirects: bool = False
):  # noqa: ANN201
    _validate_network_endpoint(request.full_url)
    opener = urllib.request.build_opener(
        _AllowlistedRedirect() if allow_redirects else _NoRedirect()
    )
    response = opener.open(request, timeout=60)
    final_url = response.geturl()
    if not allow_redirects and final_url != request.full_url:
        response.close()
        raise RuntimeError("Model download redirect was rejected")
    if allow_redirects:
        _validate_network_endpoint(final_url, allow_redirect_host=True)
    return response


def _write_sidecar(
    destination: Path, record: dict[str, Any], *, license_accepted: bool
) -> Path:
    sidecar = destination.with_name(destination.name + ".source-license.json")
    payload = {
        "schema_version": "karaoke-model-source-license/v1",
        "model": record["name"],
        "source_url": record["url"],
        "sha256": record["sha256"],
        "size": record["size"],
        "license": record["license"],
        "license_accepted_for_download": license_accepted,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{sidecar.name}.", suffix=".partial", dir=sidecar.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, sidecar)
    finally:
        temporary.unlink(missing_ok=True)
    return sidecar


def _download(
    record: dict[str, Any],
    destination: Path,
    *,
    license_accepted: bool,
    cache_path: Path | None = None,
    allow_redirects: bool = False,
    write_sidecar: bool = True,
) -> Path | None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        models_root = next(parent for parent in destination.parents if parent.name == "models")
    except StopIteration as error:
        raise ValueError("Model download destination has no models root") from error
    target_root = models_root.parent
    _assert_no_reparse_chain(models_root, destination, "Model download destination")
    if cache_path is not None:
        cache_root = target_root / ".cache"
        try:
            cache_path.resolve().relative_to(cache_root.resolve())
        except ValueError as error:
            raise ValueError("Model download cache escapes target .cache") from error
        _assert_no_reparse_chain(target_root, cache_path, "Model download cache")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            cache_path.is_file()
            and cache_path.stat().st_size == record["size"]
            and sha256(cache_path) == record["sha256"]
        ):
            fd, publish_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
            )
            os.close(fd)
            publish = Path(publish_name)
            try:
                shutil.copyfile(cache_path, publish)
                os.replace(publish, destination)
            finally:
                publish.unlink(missing_ok=True)
            return (
                _write_sidecar(destination, record, license_accepted=license_accepted)
                if write_sidecar
                else None
            )
    download_parent = cache_path.parent if cache_path is not None else destination.parent
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=download_parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    try:
        request = urllib.request.Request(
            record["url"], headers={"User-Agent": "karaoke-bootstrap/1"}
        )
        response_context = (
            _open_download(request, allow_redirects=True)
            if allow_redirects
            else _open_download(request)
        )
        with response_context as response, temporary.open("wb") as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                total += len(chunk)
                if total > record["size"]:
                    raise ValueError(f"Downloaded model exceeds configured size: {record['name']}")
                digest.update(chunk)
                output.write(chunk)
        if total != record["size"] or digest.hexdigest() != record["sha256"]:
            raise ValueError(
                f"Downloaded model failed size or SHA-256 verification: {record['name']}"
            )
        if cache_path is not None:
            os.replace(temporary, cache_path)
            fd, publish_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
            )
            os.close(fd)
            publish = Path(publish_name)
            try:
                shutil.copyfile(cache_path, publish)
                os.replace(publish, destination)
            finally:
                publish.unlink(missing_ok=True)
        else:
            os.replace(temporary, destination)
        return (
            _write_sidecar(destination, record, license_accepted=license_accepted)
            if write_sidecar
            else None
        )
    finally:
        temporary.unlink(missing_ok=True)


def redact_report_paths(value: Any) -> Any:
    """Recursively redact absolute local filesystem paths from a report."""

    if isinstance(value, dict):
        return {key: redact_report_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_report_paths(item) for item in value]
    if isinstance(value, str) and _ABSOLUTE_TEXT_RE.match(value):
        return "<redacted:absolute-path>"
    return value


def bootstrap(
    target: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    dry_run: bool = False,
    offline: bool = False,
    allow_custom_manifest: bool = False,
    accept_mms_cc_by_nc_4_0: bool = False,
    include_nextfire_mms_ja_latn: bool = False,
    accept_nextfire_agpl_3_0: bool = False,
    allow_python_download: bool = False,
) -> dict[str, Any]:
    """Explicitly bootstrap the one permitted target environment and models."""

    target = validate_target(target)
    manifest = load_manifest(
        manifest_path, allow_custom_manifest=allow_custom_manifest
    )
    manifest, nextfire_model_set = _select_optional_models(
        manifest, include_nextfire_mms_ja_latn=include_nextfire_mms_ja_latn
    )
    # Explicit bootstrap deep-verifies existing models before deciding to skip them.
    before = check(
        target,
        manifest_path,
        deep_verify=True,
        allow_custom_manifest=allow_custom_manifest,
        include_nextfire_mms_ja_latn=include_nextfire_mms_ja_latn,
    )
    python = validate_target_venv(target, require_python=False)
    requirements = Path(manifest["requirements_resolved"])
    actions: list[dict[str, Any]] = []
    if not python.is_file():
        actions.append(
            {
                "kind": "create-environment",
                "path": str(target / ".venv"),
                "python_download_allowed": allow_python_download,
                "python_download_behavior": (
                    "uv may download a managed Python"
                    if allow_python_download
                    else "uv receives --no-python-downloads"
                ),
            }
        )
    environment_needs_install = not before["status"]["environment_ok"]
    if environment_needs_install:
        actions.append(
            {
                "kind": "install-version-pinned-dependencies",
                "requirements": str(requirements),
                "target_environment": str(target / ".venv"),
                "selected_backend": before["backend_selection"]["accelerator"],
                "uv_torch_backend": before["backend_selection"]["uv_torch_backend"],
                "effective_local_source": str(target),
                "executes_local_editable_install": True,
            }
        )

    records = {record["name"]: record for record in manifest["models"]}
    nextfire_license_gate_added = False
    for model in before["models"]:
        if model["ok"]:
            continue
        record = records[model["name"]]
        license_data = record["license"]
        if record.get("optional_model_set") == NEXTFIRE_MODEL_SET and not (
            accept_nextfire_agpl_3_0 and accept_mms_cc_by_nc_4_0
        ):
            if not nextfire_license_gate_added:
                required_flags = []
                if not accept_nextfire_agpl_3_0:
                    required_flags.append("--accept-nextfire-agpl-3-0")
                if not accept_mms_cc_by_nc_4_0:
                    required_flags.append("--accept-mms-cc-by-nc-4-0")
                actions.append(
                    {
                        "kind": "license-acceptance-required",
                        "name": NEXTFIRE_MODEL_SET,
                        "status": "blocked",
                        "license": license_data["spdx"],
                        "notice": license_data["notice"],
                        "required_flags": required_flags,
                    }
                )
                nextfire_license_gate_added = True
            continue
        if license_data["requires_acceptance"] and not accept_mms_cc_by_nc_4_0:
            actions.append(
                {
                    "kind": "license-acceptance-required",
                    "name": model["name"],
                    "status": "blocked",
                    "license": license_data["spdx"],
                    "notice": license_data["notice"],
                    "required_flag": "--accept-mms-cc-by-nc-4-0",
                }
            )
            continue
        actions.append(
            {
                "kind": "download-model",
                "name": model["name"],
                "path": model["path"],
                "url": record["url"],
                "license": license_data["spdx"],
                "license_accepted": bool(license_data["requires_acceptance"]),
                "optional_model_set": record.get("optional_model_set"),
            }
        )

    if nextfire_model_set is not None:
        provenance_before = (
            before.get("optional_models", {})
            .get(NEXTFIRE_MODEL_SET, {})
            .get("provenance")
        )
        if not isinstance(provenance_before, dict) or not provenance_before.get("ok"):
            actions.append(
                {
                    "kind": "write-model-provenance",
                    "name": NEXTFIRE_MODEL_SET,
                    "path": str(
                        model_destination(
                            target,
                            f"{nextfire_model_set['destination']}/"
                            f"{nextfire_model_set['provenance']}",
                        )
                    ),
                    "repository": NEXTFIRE_REPOSITORY,
                    "revision": NEXTFIRE_REVISION,
                    "trust_remote_code": False,
                }
            )

    blocked_actions: list[dict[str, Any]] = [
        action for action in actions if action["kind"] == "license-acceptance-required"
    ]
    downloaded: list[dict[str, Any]] = []
    if not dry_run:
        uv = shutil.which("uv")
        environment_actions = {
            "create-environment",
            "install-version-pinned-dependencies",
        }
        if uv is None and any(action["kind"] in environment_actions for action in actions):
            raise RuntimeError("uv is required to create or update the target environment")
        if not python.is_file():
            command = [
                str(uv),
                "--no-config",
                "venv",
                str(target / ".venv"),
                "--python",
                manifest["python"]["minimum"],
            ]
            if not allow_python_download:
                command.append("--no-python-downloads")
            _run(command, target, offline=offline)
            python = validate_target_venv(target, require_python=True)
        if environment_needs_install:
            validate_target_venv(target, require_python=True)
            _run(
                [
                    str(uv),
                    "--no-config",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--torch-backend",
                    before["backend_selection"]["uv_torch_backend"],
                    "--default-index",
                    PYPI_INDEX,
                    "--reinstall-package",
                    "torch",
                    "--reinstall-package",
                    "torchaudio",
                    "-r",
                    str(requirements),
                ],
                target,
                offline=offline,
            )
        for action in actions:
            if action["kind"] != "download-model":
                continue
            if offline:
                blocked_actions.append(
                    {**action, "status": "blocked", "reason": "offline bootstrap"}
                )
                continue
            destination = Path(action["path"])
            record = records[action["name"]]
            if record.get("optional_model_set") == NEXTFIRE_MODEL_SET:
                cache_path = (
                    target
                    / ".cache"
                    / "karaoke-bootstrap"
                    / NEXTFIRE_MODEL_SET
                    / record["sha256"]
                )
                sidecar = _download(
                    record,
                    destination,
                    license_accepted=True,
                    cache_path=cache_path,
                    allow_redirects=True,
                    write_sidecar=False,
                )
            else:
                sidecar = _download(
                    record,
                    destination,
                    license_accepted=bool(record["license"]["requires_acceptance"]),
                )
            downloaded.append(
                {
                    "name": action["name"],
                    "path": str(destination),
                    "sidecar": str(sidecar) if sidecar is not None else None,
                }
            )

        if nextfire_model_set is not None:
            nextfire_names = {record["name"] for record in nextfire_model_set["files"]}
            ready_before = {
                model["name"] for model in before["models"] if model["ok"]
            }
            ready_downloaded = {item["name"] for item in downloaded}
            nextfire_blocked = any(
                action.get("name") in nextfire_names for action in blocked_actions
            )
            if nextfire_names.issubset(ready_before | ready_downloaded) and not nextfire_blocked:
                _write_nextfire_provenance(
                    target,
                    nextfire_model_set,
                    agpl_accepted=accept_nextfire_agpl_3_0,
                    base_license_accepted=accept_mms_cc_by_nc_4_0,
                )

    after = (
        before
        if dry_run
        else check(
            target,
            manifest_path,
            deep_verify=True,
            allow_custom_manifest=allow_custom_manifest,
            include_nextfire_mms_ja_latn=include_nextfire_mms_ja_latn,
        )
    )
    return {
        "schema_version": "karaoke-bootstrap-result/v2",
        "dry_run": dry_run,
        "offline": offline,
        "network_allowed": not offline and not dry_run,
        "python_download_allowed": allow_python_download and not offline and not dry_run,
        "selected_backend": before["backend_selection"],
        "dependency_install": before["dependency_install"],
        "actions": actions,
        "blocked_actions": blocked_actions,
        "downloads_completed": downloaded,
        "before": before,
        "after": after,
        "bootstrap_scope_ok": after["status"]["bootstrap_scope_ok"],
        "external_tools_ok": after["status"]["external_tools_ok"],
        "ok": after["core_ok"] and not blocked_actions,
    }
