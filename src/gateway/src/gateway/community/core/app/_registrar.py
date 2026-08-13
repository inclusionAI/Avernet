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

import asyncio
from dataclasses import dataclass
from typing import Any

from ._key_gen import APIKeyGenerator
from ._orm import API_KEY_PREFIX_LEN
from ._repository import AppRepository, PrefixTakenError

# Prefix collisions are vanishingly unlikely (8 base62 characters), but the
# column is unique, so one would otherwise surface as a 500. Retrying costs
# nothing and mirrors the secbaas key service.
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
        """Register an app, returning its record and its one-time plaintext key.

        The insert is inside the retry, not after it: ``exists_prefix`` alone is
        a check-then-act, and two concurrent registrations that generate the
        same prefix would both pass it and leave the second insert to fail. The
        unique index is the real guarantee, so a lost race is retried like any
        other collision.
        """
        last_race: PrefixTakenError | None = None
        for _ in range(_MAX_PREFIX_ATTEMPTS):
            api_key = APIKeyGenerator.generate()
            api_key_prefix = api_key[:API_KEY_PREFIX_LEN]
            if await self._repository.exists_prefix(api_key_prefix):
                continue  # cheap pre-check: skip the hash we would throw away
            # Hashing is the same ~30ms PBKDF2 as verification, so it goes off
            # the event loop for the same reason: registration must not stall
            # unrelated traffic, and the retry can run it more than once.
            api_key_hash = await asyncio.to_thread(APIKeyGenerator.hash_key, api_key)
            try:
                # The registering caller is both creator and modifier.
                app_id = await self._repository.store(
                    api_key_hash=api_key_hash,
                    api_key_prefix=api_key_prefix,
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
            except PrefixTakenError as exc:
                last_race = exc  # lost the race to a concurrent registration
                continue
            return IssuedApp(
                id=app_id,
                app_name=app_name,
                owners=owners,
                app_type=app_type,
                tenant=tenant,
                api_key=api_key,
            )
        # ``from last_race`` is a no-op when no race occurred: this raise is
        # outside any handler, so there is no exception context to suppress.
        raise PrefixAllocationError(
            f"no unused API key prefix found in {_MAX_PREFIX_ATTEMPTS} attempts"
        ) from last_race
