"""``BaasMigrationRepository`` — every database touch the migration makes.

Reads the secbaas key table, and writes the gateway's application row together
with the backend's grant rows **in one transaction**.

That single transaction is why this write does not go through
:class:`~gateway.community.core.app.AppRepository`, which otherwise owns every
``avernet_application`` insert. ``DataSourcePlugin.orm_session()`` commits when
its block exits, so routing the application row through that repository and the
grants through this one would produce two commits with a window between them —
and a failure in that window leaves a live credential authorizing nothing, which
looks to its holder exactly like a working migration. There is no compensating
delete to fall back on: nothing in the gateway deletes app rows. So the unit of
work is the whole migration, and it lives here.

The classification of a failed insert mirrors ``AppRepository.store`` and for
the same reason: which unique key was hit is decided by re-reading the table,
not by matching a driver message that differs per backend and moves with index
names.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gateway.community.api.app_registration import AppNameTakenError
from gateway.community.core.app import APIKeyGenerator, AppRow
from gateway.community.logger import get_logger
from gateway.community.spi.database import DataSourcePlugin

from ._orm import (
    BAAS_API_KEY_PREFIX_LEN,
    BaasApiKeyRow,
    BotAppGrantLogRow,
    BotAppGrantRow,
)
from ._records import GrantTarget, SourceKey

logger = get_logger("baas-migration")

_ACTIVE = "ACTIVE"

#: What a migration writes into ``ac_bot_app_grant_log.action``. The backend's
#: :class:`GrantAction` vocabulary, which has no separate "migrated" verb —
#: correctly, because this *is* a grant coming into force. Where it came from is
#: recorded on the application row's ``config``, not by inventing an action the
#: backend's readers would not recognise.
_GRANT_ACTION = "granted"


class AlreadyMigratedError(RuntimeError):
    """This exact key already has an ``avernet_application`` row.

    Established by hash equality, not by the prefix alone — see
    :meth:`BaasMigrationRepository.find_app_holding_prefix`.
    """

    def __init__(self, app_id: int, app_name: str, env: str) -> None:
        super().__init__(f"key already migrated as app id={app_id}")
        self.app_id = app_id
        self.app_name = app_name
        self.env = env


class PrefixConflictError(RuntimeError):
    """A *different* application already holds this key's 8-character prefix."""

    def __init__(self, api_key_prefix: str) -> None:
        super().__init__(f"api_key_prefix {api_key_prefix!r} belongs to another app")
        self.api_key_prefix = api_key_prefix


