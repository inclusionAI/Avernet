from secbaas.community.plugins.scheduler.real import ApsSchedulerPlugin
from secbaas.community.spi.scheduler import SchedulerPlugin as SchedulerPluginProtocol

# Assign value, will trigger mypy type check
_scheduler_plugin: SchedulerPluginProtocol = ApsSchedulerPlugin(
    job_func=lambda: None,
)
