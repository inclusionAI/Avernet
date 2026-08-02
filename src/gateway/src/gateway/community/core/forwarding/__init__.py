"""Core forwarding — transport-agnostic routing + doc-generation logic.

``DomainMap`` resolves a request's leading path segment to its upstream server;
companion modules generate the served OpenAPI and check backward compatibility.
No web framework here (Rule 7).
"""

from ._compat import Breaking, check_compatible
from ._domains import Domain, DomainMap, SchemaSource, Server
from ._engine import ENGINE_PREFIX, PROXYPASS_PREFIX, EngineRoute, build_engine_route
from ._openapi import build_served_openapi, generate_openapi
from ._orchestration import Forwarding

__all__ = [
    "ENGINE_PREFIX",
    "PROXYPASS_PREFIX",
    "Breaking",
    "Domain",
    "DomainMap",
    "EngineRoute",
    "Forwarding",
    "SchemaSource",
    "Server",
    "build_engine_route",
    "build_served_openapi",
    "check_compatible",
    "generate_openapi",
]
