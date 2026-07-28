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

    In the current pre-auth state ``require_principal`` is a stub returning
    ``None``, so every real request raises this until the gateway verifier lands.
    """
