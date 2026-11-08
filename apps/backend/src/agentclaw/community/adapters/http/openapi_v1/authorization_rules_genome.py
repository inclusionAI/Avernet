"""Authorization rows of ``/openapi/v1/bots/{bot_id}/genome`` (RSI-03), merged into ``AUTHORIZATION``.

Reads sit at MEMBER beside ``GET …/config-manifest``: reading how a bot is
defined is part of working on it. Recording (a patch, a capture, an upload,
the draft ref) is ADMIN like ``PUT …/config-manifest``: it writes the
registry, not the bot. The policy is OWNER because it bounds what automation
may do to the bot. Promotion takes apply's own bar — OWNER behind the edit
lock — because it is the one operation that reaches the running bot
(``test_config_manifest_apply_bars.py`` pins that dominance).
"""

from agentclaw.community.core.bot_collaborator.models import PermissionLevel

from .models_authorization import EDIT_LOCK, Authorization, Check

_GENOME = "/openapi/v1/bots/{bot_id}/genome"

GENOME_AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("GET", f"{_GENOME}/revisions"): Check(PermissionLevel.MEMBER),
    ("POST", f"{_GENOME}/revisions"): Check(PermissionLevel.ADMIN),
    ("POST", f"{_GENOME}/revisions:capture"): Check(PermissionLevel.ADMIN),
    ("GET", f"{_GENOME}/revisions/{{rev:path}}/diff"): Check(PermissionLevel.MEMBER),
    ("GET", f"{_GENOME}/revisions/{{rev:path}}"): Check(PermissionLevel.MEMBER),
    ("PUT", f"{_GENOME}/policy"): Check(PermissionLevel.OWNER),
    ("GET", f"{_GENOME}/refs"): Check(PermissionLevel.MEMBER),
    ("PUT", f"{_GENOME}/refs/draft"): Check(PermissionLevel.ADMIN),
    ("GET", f"{_GENOME}/content/{{digest}}"): Check(PermissionLevel.MEMBER),
    ("PUT", f"{_GENOME}/content"): Check(PermissionLevel.ADMIN),
    ("POST", f"{_GENOME}/promotions"): Check(PermissionLevel.OWNER, EDIT_LOCK),
    ("GET", f"{_GENOME}/promotions"): Check(PermissionLevel.MEMBER),
    ("GET", f"{_GENOME}/promotions/{{promotion_id}}"): Check(PermissionLevel.MEMBER),
}
