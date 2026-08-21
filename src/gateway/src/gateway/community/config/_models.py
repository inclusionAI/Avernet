"""Configuration data models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass
class WebConfig:
    port: int = 8888
    start: str = "gateway.community.adapters.web.app:app"
    enable_api_docs: bool = True


@dataclass
class ModuleConfig:
    web: WebConfig | None = None


@dataclass
class LogConfig:
    trace_log_dir: str = ""
    log_level: str = "INFO"
    log_dir: str = ""


class AuthnPluginConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="allow")
    app_token: str = Field(default="stub")
    tenant: str = Field(default="stub")


class DatabasePluginConfig(BaseSettings):
    """Database plugin selection — ``sqlite`` by default (community-safe).

    ``mariadb`` requires a ``database_url``. Credentials may be supplied via
    the ``DATABASE_URL`` environment variable (expanded by the config loader).
    """

    model_config = SettingsConfigDict(extra="allow")
    plugin_database: str = Field(default="sqlite")
    database_url: str = ""
    create_schema: bool = Field(default=False)
    seed_data: bool = Field(default=False)


class SecretConfig(BaseModel):
    """SecretResolver configuration (mirrors backend ``CommunitySecretConfig``).

    ``env_prefix`` is the prefix the community (env-backed) SecretResolver
    prepends to ``{NAME}_VALUE`` / ``{NAME}_USER`` lookups. Enterprise overlays
    may swap the resolver implementation entirely via the plugin registry.
    """

    model_config = {"extra": "allow"}
    env_prefix: str = "AVERNET_SECRET_"


class PrincipalSignerPluginConfig(BaseModel):
    """Non-secret PrincipalSigner config — read from ``user_config.principal_signer``.

    The signing key (a secret) is resolved at runtime via the
    :class:`~gateway.community.spi.secret_resolver.SecretResolver` using
    ``secret_name``; ``kid`` / ``issuer`` / ``ttl_seconds`` are non-secret and
    live in the config file.
    """

    model_config = {"extra": "allow"}
    secret_name: str = "principal_signing_key"
    kid: str = "bare"
    issuer: str = "gateway"
    ttl_seconds: int = 60


#: Probes for a CORS pattern that admits origins its author never enumerated.
#: Every host is under ``.invalid`` (RFC 2606), which no real deployment can
#: serve from, so a pattern pinned to a real host suffix cannot match one by
#: accident. Both schemes a browser origin can carry are probed, with and
#: without a port: a catch-all is a catch-all whether it is written
#: ``https://.*``, ``http://.*`` or ``https://.*:8443``, and probing only one
#: shape would refuse that shape while admitting its neighbours.
_CANARY_ORIGINS = (
    "https://canary-origin.invalid",
    "http://canary-origin.invalid",
    "https://canary-origin.invalid:8443",
    "http://canary-origin.invalid:8080",
)


class CorsConfig(BaseModel):
    """Browser CORS allow-list for the gateway's own edge (``user_config.cors``).

    The gateway is the origin a browser actually talks to, so it is the only hop
    that can answer a preflight: the request that carries no credential and must
    never reach authentication. An upstream's own allow-list governs callers that
    reach *it* directly and says nothing about the gateway's address, which is
    why this list lives here rather than being inherited from a component.

    Neutral default = localhost origins only, so a single-box UI works out of the
    box; every deployment adds its own frontend origin through the ``cors`` block
    of its ``application-<env>.yaml`` overlay. Origins are enumerated exactly or
    matched by one of ``allow_origin_regex``; either field is REFUSED at load
    time when it would admit an arbitrary origin (see
    :meth:`_reject_wildcard_origin` and :meth:`_reject_universal_regex`).
    """

    model_config = {"extra": "allow"}
    allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost",
            "http://localhost:3000",
            "http://localhost:8000",
            "http://localhost:8080",
            "http://localhost:8888",
        ]
    )
    allow_origin_regex: list[str] = Field(default_factory=list)

    @field_validator("allow_origins")
    @classmethod
    def _reject_wildcard_origin(cls, origins: list[str]) -> list[str]:
        """Refuse ``"*"`` at load time rather than serving it.

        The edge always answers with ``Access-Control-Allow-Credentials: true``,
        and a wildcard does not fail loudly under that setting: Starlette
        replaces ``"*"`` with whichever origin asked (``CORSMiddleware.send``
        takes the ``allow_all_origins and allow_credentials`` branch), so a
        deployment that wrote the conventional ``allow_origins: ["*"]`` would
        boot, look correct, and admit every site on the internet to credentialed
        calls through the gateway. A config error a browser cannot catch for us
        is one this boundary has to catch itself.
        """
        if any(origin.strip() == "*" for origin in origins):
            raise ValueError(
                'cors.allow_origins must not contain "*": the gateway sends '
                "Access-Control-Allow-Credentials: true, so a wildcard admits "
                "every origin to credentialed calls. List each origin, or match "
                "them with cors.allow_origin_regex."
            )
        return origins

    @field_validator("allow_origin_regex")
    @classmethod
    def _reject_universal_regex(cls, patterns: list[str]) -> list[str]:
        """Refuse a pattern that would admit an origin nobody configured.

        The sibling check above points an operator at this field, so this field
        must not be the way back into the hole it closes: with credentials
        enabled, ``allow_origin_regex: [".*"]`` — a plausible shorthand for "any
        localhost port" — admits every site on the internet exactly as ``"*"``
        would, and boots just as quietly.

        The probes are canaries rather than a proof: a pattern that
        ``fullmatch``es a host under the reserved ``.invalid`` TLD (RFC 2606 —
        never resolvable, so no real allow-list names it) is matching things its
        author did not enumerate. Both schemes are probed, with and without a
        port, so a catch-all is caught however it is spelled. That covers the
        ``.*`` / ``.+`` / ``<scheme>://.*`` shapes an operator actually writes;
        a deliberately broad pattern that dodges every probe is not claimed to
        be caught. A pattern that pins anything real — ``http://localhost:[0-9]+``,
        a host suffix — matches no probe and passes.

        Compiling here is the second half of the boundary: a malformed pattern
        fails at config load, naming the entry, rather than at middleware
        construction — where it would take the whole gateway down at boot.
        """
        for pattern in patterns:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"cors.allow_origin_regex entry {pattern!r} is not a valid "
                    f"regular expression: {exc}"
                ) from exc
            for canary in _CANARY_ORIGINS:
                if compiled.fullmatch(canary):
                    raise ValueError(
                        f"cors.allow_origin_regex entry {pattern!r} matches the "
                        f"arbitrary origin {canary!r}: the gateway sends "
                        "Access-Control-Allow-Credentials: true, so a pattern "
                        "this broad admits every origin to credentialed calls. "
                        "Pin the host suffix each environment actually serves."
                    )
        return patterns


class PluginConfig(BaseSettings):
    """Plugin selection config for gateway DI container.

    Values are validated only for minimum length. The community
    package MUST NOT restrict which selectors enterprise can use —
    enterprise registrations (auth, cache, database, etc.) inject
    additional provider options at bootstrap time through the
    ``plugin_registry`` module.
    """

    model_config = SettingsConfigDict(extra="allow")
    config_section: ClassVar[str] = "plugins"

    forwarder: str = Field(default="httpx", min_length=1)
    schema_catalog: str = Field(default="file", min_length=1)
    cache: str = Field(default="stub", min_length=1)
    auth: str = Field(default="stub", min_length=1)
    secret: str = Field(default="community", min_length=1)
    authn: AuthnPluginConfig = Field(default_factory=AuthnPluginConfig)
    database: DatabasePluginConfig = Field(default_factory=DatabasePluginConfig)
    cache_redis: dict[str, Any] = Field(default_factory=dict)
    secret_aliyun_kms: dict[str, Any] = Field(default_factory=dict)


class UserConfig(BaseModel):
    model_config = {"extra": "allow"}
    plugins: PluginConfig = Field(default_factory=PluginConfig)
    secret: SecretConfig = Field(default_factory=SecretConfig)
    principal_signer: PrincipalSignerPluginConfig = Field(
        default_factory=PrincipalSignerPluginConfig
    )
    upstream_vars: dict[str, str] = Field(default_factory=dict)
    identity_strategies: dict[str, list[str]] = Field(default_factory=dict)
    route_security: dict[str, dict[str, str]] = Field(default_factory=dict)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    upstreams: dict[str, Any] = Field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            return default

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


@dataclass
class Config:
    app_name: str = "gateway"
    enable_sidecar: bool = False
    workers: int = 1
    log_config: LogConfig = field(default_factory=LogConfig)
    module_config: ModuleConfig = field(default_factory=ModuleConfig)
    user_config: UserConfig = field(default_factory=UserConfig)
    raw: dict = field(default_factory=dict)
    config_dir: Path | None = None
