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


class UnsupportedEngineError(Exception):
    """Raised when a request names an engine the platform does not support (→ 400).

    Checked up front so an unknown engine is rejected before a bot id is
    allocated or a Passport is applied for — otherwise the request would create
    side effects and only fail later at device provisioning.
    """


class EngineOptionsUnsupportedError(Exception):
    """Raised when a create request supplies ``engine_options`` (→ 400).

    ``BotCreateSpec.extra_properties`` is the designated home for these values,
    but nothing downstream reads it yet, so accepting a non-empty bag would
    answer 201 while silently discarding configuration the caller explicitly
    asked for. Rejecting keeps the contract honest until the create service
    consumes them; the request field stays in the schema so nothing changes
    shape when it does.
    """
