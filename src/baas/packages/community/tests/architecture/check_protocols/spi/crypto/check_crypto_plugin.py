from unittest.mock import MagicMock

from secbaas.plugins.crypto.real import RealCryptoPlugin
from secbaas.spi.crypto import CryptoPlugin as CryptoPluginProtocol

# Assign value, will trigger mypy type check
_crypto_plugin: CryptoPluginProtocol = RealCryptoPlugin(
    secret_store=MagicMock(),
    env="dev",
)
