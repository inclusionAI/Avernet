"""User identity models for the auth layer."""
# AuthenticatedIdentity is defined in plugin_api/auth.py (the Protocol's neutral
# identity type) and re-exported here so core consumers import it from core.auth.
from agentclaw.community.plugin_api.auth import AuthenticatedIdentity  # noqa: F401
