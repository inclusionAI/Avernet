"""Core forwarding — transport-agnostic routing + doc-generation logic.

``DomainMap`` resolves a request's leading path segment to its upstream server;
companion modules generate the served OpenAPI and check backward compatibility.
No web framework here (Rule 7).
"""

from ._compat import Breaking, check_compatible
from ._domains import Domain, DomainMap, SchemaSource, Server
from ._openapi import build_served_openapi, generate_openapi
from ._orchestration import Forwarding

__all__ = [
    "Breaking",
    "Domain",
    "DomainMap",
    "Forwarding",
    "SchemaSource",
    "Server",
    "build_served_openapi",
    "check_compatible",
    "generate_openapi",
]
