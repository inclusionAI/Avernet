"""Composition root for credential issuance (access_key / app registration).

Builds :class:`AccessKeyIssuer` / :class:`AppRegistrar` wired to a repository
backed by the shared ``DataSourcePlugin``. All DB touch lives in the
repositories; the issuer/registrar only mint + delegate. The adapters receive
them via ``app.state``.

Only the access-key issuer still takes the gateway ``PrincipalSigner``: access
keys are signed JWTs, whereas app registration now mints a random API key and
stores only its hash, so it has no signing key to share.
"""

from __future__ import annotations

from gateway.community.core.access_key import AccessKeyIssuer, AccessKeyRepository
from gateway.community.core.app import AppRegistrar, AppRepository
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
