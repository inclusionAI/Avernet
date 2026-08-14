"""App-domain SPI — the ``AppRegistry`` contract.

See ``_ports`` for the :class:`AppRegistry` protocol and the
:class:`RegisteredApp` record. The canonical ORM implementation lives in
``core/app``; the authn ``app_token`` strategy depends on this interface.
"""

from ._ports import AppRegistry, RegisteredApp

__all__ = [
    "AppRegistry",
    "RegisteredApp",
]
