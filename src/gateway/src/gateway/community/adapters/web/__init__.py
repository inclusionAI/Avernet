"""FastAPI Web adapter for the community gateway.

Import the app explicitly to avoid eager bootstrapping on package import::

    from gateway.community.adapters.web.app import app

Group routers depend on ``require_identities`` for authenticated access and
receive an :class:`~gateway.community.core.authn.Identities` set.
"""

from ._auth import require_identities

__all__ = [
    "require_identities",
]
