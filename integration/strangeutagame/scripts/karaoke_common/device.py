"""Shared device selection for the public karaoke integration.

The public bundle defaults to ``auto`` because its runtime hardware is not
known in advance.  Explicit ``cuda`` is strict, while explicit ``cpu`` is a
portable override.  The torch import stays lazy so report and parser modules
can be used without loading a model or contacting a model repository.
"""

from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass
from typing import Any

DEVICE_CHOICES = ("auto", "cpu", "cuda")
DEFAULT_DEVICE = "auto"


class DeviceResolutionError(RuntimeError):
    """Raised when the requested device cannot be used."""


@dataclass(frozen=True)
class DeviceSelection:
    """The requested device and the concrete device selected for a run."""

    requested: str
    resolved: str

    def as_report(self) -> dict[str, str]:
        """Return stable evidence fields for JSON reports."""

        return {
            "requested_device": self.requested,
            "resolved_device": self.resolved,
        }


def normalize_device(value: str | None, *, default: str = DEFAULT_DEVICE) -> str:
    """Normalize and validate a device choice without importing torch."""

    candidate = default if value is None else str(value).strip().lower()
    if candidate not in DEVICE_CHOICES:
        choices = ", ".join(DEVICE_CHOICES)
        raise ValueError(f"unsupported device {value!r}; expected one of {choices}")
    return candidate


def _load_torch() -> Any:
    try:
        return importlib.import_module("torch")
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DeviceResolutionError(
            "CUDA was requested but PyTorch is not importable; rerun with "
            "--device cpu or install a runtime with PyTorch support"
        ) from exc


def _cuda_is_available(torch_module: Any) -> bool:
    try:
        return bool(torch_module.cuda.is_available())
    except Exception as exc:  # pragma: no cover - backend-specific failure
        raise DeviceResolutionError(
            "CUDA availability could not be determined; rerun with "
            "--device cpu if a CPU-only runtime is intended"
        ) from exc


def resolve_device(
    requested: str | None = None,
    *,
    torch_module: Any | None = None,
) -> DeviceSelection:
    """Resolve a public runtime device.

    ``auto`` resolves to CUDA when available and CPU otherwise.  Explicit
    ``cuda`` fails clearly when CUDA is unavailable; it never falls back.
    """

    candidate = normalize_device(requested)
    if candidate == "cpu":
        return DeviceSelection(requested=candidate, resolved="cpu")

    if torch_module is None:
        try:
            torch_module = _load_torch()
        except DeviceResolutionError:
            if candidate == "auto":
                return DeviceSelection(requested=candidate, resolved="cpu")
            raise

    available = _cuda_is_available(torch_module)
    if candidate == "cuda" and not available:
        raise DeviceResolutionError(
            "CUDA was requested but is unavailable in the active PyTorch runtime; "
            "rerun with --device cpu or explicitly choose --device auto"
        )
    return DeviceSelection(
        requested=candidate,
        resolved="cuda" if available else "cpu",
    )


def add_device_argument(
    parser: argparse.ArgumentParser,
    *,
    default: str = DEFAULT_DEVICE,
) -> None:
    """Add the shared public-runtime device option to a parser."""

    parser.add_argument(
        "--device",
        choices=DEVICE_CHOICES,
        default=default,
        help=(
            "ML device (default: auto; uses CUDA when available, otherwise CPU; "
            "explicit cuda is strict)"
        ),
    )


__all__ = [
    "DEFAULT_DEVICE",
    "DEVICE_CHOICES",
    "DeviceResolutionError",
    "DeviceSelection",
    "add_device_argument",
    "normalize_device",
    "resolve_device",
]
