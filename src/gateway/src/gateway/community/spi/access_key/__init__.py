"""Access-key-domain SPI — the ``AccessKeyRegistry`` contract.

See ``_ports`` for the :class:`AccessKeyRegistry` protocol and the
:class:`RegisteredAccessKey` record. The canonical ORM implementation lives in
``core/access_key``; the authn ``access_key_token`` strategy depends on this
interface.
"""

from ._ports import AccessKeyRegistry, RegisteredAccessKey

__all__ = [
    "AccessKeyRegistry",
    "RegisteredAccessKey",
]
