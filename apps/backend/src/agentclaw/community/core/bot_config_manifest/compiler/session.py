"""The per-compile fetch state: the budget, the named-source session, the content scope.

One ``CompileSession`` per ``ManifestCompiler.compile`` call. It is the
``FetchContext`` the fetch funnel reads (bot identity, apply id, budget,
source session), built from the apply's ``ApplyContext`` plus the two
things the fetch funnel needs: the execution's ``ApplyFetchBudget`` and a
``SourceSession`` over the document's ``sources`` and the bot's git
baselines. It is closed before the compiler returns, because by then every
byte worth keeping is in the content store and the checkout trees are not.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from agentclaw.community.core.apply_engine.fetch_budget import (
    ApplyFetchBudget,
    current_fetch_budget,
    new_fetch_budget,
)
from agentclaw.community.core.bot_config_manifest.sources.source_session import SourceSession
from agentclaw.community.core.content_store import ContentScope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.apply_engine.context import ApplyContext

__all__ = ["CompileSession", "digest_of"]


def digest_of(data: bytes) -> str:
    """The content-store address of ``data``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CompileSession:
    """Satisfies ``FetchContext``; carries the shared budget and owned source session."""

    bot_id: str
    entity_id: str
    owner_id: str
    env: str
    tenant: str
    engine_type: str
    actor_id: str
    #: The apply this compile serves, carried from ``ApplyContext.apply_id``. ``None`` when the context
    #: predates an apply id: the synchronous ``check`` before one is minted, and a dry run, which never mints
    #: one. Set in the worker's compile, where every fetch receipt the content store files is stamped with it.
    apply_id: Optional[str]
    budget: ApplyFetchBudget
    source_session: SourceSession

    @classmethod
    def open(cls, ctx: ApplyContext, *, source_session: SourceSession) -> CompileSession:
        return cls(
            bot_id=ctx.bot_id,
            entity_id=ctx.entity_id,
            owner_id=ctx.owner_id,
            env=ctx.env,
            tenant=ctx.tenant,
            engine_type=ctx.engine_type,
            actor_id=ctx.actor_id,
            apply_id=ctx.apply_id,
            # One ledger spans compilation and preparation within a run.
            # Standalone compilation still has its own bounded allowance.
            budget=current_fetch_budget() or new_fetch_budget(),
            source_session=source_session,
        )

    @property
    def scope(self) -> ContentScope:
        return ContentScope(env=self.env, entity_id=self.entity_id, bot_id=self.bot_id)

    def close(self) -> None:
        self.source_session.close()
