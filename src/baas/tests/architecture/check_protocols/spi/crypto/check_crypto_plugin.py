from unittest.mock import MagicMock

from secbaas.community.plugins.crypto.real import RealCryptoPlugin
from secbaas.community.spi.crypto import CryptoPlugin as CryptoPluginProtocol

# Assign value, will trigger mypy type check
_crypto_plugin: CryptoPluginProtocol = RealCryptoPlugin(
    secret_store=MagicMock(),
    env="dev",
)
