"""The per-compile fetch state: the budget, the named-source session, the content scope.

One ``CompileSession`` per ``ManifestCompiler.compile`` call. It is the
``FetchContext`` the fetch funnel reads (bot identity, apply id, budget,
source session), built from the apply's ``ApplyContext`` plus the two
things only the compiler owns: a fresh ``ApplyFetchBudget`` and a
``SourceSession`` over the document's ``sources`` and the bot's git
baselines. It is closed before the compiler returns, because by then every
byte worth keeping is in the content store and the checkout trees are not.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from agentclaw.community.core.bot_config_manifest.sources.budget import ApplyFetchBudget
from agentclaw.community.core.bot_config_manifest.sources.source_session import SourceSession
from agentclaw.community.core.bot_config_manifest.fetch.limits import APPLY_BUDGET_S, APPLY_FETCH_TOTAL_LIMIT
from agentclaw.community.core.content_store import ContentScope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentclaw.community.core.apply_engine.context import ApplyContext

__all__ = ["CompileSession", "digest_of"]


def digest_of(data: bytes) -> str:
    """The content-store address of ``data``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CompileSession:
    """Satisfies ``FetchContext``; carries the compile's own budget and source session."""

    bot_id: str
    entity_id: str
    owner_id: str
    env: str
    tenant: str
    engine_type: str
    actor_id: str
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
            # One ledger per compile, consulted before each entry's fetch and
            # charged after. Its deadline is a monotonic reading of this
            # process's clock, which is why it is built here and not at enqueue.
            budget=ApplyFetchBudget(deadline=time.monotonic() + APPLY_BUDGET_S, total_bytes=APPLY_FETCH_TOTAL_LIMIT),
            source_session=source_session,
        )

    @property
    def scope(self) -> ContentScope:
        return ContentScope(env=self.env, entity_id=self.entity_id, bot_id=self.bot_id)

    def close(self) -> None:
        self.source_session.close()
