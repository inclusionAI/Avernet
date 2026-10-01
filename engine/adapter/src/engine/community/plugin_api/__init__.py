"""Plugin API — per-engine plugin Protocol declarations.

Each engine exposes a single native-shaped Protocol here (e.g.
``OpenClawPlugin``) aggregating every operation its
``core/adapters/<engine>/`` ACL adapter delegates to. Concrete
implementations live under ``engine.plugins`` and are wired by
``engine.community.di``.

This package is the shared abstraction that both ``engine.community.core`` (via the
ACL adapters) and ``engine.plugins`` (the impls) depend on — it imports
neither, which is what keeps the dependency graph acyclic. Native value
types shared with the kernel come from ``engine.community.kernel``.

Empty skeleton in F1; per-engine ports land starting F2. See
``specs/2026-05-25-engine-arch-f1-foundation/spec.md`` → Roadmap.
"""
