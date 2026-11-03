"""Explicit opt-in composition; importing this module never enables hosting."""

from collections.abc import Callable
from pathlib import Path

from engine.community.core.dws_hosting.protocol import HostingRuntimeService
from engine.community.core.dws_hosting.connection import DwsIntake
from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.core.dws_hosting.dialogue import HostingDialogue
from engine.community.core.dws_hosting.owner_activity import HostingOwnerActivity
from engine.community.core.dws_hosting.runtime import HostingRuntime
from engine.community.core.dws_hosting.senders import HostingDeliveryRouter
from engine.community.kernel.dws_connection import IntakePolicy
from engine.community.kernel.dws_hosting import Binding, Value
from engine.community.kernel.dws_runtime import RuntimePolicy
from engine.community.plugin_api.dws_connection import DwsConnection
from engine.community.plugin_api.dws_delivery import HostingSender
from engine.community.plugin_api.dws_execution import HostingEngine, HostingFormatRepair
from engine.community.plugin_api.dws_context import HostingContextProvider
from engine.community.plugins.dws_hosting.executions import SqliteHostingExecutionStore
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore
from engine.community.plugins.dws_hosting.runtime import SqliteHostingRuntimeStore
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore


class HostingRuntimeConfig(Value):
    state_dir: Path
    bot_type: str
    engine: str
    enabled: bool = False
    storage_qualified: bool = False
    runtime: RuntimePolicy = RuntimePolicy()
    intake: IntakePolicy = IntakePolicy()


def build_dws_runtime(
    config: HostingRuntimeConfig,
    binding: Binding,
    *,
    connection: DwsConnection,
    engine: HostingEngine,
    peer_sender: HostingSender,
    owner_sender: HostingSender,
    clock_ms: Callable[[], int],
    context: HostingContextProvider | None = None,
    repair: HostingFormatRepair | None = None,
) -> HostingRuntimeService | None:
    """None means disabled: no file, migration, network call or background task.

    Deployment must qualify SQLite locks on the actual persistent mount. This
    flag is operator configuration, not a field accepted from a public request.
    Credentials/providers are supplied by the trusted container composition root.
    A future lifecycle hook explicitly awaits run(); no interactive pool is used.
    """
    if not config.enabled:
        return None
    if config.bot_type != "personal" or config.engine != "openclaw":
        raise ValueError("unsupported_hosting_bot")
    if not config.storage_qualified or not config.state_dir.is_absolute():
        raise ValueError("qualified_persistent_storage_required")
    state = SqliteHostingStateStore(config.state_dir, runtime_policy=config.runtime)
    state.bind(binding)
    recovery = SqliteHostingRecoveryStore(config.state_dir, config.runtime)
    ledger = SqliteHostingExecutionStore(config.state_dir)
    outbox = SqliteHostingOutbox(config.state_dir)
    dialogue = HostingDialogue(connection, clock_ms)
    owner_activity = HostingOwnerActivity(state, ledger, outbox, dialogue)
    return HostingRuntime(
        binding,
        state,
        recovery,
        SqliteHostingRuntimeStore(config.state_dir, config.runtime),
        DwsIntake(
            binding,
            connection,
            state,
            recovery,
            clock_ms=clock_ms,
            policy=config.intake,
        ),
        HostingExecutor(
            state,
            ledger,
            engine,
            context,
            repair,
            dialogue,
            owner_activity,
        ),
        HostingDeliveryWorker(
            outbox,
            HostingDeliveryRouter(peer_sender, owner_sender),
            clock_ms,
            owner_activity=owner_activity,
        ),
        outbox,
        clock_ms=clock_ms,
        policy=config.runtime,
    )
