# DEPRECATED: Use bootstrap/RepositoryContainer providers instead.
# Repository instances are now managed by the DI container.
# See src/secbaas/bootstrap/repository.py

"""Factory for WS relay session repository."""

from ._protocol import WsRelaySessionRepository


def get_ws_relay_session_repository() -> WsRelaySessionRepository:
    from secbaas.community.bootstrap import get_container

    return get_container().repository.ws_relay_session_repository()
