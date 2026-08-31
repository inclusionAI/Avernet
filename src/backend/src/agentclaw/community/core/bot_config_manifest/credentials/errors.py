"""Error vocabulary for tenant source credentials (W3, #1471).

Names, never values: every message in this family may carry the credential
*name* (it is the identifier callers must be able to discuss), and must not
carry the secret, the ciphertext, or the decrypted header value.
"""
from __future__ import annotations


class CredentialError(ValueError):
    """Invalid credential input (400-class): bad name/header/prefixes/type."""


class CredentialNotFoundError(CredentialError):
    """The named credential does not exist (404)."""


class MasterKeyUnavailableError(CredentialError):
    """Fail-closed refusal: the profile requires ciphertext at rest (503).

    ``TokenVault`` deliberately falls through to plaintext when the master
    key is empty — right for singlebox/CI, unacceptable for tenant tokens
    in production: one keystore misconfiguration would leave every
    tenant's tokens in cleartext. The service raises this before any
    write when its profile is fail-closed and no master key resolved.
    """
