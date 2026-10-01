"""Every rule the public surface enforces for one config category, in one list.

The five categories manifest apply touches — ``identity``, ``resources``,
``skills``, ``mcp``, ``engine_config`` — each enforced their rules inside the
handler bodies of their own ``openapi_v1`` router, where nothing but a request
could reach them. This names what governs each, now that each lives in ``core``.

**This module is an index. It must not grow logic.** Every callable named below
is defined in the package that owns that category's domain, and is imported here
by reference. A rule implemented *in* this file would be a rule the router does
not run, which is the drift the whole arrangement exists to prevent.

The guarantee is not that these names exist — it is that they are the **same
objects** the routers call. ``tests/community/core/bot_config_surface/`` pins
that with ``is``, so a router growing a private copy of one fails a test rather
than quietly diverging.

Two fields for coordinates rather than one, because they have different
requirements. ``from_record`` reads a bot record. ``from_spec`` reads a create
request's parameters, for the create path, where the manifest is validated at
preflight and no bot record exists yet
(``core/bot_management/create_flow.py``). Both return
:class:`~.coords.BotConfigCoords`, so a validator handed one cannot tell which
it came from — which is what lets one validation path serve both entries.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.bot_config_surface.coords import BotConfigCoords
from agentclaw.community.core.mcp.config_flow import (
    mcp_coords_from_record,
    mcp_coords_from_spec,
)
from agentclaw.community.core.services.engine_config import (
    engine_config_coords_from_record,
    engine_config_coords_from_spec,
)
from agentclaw.community.core.services.identity import (
    identity_coords_from_record,
    identity_coords_from_spec,
    identity_physical_file_name,
)
from agentclaw.community.core.services.resource_file_service import (
    is_write_forbidden,
    require_workspace_path,
    resource_coords_from_record,
    resource_coords_from_spec,
    safe_workspace_path,
)
from agentclaw.community.core.skill_center.services.skill_query_service import (
    require_addressed_bot,
    skill_coords_from_record,
    skill_coords_from_spec,
)


@dataclass(frozen=True)
class CategoryChecks:
    """What governs one config category.

    ``validators`` are record-free by contract: given values, they answer. That
    is what lets the create path run them at preflight, and the record-free test
    is what holds them to it.
    """

    category: str
    from_record: Callable[..., BotConfigCoords]
    from_spec: Callable[..., BotConfigCoords]
    validators: tuple[Callable[..., Any], ...]


#: The categories manifest apply touches, each exactly once.
#:
#: ``engine_config`` carries a row although W4 excludes it from the first phase
#: (§4, X2/T3): the seam is where it plugs in when its materializer returns, and
#: a row missing then is a row nobody remembers is missing.
#:
#: ``mcp``'s ``validators`` is empty, and that is a finding rather than a gap.
#: Its router held no domain policy to move — the permission rule for a
#: ``server_code`` already lives in ``DirectActivationService``, and the unified
#: config flow already lives in ``core.mcp.config_flow``. The category the
#: manifest work most expected to need this seam turned out to have arrived
#: already.
CONFIG_SURFACE: dict[str, CategoryChecks] = {
    "identity": CategoryChecks(
        category="identity",
        from_record=identity_coords_from_record,
        from_spec=identity_coords_from_spec,
        validators=(identity_physical_file_name,),
    ),
    "resources": CategoryChecks(
        category="resources",
        from_record=resource_coords_from_record,
        from_spec=resource_coords_from_spec,
        validators=(
            safe_workspace_path,
            require_workspace_path,
            is_write_forbidden,
        ),
    ),
    "skills": CategoryChecks(
        category="skills",
        from_record=skill_coords_from_record,
        from_spec=skill_coords_from_spec,
        validators=(require_addressed_bot,),
    ),
    "mcp": CategoryChecks(
        category="mcp",
        from_record=mcp_coords_from_record,
        from_spec=mcp_coords_from_spec,
        validators=(),
    ),
    "engine_config": CategoryChecks(
        category="engine_config",
        from_record=engine_config_coords_from_record,
        from_spec=engine_config_coords_from_spec,
        validators=(),
    ),
}

__all__ = ["CONFIG_SURFACE", "CategoryChecks"]
