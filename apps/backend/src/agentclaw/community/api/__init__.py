"""Backend public API contracts.

See ocb/docs/re-architecture-proposal.md §5.1/5.2.

This package only declares the *shape* (Protocols / Pydantic schemas) of
the interfaces the backend exposes to its callers (frontend, other modules).
Concrete logic lives under ``agentclaw.community.core`` and basic capability backends
live under ``agentclaw.community.plugins``.
"""
