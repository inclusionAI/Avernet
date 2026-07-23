"""FastAPI Web adapter for the community gateway.

Import the app explicitly to avoid eager bootstrapping on package import::

    from gateway.community.adapters.web.app import app

Group routers depend on ``require_principal`` for authenticated access.
"""

from ._auth import require_principal

__all__ = [
    "require_principal",
]
