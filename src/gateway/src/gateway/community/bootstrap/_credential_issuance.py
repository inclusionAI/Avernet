"""Composition root for credential issuance (access_key / app registration).

Builds :class:`AccessKeyIssuer` / :class:`AppRegistrar` wired to a repository
(backed by the shared ``DataSourcePlugin``) and the gateway ``PrincipalSigner``.
All DB touch lives in the repositories; the issuer/registrar only mint + delegate.
The adapters receive them via ``app.state``.
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


def build_app_registrar(db: DataSourcePlugin, signer: PrincipalSigner) -> AppRegistrar:
    """Build the AppRegistrar (shares the gateway signer + DB-backed repository)."""
    return AppRegistrar(AppRepository(db), signer)
