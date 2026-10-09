"""Composition root for credential issuance (access_key / app registration).

Builds :class:`AccessKeyIssuer` / :class:`AppRegistrar` wired to a repository
backed by the shared ``DataSourcePlugin``. All DB touch lives in the
repositories; the issuer/registrar only mint + delegate. The adapters receive
them via ``app.state``.

Only the access-key issuer still takes the gateway ``PrincipalSigner``: access
keys are signed JWTs, whereas app registration now mints a random API key and
stores only its hash, so it has no signing key to share.

:func:`build_baas_key_migrator` belongs to a **temporary** subsystem — see
``core/baas_migration`` — and is wired here rather than beside it because the
migrator's one knob, the data-isolation tenant it writes under, is a composition
decision. It is passed explicitly instead of read from a constant at the call
site so that when the tenant becomes configuration (or the migration is
deleted), exactly one place changes.
"""

from __future__ import annotations

from gateway.community.core.access_key import AccessKeyIssuer, AccessKeyRepository
from gateway.community.core.app import AppRegistrar, AppRepository
from gateway.community.core.baas_migration import (
    DEFAULT_MIGRATION_TENANT,
    BaasKeyMigrator,
    BaasMigrationRepository,
)
from gateway.community.spi.database import DataSourcePlugin
from gateway.community.spi.principal_signer import PrincipalSigner


def build_access_key_issuer(
    db: DataSourcePlugin, signer: PrincipalSigner
) -> AccessKeyIssuer:
    """Build the AccessKeyIssuer (shares the gateway signer + DB-backed repository)."""
    return AccessKeyIssuer(AccessKeyRepository(db), signer)


def build_app_registrar(db: DataSourcePlugin) -> AppRegistrar:
    """Build the AppRegistrar (DB-backed repository; no signer — API keys are random)."""
    return AppRegistrar(AppRepository(db))


def build_baas_key_migrator(
    db: DataSourcePlugin, *, tenant: str = DEFAULT_MIGRATION_TENANT
) -> BaasKeyMigrator:
    """Build the secbaas → gateway key migrator (DB-backed repository).

    No signer and no key generation: the migration copies a hash that already
    exists, which is the entire point — the caller's key keeps working.
    """
    return BaasKeyMigrator(BaasMigrationRepository(db), tenant=tenant)
