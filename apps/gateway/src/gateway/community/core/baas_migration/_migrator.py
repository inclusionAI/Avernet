"""``BaasKeyMigrator`` — move one secbaas API key onto the gateway, grants and all.

The endpoint this serves is authenticated by the credential it migrates: the
caller presents their own plaintext key, and possession of a key that verifies
against an ACTIVE ``baas_api_key`` row is the whole authorization. That is the
only reason a migration can be self-service — the gateway holds no mapping from
a user to their secbaas keys, and neither does anyone running the migration for
them.

Two rules shape every decision below.

**Never grant more than secbaas did.** Anything that could only be resolved by
guessing wider — an allow-all policy, a bot reference that is not
``{bot_id}:{entity_id}``, an ``app_type`` with no defined grant shape — is
refused outright rather than approximated.

**Never appear to grant what was not written.** A partial migration is worse
than a refused one: its holder sees a working credential and discovers the
missing bots at some later, unrelated moment. So refusals here are whole-request
and the write is a single transaction.
"""

from __future__ import annotations

from collections.abc import Sequence

from gateway.community.api.app_registration import AppNameTakenError
from gateway.community.api.baas_migration import (
    MigratedApp,
    MigratedGrant,
    MigrationOutcome,
    MigrationResult,
)
from gateway.community.logger import get_logger

from ._orm import (
    APP_NAME_MAX_LENGTH,
    GRANT_ENV_MAX_LENGTH,
    GRANT_IDENTITY_MAX_LENGTH,
)
from ._policy import WILDCARD, parse_allowed_bots, split_bot_reference
from ._records import GrantTarget, SourceKey
from ._repository import (
    AlreadyMigratedError,
    BaasMigrationRepository,
    PrefixConflictError,
)

logger = get_logger("baas-migration")

#: Data-isolation tenant every migrated row is written under.
#:
#: A constant rather than a copy of ``baas_api_key.tenant``, because the two
#: columns are not the same namespace: secbaas's default is ``team_claw`` while
#: the backend's grant tables default to ``teamclaw``, and a grant written under
#: a tenant the backend's guard does not scope to is invisible to every lookup
#: that would authorize it — a silent authorization failure rather than a visible
#: one. The source value is not discarded; it is recorded on the application
#: row's ``config`` so the original is still answerable.
DEFAULT_MIGRATION_TENANT = "teamclaw"

#: secbaas's two key shapes, and the only two with a defined grant meaning.
_APP_TYPE_APP = "app"
_APP_TYPE_BOT = "bot"


class BaasKeyMigrator:
    """Copy an ACTIVE secbaas key into ``avernet_application`` with its grants."""

    def __init__(
        self,
        repository: BaasMigrationRepository,
        *,
        tenant: str = DEFAULT_MIGRATION_TENANT,
    ) -> None:
        self._repository = repository
        self._tenant = tenant

    async def migrate(self, *, api_key: str, app_name: str) -> MigrationResult:
        """Migrate the key ``api_key`` under the caller-chosen ``app_name``.

        ``app_name`` is the caller's to choose and need not match anything in
        secbaas: ``baas_api_key`` has no name that is unique per environment, so
        there is nothing to carry over even when a key has a ``key_name``.
        """
        if len(app_name) > APP_NAME_MAX_LENGTH:
            return _refuse(
                MigrationOutcome.VALUE_TOO_LONG,
                f"app_name exceeds {APP_NAME_MAX_LENGTH} characters",
                {"field": "app_name", "limit": APP_NAME_MAX_LENGTH},
            )

        key = await self._repository.find_active_key(api_key)
        if key is None:
            # One message for "no such key" and for "wrong key". Splitting them
            # would let anyone probe which prefixes exist upstream.
            return _refuse(
                MigrationOutcome.KEY_NOT_FOUND,
                "no active secbaas API key matches the presented credential",
            )

        targets_or_refusal = _grant_targets(key)
        if isinstance(targets_or_refusal, MigrationResult):
            return targets_or_refusal
        targets = targets_or_refusal

        overflow = _overflowing_field(key, targets)
        if overflow is not None:
            field, value, limit = overflow
            return _refuse(
                MigrationOutcome.VALUE_TOO_LONG,
                f"{field} exceeds {limit} characters and cannot be stored on a "
                "grant; a truncated value would produce an authorization no "
                "request can ever resolve",
                {"field": field, "limit": limit, "length": len(value)},
            )

        app_type = key.app_type or "UNKNOWN"
        try:
            app_id = await self._repository.migrate(
                key=key,
                app_name=app_name,
                app_type=app_type,
                tenant=self._tenant,
                targets=targets,
                config=_provenance(key),
            )
        except AlreadyMigratedError as exc:
            return _refuse(
                MigrationOutcome.ALREADY_MIGRATED,
                "this key has already been migrated",
                {"app_id": exc.app_id, "app_name": exc.app_name, "env": exc.env},
            )
        except PrefixConflictError as exc:
            return _refuse(
                MigrationOutcome.PREFIX_CONFLICT,
                "another application already holds this key's prefix; the key "
                "cannot be migrated and must be reissued",
                {"api_key_prefix": exc.api_key_prefix},
            )
        except AppNameTakenError as exc:
            return _refuse(
                MigrationOutcome.APP_NAME_TAKEN,
                f"app_name {exc.app_name!r} is already used in env {exc.env!r}; "
                "retry with a different app_name",
                {"app_name": exc.app_name, "env": exc.env},
            )

        logger.info(
            "migrated secbaas key: source_key_id=%s app_id=%s app_type=%s "
            "env=%s grants=%s",
            key.id,
            app_id,
            app_type,
            key.env,
            len(targets),
        )
        return MigrationResult(
            outcome=MigrationOutcome.MIGRATED,
            app=MigratedApp(
                id=app_id,
                app_name=app_name,
                app_type=app_type,
                owners=key.owner,
                tenant=self._tenant,
                env=key.env,
                api_key_prefix=key.api_key_prefix,
                source_key_id=key.id,
                grants=tuple(
                    MigratedGrant(
                        bot_id=t.bot_id,
                        user_id=t.user_id,
                        owner_id=t.owner_id,
                        env=key.env,
                    )
                    for t in targets
                ),
            ),
        )


