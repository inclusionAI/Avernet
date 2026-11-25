"""Validate the optional, provider-issued HTTP proxy face independently of WS."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse


def resolve_proxy_face(
    info: "BotWsConnectionInfoResponse", *, engine_type: str,
    connection_type: str, gray_decision: tuple[str, bool],
) -> tuple[str, bool, str]:
    base, withheld = gray_decision
    auth = getattr(info, "a2a_auth", "")
    if not base or not auth:
        return base, withheld, ""
    if (
        auth == "proxypass" and engine_type == "openclaw" and connection_type != "desktop"
        and usable_proxy_face(base=base, target=info.target, token=info.token, expires_at=info.expires_at)
    ):
        return base, withheld, info.token
    # Reject an optional invalid face without breaking the working WS path.
    return "", True, ""


def usable_proxy_face(*, base: str, target: str, token: str, expires_at: str) -> bool:
    """Never infer a route or mix the credentials of different device targets."""
    try:
        url = urlsplit(base)
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return bool(
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and target.startswith("ARCA_")
            and not any(c in target for c in "/?#%\\")
            and url.path == f"/proxypass/{target}"
            and token
            and all(32 < ord(c) < 127 for c in token)
            and expiry.tzinfo is not None
            and expiry > datetime.now(timezone.utc)
        )
    except (TypeError, ValueError, AttributeError):
        return False
