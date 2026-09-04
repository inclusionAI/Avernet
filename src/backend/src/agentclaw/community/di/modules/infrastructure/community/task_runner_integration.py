"""Community (public) task-runner port integration.

Binds the task execution Ports (``single_bot`` / ``coop_group``) to their
adapter implementations, fed by the community ``openapi_bot`` / ``bcs_client``
``user_config`` blocks. Community (public) flavor: the secret-suffix fields
(``api_key_secret`` / ``token_secret`` / ``secret_secret``) are LITERAL
env-injected values (the community build has no Mist) — they read straight as
the real Bearer api_key / HMAC key+secret, mirroring how the corp
``corp_task_integration`` port provider consumes them.

- ``OpenApiBotPort`` <- ``OpenApiBotAdapter`` (BaaS Open API, Bearer api_key)
  -> task ``single_bot`` dispatch (``/openapi/v1/messages``).
- ``BcsClientPort`` <- ``BcsHttpAdapter`` (BCS coordinator, HMAC
  ``X-ECB-Token``/``X-ECB-Signature``) -> task ``coop_group``
  (``/groups`` + ``/sessions``).
- ``BcsTokenProvider`` (callback route base_url) overrides ``TaskModule``'s
  default ``LocalBcsTokenProvider`` so the task-result callback base_url points
  to the configured ``bcs_client.task_callback_url[_pre]`` (not localhost).

Resolution rules (fail-closed literal credentials):
- missing block / empty env-resolved ``base_url`` / empty ``api_key_secret``
  (openapi) or ``token_secret`` / ``secret_secret`` (bcs) -> port ``None``
  (fail-closed; ``task_service``'s ``injector.get(...)`` returns ``None`` and
  degrades dispatch without raising).
- all present -> the real adapter is bound to the Port.

Installed ONLY in the ``community`` deployment profile (see ``profile_modules``)
— NOT in base, so the corp column (ocb) keeps its own
``CorpTaskIntegrationModule`` unchanged; the two columns are import-disjoint and
a community distribution never imports ``agentclaw.corp``.

Auth split (do NOT conflate with the ``bcn`` block):
- ``bcn`` block -> ``BcnService`` (Bearer ``provider_admin_token``, management
  plane ``/providers/{id}/bots``).
- ``openapi_bot`` (here) -> ``single_bot`` dispatch (BaaS Open API, Bearer).
- ``bcs_client`` (here) -> ``coop_group`` coordination (BCS, HMAC).
"""
from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsHttpAdapter,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    BcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiBotAdapter,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BcsClientPort,
    OpenApiBotPort,
)
from agentclaw.community.di.config import (
    BcnConfig,
    BcsClientConfig,
    OpenApiBotConfig,
)
from agentclaw.community.di.modules.config_module import _block
from agentclaw.community.log import get_logger

from injector import Module, inject, provider, singleton

logger = get_logger()


@dataclass(frozen=True)
class ApiKeyProviderImpl:
    """Frozen credential holder satisfying the ``ApiKeyProvider`` Protocol.

    Drives ``OpenApiBotAdapter`` (Bearer). ``cookie`` / ``referer`` are kept
    empty on purpose: a service-level ``api_key`` pre-grants the target bots OOB,
    so ``ensure_grant`` hits the allowed-bots list and is a no-op — the grant
    POST (which needs a logged-in Cookie/Referer) never fires in this path.
    """

    api_key: str = ""
    api_key_prefix: str = ""
    base_url: str = ""
    cookie: str = ""
    referer: str = ""


@dataclass(frozen=True)
class BcsTokenProviderImpl:
    """Frozen credential holder satisfying the ``BcsTokenProvider`` Protocol.

    Drives ``BcsHttpAdapter`` on two surfaces:
    - coop_group group/session lifecycle — HMAC ``X-ECB-Token`` /
      ``X-ECB-Signature`` (sign-string ``f\"{ts}{method}{path}\"``) from
      ``token`` / ``secret`` (HMAC key/secret literals read from the
      ``bcs_client`` user_config block).
    - task-mode roster ``GET /providers/{provider_id}/bots/by-task-modes`` —
      Bearer ``provider_admin_token``; the ``provider_id`` /
      ``provider_admin_token`` pair REUSES the ``bcn`` block identity (one BCS
      provider per env), resolved env-aware. Empty bcn -> provider_id empty ->
      roster degrades (HMAC group creation still works).
    - task-result callback — env-resolved ``task_callback_url`` handed to BCS so
      it can post task results back to the configured endpoint; empty -> not
      surfaced (callback off).
    """

    base_url: str = ""
    token: str = ""
    secret: str = ""
    provider_id: str = ""  # env-resolved bcn provider_id (reuses bcn identity)
    provider_admin_token: str = ""  # env-resolved bcn Bearer mgmt token
    task_callback_url: str = ""  # env-resolved task-result callback host