def _refuse(
    outcome: MigrationOutcome,
    message: str,
    detail: dict[str, object] | None = None,
) -> MigrationResult:
    """A refusal, with nothing written."""
    return MigrationResult(outcome=outcome, message=message, detail=detail or {})


def _grant_targets(key: SourceKey) -> list[GrantTarget] | MigrationResult:
    """Derive the grants a key implies, or the refusal that stops the migration.

    The two shapes secbaas supports carry their bots in different places, and
    both resolve to the same ``{bot_id}:{entity_id}`` references:

    * ``bot`` — the key *is* the bot's; ``app_id`` holds the single reference.
    * ``app`` — the key is a third-party app's; ``policy.allowed_bots`` holds
      however many were granted to it.

    ``entity_id`` becomes both ``user_id`` and ``owner_id``. secbaas could only
    express "the bot's own owner authorized this", which its permission check
    enforced as ``operator == entity_id``, so there is no second person to
    recover — inventing a distinction the source never recorded would be worse
    than stating this one plainly.
    """
    app_type = (key.app_type or "").strip()

    if app_type == _APP_TYPE_BOT:
        references: Sequence[str] = [key.app_id]
    elif app_type == _APP_TYPE_APP:
        references = parse_allowed_bots(key.policy)
        if WILDCARD in references:
            return _refuse(
                MigrationOutcome.WILDCARD_POLICY,
                'this key allows every bot (allowed_bots: ["*"]), which the '
                "gateway records one bot at a time and cannot represent; grant "
                "the bots explicitly after migrating, or narrow the policy first",
                {"allowed_bots": [WILDCARD]},
            )
    else:
        return _refuse(
            MigrationOutcome.UNSUPPORTED_APP_TYPE,
            f"app_type {key.app_type!r} has no defined grant shape; only "
            f"{_APP_TYPE_APP!r} and {_APP_TYPE_BOT!r} keys can be migrated",
            {"app_type": key.app_type},
        )

    targets: list[GrantTarget] = []
    seen: set[tuple[str, str]] = set()
    invalid: list[str] = []
    for reference in references:
        split = split_bot_reference(reference)
        if split is None:
            invalid.append(reference)
            continue
        bot_id, entity_id = split
        if (bot_id, entity_id) in seen:
            # The destination's unique key is (tenant, app_id, bot_id, user_id,
            # env); the first three are fixed for this migration, so a repeated
            # reference is the same row twice and would abort the transaction.
            continue
        seen.add((bot_id, entity_id))
        targets.append(
            GrantTarget(bot_id=bot_id, user_id=entity_id, owner_id=entity_id)
        )

    if invalid:
        # Refused whole rather than migrated minus these. A credential that
        # authorizes fewer bots than it used to, while reporting success, fails
        # at some later moment with nothing pointing back here.
        return _refuse(
            MigrationOutcome.INVALID_GRANT_TARGETS,
            "one or more bot references are not in {bot_id}:{entity_id} form, "
            "so the grants they stand for cannot be reconstructed; nothing was "
            "migrated",
            {"invalid_bots": invalid},
        )

    return targets


def _overflowing_field(
    key: SourceKey, targets: Sequence[GrantTarget]
) -> tuple[str, str, int] | None:
    """The first value that will not fit its grant column, if any.

    Checked only when there are grants to write. ``env`` is the interesting one:
    ``baas_api_key.env`` is ``varchar(32)`` and ``ac_bot_app_grant.env`` is
    ``varchar(20)``, so a valid source row can hold an ``env`` the destination
    cannot. Nothing here is truncated to fit — see
    :attr:`MigrationOutcome.VALUE_TOO_LONG`.
    """
    if not targets:
        return None
    if len(key.env) > GRANT_ENV_MAX_LENGTH:
        return "env", key.env, GRANT_ENV_MAX_LENGTH
    for target in targets:
        for field, value in (
            ("bot_id", target.bot_id),
            ("user_id", target.user_id),
        ):
            if len(value) > GRANT_IDENTITY_MAX_LENGTH:
                return field, value, GRANT_IDENTITY_MAX_LENGTH
    return None


def _provenance(key: SourceKey) -> dict[str, object]:
    """What this application row was made from.

    Lives on ``config`` rather than in the audit columns, which carry secbaas's
    real people. Keeps the source row answerable — including its original
    ``tenant``, which :data:`DEFAULT_MIGRATION_TENANT` deliberately does not
    carry over — so a migration can be traced back without a join to a table the
    gateway does not own.
    """
    return {
        "migrated_from": {
            "source": "baas_api_key",
            "id": key.id,
            "app_id": key.app_id,
            "app_type": key.app_type,
            "tenant": key.tenant,
            "env": key.env,
        }
    }
