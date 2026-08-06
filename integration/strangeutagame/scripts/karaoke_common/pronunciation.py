"""Japanese pronunciation validation policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from scripts.karaoke_language import DEFAULT_LANGUAGE, normalize_language
from scripts.sug_ruby import RubyValidationError, iter_sug_ruby_spans, validate_sug_ruby

PRONUNCIATION_VALIDATION_MODES = ("optional", "required", "off")


@dataclass(frozen=True)
class PronunciationValidationResult:
    mode: str
    status: str
    reason: str
    language: str
    source_ruby_count: int
    rendered_ruby_count: int
    sidecar_present: bool
    sidecar_record_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _language(source: Any) -> str:
    metadata = (
        source.get("metadata", {})
        if isinstance(source, Mapping)
        else getattr(source, "metadata", None)
    )
    value = (
        metadata.get("language")
        if isinstance(metadata, Mapping)
        else getattr(metadata, "language", None)
    )
    return normalize_language(value, default=DEFAULT_LANGUAGE)


def validate_pronunciation(
    source: Any,
    *,
    mode: str,
    sidecar: Mapping[str, Any] | None,
    sidecar_validator: Callable[[Any, Mapping[str, Any] | None], list[str]] | None,
    rendered_ruby_count: int | None = None,
) -> PronunciationValidationResult:
    """Apply one pronunciation policy without generating or changing ruby."""

    if mode not in PRONUNCIATION_VALIDATION_MODES:
        raise ValueError(f"unsupported pronunciation validation mode: {mode!r}")
    language = _language(source)
    structural_errors = validate_sug_ruby(source)
    if structural_errors:
        raise RubyValidationError("; ".join(structural_errors))
    spans = iter_sug_ruby_spans(source)
    source_count = len(spans)
    rendered_count = source_count if rendered_ruby_count is None else rendered_ruby_count
    records = sidecar.get("records", []) if isinstance(sidecar, Mapping) else []
    record_count = len(records) if isinstance(records, list) else 0

    if mode == "off":
        return PronunciationValidationResult(
            mode=mode,
            status="not-performed",
            reason="sidecar-review-disabled-by-policy",
            language=language,
            source_ruby_count=source_count,
            rendered_ruby_count=rendered_count,
            sidecar_present=sidecar is not None,
            sidecar_record_count=record_count,
        )
    if sidecar is None:
        if mode == "required":
            raise RubyValidationError(
                "pronunciation validation required but current approved sidecar is missing"
            )
        return PronunciationValidationResult(
            mode=mode,
            status="not-performed",
            reason="optional-sidecar-missing",
            language=language,
            source_ruby_count=source_count,
            rendered_ruby_count=rendered_count,
            sidecar_present=False,
            sidecar_record_count=0,
        )
    if sidecar_validator is None:
        raise RubyValidationError("canonical ruby review validator is unavailable")
    try:
        errors = sidecar_validator(source, sidecar)
    except RubyValidationError:
        raise
    except Exception as error:
        raise RubyValidationError(
            f"canonical ruby review validation failed: {error}"
        ) from error
    if errors:
        raise RubyValidationError(
            "canonical ruby review sidecar rejected: "
            + "; ".join(str(error) for error in errors)
        )
    if mode == "required" and source_count < 1:
        raise RubyValidationError(
            "pronunciation validation required but canonical SUG has no ruby spans"
        )
    return PronunciationValidationResult(
        mode=mode,
        status="pass",
        reason="current-approved-sidecar-validated",
        language=language,
        source_ruby_count=source_count,
        rendered_ruby_count=rendered_count,
        sidecar_present=True,
        sidecar_record_count=record_count,
    )