def _env_select(prod: str, pre: str) -> str:
    """Env-aware pair selector — pre env -> ``pre`` value, else ``prod`` value.

    ``get_current_env()`` normalizes prepub->pre / gray->prod.
    """
    from agentclaw.community.utils.env_utils import get_current_env

    return pre if get_current_env() == "pre" else prod


def _env_base_url(cfg: OpenApiBotConfig | BcsClientConfig) -> str:
    """Select the env-resolved base url from a task config block."""
    return _env_select(cfg.base_url, cfg.base_url_pre)


def _resolve_bcn_provider_identity(bcn_cfg: BcnConfig) -> tuple[str, str]:
    """Env-resolve the BCS provider management identity reused from the bcn block.

    One BCS provider per env: prod -> bcn.provider_id_prod /
    provider_admin_token_prod, pre/prepub -> ..._pre. Other envs (dev) ->
    ("", "") (roster degrades). Mirrors ``BcnService._get_provider_config`` env
    selection but only the env switch (an empty provider_id is dealt with by
    ``BcsHttpAdapter.list_bots_by_task_modes`` itself).
    """
    from agentclaw.community.utils.env_utils import get_current_env

    env = get_current_env()
    if env == "prod":
        return bcn_cfg.provider_id_prod, bcn_cfg.provider_admin_token_prod
    if env == "pre":
        return bcn_cfg.provider_id_pre, bcn_cfg.provider_admin_token_pre
    return "", ""


