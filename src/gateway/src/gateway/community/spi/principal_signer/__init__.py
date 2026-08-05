"""Principal-signer SPI — the ``PrincipalSigner`` contract.

See ``_ports`` for the protocol. The canonical bare (HMAC) impl lives in
``plugins/principal_signer/bare``; the adapter injects the built signer and
calls it at the forwarder seam.
"""

from ._ports import PrincipalSigner

__all__ = ["PrincipalSigner"]
