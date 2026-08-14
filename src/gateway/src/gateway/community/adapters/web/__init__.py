"""FastAPI Web adapter for the community gateway.

Import the app explicitly to avoid eager bootstrapping on package import::

    from gateway.community.adapters.web.app import app

Group routers depend on ``require_principal`` for authenticated access.

``WebsocketsForwarder`` is the outbound socket transport the composition root
wires onto ``app.state`` — re-exported here because it is what the ``/engine``
relay dials with, and the layer that speaks protocols is where a socket library
belongs.
"""

from ._auth import require_principal
from ._ws_forwarder import WebsocketsForwarder

__all__ = [
    "WebsocketsForwarder",
    "require_principal",
]
