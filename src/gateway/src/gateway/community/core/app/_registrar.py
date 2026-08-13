"""AppRegistrar — register an app: mint an API key, delegate persistence, return the record.

The generated key is returned to the caller **once**, in
:attr:`IssuedApp.api_key`. Only its PBKDF2 hash and its 8-character prefix are
persisted (via :meth:`AppRepository.store`, since all DB touch lives in the
repository), so the key cannot be recovered from the registry afterwards — a
lost key means issuing a new one.

App credentials do not expire (the table has no ``expire_at``). Registration no
longer mints a JWT and therefore no longer needs the gateway
``PrincipalSigner``; that signer still serves per-request principal assertions
and access-key issuance. The app's stable identity is its surrogate ``id``,
returned by ``store``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._key_gen import APIKeyGenerator
from ._repository import _PREFIX_LEN, AppRepository

# Prefix collisions are vanishingly unlikely (8 base62 characters), but the
# column is unique, so a collision would surface as an IntegrityError on insert.
# Retrying costs nothing and mirrors the secbaas key service.
_MAX_PREFIX_ATTEMPTS = 3


class PrefixAllocationError(RuntimeError):
    """No unused API-key prefix could be allocated; nothing was written."""


@dataclass(frozen=True)
class IssuedApp:
    """An app just registered: its record fields plus the plaintext key.

    ``api_key`` is the only time the plaintext exists outside the caller's
    hands — the registry keeps just its hash.
    """

    id: int
    app_name: str
    owners: str
    app_type: str
    tenant: str
    api_key: str


class AppRegistrar:
    """Mint an API key, persist its hash via the repository, return the record."""

    def __init__(self, repository: AppRepository) -> None:
        self._repository = repository

    async def register(
        self,
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
        *,
        creator: str,
        status: str = "ACTIVE",
        env: str = "",
        config: dict[str, Any] | None = None,
    ) -> IssuedApp:
        api_key = await self._allocate_key()
        # The registering caller is both creator and modifier on register.
        app_id = await self._repository.store(
            api_key_hash=APIKeyGenerator.hash_key(api_key),
            api_key_prefix=api_key[:_PREFIX_LEN],
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
            status=status,
            env=env,
            config=config,
            creator=creator,
            modifier=creator,
        )
        return IssuedApp(
            id=app_id,
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
            api_key=api_key,
        )

    async def _allocate_key(self) -> str:
        """Generate a key whose prefix no app holds yet.

        Hashing is deliberately left until after a prefix is settled: it costs
        ~60ms of CPU, and a collision would throw that work away.
        """
        for _ in range(_MAX_PREFIX_ATTEMPTS):
            api_key = APIKeyGenerator.generate()
            if not await self._repository.exists_prefix(api_key[:_PREFIX_LEN]):
                return api_key
        raise PrefixAllocationError(
            f"no unused API key prefix found in {_MAX_PREFIX_ATTEMPTS} attempts"
        )
