"""Core forwarding — transport-agnostic routing + doc-generation logic.

``DomainMap`` resolves a request's leading path segment to its upstream server,
along with the protocols that domain answers and any declared path rewrite;
companion modules generate the served OpenAPI and check backward compatibility.
No web framework here (Rule 7).
"""

from ._compat import Breaking, check_compatible
from ._domains import (
    HTTP,
    WEBSOCKET,
    Domain,
    DomainMap,
    PathRewrite,
    SchemaSource,
    Server,
    websocket_base_url,
)
from ._openapi import build_served_openapi, generate_openapi
from ._orchestration import Forwarding

__all__ = [
    "HTTP",
    "WEBSOCKET",
    "Breaking",
    "Domain",
    "DomainMap",
    "Forwarding",
    "PathRewrite",
    "SchemaSource",
    "Server",
    "build_served_openapi",
    "check_compatible",
    "generate_openapi",
    "websocket_base_url",
]
