"""Template-write extension seam: the single hook every
``ac_templates.ext`` persistence passes through.

Engine or product domains that own template-persistence invariants (like a
server-owned ext key, a tri-state capability, or an own-config mutual
exclusion) bind an implementation here; ``TemplateService.create_template``
/ ``update_template`` call it once per persistence, before validation and
encryption — the wrapper ``create_or_update_template`` reaches the same
choke through those two methods, so a wrapped call fires the hook exactly
once. The contract is businessless: inputs are opaque mappings whose field
names and validity rules are entirely the implementation's concern, and this
layer never learns them.

The default binding is :class:`NoopTemplateWriteExtension`: every
persistence keeps its incoming config unchanged, so an unextended runtime
persists exactly as before.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, runtime_checkable


class TemplateWriteInvalidInput(ValueError):
    """The persistence candidate carries a shape/value the write-extension
    domain rejects (form-level). The surfaces map this to their 400 family:
    the message names the violated rule and carries no internals."""


@runtime_checkable
class TemplateWriteExtension(Protocol):
    """The template-persistence hook every write passes through.

    Called once per write, before validation/encryption/persistence, with
    the raw incoming candidate and what the row currently holds. The
    implementation may return a (possibly adjusted) candidate mapping to
    persist instead of the incoming one, or raise
    :class:`TemplateWriteInvalidInput` to abort the write. Returning
    ``None`` means "keep the incoming candidate unchanged".
    """

    def validate_template_write(
        self,
        *,
        bot_id: str,
        template_config: Mapping[str, Any],
        source: str,
        existing_ext: Optional[Mapping[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        """Validate / adjust a candidate before it persists.

        ``source`` is the persisting service's own opaque tag (``"create"``
        on a fresh insert, ``"update"`` on an overwrite); ``existing_ext``
        is the decoded ext of the row that already exists for ``bot_id``
        (``None`` when there is none). Community passes the values and
        sequences the call only — every business decision lives in the
        implementation.
        """
        ...


class NoopTemplateWriteExtension:
    """Default extension: every persistence keeps its incoming config
    unchanged and nothing is ever rejected."""

    def validate_template_write(
        self,
        *,
        bot_id: str,
        template_config: Mapping[str, Any],
        source: str,
        existing_ext: Optional[Mapping[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        return None
