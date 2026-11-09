"""``manifest.bot_config`` → ``DesiredBotConfig``: the kind mapping, carried.

The mapping a copy serialised — kind → entry list, ``render_screen`` in v1 —
becomes one ``DesiredBotConfig`` whose ``config`` is that mapping, frozen by
the dataclass's ``__post_init__``. Nothing is fetched: each kind's entries
are plain ``{name, cdn_url, …}`` dicts the handler reads at apply, not content
to store. The validator constrained the kind set and each kind's entry shape
at ``PUT``, so a raw mapping that reached compile is one the handlers can act
on — the same property ``engine_config``'s compile keeps, only here the
mapping is the kind set rather than an engine-owned config blob.

Mirrors :func:`compile_engine_config`: a mapping carries the whole construct
on one entry, a non-mapping is a document that skipped the validator and is
refused under the construct's identity.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredBotConfig,
    EntryFailure,
)
from agentclaw.community.core.apply_engine.outcomes import entry_identity

__all__ = ["compile_bot_config"]


def compile_bot_config(raw: Sequence[Any] | Mapping[str, Any] | None) -> Declaration[DesiredBotConfig]:
    """The validated form is one mapping, kind → entry list.

    A non-mapping is a document that skipped the validator; refused by the
    construct's identity. ``None`` → an empty declaration (undecared).
    """
    if raw is None:
        return Declaration(())
    if isinstance(raw, Mapping):
        # Strip empty kinds in pass: a kind declared with ``[]`` is "overwrite to
        # empty" against the destination, which the handler honours; a kind key
        # carrying a non-list or a list that failed the validator's shape is
        # already refused at PUT and never reaches compile. The mapping is
        # carried as-is so the handler sees the same kind set the validator
        # accepted, and so a future ``task_nav`` kind needs no change here.
        return Declaration((DesiredBotConfig(raw),))
    reason = "'bot_config' must be a mapping of kind to entry list"
    return Declaration((), failures=tuple(EntryFailure(entry_identity(e, i), reason) for i, e in enumerate(raw)))
