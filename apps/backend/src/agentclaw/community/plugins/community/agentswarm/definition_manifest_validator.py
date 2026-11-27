"""Community adapter — write-time validation of a definition's manifest.

Implements ``core.agentswarm.service.protocols.ManifestDocumentValidator``.

A definition is human-authored configuration, so its manifest is checked once
when the definition is written rather than on every run that uses it. The
grammar belongs to the platform's manifest preflight, which the swarm core may
not import (D17/D35); this adapter is the plugin layer that is allowed to reach
it, exactly as ``host.py`` and ``bot_authorization.py`` are.

It delegates to ``ManifestCreationSeam.preflight`` — the same refusal a bot
creation performs before it applies for a passport — so an accepted definition
is one whose document could actually be delivered at creation time. Validation
is all-or-nothing there: every reason is reported at once.
"""
from __future__ import annotations

from agentclaw.community.core.agentswarm.service.protocols import (
    DefinitionManifestInvalid, ManifestDocumentValidator,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)


class PlatformManifestDocumentValidator(ManifestDocumentValidator):
    """Validate a definition manifest with the platform's own preflight."""

    def __init__(self, seam: ManifestCreationSeam) -> None:
        self._seam = seam

    def validate_document(self, document: str) -> None:
        # A blank document means "no manifest": provisioning then synthesizes
        # the identity-only persona document, so there is nothing to validate.
        if not document.strip():
            return
        from agentclaw.community.core.bot_config_manifest.creation import (
            ManifestValidationError,
        )

        try:
            self._seam.preflight(document=document, engine_type=None, bot_type=None)
        except ManifestValidationError as exc:
            raise DefinitionManifestInvalid(str(exc)) from exc
