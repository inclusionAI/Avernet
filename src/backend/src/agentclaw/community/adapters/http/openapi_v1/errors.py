"""Public-API error types with no heavy imports.

Kept dependency-free so the lightweight schema / cluster modules can raise these
without pulling in the service layer (which would create an import cycle). The
error → HTTP/envelope mapping lives in ``responses.py``.
"""

from __future__ import annotations


class ClusterMismatchError(Exception):
    """Raised when a request's ``engine`` and ``cluster_name`` violate the rule.

    The public cluster enum is in strict bijection with the engine (``ANDC`` for
    ``teclaw``, ``ACRA`` for everything else); a pair that breaks it is a client
    error mapped to 400.
    """


class MissingPrincipalError(Exception):
    """Raised when a public request has no authenticated caller (→ 401).

    One error for two causes, deliberately: no ``X-Avernet-Principal`` header at
    all, and a header whose token failed verification. ``require_principal``
    funnels both here so the caller cannot tell them apart — see
    ``openapi_v1/dependencies.py``. Which one it was is logged, with the reason,
    at the point of failure.

    ``app.py`` registers a handler for this type directly rather than letting
    the catch-all answer it; the docstring there explains why that placement
    matters.
    """


class UnsupportedEngineError(Exception):
    """Raised when a request names an engine the platform does not support (→ 400).

    Checked up front so an unknown engine is rejected before a bot id is
    allocated or a Passport is applied for — otherwise the request would create
    side effects and only fail later at device provisioning.
    """
