"""Local audit plugin exercises the same database projection with SQLite."""
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.SIMULATOR,
             rationale="real audit projection against the local SQLite DatabasePlugin")
class LocalDatabaseServiceBotReleaseAudit(DatabaseServiceBotReleaseAudit):
    """Keep local and production audit persistence behavior identical."""
