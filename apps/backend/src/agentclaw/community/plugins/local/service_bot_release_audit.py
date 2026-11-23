"""Local audit plugin participates in the same SQLite publication transaction."""
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.community.service_bot_release_audit import DatabaseServiceBotReleaseAudit


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.SIMULATOR,
             rationale="audit writes in the caller-owned SQLite transaction")
class LocalDatabaseServiceBotReleaseAudit(DatabaseServiceBotReleaseAudit):
    """Keep local and production audit persistence behavior identical."""
