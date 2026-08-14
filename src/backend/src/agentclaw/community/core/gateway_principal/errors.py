"""Errors raised while verifying the gateway's forwarded principal.

One error type on purpose. Every way verification can fail — no signing key
configured, a bad signature, an expired token, the wrong audience, a payload we
cannot parse — is the same answer to the caller: ``401`` with a fixed message.
Distinguishing them in the response would tell an attacker which part of a forged
token to fix next, so the distinction lives in the log, not in the envelope.
"""

from __future__ import annotations


class PrincipalVerificationError(Exception):
    """Raised when a forwarded principal token cannot be trusted (→ 401).

    The message is internal-facing (it names the specific failure for the log);
    ``ENVELOPE_ERRORS`` maps this type to a fixed public message, so it is never
    rendered with ``str(exc)``.
    """
