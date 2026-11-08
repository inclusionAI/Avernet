"""``script`` → ``DesiredScript``: the body, with ``${BOT_*}`` substituted."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import Declaration, DesiredScript, EntryFailure
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession
from agentclaw.community.core.bot_config_manifest.schema import placeholders

__all__ = ["compile_script"]


def compile_script(session: CompileSession, entries: Sequence[Mapping[str, Any]]) -> Declaration[DesiredScript]:
    """The document's ``script`` section → the one desired script.

    ``entries`` is the section as ``declared_entries`` hands it over: a
    one-element list holding the ``script`` mapping, ``[{"body": "#!/bin/bash\n…"}]``,
    or ``[]`` for ``script: null`` — declared, and declared to be nothing
    (#2862). An absent ``script`` never reaches here (the construct stays
    undeclared).
    """
    if not entries:
        return Declaration(())
    body = entries[0].get("body")
    if not isinstance(body, str):
        return Declaration((), failures=(EntryFailure(DesiredScript.identity, "'script.body' must be a string"),))
    # One whitelist, one resolver: the write path validates ``${...}`` names
    # against ``ALLOWED_PLACEHOLDERS`` and this substitutes the same set.
    substituted = placeholders.resolve(body, engine_type=session.engine_type, env=session.env, tenant=session.tenant)
    return Declaration((DesiredScript(substituted),))
