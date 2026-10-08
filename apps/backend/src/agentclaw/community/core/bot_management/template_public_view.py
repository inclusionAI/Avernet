"""Public view of ``ac_templates.ext`` (``template_config``).

Decision (2026-09-01): the public query faces return the stored template
snapshot **verbatim** — no allowlist filtering. The snapshot is exactly what
the bot's creation input supplied, the query faces are owner-scoped (the
caller is the owner or a delegate the owner authorized), and echoing
``token`` / ``bot_template_config.ext_config.thetaKey`` therefore echoes the
caller's own input rather than disclosing a secret. The previous allowlist
projection was removed by that same decision; reverting to filtering is a
product call, not a bug fix, and must go through it.

The one rule that survives: the result is a fresh deep copy — callers may
mutate it without ever aliasing the stored snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def template_config_for_public(
    config: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the stored template snapshot, detached, for public responses.

    ``None`` for anything that is not a mapping (a missing template row); a
    verbatim deep copy otherwise — including secrets such as ``token`` and
    ``bot_template_config.ext_config.thetaKey``, per the 2026-09-01
    passthrough decision.
    """
    if not isinstance(config, Mapping):
        return None
    return deepcopy(config)