class TaskRunnerIntegrationModule(Module):
    """Bind OpenApiBotPort / BcsClientPort to community credential-fed adapters.

    Installed ONLY in the ``community`` deployment profile (see
    ``profile_modules``); NOT in base, so the corp column keeps its own
    ``CorpTaskIntegrationModule`` unchanged (the two columns are import-disjoint;
    a community distribution never imports ``agentclaw.corp``). ``task_service``
    resolves an unbound port to ``None`` and degrades dispatch without raising.
    """

    # ── config providers (read user_config blocks -> typed dataclasses) ────

    @singleton
    @provider
    def openapi_bot(self) -> OpenApiBotConfig:
        """Read the ``openapi_bot`` block -> ``OpenApiBotConfig``.

        Stores the raw env-aware host pair (``base_url``=prod, ``base_url_pre``
        =pre); per-env selection happens in ``_env_base_url`` below. Hosts are
        non-secret and stay literal in YAML. ``api_key_secret`` is the LITERAL
        Bearer api_key (env-injected, no Mist in the community build) — NOT a
        credential name. Missing block -> all empty -> port None (fail-closed;
        unconfigured community singlebox/CI degrades).
        """
        block = _block("openapi_bot")
        return OpenApiBotConfig(
            base_url=block.get("base_url", ""),
            base_url_pre=block.get("base_url_pre", ""),
            api_key_secret=block.get("api_key_secret", ""),
            api_key_prefix=block.get("api_key_prefix", ""),
        )

    @singleton
    @provider
    def bcs_client(self) -> BcsClientConfig:
        """Read the ``bcs_client`` block -> ``BcsClientConfig``.

        Stores the raw env-aware host pair; per-env selection in ``_env_base_url``.
        ``token_secret`` / ``secret_secret`` are the LITERAL HMAC key/secret
        (env-injected, no Mist); BOTH required or the port stays None
        (fail-closed). ``task_callback_url`` / ``task_callback_url_pre`` env-aware
        callback hosts (empty -> callback off).
        """
        block = _block("bcs_client")
        return BcsClientConfig(
            base_url=block.get("base_url", ""),
            base_url_pre=block.get("base_url_pre", ""),
            token_secret=block.get("token_secret", ""),
            secret_secret=block.get("secret_secret", ""),
            task_callback_url=block.get("task_callback_url", ""),
            task_callback_url_pre=block.get("task_callback_url_pre", ""),
        )

    # ── port providers (construct adapters, bind Ports) ────────────────────

    @singleton
    @provider
    @inject
    def openapi_bot_port(
        self,
        cfg: OpenApiBotConfig,
    ) -> OpenApiBotPort:
        # api_key_secret is read as the LITERAL Bearer api_key (not a Mist name);
        # must be a real BaaS Open API key value; empty -> fail-closed None.
        api_key = cfg.api_key_secret
        base_url = _env_base_url(cfg)
        if not base_url or not api_key:
            logger.warning(
                "[task][community-task] openapi_bot not configured "
                "(base_url/api_key_secret empty) — single_bot port None (fail-closed)。"
                "排查: openapi_bot.api_key_secret 是否填了真 BaaS Open API key?"
            )
            return None  # type: ignore[return-value]
        keys = ApiKeyProviderImpl(
            api_key=api_key,
            api_key_prefix=cfg.api_key_prefix or "",
            base_url=base_url,
        )
        logger.info(
            "[task][community-task] openapi_bot 端口装配 OK -> OpenApiBotAdapter base_url=%s api_key_prefix=%s",
            base_url, keys.api_key_prefix or "<none>",
        )
        return OpenApiBotAdapter(keys)

    @singleton
    @provider
    @inject
    def bcs_client_port(
        self,
        cfg: BcsClientConfig,
        bcn_cfg: BcnConfig,
    ) -> BcsClientPort:
        token = cfg.token_secret
        secret = cfg.secret_secret
        base_url = _env_base_url(cfg)
        if not base_url or not token or not secret:
            logger.warning(
                "[task][community-task] bcs_client not configured "
                "(base_url/token_secret/secret_secret empty) — coop_group port None (fail-closed)。"
                "排查: bcs_client.token_secret/secret_secret 是否填了真 HMAC key/secret 字面值?"
            )
            return None  # type: ignore[return-value]
        provider_id, provider_admin_token = _resolve_bcn_provider_identity(bcn_cfg)
        token_provider = BcsTokenProviderImpl(
            base_url=base_url,
            token=token,
            secret=secret,
            provider_id=provider_id,
            provider_admin_token=provider_admin_token,
            task_callback_url=_env_select(cfg.task_callback_url, cfg.task_callback_url_pre),
        )
        logger.info(
            "[task][community-task] bcs_client 端口装配 OK -> BcsHttpAdapter base_url=%s provider_id=%s",
            base_url, provider_id or "<none(roster off)>",
        )
        return BcsHttpAdapter(token_provider)

    @singleton
    @provider
    @inject
    def bcs_token_provider(
        self,
        cfg: BcsClientConfig,
        bcn_cfg: BcnConfig,
    ) -> BcsTokenProvider:
        """Callback route base_url source (overriding TaskModule default binding).

        Same ``bcs_client`` block as ``bcs_client_port`` (env-aware base_url).
        Overrides ``TaskModule``'s ``LocalBcsTokenProvider`` (last-binding-wins;
        this module is registered after base in the community profile) so the
        task-result callback base_url points at the community BCS endpoint, not
        localhost. ALWAYS returns a holder (never fail-closed None) — the callback
        route reads ``bcs_token.base_url``; None would ``AttributeError``. Unset
        -> ``base_url=""`` -> relative URL -> httpx raises -> ``_dispatch`` except
        fallback ingest. HMAC creds (token/secret) stay on ``bcs_client_port``;
        this provider only serves the bare GET run-detail/DAG callback route.
        """
        base_url = _env_base_url(cfg)
        provider_id, provider_admin_token = _resolve_bcn_provider_identity(bcn_cfg)
        return BcsTokenProviderImpl(
            base_url=base_url,
            token=cfg.token_secret,
            secret=cfg.secret_secret,
            provider_id=provider_id,
            provider_admin_token=provider_admin_token,
            task_callback_url=_env_select(cfg.task_callback_url, cfg.task_callback_url_pre),
        )
