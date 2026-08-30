"""DB-backed tests for ``BaasKeyMigrator`` against a real (SQLite) schema.

Exercised through an actual database rather than a mocked repository, because
most of what this migration has to get right is what the *tables* do: the copied
hash still verifying, two unique keys refusing an insert for different reasons,
and the whole write rolling back together. A fake repository would assert the
code's own beliefs about all three.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from gateway.community.api.baas_migration import MigrationOutcome
from gateway.community.bootstrap import initialize_database
from gateway.community.core.app import APIKeyGenerator, AppRepository, AppRow
from gateway.community.core.baas_migration import (
    BaasApiKeyRow,
    BaasKeyMigrator,
    BaasMigrationRepository,
    BotAppGrantLogRow,
    BotAppGrantRow,
)
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.database import DataSourcePlugin

_TENANT = "teamclaw"


@pytest.fixture
def db() -> DataSourcePlugin:
    """A fresh in-memory schema per test — migrations write, so state must not leak."""
    return initialize_database(SqliteDatabasePlugin())


@pytest.fixture
def migrator(db: DataSourcePlugin) -> BaasKeyMigrator:
    return BaasKeyMigrator(BaasMigrationRepository(db), tenant=_TENANT)


def seed_key(
    db: DataSourcePlugin,
    *,
    app_type: str | None = "app",
    app_id: str = "third-party-app",
    policy: str | None = None,
    status: str = "ACTIVE",
    env: str = "prod",
    owner: str = "u1",
    tenant: str | None = "team_claw",
) -> str:
    """Insert a ``baas_api_key`` row and return the plaintext key for it."""
    api_key = APIKeyGenerator.generate()
    with db.orm_session() as session:
        session.add(
            BaasApiKeyRow(
                api_key_hash=APIKeyGenerator.hash_key(api_key),
                api_key_prefix=api_key[:8],
                key_name="upstream name",
                app_id=app_id,
                app_type=app_type,
                status=status,
                owner=owner,
                tenant=tenant,
                env=env,
                creator="creator-1",
                modifier="modifier-1",
                policy=policy,
            )
        )
    return api_key


def allow(*references: str) -> str:
    return json.dumps({"allowed_bots": list(references)})


def grants_in(db: DataSourcePlugin) -> list[BotAppGrantRow]:
    with db.orm_session() as session:
        return list(session.scalars(select(BotAppGrantRow).order_by(BotAppGrantRow.id)))


def apps_in(db: DataSourcePlugin) -> list[AppRow]:
    with db.orm_session() as session:
        return list(session.scalars(select(AppRow)))


# ── The point of the whole exercise: the caller's key keeps working ──────────


async def test_migrated_key_authenticates_against_the_gateway_registry(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """The plaintext key the caller already holds resolves to the new app row.

    This is the migration's entire reason to exist. It works because the hash is
    copied rather than re-derived and both registries run the same PBKDF2 — if
    ``core/app/_key_gen.py`` ever drifts from secbaas's copy, this test is where
    it shows up as a failure rather than as a production outage.
    """
    api_key = seed_key(db, policy=allow("bot-1:u1"))

    result = await migrator.migrate(api_key=api_key, app_name="My App")
    assert result.outcome is MigrationOutcome.MIGRATED

    resolved = await AppRepository(db).find_app_by_credential(api_key)
    assert resolved is not None
    assert resolved.id == result.app.id
    assert resolved.app_name == "My App"


async def test_response_never_carries_key_material(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    api_key = seed_key(db, policy=allow("bot-1:u1"))
    result = await migrator.migrate(api_key=api_key, app_name="My App")

    rendered = repr(result)
    assert api_key not in rendered
    with db.orm_session() as session:
        stored = session.scalar(select(AppRow))
        assert stored.api_key_hash not in rendered


# ── App-type keys: the policy is flattened into one grant per bot ────────────


async def test_app_key_flattens_allowed_bots_into_grants(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    api_key = seed_key(db, policy=allow("bot-1:u1", "bot-2:u2"), env="prod")

    result = await migrator.migrate(api_key=api_key, app_name="Flattened")

    assert result.outcome is MigrationOutcome.MIGRATED
    rows = grants_in(db)
    assert [(r.bot_id, r.user_id, r.owner_id, r.env) for r in rows] == [
        ("bot-1", "u1", "u1", "prod"),
        ("bot-2", "u2", "u2", "prod"),
    ]
    assert {r.avernet_tenant for r in rows} == {_TENANT}
    assert {r.app_id for r in rows} == {result.app.id}
    assert {r.app_name for r in rows} == {"Flattened"}


async def test_owner_and_delegating_user_are_the_entity_id(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """secbaas could only express "the bot's own owner granted this".

    Its permission check was ``operator == entity_id``, so there is no second
    person to recover from the source row — both columns get the entity id.
    """
    api_key = seed_key(db, policy=allow("default:012345"))

    await migrator.migrate(api_key=api_key, app_name="Owned")

    (row,) = grants_in(db)
    assert row.user_id == "012345"
    assert row.owner_id == "012345"


async def test_repeated_bot_reference_writes_one_grant(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """A duplicate in ``allowed_bots`` is the same row twice, not two grants.

    Left in, it would violate ``uk_bot_app_grant_scope`` and abort a migration
    that has nothing wrong with it.
    """
    api_key = seed_key(db, policy=allow("bot-1:u1", "bot-1:u1"))

    result = await migrator.migrate(api_key=api_key, app_name="Deduped")

    assert result.outcome is MigrationOutcome.MIGRATED
    assert len(grants_in(db)) == 1
    assert len(result.app.grants) == 1


async def test_empty_policy_migrates_the_credential_with_no_grants(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """Deny-all is a real permission, and it migrates faithfully.

    An app key created through secbaas's own endpoint starts at
    ``allowed_bots: []``. Refusing it would block the most common shape there is.
    """
    api_key = seed_key(db, policy=allow())

    result = await migrator.migrate(api_key=api_key, app_name="No Bots Yet")

    assert result.outcome is MigrationOutcome.MIGRATED
    assert result.app.grants == ()
    assert grants_in(db) == []
    assert len(apps_in(db)) == 1


# ── Bot-type keys: the single bot lives in app_id itself ─────────────────────


async def test_bot_key_grants_its_own_bot(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    api_key = seed_key(db, app_type="bot", app_id="bot-9:u9", policy=None)

    result = await migrator.migrate(api_key=api_key, app_name="Bot Key")

    assert result.outcome is MigrationOutcome.MIGRATED
    (row,) = grants_in(db)
    assert (row.bot_id, row.user_id, row.owner_id) == ("bot-9", "u9", "u9")


async def test_bot_key_ignores_any_policy_column(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """``app_type=bot`` has no policy in secbaas; a stray one must not grant more."""
    api_key = seed_key(
        db, app_type="bot", app_id="bot-9:u9", policy=allow("other:u1", "*")
    )

    result = await migrator.migrate(api_key=api_key, app_name="Bot Key")

    assert result.outcome is MigrationOutcome.MIGRATED
    assert [(r.bot_id, r.user_id) for r in grants_in(db)] == [("bot-9", "u9")]


# ── The audit trail ─────────────────────────────────────────────────────────


async def test_every_grant_is_logged(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """The live row says access is in force; the log says how it came to be.

    A migrated grant with no log entry is one whose provenance can never be
    answered — and the log is read precisely when the live row is gone.
    """
    api_key = seed_key(db, policy=allow("bot-1:u1", "bot-2:u2"))

    await migrator.migrate(api_key=api_key, app_name="Logged")

    with db.orm_session() as session:
        logged = list(session.scalars(select(BotAppGrantLogRow)))
    assert [(r.bot_id, r.action) for r in logged] == [
        ("bot-1", "granted"),
        ("bot-2", "granted"),
    ]


async def test_source_row_is_recorded_on_the_application_config(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """Provenance goes on ``config``; the audit columns keep secbaas's people.

    The source ``tenant`` is carried here specifically because the row itself is
    written under the gateway's tenant — that value would otherwise be lost.
    """
    api_key = seed_key(db, app_id="upstream-app", tenant="team_claw")

    await migrator.migrate(api_key=api_key, app_name="Traceable")

    (app,) = apps_in(db)
    assert app.config["migrated_from"]["source"] == "baas_api_key"
    assert app.config["migrated_from"]["app_id"] == "upstream-app"
    assert app.config["migrated_from"]["tenant"] == "team_claw"
    assert app.creator == "creator-1"
    assert app.modifier == "modifier-1"
    assert app.tenant == _TENANT
    assert app.status == "ACTIVE"
    assert app.token is None


# ── Refusals. Every one of these must leave the database untouched ──────────


@pytest.mark.parametrize("status", ["INACTIVE", "REVOKED"])
async def test_only_active_keys_migrate(
    db: DataSourcePlugin, migrator: BaasKeyMigrator, status: str
) -> None:
    """A retired key is not a key; migrating one would revive it."""
    api_key = seed_key(db, status=status, policy=allow("bot-1:u1"))

    result = await migrator.migrate(api_key=api_key, app_name="Retired")

    assert result.outcome is MigrationOutcome.KEY_NOT_FOUND
    assert apps_in(db) == []


async def test_unknown_key_is_indistinguishable_from_a_wrong_one(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """Splitting the two would make this endpoint an oracle for live prefixes."""
    seed_key(db, policy=allow("bot-1:u1"))
    absent = await migrator.migrate(api_key=APIKeyGenerator.generate(), app_name="Nope")

    # Same prefix as a real row, wrong remainder — the hash check is what fails.
    real = seed_key(db, policy=allow("bot-1:u1"))
    forged = real[:8] + APIKeyGenerator.generate()[8:]
    wrong = await migrator.migrate(api_key=forged, app_name="Nope")

    assert absent.outcome is MigrationOutcome.KEY_NOT_FOUND
    assert wrong.outcome is MigrationOutcome.KEY_NOT_FOUND
    assert absent.message == wrong.message


async def test_wildcard_policy_is_refused_whole(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """Allow-all has no representation in a table of one row per bot.

    Materialising today's bots would silently freeze an open-ended permission,
    so nothing is written and the caller is told why.
    """
    api_key = seed_key(db, policy=allow("*"))

    result = await migrator.migrate(api_key=api_key, app_name="Everything")

    assert result.outcome is MigrationOutcome.WILDCARD_POLICY
    assert apps_in(db) == []
    assert grants_in(db) == []


async def test_malformed_bot_reference_refuses_the_whole_migration(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """Migrating the valid half would under-grant while reporting success."""
    api_key = seed_key(db, policy=allow("bot-1:u1", "no-entity-id", ":u2"))

    result = await migrator.migrate(api_key=api_key, app_name="Partial")

    assert result.outcome is MigrationOutcome.INVALID_GRANT_TARGETS
    assert result.detail["invalid_bots"] == ["no-entity-id", ":u2"]
    assert apps_in(db) == []
    assert grants_in(db) == []


@pytest.mark.parametrize(
    "app_type", [None, "", "baas", "unknown"], ids=["null", "blank", "baas", "unknown"]
)
async def test_unsupported_app_type_is_refused(
    db: DataSourcePlugin, migrator: BaasKeyMigrator, app_type: str | None
) -> None:
    api_key = seed_key(db, app_type=app_type, policy=allow("bot-1:u1"))

    result = await migrator.migrate(api_key=api_key, app_name="Odd")

    assert result.outcome is MigrationOutcome.UNSUPPORTED_APP_TYPE
    assert apps_in(db) == []


async def test_env_too_long_for_a_grant_is_refused_not_truncated(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """``baas_api_key.env`` is varchar(32); ``ac_bot_app_grant.env`` is varchar(20).

    A truncated ``env`` produces a grant no request can resolve — an
    authorization that looks live in every listing and answers "no" every time.
    """
    api_key = seed_key(db, env="e" * 21, policy=allow("bot-1:u1"))

    result = await migrator.migrate(api_key=api_key, app_name="Long Env")

    assert result.outcome is MigrationOutcome.VALUE_TOO_LONG
    assert result.detail["field"] == "env"
    assert apps_in(db) == []


async def test_long_env_still_migrates_when_there_are_no_grants(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """The limit belongs to the grant tables, so it only binds when one is written."""
    api_key = seed_key(db, env="e" * 21, policy=allow())

    result = await migrator.migrate(api_key=api_key, app_name="Long Env")

    assert result.outcome is MigrationOutcome.MIGRATED


async def test_over_long_identity_is_refused(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    api_key = seed_key(db, policy=allow(f"bot-1:{'u' * 257}"))

    result = await migrator.migrate(api_key=api_key, app_name="Long User")

    assert result.outcome is MigrationOutcome.VALUE_TOO_LONG
    assert result.detail["field"] == "user_id"
    assert apps_in(db) == []


async def test_over_long_app_name_is_refused(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    api_key = seed_key(db, policy=allow("bot-1:u1"))

    result = await migrator.migrate(api_key=api_key, app_name="n" * 257)

    assert result.outcome is MigrationOutcome.VALUE_TOO_LONG
    assert result.detail["field"] == "app_name"


# ── Unique-key collisions, told apart by re-reading the table ───────────────


async def test_app_name_taken_in_the_same_env_is_reported_as_such(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """The one refusal the caller fixes by changing their request."""
    seed_key(db, policy=allow("bot-1:u1"))
    first = seed_key(db, policy=allow("bot-1:u1"), env="prod")
    second = seed_key(db, policy=allow("bot-2:u2"), env="prod")

    await migrator.migrate(api_key=first, app_name="Taken")
    result = await migrator.migrate(api_key=second, app_name="Taken")

    assert result.outcome is MigrationOutcome.APP_NAME_TAKEN
    assert result.detail == {"app_name": "Taken", "env": "prod"}
    assert "different app_name" in result.message
    # The failed migration wrote nothing — not even its grants.
    assert [r.bot_id for r in grants_in(db)] == ["bot-1"]
    assert len(apps_in(db)) == 1


async def test_the_same_name_is_free_in_another_env(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """``env`` is in the key so one environment cannot lock a name out of another."""
    prod = seed_key(db, policy=allow("bot-1:u1"), env="prod")
    dev = seed_key(db, policy=allow("bot-1:u1"), env="dev")

    assert (
        await migrator.migrate(api_key=prod, app_name="Billing")
    ).outcome is MigrationOutcome.MIGRATED
    assert (
        await migrator.migrate(api_key=dev, app_name="Billing")
    ).outcome is MigrationOutcome.MIGRATED
    assert len(apps_in(db)) == 2


async def test_migrating_the_same_key_twice_reports_already_migrated(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """Idempotent in the way that matters: the second call writes nothing.

    Reported distinctly from a name clash, because the caller's next move is the
    opposite — stop, rather than retry with a different name.
    """
    api_key = seed_key(db, policy=allow("bot-1:u1"))

    first = await migrator.migrate(api_key=api_key, app_name="Once")
    again = await migrator.migrate(api_key=api_key, app_name="Twice")

    assert again.outcome is MigrationOutcome.ALREADY_MIGRATED
    assert again.detail["app_id"] == first.app.id
    assert again.detail["app_name"] == "Once"
    assert len(apps_in(db)) == 1
    assert len(grants_in(db)) == 1


async def test_prefix_held_by_an_unrelated_app_is_not_reported_as_migrated(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """Hash equality, not the prefix, decides "already migrated".

    Calling a genuine collision an idempotent re-run would tell the caller their
    key works when in fact it resolves to somebody else's application.
    """
    api_key = seed_key(db, policy=allow("bot-1:u1"))
    with db.orm_session() as session:
        session.add(
            AppRow(
                app_name="Unrelated",
                app_type="assistant",
                api_key_hash=APIKeyGenerator.hash_key(APIKeyGenerator.generate()),
                api_key_prefix=api_key[:8],
                owners="someone-else",
                tenant=_TENANT,
                status="ACTIVE",
                env="prod",
            )
        )

    result = await migrator.migrate(api_key=api_key, app_name="Mine")

    assert result.outcome is MigrationOutcome.PREFIX_CONFLICT
    assert result.detail["api_key_prefix"] == api_key[:8]
    assert len(apps_in(db)) == 1


async def test_a_refused_grant_takes_the_application_row_with_it(
    db: DataSourcePlugin, migrator: BaasKeyMigrator
) -> None:
    """Atomicity in the direction the ``app_name_taken`` case cannot show.

    That case proves a refused *application* row leaves no grants. This proves
    the reverse: a grant the destination refuses must not leave a live
    credential behind, because a credential that authenticates while
    authorizing nothing is indistinguishable, to its holder, from a successful
    migration.

    Forced by pre-claiming the grant's unique key. The application row has not
    been inserted yet, so its id is the sequence's first value on this fresh
    schema — asserted below rather than assumed, so a change in allocation
    fails here instead of quietly making this test vacuous.
    """
    api_key = seed_key(db, policy=allow("bot-1:u1"), env="prod")
    with db.orm_session() as session:
        session.add(
            BotAppGrantRow(
                app_id=1,
                app_name="Pre-existing",
                bot_id="bot-1",
                user_id="u1",
                owner_id="u1",
                env="prod",
                avernet_tenant=_TENANT,
            )
        )

    # Not a migration outcome: nothing the caller can do about it, so it
    # propagates to the adapter's 500 rather than becoming a refusal value.
    with pytest.raises(IntegrityError):
        await migrator.migrate(api_key=api_key, app_name="Rolled Back")

    assert apps_in(db) == []
    assert len(grants_in(db)) == 1  # only the row seeded above
    with db.orm_session() as session:
        assert list(session.scalars(select(BotAppGrantLogRow))) == []
