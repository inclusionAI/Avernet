"""Bot Runtime Dispatchers.

Concrete dispatcher implementations that resolve bot→device
then dispatch a specific action (command, HTTP, WebSocket).

Package structure follows core/service/bot_manage/ convention.
"""

from ._base_dispatcher import BotBaseDispatcher
from ._cmd_dispatcher import DefaultBotCmdDispatcher
from ._device_selector import select_active_device, select_available_device
from ._fetch_start_progress_dispatcher import DefaultBotFetchStartProgressDispatcher
from ._file_transfer_dispatcher import DefaultBotFileTransferDispatcher
from ._http_conn_info_dispatcher import DefaultBotHttpConnInfoDispatcher
from ._http_dispatcher import DefaultBotHttpDispatcher
from ._open_folder_dispatcher import DefaultBotOpenFolderDispatcher
from ._wss_dispatcher import DefaultBotWssDispatcher

__all__ = [
    "BotBaseDispatcher",
    "DefaultBotCmdDispatcher",
    "DefaultBotFetchStartProgressDispatcher",
    "DefaultBotFileTransferDispatcher",
    "DefaultBotHttpConnInfoDispatcher",
    "DefaultBotHttpDispatcher",
    "DefaultBotOpenFolderDispatcher",
    "DefaultBotWssDispatcher",
    "select_active_device",
    "select_available_device",
]
