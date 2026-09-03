"""Per-bot CLI tool repository contract (W9, issue #1477).

Every member is ``@abstractmethod``: an implementation that omits one fails at
construction naming the missing member, instead of raising ``AttributeError``
at the call site. Domain imports are ``TYPE_CHECKING``-only — see
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional, Protocol, Sequence, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.cli_tools import (
        BotCliToolRecord,
    )


@runtime_checkable
class BotCliToolRepositoryProtocol(Protocol):
    """CLI 工具仓库接口.

    Keyed on ``(env, entity_id, bot_id, name)`` and backed by a UNIQUE
    constraint on ``ac_bot_cli_tool``. A bot has zero or more tools; each row
    is one installed command.

    These rows are the platform's record of what it asked for, which is what
    makes a manifest apply's full override decidable: :meth:`list` is where
    removals come from, so a tool the platform installed is removed even when
    the engine's own view has drifted.
    """

    @abstractmethod
    def get(
        self, *, env: str, entity_id: str, bot_id: str, name: str
    ) -> Optional[BotCliToolRecord]:
        """The row for one tool, or ``None`` when the bot does not have it."""
        ...

    @abstractmethod
    def list(
        self, *, env: str, entity_id: str, bot_id: str
    ) -> Sequence[BotCliToolRecord]:
        """Every tool installed on the bot, in ``name`` **code-point order**.

        Ordered so a report, an artifact's ref list and a test all see the same
        sequence for the same state — an arbitrary order would make byte
        comparison of composed artifacts flaky for no reason.

        The contract says *code-point* order, not merely "by name", because
        "by name" is ambiguous across collations: SQLite sorts ``Zip`` before
        ``aws`` and a case-insensitive MySQL collation sorts it after. An
        implementation satisfying this with a plain ``ORDER BY name`` would
        pass CI and diverge in production, where the composed artifact's bytes
        are compared.
        """
        ...

    @abstractmethod
    def upsert(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        name: str,
        source: str,
        digest: str,
        subpath: Optional[str],
        md5: str,
        size_bytes: int,
        version: Optional[str],
        oss_key: str,
        installed_by: str,
        modifier: str,
    ) -> BotCliToolRecord:
        """Insert the tool, or replace an existing row of the same name.

        ``modifier`` is the acting user resolved from the request principal;
        it is never supplied by the client. ``installed_by`` records whether a
        manifest apply or a user put the tool there.
        """
        ...

    @abstractmethod
    def delete(self, *, env: str, entity_id: str, bot_id: str, name: str) -> bool:
        """Hard-delete one row. Idempotent — returns ``False`` when absent.

        A caller that also removes the object this row pointed at must first
        satisfy the same invariant :meth:`delete_all` documents: **no object is
        referenced by more than one row**. This method does not check it, and
        the row is gone by the time it returns.
        """
        ...

    @abstractmethod
    def delete_all(self, *, env: str, entity_id: str, bot_id: str) -> Sequence[str]:
        """Hard-delete every row for the bot; return their ``oss_key``s.

        The creation-cleanup entry point: a W13 job that fails after installing
        tools but before a bot exists would otherwise leave rows for a
        ``bot_id`` that was never created, and nothing else would collect them.

        It returns the object keys rather than a count **so the objects can be
        cleaned up too**. ``oss_key`` lives only on these rows, so a caller that
        deleted first and asked afterwards could never enumerate what it had
        just orphaned.

        **The keys are safe to delete only while no object is referenced by
        more than one row.** Nothing in this table enforces that — ``oss_key``
        carries no uniqueness constraint — and a per-bot prefix alone is not
        enough: a content-addressed layout like ``tools/{bot}/{sha256}`` keeps
        bots apart yet gives *one* bot's two commands the same key whenever the
        same archive is installed under two names, which the model explicitly
        allows (two rows may share a ``digest``). The store's one-object-per-row
        layout is what makes this contract true; a change to it is a change to
        this contract.
        """
        ...
