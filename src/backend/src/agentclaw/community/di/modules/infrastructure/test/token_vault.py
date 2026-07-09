"""Token-vault concern — corp-free test / singlebox binding (B11).

The corp column binds ``CorpTokenVaultModule`` (a Mist-backed AES key via the
``SecretResolver``); under test/singlebox that resolver is ``LocalSecretResolver``,
which returns ``None``, so the vault always degrades to an **empty master key**
(``encrypt`` = passthrough, plaintext at rest). This module binds that end state
directly — ``TokenVault(master_key="")`` — with no corp import and no
``SecretResolver`` dependency, so the ``test``/``singlebox`` column is corp-free.

Behavior is identical to the prior corp-reuse path under test/singlebox (both
yield an empty-key vault). ``corp_test`` keeps ``CorpTokenVaultModule`` (via the
reuse column) so the corp suite exercises the real Mist-backed construction seam.
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.core.bot_management.token_vault import TokenVault


class TestTokenVaultModule(Module):
    """test / singlebox: empty-key ``TokenVault`` (encrypt = passthrough)."""

    def configure(self, binder: Binder) -> None:
        binder.bind(TokenVault, to=TokenVault(master_key=""), scope=singleton)
