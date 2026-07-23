"""Core authn — transport-agnostic route-security resolution + auth runner.

``RouteSecurity`` resolves a request to its required strategies; ``authenticate``
runs them against the strategy registry to produce a Principal. Neither depends
on any web framework (Rule 7).
"""

from ._route_security import Requirement, RouteSecurity
from ._runner import authenticate

__all__ = [
    "Requirement",
    "RouteSecurity",
    "authenticate",
]
