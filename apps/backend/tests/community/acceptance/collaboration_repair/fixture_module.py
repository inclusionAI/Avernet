"""Explicit coverage-only credentials; all repair readers and HTTP delivery stay real.

The runner registers an ephemeral provider over BCS HTTP and adds that returned
ID to the disposable BCS allowlist before acceptance. Read its private runtime
file lazily: Backend boots before that provisioning has completed. Ordinary
singlebox configuration and the daily-sync BcnService remain credentialless.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
from typing import Annotated

from injector import Module, provider, singleton

from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.di.config import BcnConfig, SecretNamesConfig
from agentclaw.community.di.modules.bot_manager_sync_module import _build_principal_minter
from agentclaw.community.di.modules.collaboration_repair_module import (
    CollaborationRepairPlugins,
    HttpCollaborationRepairPlugins,
)
from agentclaw.community.plugin_api.http_client import QUALIFIER_BCN, HttpClient
from agentclaw.community.plugin_api.secret_resolver import SecretResolver


class LiveRepairPlugins:
    """Lazy production plugin source, not a recording or simulated delivery."""

    def __init__(self, path, config, http_client, mint_principal, *, timeout_seconds):
        self._path = path
        self._config = config
        self._http_client = http_client
        self._mint_principal = mint_principal
        self._timeout_seconds = timeout_seconds
        self._resolved = None

    def _resolve(self):
        if self._resolved is None:
            if not self._path.is_file():
                return HttpCollaborationRepairPlugins.unavailable("fixture-provider-not-ready")
            payload = json.loads(self._path.read_text())
            values = [payload.get(name) for name in ("provider_id", "provider_admin_token")]
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError("acceptance provider credentials are incomplete")
            config = replace(
                self._config, provider_id=values[0], provider_admin_token=values[1],
                provider_registration_enabled=True,
            )
            self._resolved = HttpCollaborationRepairPlugins(
                bcn_service=BcnService(self._http_client, config, self._timeout_seconds),
                bcs_base_url=config.base_url,
                mint_principal=self._mint_principal,
            )
        return self._resolved

    @property
    def available(self):
        return self._resolve().available

    @property
    def reason(self):
        return self._resolve().reason

    def metadata(self, profile_headers):
        return self._resolve().metadata(profile_headers)

    def ownership(self):
        return self._resolve().ownership()

    def managers(self):
        return self._resolve().managers()


class CollaborationRepairFixtureModule(Module):
    @singleton
    @provider
    def plugins(
        self, config: BcnConfig,
        http_client: Annotated[HttpClient, QUALIFIER_BCN],
        secrets: SecretResolver, names: SecretNamesConfig,
    ) -> CollaborationRepairPlugins:
        path = os.environ.get("SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE")
        if not path:
            return HttpCollaborationRepairPlugins.unavailable("fixture-provider-not-ready")
        # Reuse the production minter and timeout. This source does not borrow
        # background-loop ownership/managers plugins or mutate shared config.
        minter = _build_principal_minter(secrets, names)
        if minter is None:
            return HttpCollaborationRepairPlugins.unavailable("principal-signing-key-required")
        timeout = self.__injector__.get(BcnService)._timeout
        return LiveRepairPlugins(Path(path), config, http_client, minter, timeout_seconds=timeout)
