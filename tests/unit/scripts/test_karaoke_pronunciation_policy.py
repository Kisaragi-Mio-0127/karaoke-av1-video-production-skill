from __future__ import annotations

import pytest

from scripts.karaoke_common.pronunciation import validate_pronunciation
from scripts.sug_ruby import RubyValidationError


def _project(language: str, *, ruby: bool = False, unterminated: bool = False):
    character = {
        "char": "字",
        "linked_to_next": unterminated,
        "ruby": {"parts": [{"text": "じ"}]} if ruby else None,
    }
    return {
        "metadata": {"language": language},
        "sentences": [{"id": "line-1", "characters": [character]}],
    }


def test_optional_japanese_without_sidecar_is_not_performed():
    result = validate_pronunciation(
        _project("ja", ruby=True),
        mode="optional",
        sidecar=None,
        sidecar_validator=lambda *_args: [],
    )

    assert result.status == "not-performed"
    assert result.reason == "optional-sidecar-missing"
    assert result.source_ruby_count == 1


def test_optional_japanese_sidecar_is_strictly_validated():
    with pytest.raises(RubyValidationError, match="stale hash"):
        validate_pronunciation(
            _project("ja", ruby=True),
            mode="optional",
            sidecar={"records": []},
            sidecar_validator=lambda *_args: ["stale hash"],
        )


def test_required_japanese_needs_current_sidecar_and_at_least_one_span():
    with pytest.raises(RubyValidationError, match="sidecar is missing"):
        validate_pronunciation(
            _project("ja", ruby=True),
            mode="required",
            sidecar=None,
            sidecar_validator=lambda *_args: [],
        )
    with pytest.raises(RubyValidationError, match="no ruby spans"):
        validate_pronunciation(
            _project("ja"),
            mode="required",
            sidecar={"records": []},
            sidecar_validator=lambda *_args: [],
        )

    result = validate_pronunciation(
        _project("ja", ruby=True),
        mode="required",
        sidecar={"records": [{}]},
        sidecar_validator=lambda *_args: [],
    )
    assert (result.status, result.reason) == (
        "pass",
        "current-approved-sidecar-validated",
    )


def test_off_skips_sidecar_review_but_not_japanese_structure():
    def must_not_run(*_args):
        raise AssertionError("sidecar validator was called")

    result = validate_pronunciation(
        _project("ja", ruby=True),
        mode="off",
        sidecar={"records": []},
        sidecar_validator=must_not_run,
    )
    assert (result.status, result.reason) == (
        "not-performed",
        "sidecar-review-disabled-by-policy",
    )

    with pytest.raises(RubyValidationError, match="does not terminate"):
        validate_pronunciation(
            _project("ja", ruby=True, unterminated=True),
            mode="off",
            sidecar=None,
            sidecar_validator=must_not_run,
        )


@pytest.mark.parametrize("language", ("zh", "en"))
@pytest.mark.parametrize("mode", ("optional", "required", "off"))
def test_zh_en_require_zero_source_and_rendered_ruby(language: str, mode: str):
    result = validate_pronunciation(
        _project(language),
        mode=mode,
        sidecar=None,
        sidecar_validator=None,
        rendered_ruby_count=0,
    )
    assert result.status == "pass"
    assert result.reason == "ruby-disabled-source-and-rendered-zero"

    with pytest.raises(RubyValidationError, match="ruby is disabled"):
        validate_pronunciation(
            _project(language, ruby=True),
            mode=mode,
            sidecar=None,
            sidecar_validator=None,
            rendered_ruby_count=0,
        )
    with pytest.raises(RubyValidationError, match="rendered ruby=0"):
        validate_pronunciation(
            _project(language),
            mode=mode,
            sidecar=None,
            sidecar_validator=None,
            rendered_ruby_count=1,
        )
