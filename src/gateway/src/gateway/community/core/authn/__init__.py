"""Core authn: the runner, the route table, and the chain-config resolver.

``Identities`` lives in the authn SPI (``gateway.community.spi.authn``) since
adapters consume it as a boundary contract; it is re-exported here for
convenience within core.
"""

from gateway.community.spi.authn import Identities

from ._config import build_strategy_registry, load_chains
from ._route_security import RouteSecurity
from ._runner import authenticate

__all__ = [
    "Identities",
    "RouteSecurity",
    "authenticate",
    "build_strategy_registry",
    "load_chains",
]
