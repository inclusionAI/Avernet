"""Composition root for the PrincipalSigner (auth design §7.1).

Builds the bare (HMAC) signer from typed config: non-secret parameters
(``kid`` / ``issuer`` / ``ttl_seconds``) come from ``user_config.principal_signer``
and the signing key is resolved via the :class:`SecretResolver` SPI. The
``SecretResolver`` implementation is selected by the ``plugins.secret`` config
selector (community = env-backed; enterprise may register a corp/KMS-backed
option via ``plugin_registry``), so the resolver is resolved from the DI
container and passed in — this module never constructs a concrete flavor. The
adapter receives the built signer via ``app.state.principal_signer`` and calls
it at the forwarder seam.
"""

from __future__ import annotations

import os

from gateway.community.config import UserConfig
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    load_signer_config,
)
from gateway.community.spi.principal_signer import PrincipalSigner
from gateway.community.spi.secret_resolver import SecretResolver

# ``SERVER_ENV`` spellings that mean a deployment which must have a real signing
# key. Read from the same variable the config loader reads.
#
# The **aliases are load-bearing**, not defensive padding. The backend does not
# compare ``SERVER_ENV`` raw: ``utils/env_utils.py::get_current_env`` folds
# ``prepub`` into ``pre`` and ``gray`` into ``prod`` before gating its verifier
# on the result. Both aliases are reachable — ``scripts/app.sh`` exports
# ``SERVER_ENV=prepub`` for its supported ``--env prepub`` — and a raw
# comparison here would silently split the two sides apart in exactly those
# profiles: the backend refuses to boot without a key while the gateway boots
# and answers 500 per request. That is the "one contract, one rule" claim
# failing in the deployments that most need it, so the two mappings must stay in
# step. Backend-side change here means a change there, and vice versa.
_STRICT_ENVS = frozenset({"pre", "prepub", "prod", "gray"})


def build_principal_signer(
    *,
    user_config: UserConfig,
    secret_resolver: SecretResolver,
) -> PrincipalSigner:
    """Build the PrincipalSigner from typed config + a resolved SecretResolver.

    Reads ``SERVER_ENV`` to decide whether a missing key is fatal. The
    environment read belongs here rather than in the plugin: this is the
    composition root, and the plugin stays a pure function of the arguments it
    is handed.

    Raises:
        PrincipalSigningKeyMissingError: in a strict profile with no key —
            ``pre``/``prepub``/``prod``/``gray``, matching what the backend
            normalizes those spellings to.
    """
    server_env = os.getenv("SERVER_ENV", "").strip().lower()
    return BarePrincipalSigner(
        load_signer_config(
            user_config.principal_signer,
            secret_resolver,
            strict=server_env in _STRICT_ENVS,
        )
    )
