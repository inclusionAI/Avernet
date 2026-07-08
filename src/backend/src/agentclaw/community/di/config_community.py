"""Community-only typed configuration dataclasses.

Split out of ``di/config.py`` so the community (open-source) config surface is
physically separate from the corp config. Every type here is consumed **only**
by the community deploy profile — corp/test never resolve them. Their providers
live on the per-concern community DI modules
(``di/modules/infrastructure/community/*``), not on the shared ``ConfigModule``.

Pure dataclasses (no internal-package imports), so this module ships cleanly with
the open-source distribution.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BcsAuthConfig:
    """BCS unified-auth delegation config — drives the community ``OidcAuthPlugin``.

    In community / singlebox deploys there is no corporate SSO; BCS is the
    unified-auth entry point. The plugin calls ``{base_url}{user_path}`` (BCS
    ``GET /auth/user``), forwarding inbound cookies. ``operator_subjects`` is the
    set of BCS ``user_id`` values allowed through ``require_operator``. Sourced
    from the ``bcs`` block of ``user_config``; corp/test never resolve this type.
    """

    base_url: str = "http://127.0.0.1:21000"
    user_path: str = "/auth/user"
    timeout: float = 10.0
    operator_subjects: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CommunityDatabaseConfig:
    """Relational-store connection for the community ``CommunityDatabase``.

    Community-only: sourced from the ``database`` block of ``user_config``
    (overridable by the ``DATABASE_URL`` env var in the provider). ``url`` is a
    SQLAlchemy URL — SQLite file / Postgres / MySQL. The default is a local
    SQLite file so a bare community boot works; a real deploy points it at a
    pre-provisioned store. Corp/test never resolve this type.
    """

    url: str = "sqlite:///./data/agentclaw.db"


@dataclass(frozen=True)
class CommunityCacheConfig:
    """Cache + distributed-lock backend for the community ``CommunityCache``.

    Community-only: sourced from the ``cache`` block of ``user_config``
    (overridable by the ``REDIS_URL`` env var in the provider). A non-empty
    ``redis_url`` selects the Redis backend (KV + ``SET NX`` lock); empty selects
    the in-process fallback (single-process only — unsafe for a multi-worker
    deploy). Corp/test never resolve this type.
    """

    redis_url: str = ""


@dataclass(frozen=True)
class CommunityS3Config:
    """S3-compatible connection for ``CommunityS3ObjectStorage``.

    Credentials are NOT held here — they come from boto3's standard env chain
    (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``), never committed to yaml.
    ``endpoint`` empty means AWS's default endpoint; set it for MinIO / R2 / the
    Aliyun-OSS S3 endpoint.
    """

    endpoint: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    secure: bool = True


@dataclass(frozen=True)
class CommunityObjectStorageConfig:
    """Object-storage backend selector for the community profile.

    Community-only: sourced from the ``object_storage`` block of ``user_config``.
    ``backend`` picks the impl: ``"fs"`` (default, local filesystem under
    ``fs_root``) or ``"s3"`` (S3-compatible via ``s3``). Corp/test never resolve
    this type.
    """

    backend: str = "fs"
    fs_root: str = "./data/objstore"
    s3: CommunityS3Config = field(default_factory=CommunityS3Config)


@dataclass(frozen=True)
class CommunityMcpConfig:
    """MCP-center registry config for the community ``CommunityMCPCenter``.

    Community-only: sourced from the ``mcp`` block of ``user_config``.
    ``registry_config_path`` optionally points at a local MCP-server catalog file
    (same shape as ``configs/local-mcp-servers.yaml``). Empty means no
    marketplace catalog — bring-your-own MCP servers (per-user config) still work,
    since the bot-run path does not depend on the catalog. Corp/test never resolve
    this type.
    """

    registry_config_path: str = ""


@dataclass(frozen=True)
class CommunitySecretConfig:
    """Env-var secret resolution for the community ``CommunitySecretResolver``.

    Community-only: sourced from the ``secret`` block of ``user_config``.
    ``get_secret(name)`` reads ``{env_prefix}{NAME}_USER`` /
    ``{env_prefix}{NAME}_VALUE`` from the environment. Corp/test never resolve
    this type.
    """

    env_prefix: str = "AGENTCLAW_SECRET_"
