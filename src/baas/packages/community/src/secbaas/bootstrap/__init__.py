"""Dependency injection container hierarchy (packages tree bridge).

The packages tree (secbaas.*) mirrors the flat tree (secbaas.community.*).
This bridge module re-exports ``ApplicationContainer`` from the authoritative
flat tree so that packages tree routers can use ``from secbaas.bootstrap import``.
"""

from secbaas.community.bootstrap import ApplicationContainer  # noqa: F401

__all__ = ["ApplicationContainer"]
