"""The schema's public validation seam shared by envelope and entry suites."""

from __future__ import annotations

import pytest
from agentclaw.community.core.apply_engine.capabilities import resolve_capabilities
from agentclaw.community.core.bot_config_manifest.schema import (
    ManifestValidationError,
    validate_document,
)


def _is_teclaw(engine: str | None) -> bool:
    return (engine or "").strip().lower() == "teclaw"


def _caps(engine="openclaw", bot_type="personal"):
    return resolve_capabilities(
        active_engine=engine, bot_type=bot_type, is_teclaw=_is_teclaw
    )


def _accept(document: str, **kwargs):
    return validate_document(document, _caps(**kwargs))


def _reject(document: str, **kwargs) -> list[tuple[str, str]]:
    """Return ``[(location, code), …]`` — the refusal, in the caller's terms."""
    with pytest.raises(ManifestValidationError) as excinfo:
        validate_document(document, _caps(**kwargs))
    return [(v.location, v.code) for v in excinfo.value.violations]


def _codes(document: str, **kwargs) -> set[str]:
    return {code for _, code in _reject(document, **kwargs)}