class BaasMigrationRepository:
    """Read secbaas keys; write the migrated application and its grants."""

    def __init__(self, db: DataSourcePlugin) -> None:
        self._db = db

    async def find_active_key(self, api_key: str) -> SourceKey | None:
        """Resolve a presented plaintext key to its ACTIVE secbaas row.

        Prefix first, then hash — the stored hash is salted PBKDF2, so deriving
        it needs the row's own salt, which needs the row. ``status`` is filtered
        in the query rather than after it: an INACTIVE or REVOKED key is not a
        key, and reporting it as one would migrate a credential its owner had
        already retired.

        Returns ``None`` for both "no such prefix" and "prefix found, hash did
        not verify". The caller must keep the two indistinguishable.

        No format pre-check, deliberately: secbaas's own ``DefaultAPIKeyValidator``
        gates on ``len >= 8`` and nothing more, and adding ``validate_format``
        here would refuse to migrate any key that predates the current 32-base62
        shape while secbaas itself still accepts it.

        **Divergence from that validator, and the one that matters:** it also
        pins the lookup to the process's own ``env``, so that a shared database
        cannot let one environment's key authenticate in another. This does not,
        because the gateway's core layer has no injected notion of its own
        environment (``SERVER_ENV`` is read in ``config`` / ``bootstrap`` /
        ``plugins`` only, by the layer rules) — and because the registry this
        migrates *into* does not filter on ``env`` either: ``AppRepository``
        resolves a credential by prefix and status alone. Adding the filter on
        this side would therefore narrow who may migrate without narrowing who
        may authenticate, which is the wrong half. The consequence to be aware
        of is that a key holder may migrate through any environment's gateway,
        with the source row's ``env`` copied faithfully either way. Pin both
        sides together if the gateway ever gains a configured environment.
        """
        if not api_key or len(api_key) < BAAS_API_KEY_PREFIX_LEN:
            return None  # too short to carry a prefix — reject without a query

        with self._db.orm_session() as session:
            row = session.scalar(
                select(BaasApiKeyRow).where(
                    BaasApiKeyRow.api_key_prefix == api_key[:BAAS_API_KEY_PREFIX_LEN],
                    BaasApiKeyRow.status == _ACTIVE,
                )
            )
            if row is None:
                return None
            # Everything is read inside the session: attribute access on an
            # expired row after the block raises DetachedInstanceError.
            record = SourceKey(
                id=row.id,
                api_key_hash=row.api_key_hash,
                api_key_prefix=row.api_key_prefix,
                app_id=row.app_id,
                app_type=row.app_type,
                owner=row.owner,
                tenant=row.tenant,
                env=row.env,
                creator=row.creator,
                modifier=row.modifier,
                policy=row.policy,
            )

        # ~30ms of PBKDF2, off the event loop for the reason ``AppRepository``
        # gives: run inline it stalls every other coroutine in the process, and
        # ``pbkdf2_hmac`` releases the GIL so a worker thread genuinely overlaps.
        verified = await asyncio.to_thread(
            APIKeyGenerator.verify_key, api_key, record.api_key_hash
        )
        return record if verified else None

    async def find_app_holding_prefix(
        self, api_key_prefix: str
    ) -> tuple[int, str, str, str | None] | None:
        """Return ``(id, app_name, env, api_key_hash)`` of the app holding a prefix.

        The hash comes back so the caller can tell "this key, migrated already"
        from "another app happens to hold this prefix". Guessing would be wrong
        in both directions: reporting a genuine collision as an idempotent
        re-run tells the caller their key works when it does not, and reporting a
        re-run as a collision sends them chasing a problem that does not exist.
        """
        with self._db.orm_session() as session:
            row = session.scalar(
                select(AppRow).where(AppRow.api_key_prefix == api_key_prefix)
            )
            if row is None:
                return None
            return row.id, row.app_name, row.env, row.api_key_hash

    async def exists_app_name(self, app_name: str, env: str) -> bool:
        """Whether ``(app_name, env)`` is already claimed in ``avernet_application``."""
        with self._db.orm_session() as session:
            found = session.scalar(
                select(AppRow.id).where(
                    AppRow.app_name == app_name,
                    AppRow.env == env,
                )
            )
        return found is not None

    async def migrate(
        self,
        *,
        key: SourceKey,
        app_name: str,
        app_type: str,
        tenant: str,
        targets: Sequence[GrantTarget],
        config: dict[str, object],
    ) -> int:
        """Write the application row and its grants atomically; return the app id.

        Raises :class:`AlreadyMigratedError`, :class:`PrefixConflictError` or
        :class:`~gateway.community.api.app_registration.AppNameTakenError` when a
        unique key refuses the insert. Anything else propagates: a database that
        is down is not a migration outcome.
        """
        try:
            with self._db.orm_session() as session:
                app_row = AppRow(
                    # The hash and prefix are copied verbatim. That is the whole
                    # mechanism: the caller's existing plaintext key keeps
                    # verifying because both registries run the same PBKDF2.
                    api_key_hash=key.api_key_hash,
                    api_key_prefix=key.api_key_prefix,
                    app_name=app_name,
                    app_type=app_type,
                    owners=key.owner,
                    tenant=tenant,
                    status=_ACTIVE,
                    env=key.env,
                    config=config,
                    # Audit columns carry secbaas's people, not the migration:
                    # who created this credential is a fact about the credential
                    # and survives its move. Where it moved from is on ``config``.
                    creator=key.creator,
                    modifier=key.modifier or "",
                    # ``token`` stays NULL — this row's credential form is the
                    # API key, and the table permits exactly one per row.
                )
                session.add(app_row)
                # Flush, not commit: the grants need the surrogate id, and they
                # must land in this same transaction or not at all.
                session.flush()
                app_id = app_row.id

                for target in targets:
                    session.add(
                        BotAppGrantRow(
                            app_id=app_id,
                            app_name=app_name,
                            bot_id=target.bot_id,
                            user_id=target.user_id,
                            owner_id=target.owner_id,
                            env=key.env,
                            avernet_tenant=tenant,
                        )
                    )
                    session.add(
                        BotAppGrantLogRow(
                            app_id=app_id,
                            app_name=app_name,
                            bot_id=target.bot_id,
                            user_id=target.user_id,
                            owner_id=target.owner_id,
                            action=_GRANT_ACTION,
                            env=key.env,
                            avernet_tenant=tenant,
                        )
                    )
                session.flush()
                return app_id
        except IntegrityError as exc:
            await self._classify(exc, key=key, app_name=app_name)
            raise  # unreachable; _classify raises. Kept so the type is honest.

    async def _classify(
        self, exc: IntegrityError, *, key: SourceKey, app_name: str
    ) -> None:
        """Turn a refused insert into the specific condition that refused it.

        Decided by re-reading in a fresh transaction (the failed one is rolled
        back), so a race whose winning row vanished in between would fall through
        to the original ``IntegrityError`` — which is the honest outcome, since
        nothing in the gateway deletes app rows and that state should not exist.

        Prefix before name: one insert can violate both keys at once, and the
        prefix says something stronger — it identifies *this key*, whereas the
        name only says the caller picked a taken one.
        """
        holder = await self.find_app_holding_prefix(key.api_key_prefix)
        if holder is not None:
            app_id, existing_name, existing_env, existing_hash = holder
            if existing_hash == key.api_key_hash:
                logger.info(
                    "baas key already migrated: source_key_id=%s app_id=%s",
                    key.id,
                    app_id,
                )
                raise AlreadyMigratedError(app_id, existing_name, existing_env) from exc
            logger.error(
                "api_key_prefix collision between a secbaas key and app id=%s "
                "(prefix is a property of the key and cannot be regenerated)",
                app_id,
            )
            raise PrefixConflictError(key.api_key_prefix) from exc
        if await self.exists_app_name(app_name, key.env):
            raise AppNameTakenError(app_name, key.env) from exc
        raise exc
