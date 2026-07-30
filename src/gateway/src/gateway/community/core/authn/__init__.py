"""Core authn — transport-agnostic route-security resolution + auth runner.

``RouteSecurity`` resolves a request to its required identities; the
:class:`IdentityChain` runs each identity's ordered plugin chain; ``authenticate``
ties the chains to the route requirement to produce a Principal set. None of
this depends on any web framework (Rule 7).
"""

from ._authenticator import Authenticator
from ._chain import IdentityChain
from ._route_security import Requirement, RouteSecurity
from ._runner import authenticate

__all__ = [
    "Authenticator",
    "IdentityChain",
    "Requirement",
    "RouteSecurity",
    "authenticate",
]
