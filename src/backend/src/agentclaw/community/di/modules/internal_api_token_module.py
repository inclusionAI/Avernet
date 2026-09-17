"""Composition root for the general-purpose internal Bearer token.

``InternalApiToken`` gates routes whose caller is a platform component rather
than an end user — the ones that historically shipped as "noauth" because
there was no user credential to check. Unlike
``DormantInternalToken`` / ``SkillCenterInternalToken``, which each gate one
operation and are granted independently, this token is the shared one: a route
that needs *a* caller-is-trusted check, not its own grant, uses this.

Resolution follows ``TcFileUploadIntegrationModule.tc_file_service_token``
rather than the dormant/skill-center providers, because the difference matters
for a token whose job is closing a hole: an unconfigured **corp** deployment
resolves to ``""`` (401 everything) instead of the fallback constant below. A
token published in this repository must never be what stands between the
internet and a write.
"""

from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = get_logger()


# Mirrors ``bot_dormant_module._SINGLEBOX_FALLBACK_TOKEN``: intentionally a
# publicly-visible string, because it only ever gates local profiles
# (singlebox / test / community), where no real authority decision is at
# stake. A corp deployment never reaches it — see the module docstring.
_SINGLEBOX_FALLBACK_TOKEN = "singlebox-internal-api-token-local"


class InternalApiTokenBindings:
    """Provider mixin: resolves the shared internal Bearer token."""

    def __init__(self, *, local: bool = False) -> None:
        self._local = local

    @singleton
    @provider
    @inject
    def _resolved_internal_api_token(
        self,
        secret_resolver: SecretResolver,
        secret_names: cfg.SecretNamesConfig,
    ) -> cfg.InternalApiToken:
        """Resolve the shared internal Bearer token.

        The secret name comes from ``SecretNamesConfig.internal_api_token``
        (the ``secret_names`` yaml block).

          - name is empty
              → the local fallback token in a local profile, so singlebox /
                community development can call the routes; ``""`` in corp,
                which 401s every request
          - the resolver returns a secret with a non-empty ``secret_value``
              → that value (prod / pre normal path)
          - the resolver returns ``None`` or an empty ``secret_value``
              → the same local-or-empty fallback as an unset name
          - the resolver raises (transient outage / network)
              → ``""`` in every profile: failure-closed, never authorize an
                unverified caller because a lookup broke
        """
        secret_name = secret_names.internal_api_token
        if not secret_name:
            logger.info(
                "[internal_api_token] no secret name configured — %s",
                "local fallback token in use"
                if self._local
                else "token empty, internal routes closed",
            )
            return cfg.InternalApiToken(value=self._unconfigured_value())

        try:
            secret = secret_resolver.get_secret(secret_name=secret_name)
        except Exception:
            logger.exception(
                "[internal_api_token] SecretResolver.get_secret failed for %r "
                "— returning empty token (failure-closed)",
                secret_name,
            )
            return cfg.InternalApiToken(value="")

        value = getattr(secret, "secret_value", None) if secret is not None else None
        if not value:
            logger.warning(
                "[internal_api_token] secret %r resolved to no value — %s",
                secret_name,
                "local fallback token in use"
                if self._local
                else "token empty, internal routes closed",
            )
            return cfg.InternalApiToken(value=self._unconfigured_value())

        return cfg.InternalApiToken(value=str(value))

    def _unconfigured_value(self) -> str:
        """What an unresolvable secret name means for this profile."""
        return _SINGLEBOX_FALLBACK_TOKEN if self._local else ""


class InternalApiTokenModule(InternalApiTokenBindings, Module):
    """Installs the shared internal token binding for the whole app."""
