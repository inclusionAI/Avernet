"""Internal records for the secbaas migration — core-side, never on the wire.

Distinct from ``api/baas_migration``: those types describe what a *caller*
learns, these describe what the repository read and what the migrator asked it
to write. Keeping them apart is what lets the source row carry fields the
response must never echo — its hash above all.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceKey:
    """An ACTIVE ``baas_api_key`` row whose plaintext key the caller proved.

    ``api_key_hash`` travels with it because the migration *copies* it: the
    gateway's credential scheme is a byte-identical copy of secbaas's, so
    re-using the stored hash is what makes the caller's existing key keep
    working. Re-hashing the plaintext would work equally well and is
    deliberately not done — it would leave the gateway free to drift onto a
    different scheme without anything failing until every migrated key stopped
    verifying at once.
    """

    id: int
    api_key_hash: str
    api_key_prefix: str
    app_id: str
    app_type: str | None
    owner: str
    tenant: str | None
    env: str
    creator: str
    modifier: str | None
    policy: str | None


@dataclass(frozen=True)
class GrantTarget:
    """One (bot, delegating user) pair a migrated key is entitled to.

    ``user_id`` and ``owner_id`` are separate fields even though this migration
    always sets them to the same person. The distinction is the destination
    table's, not ours: ``user_id`` is whose access is lent and ``owner_id`` is
    who owns the bot, and they diverge for grants made through the backend's own
    surface. Collapsing them here would make a migrated row structurally
    different from every other row in that table.
    """

    bot_id: str
    user_id: str
    owner_id: str
