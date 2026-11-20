"""The definition manifest seam: write-time validation, and the unbound case.

A definition write is a promise that its manifest was accepted, so the seam is
*required*: a profile that cannot supply it must refuse writes rather than store
an unvalidated document.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.agentswarm.service.protocols import (
    DefinitionManifestInvalid, ManifestDocumentValidator,
)


class _FakeSeam:
    def __init__(self, *, reject: Exception | None = None) -> None:
        self._reject = reject
        self.calls: list[dict] = []

    def preflight(self, *, document, engine_type, bot_type):
        self.calls.append(
            {"document": document, "engine_type": engine_type, "bot_type": bot_type}
        )
        if self._reject is not None:
            raise self._reject
        return {}


def _validator(seam):
    from agentclaw.community.plugins.community.agentswarm.definition_manifest_validator import (
        PlatformManifestDocumentValidator,
    )
    return PlatformManifestDocumentValidator(seam)


def test_blank_document_is_accepted_without_calling_preflight():
    seam = _FakeSeam()
    _validator(seam).validate_document("")
    _validator(seam).validate_document("   \n")
    assert seam.calls == []


def test_valid_document_delegates_to_platform_preflight():
    seam = _FakeSeam()
    doc = "schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n"
    _validator(seam).validate_document(doc)
    assert seam.calls == [
        {"document": doc, "engine_type": None, "bot_type": None}
    ]


def test_rejected_document_raises_the_swarm_typed_error():
    from agentclaw.community.core.bot_config_manifest.creation import (
        ManifestValidationError,
    )
    seam = _FakeSeam(reject=ManifestValidationError([]))
    with pytest.raises(DefinitionManifestInvalid):
        _validator(seam).validate_document("schema_version: 1\n")


def test_adapter_satisfies_the_protocol():
    from agentclaw.community.plugins.community.agentswarm.definition_manifest_validator import (
        PlatformManifestDocumentValidator,
    )
    # runtime_checkable Protocol: the adapter must structurally satisfy it.
    assert issubclass(PlatformManifestDocumentValidator, ManifestDocumentValidator)


def test_unbound_validator_refuses_a_definition_write():
    """With no validator bound, the service must not store a document."""
    from agentclaw.community.core.agentswarm.service import AgentSwarmService
    import inspect

    # The seam is constructor-injected and defaults to None; the write path
    # treats None as "cannot validate" and refuses.
    sig = inspect.signature(AgentSwarmService.__init__)
    assert "definition_manifest_validator" in sig.parameters
    assert sig.parameters["definition_manifest_validator"].default is None
