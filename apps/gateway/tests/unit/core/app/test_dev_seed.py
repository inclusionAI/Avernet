"""DB-backed tests for the config-gated local dev app seed.

Exercised against a real in-memory schema because the whole point of the
seed is what lands in ``avernet_application`` — the hash verifying through
the same :meth:`AppRepository.find_app_by_credential` path the authn
strategy takes, the prefix/hash pair being complete, and the config gate
staying closed when nothing is configured.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from gateway.community.bootstrap import initialize_database
from gateway.community.config._models import LocalDevApp, UserConfig
from gateway.community.core.app import (
    APIKeyGenerator,
    AppRepository,
    AppRow,
    seed_local_dev_apps,
)
from gateway.community.core.app._dev_seed import seed_local_dev_apps as _seed
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.spi.database import DataSourcePlugin

_DEV_KEY = "SingleboxManagerSyncDevAppKey000"
_DEV_APP = LocalDevApp(
    app_name="singlebox-manager-sync", tenant="teamclaw", api_key=_DEV_KEY
)


@pytest.fixture
def db() -> DataSourcePlugin:
    return initialize_database(SqliteDatabasePlugin())


def _config(apps: list[LocalDevApp]) -> UserConfig:
    return UserConfig(local_dev_apps=apps)


def test_empty_config_seeds_nothing(db: DataSourcePlugin):
    """The dev-stack gate is absence: production configs seed nothing."""
    assert seed_local_dev_apps(db, _config([])) == 0
    # Also robust to a configuration object that predates the section.
    assert seed_local_dev_apps(db, UserConfig()) == 0


def test_seeded_key_resolves_through_the_app_token_lookup_path(db):
    """The seeded credential answers the production credential lookup."""
    assert _seed(db, _config([_DEV_APP])) == 1
    with db.orm_session() as session:
        row = session.scalar(select(AppRow))
        assert row is not None
        resolved_row = row.to_record()
    assert resolved_row.app_name == _DEV_APP.app_name
    assert resolved_row.tenant == _DEV_APP.tenant

    resolved = asyncio.run(AppRepository(db).find_app_by_credential(_DEV_KEY))
    assert resolved is not None
    assert resolved.app_name == _DEV_APP.app_name
    assert resolved.tenant == _DEV_APP.tenant


def test_seeding_is_idempotent_and_loud_on_credential_drift(db):
    """A second seed skips; a same-name different-key config is an ERROR skip."""
    assert _seed(db, _config([_DEV_APP])) == 1
    assert _seed(db, _config([_DEV_APP])) == 0  # already seeded
    other_key = "AnotherSingleboxDevAppKey" + "0" * 7
    assert APIKeyGenerator.validate_format(other_key)
    drifted = _DEV_APP.model_copy(update={"api_key": other_key})
    # Same name, different credential: the existing row wins and no second
    # row appears (the caller must change app_name to re-seed).
    assert _seed(db, _config([drifted])) == 0


def test_malformed_key_is_skipped_never_legacy(db: DataSourcePlugin):
    """A dev key outside the 32-char base62 shape is skipped, not stored."""
    bad = _DEV_APP.model_copy(update={"api_key": "not-an-api-key"})
    assert _seed(db, _config([bad])) == 0
    with db.orm_session() as session:
        assert session.scalar(select(AppRow)) is None


def test_seed_works_when_the_deployment_created_no_schema():
    """A schema-less bare run (create_schema=false) still gets its dev app.

    Regression for the singlebox boot: the gateway there runs with
    ``database.create_schema: false`` and no ``avernet_application`` table,
    and the seed must create its own table rather than crash the boot.
    """
    plugin = SqliteDatabasePlugin(create_schema=False)
    db = initialize_database(plugin)
    assert _seed(db, _config([_DEV_APP])) == 1
    resolved = asyncio.run(AppRepository(db).find_app_by_credential(_DEV_KEY))
    assert resolved is not None
    assert resolved.tenant == _DEV_APP.tenant
