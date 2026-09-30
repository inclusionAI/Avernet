"""Composition root for the ``/api/internal/skill-center/*`` Bearer token."""

from __future__ import annotations

from injector import inject, provider, singleton

from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = get_logger()


# Mirrors ``bot_dormant_module._SINGLEBOX_FALLBACK_TOKEN``: intentionally a
# publicly-visible string, because it only ever gates singlebox/local, where no
# real authority decision is at stake. Deployments that carry a secret name
# resolve the real token and never reach it.
_SINGLEBOX_FALLBACK_TOKEN = "singlebox-skill-center-token-local"


class SkillCenterInternalTokenBindings:
    """Provider mixin: resolves the internal endpoints' Bearer token."""

    @singleton
    @provider
    @inject
    def _resolved_internal_token(
        self,
        secret_resolver: SecretResolver,
        secret_names: cfg.SecretNamesConfig,
    ) -> cfg.SkillCenterInternalToken:
        """Resolve the ``/api/internal/skill-center/*`` Bearer token.

        The secret name comes from
        ``SecretNamesConfig.skill_center_internal_token`` (the ``secret_names``
        yaml block). Resolution mirrors
        ``BotDormantModule._resolved_dormant_token`` exactly:

          - name is empty (community / singlebox / test)
              → the local fallback token, so local development can call
                the endpoints
          - the resolver returns a secret with a non-empty ``secret_value``
              → that value (prod / pre normal path)
          - the resolver returns ``None`` or an empty ``secret_value``
              → the local fallback token
          - the resolver raises (transient outage / network)
              → ``""``, which 401s every request rather than authorize an
                unverified caller
        """
        secret_name = secret_names.skill_center_internal_token
        if not secret_name:
            logger.info(
                "[skill_center_module] no internal token secret name "
                "configured — local fallback token in use"
            )
            return cfg.SkillCenterInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        try:
            secret = secret_resolver.get_secret(secret_name=secret_name)
        except Exception:
            logger.exception(
                "[skill_center_module] SecretResolver.get_secret failed for %r "
                "— returning empty token (failure-closed)",
                secret_name,
            )
            return cfg.SkillCenterInternalToken(value="")

        if secret is None:
            logger.info(
                "[skill_center_module] SecretResolver returned None for %r — "
                "singlebox/local fallback token in use",
                secret_name,
            )
            return cfg.SkillCenterInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        value = getattr(secret, "secret_value", None)
        if not value:
            logger.warning(
                "[skill_center_module] secret %r resolved but secret_value "
                "empty — falling back to singlebox token",
                secret_name,
            )
            return cfg.SkillCenterInternalToken(value=_SINGLEBOX_FALLBACK_TOKEN)

        return cfg.SkillCenterInternalToken(value=str(value))
