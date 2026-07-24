"""Core forwarding — transport-agnostic routing + doc-generation logic.

``DomainMap`` resolves a request's leading path segment to its upstream server;
companion modules generate the served OpenAPI and check backward compatibility.
No web framework here (Rule 7).
"""

from ._domains import Domain, DomainMap, SchemaSource, Server
from ._openapi import generate_openapi

__all__ = [
    "Domain",
    "DomainMap",
    "SchemaSource",
    "Server",
    "generate_openapi",
]
