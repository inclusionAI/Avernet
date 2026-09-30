"""Backend plugin implementations.

Two flavors:
- ``local``: singlebox / dev mode (no MOSN, SQLite, in-memory cache, ...).
- ``prod``: production mode (Buservice, ZCache, ZDAS, OSS, Arca, ...).

The application bootstrap selects the flavor based on runtime mode and
wires it into ``agentclaw.community.core``. Skeleton only — see ``README.md``.
"""
