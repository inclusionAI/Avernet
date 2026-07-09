"""Bot Run Application 模块

提供 Bot 对话的用例编排和 Binding 解析。
"""

from secbaas.core.repository.bot_run_queue import (
    BotRunQueueRecord,
    QueueStatus,
)

from ._async_chat_client import (
    AsyncChatClient,
    ConcurrentSessionError,
    NotConnectedError,
)
from ._async_chat_client_pool import AsyncChatClientPool
from ._async_session_client import AsyncSessionClient
from ._baas_service import BaasBotService, BaasBotServiceConfig
from ._bot_binding_resolver import BotBindingResolver
from ._bot_concurrency import BotConcurrencyManager, FixedMachineCountProvider
from ._bot_run_utils import (
    binding_data_to_info,
    extract_lifecycle_stage,
    extract_session_id_from_record,
    parse_bot_id,
    parse_wait_result,
    resolve_bot_id,
    resolve_user_id,
)
from ._bot_service_selector import BotServiceSelector
from ._bot_websocket_client import BotWebSocketClient
from ._claw_service import BotServiceConfig, ClawBotService
from ._engine_adapter_registry import BotEngineAdapterRegistry
from ._executor import BotRunRequestExecutor, SerializingExecutor
from ._internal_protocols import (
    BotService,
    MessageDispatcher,
    PostRunCallback,
)
from ._noop_message_dispatcher import NoopMessageDispatcher
from ._queue_task_message_dispatcher import QueueTaskMessageDispatcher
from ._runner import BotBindingNotFoundError, BotRunner
from ._session_key_matcher import SessionKeyMatcher
from ._task_concurrency_pool import TaskConcurrencyPool, TaskConcurrencySlot
from ._task_message_dispatcher import TaskMessageDispatcher
from ._worker import BotRequestWorker, BotRequestWorkerConfig

__all__ = [
    "AsyncChatClient",
    "AsyncChatClientPool",
    "AsyncSessionClient",
    "BaasBotService",
    "BaasBotServiceConfig",
    "BotBindingNotFoundError",
    "BotServiceConfig",
    "BotWebSocketClient",
    "ClawBotService",
    "ConcurrentSessionError",
    "NotConnectedError",
    "BotBindingResolver",
    "binding_data_to_info",
    "BotRunner",
    "BotService",
    "BotServiceSelector",
    "BotEngineAdapterRegistry",
    "MessageDispatcher",
    "NoopMessageDispatcher",
    "QueueTaskMessageDispatcher",
    "TaskConcurrencyPool",
    "TaskConcurrencySlot",
    "TaskMessageDispatcher",
    "SessionKeyMatcher",
    "resolve_user_id",
    "parse_bot_id",
    "resolve_bot_id",
    "extract_lifecycle_stage",
    "extract_session_id_from_record",
    "parse_wait_result",
    "BotRequestWorker",
    "BotRequestWorkerConfig",
    "BotConcurrencyManager",
    "FixedMachineCountProvider",
    "BotRunRequestExecutor",
    "BotRunQueueRecord",
    "PostRunCallback",
    "QueueStatus",
    "SerializingExecutor",
]
