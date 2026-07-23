"""Bootstrap — dependency injection and application lifecycle.

The composition root: wires concrete plugins and services into the app. Adapters
import the built objects from here (e.g. the ``Authenticator``) rather than
constructing plugins themselves.
"""

from ._authn import Authenticator, build_authenticator

__all__ = [
    "Authenticator",
    "build_authenticator",
]
