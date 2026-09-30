"""Version 1 plugin contracts. Implementations raise on failed or partial reads."""
from __future__ import annotations

from typing import Protocol


class DataSource(Protocol):
    def daily_counts(self, start: str, end: str) -> list[dict]: ...

    def ranking(self, start: str, end: str) -> list[dict]: ...

    def sessions(self, owner: str, bot: str, start: str, end: str, limit: int) -> list[dict]:
        """Return at most limit+1 latest distinct sessions, including successful ones."""
        ...


class EffectCenter(Protocol):
    def actions(self, owner: str, bot: str) -> list[dict]:
        """Return all scoped records, with pagination; never silently truncate."""
        ...

    def verification_candidates(self, lane: str, limit: int) -> list[dict]: ...

    def write(self, path: str, payload: dict, key: str) -> dict:
        """Allowlisted writes only, no automatic retries or redirects."""
        ...


class Analyst(Protocol):
    def review(self, evidence: dict) -> dict:
        """Return a JSON proposal only; no tools, commands, or state changes."""
        ...


class EvidenceStore(Protocol):
    def available(self) -> list[dict]: ...

    def inspect(self, tasks: list[dict], owner: str, bot: str) -> list[dict]: ...

    def scope_root(self, source: str, owner: str, bot: str):
        """Return a verified exact current scope or None; never substitute another instance."""
        ...

    def scan_since(self, source: str, owner: str, bot: str, since: str, max_bytes: int) -> dict:
        """Read bounded current sessions for the exact source; report incomplete coverage explicitly."""
        ...
