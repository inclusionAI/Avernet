"""BaaS migration domain — move a secbaas API key onto the gateway, once.

A **deliberately temporary** module. It exists to carry a finite population of
``baas_api_key`` rows into ``avernet_application`` (plus the bot authorizations
they imply into the backend's ``ac_bot_app_grant``) while their holders keep
using the keys they already have. When that population is empty, this package,
its endpoint, its ORM mirrors and its migration SQL all go together.

What makes an in-place copy possible at all is that ``core/app/_key_gen.py`` is
a byte-identical copy of secbaas's ``APIKeyGenerator``: the stored hash records
only its salt, leaving the digest, the iteration count and the encoding as
constants shared by both sides. So a hash moved from one table to the other
keeps verifying against the same plaintext key, and nothing the caller holds has
to change. That parity is load-bearing here, not incidental — the parity test in
``tests/unit/plugins/test_app_key_gen.py`` is what keeps it true.

Two tables here are **not the gateway's**. ``baas_api_key`` is read, and
``ac_bot_app_grant`` / ``ac_bot_app_grant_log`` are written, across a module
boundary. ``_orm.py`` documents the bound on that: the models are excluded from
the MariaDB plugin's ``create_all`` whitelist, and they must be kept in step
with the owning modules' definitions for as long as this package exists.
"""

from ._migrator import DEFAULT_MIGRATION_TENANT, BaasKeyMigrator
from ._orm import (
    APP_NAME_MAX_LENGTH,
    BAAS_API_KEY_PREFIX_LEN,
    GRANT_APP_NAME_MAX_LENGTH,
    GRANT_ENV_MAX_LENGTH,
    GRANT_IDENTITY_MAX_LENGTH,
    BaasApiKeyRow,
    BotAppGrantLogRow,
    BotAppGrantRow,
)
from ._policy import WILDCARD, parse_allowed_bots, split_bot_reference
from ._records import GrantTarget, SourceKey
from ._repository import (
    AlreadyMigratedError,
    BaasMigrationRepository,
    PrefixConflictError,
)

__all__ = [
    "APP_NAME_MAX_LENGTH",
    "BAAS_API_KEY_PREFIX_LEN",
    "DEFAULT_MIGRATION_TENANT",
    "GRANT_APP_NAME_MAX_LENGTH",
    "GRANT_ENV_MAX_LENGTH",
    "GRANT_IDENTITY_MAX_LENGTH",
    "WILDCARD",
    "AlreadyMigratedError",
    "BaasApiKeyRow",
    "BaasKeyMigrator",
    "BaasMigrationRepository",
    "BotAppGrantLogRow",
    "BotAppGrantRow",
    "GrantTarget",
    "PrefixConflictError",
    "SourceKey",
    "parse_allowed_bots",
    "split_bot_reference",
]
