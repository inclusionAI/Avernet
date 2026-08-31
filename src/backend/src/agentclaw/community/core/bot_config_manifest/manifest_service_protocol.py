"""Service API Protocol for the bot config manifest document (issue #1469, W1).

Re-exported for adapters by ``api/bot_config_manifest_service.py``; defined
here in the owning core module so the concrete service inherits it without a
``core -> api`` cross-layer waiver — see ``api/README.md``.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional, Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.capabilities import (
        CategorySupport,
    )
    from agentclaw.community.core.bot_config_manifest.manifest_schema import (
        ManifestDocument,
    )

# MAX_MODIFIER_CHARS mirrors ac_bot_startup_script's cap: the modifier column
# is 1024 wide in both tables, composed by callers into audit strings that can
# overflow unaided.
MAX_MODIFIER_CHARS = 1024


@runtime_checkable
class ManifestServiceProtocol(Protocol):
    """读、整体替换、清除一份 bot 的配置清单文档——存储与校验的 owner.

    The write path is **all-or-nothing**: one unsupported or invalid part
    rejects the whole document with a per-entry violation list, and nothing
    reaches storage. Apply does not exist yet (W4); a stored manifest is a
    declaration waiting to be applied, never a side effect.
    """

    @abstractmethod
    def get(
        self, *, entity_id: str, bot_id: str
    ) -> ManifestDocument:
        """Return the stored document, or the empty document — never an error.

        The read side is the payload-build path's contract too: an absent
        declaration is "no opinion", not "empty set" — that distinction lives
        in the document, not in absence.
        """
        ...

    @abstractmethod
    def get_record(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[object]:
        """The raw stored row (audit fields: modifier/gmt_modified), or ``None``."""
        ...

    @abstractmethod
    def capabilities(
        self,
        *,
        engine_type: str,
        bot_type: str,
        script_supported: Optional[bool] = None,
    ) -> CategorySupport:
        """Per-category support for ``(engine_type, bot_type)``.

        ``script_supported`` is the *narrow override* for #935's per-bot
        judgment (form factor: LOCAL/singlebox, ARCA-direct, desktop) where
        the caller has a bot record. ``None`` means "engine table only".
        Read and write paths share this one resolver — they cannot disagree.
        """
        ...

    @abstractmethod
    def put(
        self,
        *,
        entity_id: str,
        bot_id: str,
        engine_type: str,
        bot_type: str,
        document: object,
        modifier: str,
        script_supported: Optional[bool] = None,
    ) -> dict:
        """Validate and store-or-replace the whole document.

        Raises:
            ManifestInvalidError: Any violation — shape, cross-entry rule,
                unsupported category for this engine, identity type outside
                the engine's set, script unsupported, over a limit. Every
                violation is reported, and nothing is written. A composed
                audit actor over MAX_MODIFIER_CHARS is *not* an error: it is
                bounded at construction (#935's audit-width口径), so a valid
                document never dies of the platform's own actor composition.

        Returns a ``{"bot_id", "schema_version", "warnings", "modifier"}``
        dict — warnings carry non-fatal notes (sources that no entry
        references, a declared-but-reserved identity file).
        """
        ...

    @abstractmethod
    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Remove the declaration row. Idempotent — ``False`` when absent.

        Deletes nothing else: declared categories' materialized entities are
        the apply layer's business (W4+), and "no declaration" has never meant
        "delete assets".
        """
        ...
