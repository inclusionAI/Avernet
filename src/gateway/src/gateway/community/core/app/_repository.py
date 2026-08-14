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
import time
from collections.abc import Callable
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gateway.community.logger import get_logger
from gateway.community.spi.app import AppRegistry, RegisteredApp
from gateway.community.spi.database import DataSourcePlugin

from ._key_gen import APIKeyGenerator
from ._orm import API_KEY_PREFIX_LEN, AppRow

logger = get_logger("core-app")

_ACTIVE = "ACTIVE"

# When each (datasource, app) was last reported, for the two per-request
# conditions worth reporting: an app still presenting a legacy JWT, and a
# credential row that cannot ever authenticate. Reporting every request would
# bury both signals — one busy app emits millions of lines a day — but reporting
# only once per process is worse for the corrupt-row case, which is a live
# outage: a single line at deploy time leaves every rate-based alert reading zero
# for the hours it persists. Hence a re-report window rather than a one-shot set.
#
# Keyed on the datasource too, because ``AppRow.id`` is a per-database surrogate:
# two plugins in one process both number their apps from 1, and a bare id would
# let one silence the other. Module-level because the DI container builds this
# repository per request (``providers.Factory``), so instance state would never
# survive long enough to dedupe anything.
_REPORT_WINDOW_SECONDS = 300.0
# Keyed on the plugin object itself, not ``id()``: an address is recycled after
# collection, so a replacement datasource would inherit a dead one's window. The
# weak map also drops a plugin's entries when it goes away.
_last_reported: WeakKeyDictionary[object, dict[tuple[str, int], float]] = (
    WeakKeyDictionary()
)


class PrefixTakenError(RuntimeError):
    """The API-key prefix was claimed concurrently; generate another and retry."""


def _report_once_per_window(
    kind: str,
    datasource: object,
    record: RegisteredApp,
    log: Callable[..., None],
    message: str,
    *args: object,
) -> None:
    """Emit ``message`` for this app at most once per report window."""
    try:
        seen = _last_reported.setdefault(datasource, {})
    except TypeError:  # a datasource that cannot be weak-referenced
        log(message, *args, stacklevel=3)
        return

    key = (kind, record.id)
    now = time.monotonic()
    last = seen.get(key)
    if last is not None and now - last < _REPORT_WINDOW_SECONDS:
        return
    # Expired entries are due to re-report anyway, so dropping them here keeps
    # the map from growing for the process lifetime.
    for stale, when in list(seen.items()):
        if now - when >= _REPORT_WINDOW_SECONDS:
            del seen[stale]
    # ``stacklevel=3`` so the record names the branch that found the problem
    # rather than this helper, keeping the two conditions distinguishable to any
    # pipeline that routes on source location. Stamped only after a successful
    # emit, so a failing handler cannot consume the window silently.
    log(message, *args, stacklevel=3)
    seen[key] = now


class AppRepository(AppRegistry):
    """App table access (read + write) for ``avernet_application``.

    Resolves a presented credential (read) and persists a freshly registered app
    (write) — all DB touch lives here, never in the registrar.
    """

    Model: type[AppRow] = AppRow

    def __init__(self, db: DataSourcePlugin) -> None:
        self._db = db

    def _report_corrupt(self, record: RegisteredApp, problem: str) -> None:
        """A row that can never authenticate — a partial write or bad migration.

        Loud, because the symptom otherwise is an app failing every request
        while its row looks perfectly fine to whoever is reading the table.
        """
        _report_once_per_window(
            "corrupt-row",
            self._db,
            record,
            logger.error,
            "app credential row is unusable (%s): id=%s app_name=%s "
            "(repeated at most every %ss)",
            problem,
            record.id,
            record.app_name,
            int(_REPORT_WINDOW_SECONDS),
        )

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
            # Violates the table's one-credential-form invariant: a partial
            # write or a botched migration.
            self._report_corrupt(record, "api_key_prefix set but api_key_hash is NULL")
            return None
        if stored_hash.count(":") != 1 or not all(stored_hash.split(":")):
            # ``verify_key`` would return False here like any wrong key, so the
            # shape is checked first to tell corruption apart from a bad
            # credential. Deeper corruption (valid shape, unusable base64) still
            # reads as a plain rejection — that branch is inside the copied
            # generator, whose code cannot be edited without breaking parity.
            self._report_corrupt(record, "api_key_hash is not 'salt:digest'")
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
        while their holders rotate. Each still-unrotated app re-warns at most
        once per report window, so the log is a live census rather than a
        one-shot notice; once it stops naming any app across a full deploy
        cycle, delete this method, its branch in
        :meth:`find_app_by_credential`, ``AppRow.token``, and its unique index.
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

        _report_once_per_window(
            "legacy-jwt",
            self._db,
            record,
            logger.warning,
            "app is still authenticating with a deprecated JWT token: "
            "id=%s app_name=%s tenant=%s — rotate it onto an API key "
            "(repeated at most every %ss)",
            record.id,
            record.app_name,
            record.tenant,
            int(_REPORT_WINDOW_SECONDS),
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
            # Only a prefix collision is retryable: mapping every IntegrityError
            # would turn a NOT NULL or FK violation into three silent retries and
            # a "no unused prefix" error naming the wrong cause entirely.
            # Decided by re-reading the table rather than by matching the driver's
            # message, which differs per backend and moves with index names. The
            # re-read runs in a fresh transaction, so a collision whose winning
            # row disappeared in between would escape as a raw IntegrityError —
            # no delete path exists for app rows, so that cannot happen here.
            if not await self.exists_prefix(api_key_prefix):
                raise
            raise PrefixTakenError(api_key_prefix) from exc
