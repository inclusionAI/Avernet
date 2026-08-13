"""``AppRepository`` — canonical ORM third-party-app registry.

One ORM implementation behind the
:class:`~gateway.community.spi.app.AppRegistry` SPI port. Resolves a presented
credential via the ``avernet_application`` table through the
:data:`~gateway.community.spi.database.DataSourcePlugin`'s sync ``orm_session``,
mapping the row to the SPI :class:`~gateway.community.spi.app.RegisteredApp` via
:meth:`AppRow.to_record`. Flavor-neutral — the ``DataSourcePlugin`` (bare
in-memory SQLite or a sofa real DB) is injected by the composition root, so this
single body runs unchanged across runtimes (mirrors ``BotRepository`` /
``AccessKeyRepository``).

Verification lives here rather than in the authn strategy: the strategy is
handed an opaque credential and gets back an app or nothing, so how credentials
are stored stays a storage concern.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gateway.community.logger import get_logger
from gateway.community.spi.app import AppRegistry, RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

from ._key_gen import APIKeyGenerator
from ._orm import API_KEY_PREFIX_LEN, AppRow

logger = get_logger("core-app")

_ACTIVE = "ACTIVE"

# App ids already reported as still presenting a legacy JWT. Warning once per
# app rather than once per request keeps the deprecation signal readable: the
# question is *which* apps still need rotating, and one busy app would otherwise
# emit millions of lines a day and bury a quiet one. Module-level because the DI
# container builds this repository per request (``providers.Factory``), so
# instance state would never survive long enough to dedupe anything.
_warned_legacy_apps: set[int] = set()


class PrefixTakenError(RuntimeError):
    """The API-key prefix was claimed concurrently; generate another and retry."""


class AppRepository(AppRegistry):
    """App table access (read + write) for ``avernet_application``.

    Resolves a presented credential (read) and persists a freshly registered app
    (write) — all DB touch lives here, never in the registrar.
    """

    Model: type[AppRow] = AppRow

    def __init__(self, db: DataSourcePlugin) -> None:
        self._db = db

    async def find_app_by_credential(self, credential: str) -> RegisteredApp | None:
        """Resolve a presented credential to its app, or ``None`` (soft miss).

        Two credential forms are in play during the transition window, and they
        are told apart by *format*, not by trying one and falling back: an API
        key is 32 base62 characters and a JWT always contains ``.``, so the two
        sets cannot overlap. Each call therefore makes exactly one query, and
        neither form can be silently served by the other's path.
        """
        if not credential or len(credential) < API_KEY_PREFIX_LEN:
            return None  # too short to carry a prefix — reject without a query
        if APIKeyGenerator.validate_format(credential):
            return await self._by_api_key(credential)
        return self._by_legacy_token(credential)

    async def _by_api_key(self, api_key: str) -> RegisteredApp | None:
        """Locate by prefix, then verify the hash in constant time.

        The prefix is the lookup key because the stored hash is salted: deriving
        it requires the row's own salt, which requires already having the row.
        """
        with self._db.orm_session() as session:
            row = session.scalar(
                select(self.Model).where(
                    self.Model.api_key_prefix == api_key[:API_KEY_PREFIX_LEN],
                    self.Model.status == _ACTIVE,
                )
            )
            if row is None:
                return None
            # Read both inside the session: attribute access on an expired row
            # after the block would raise DetachedInstanceError.
            stored_hash, record = row.api_key_hash, row.to_record()

        if stored_hash is None:
            # Violates the table's one-credential-form invariant — a partial
            # write or a botched migration. Logged loudly, because the symptom
            # otherwise is an app that cannot authenticate while its row looks
            # perfectly fine to whoever is reading the table.
            logger.error(
                "app row has an api_key_prefix but no api_key_hash: id=%s app_name=%s",
                record.id,
                record.app_name,
            )
            return None

        # PBKDF2 is ~30ms of CPU and must not run on the event loop: measured
        # inline, ten concurrent verifications stall every other coroutine for
        # the full ~300ms — unrelated user traffic, WebSocket relays, health
        # checks alike. ``pbkdf2_hmac`` releases the GIL, so a worker thread also
        # lets concurrent verifications genuinely overlap.
        verified = await asyncio.to_thread(
            APIKeyGenerator.verify_key, api_key, stored_hash
        )
        return record if verified else None

    def _by_legacy_token(self, token: str) -> RegisteredApp | None:
        """DEPRECATED — resolve a pre-API-key app by its plaintext JWT.

        Kept only so credentials issued before the API-key scheme keep working
        while their holders rotate. The first hit from each app logs at WARNING;
        once that log stops appearing across a full deploy cycle, delete this
        method, its branch in :meth:`find_app_by_credential`, ``AppRow.token``,
        and the column's unique index.
        """
        with self._db.orm_session() as session:
            row = session.scalar(
                select(self.Model).where(
                    self.Model.token == token,
                    self.Model.status == _ACTIVE,
                )
            )
            if row is None:
                return None
            record = row.to_record()

        if record.id not in _warned_legacy_apps:
            _warned_legacy_apps.add(record.id)
            logger.warning(
                "app is still authenticating with a deprecated JWT token: "
                "id=%s app_name=%s tenant=%s — rotate it onto an API key "
                "(logged once per app per process)",
                record.id,
                record.app_name,
                record.tenant,
            )
        return record

    async def exists_prefix(self, api_key_prefix: str) -> bool:
        """Whether any app already claims this API-key prefix."""
        with self._db.orm_session() as session:
            found = session.scalar(
                select(self.Model.id).where(self.Model.api_key_prefix == api_key_prefix)
            )
        return found is not None

    async def store(
        self,
        *,
        api_key_hash: str,
        api_key_prefix: str,
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
        status: str = _ACTIVE,
        env: str = "",
        config: dict[str, Any] | None = None,
        creator: str = "",
        modifier: str = "",
    ) -> int:
        """Persist a freshly registered app; return its inserted surrogate ``id``.

        Takes the hashed credential and its prefix — the plaintext key is never
        passed here and never stored. Raises :class:`PrefixTakenError` when the
        prefix was claimed between the caller's check and this insert, so the
        caller can retry with a fresh key: the unique index, not the check, is
        what actually guarantees uniqueness. Optional ``status`` / ``env`` /
        ``config`` default to ``ACTIVE`` / ``""`` / ``{}``; ``creator`` /
        ``modifier`` default to ``""`` (the unauthenticated admin has no caller).
        """
        try:
            with self._db.orm_session() as session:
                row = AppRow(
                    api_key_hash=api_key_hash,
                    api_key_prefix=api_key_prefix,
                    app_name=app_name,
                    app_type=app_type,
                    owners=owners,
                    tenant=tenant,
                    status=status,
                    env=env,
                    config={} if config is None else config,
                    creator=creator,
                    modifier=modifier,
                )
                session.add(row)
                session.flush()
                return row.id
        except IntegrityError as exc:
            raise PrefixTakenError(api_key_prefix) from exc
