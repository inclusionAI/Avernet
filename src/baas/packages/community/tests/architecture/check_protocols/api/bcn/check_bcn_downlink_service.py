from unittest.mock import MagicMock

from secbaas.api.bcn import BcnDownlinkService as BcnDownlinkServiceProtocol
from secbaas.core.repository.api_gateway import APIKeyRepository
from secbaas.core.repository.bot_run import BotRunRepository
from secbaas.core.service.bcn import DefaultBcnDownlinkService
from secbaas.core.service.bcn.uplink import BcnUplinkClient
from secbaas.core.service.bot_run import BotRunner

# Assign value, will trigger mypy type check
_bcn_downlink_service: BcnDownlinkServiceProtocol = DefaultBcnDownlinkService(
    bot_runner=MagicMock(spec=BotRunner),
    api_key_repository=MagicMock(spec=APIKeyRepository),
    bcn_api_key_prefix="test-prefix",
    uplink_client=MagicMock(spec=BcnUplinkClient),
    run_repository=MagicMock(spec=BotRunRepository),
)
