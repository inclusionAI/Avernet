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

from typing import Any

from sqlalchemy import select

from gateway.community.logger import get_logger
from gateway.community.spi.app import AppRegistry, RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

from ._key_gen import APIKeyGenerator
from ._orm import AppRow

logger = get_logger("core-app")

_ACTIVE = "ACTIVE"

# The API-key prefix length, and the shortest credential worth a lookup.
_PREFIX_LEN = 8


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
        if not credential or len(credential) < _PREFIX_LEN:
            return None  # too short to carry a prefix — reject without a query
        if APIKeyGenerator.validate_format(credential):
            return self._by_api_key(credential)
        return self._by_legacy_token(credential)

    def _by_api_key(self, api_key: str) -> RegisteredApp | None:
        """Locate by prefix, then verify the hash in constant time.

        The prefix is the lookup key because the stored hash is salted: deriving
        it requires the row's own salt, which requires already having the row.
        """
        with self._db.orm_session() as session:
            row = session.scalar(
                select(self.Model).where(
                    self.Model.api_key_prefix == api_key[:_PREFIX_LEN],
                    self.Model.status == _ACTIVE,
                )
            )
            if row is None:
                return None
            # Read both inside the session: attribute access on an expired row
            # after the block would raise DetachedInstanceError.
            stored_hash, record = row.api_key_hash, row.to_record()

        if stored_hash is None:
            return None  # an api-key prefix with no hash is a malformed row
        # Verify outside the session — PBKDF2 is ~60ms of CPU, and there is no
        # reason to hold a pooled connection across it.
        if not APIKeyGenerator.verify_key(api_key, stored_hash):
            return None
        return record

    def _by_legacy_token(self, token: str) -> RegisteredApp | None:
        """DEPRECATED — resolve a pre-API-key app by its plaintext JWT.

        Kept only so credentials issued before the API-key scheme keep working
        while their holders rotate. Every hit logs at WARNING; once that log
        goes quiet in production, delete this method, its branch in
        :meth:`find_app_by_credential`, ``AppRow.token``, and the column's
        unique index.
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

        logger.warning(
            "app authenticated with a deprecated JWT token: "
            "id=%s app_name=%s tenant=%s — rotate this app onto an API key",
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
        passed here and never stored. Optional ``status`` / ``env`` / ``config``
        default to ``ACTIVE`` / ``""`` / ``{}``; ``creator`` / ``modifier``
        default to ``""`` (the unauthenticated admin has no caller).
        """
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
