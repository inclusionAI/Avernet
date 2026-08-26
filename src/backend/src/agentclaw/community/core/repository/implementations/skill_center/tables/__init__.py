"""Per-table command modules for the capability desired-state tables.

Each module here is the ONLY code that writes its table. The functions take
the session as a parameter — the UoW composes them in one transaction.
Session-owning one-repo-per-table is deliberately rejected: it would
reintroduce the eventual-atomicity bug the UoW exists to prevent.
"""
