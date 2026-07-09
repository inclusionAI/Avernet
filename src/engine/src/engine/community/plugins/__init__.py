"""Plugins — concrete implementations of the per-engine plugin Protocols.

Implementations of the ``engine.community.plugin_api`` ports live under
``community/plugins/<engine>/`` (real transport / community impls) and
``community/local/<engine>/`` (test doubles); ``corp/plugins/<engine>/`` holds
corp-only impls. Only ``engine.community.di`` imports these packages: it is the
composition root that binds each port Protocol to its concrete impl.

Leaf rule: code here must NOT import ``engine.community.core`` or
``engine.community.api``. Anything shared with ``core`` lives in
``engine.community.kernel`` instead.
"""