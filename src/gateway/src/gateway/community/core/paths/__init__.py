"""Core paths — the shared path-pattern grammar, matcher and ranking.

Imported by both :mod:`gateway.community.core.authn` and
:mod:`gateway.community.core.forwarding` so the auth plane and the routing plane
cannot come to rank the same two patterns differently. A public package rather
than a private module in either of them, because a cross-package import of a
private module is refused by the architecture tests — and rightly: a shared rule
with one owner and one importer is not shared, it is borrowed.
"""

from ._pattern import GLOB, PathPattern, split_segments

__all__ = ["GLOB", "PathPattern", "split_segments"]
