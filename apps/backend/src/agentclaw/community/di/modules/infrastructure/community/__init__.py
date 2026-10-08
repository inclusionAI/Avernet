"""Community infrastructure column — the ``community`` profile's per-concern modules.

This subpackage is the **only** infrastructure column shipped in the community
distribution. It must never import ``plugins.prod`` / ``plugins.local`` (nor any
company-internal package) at module level or inside its bindings — selecting the
``community`` profile loads only this subpackage, keeping the community build
free of internal references. Concrete bindings land per-concern in B2–B7.
"""
