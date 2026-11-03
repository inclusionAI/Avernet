"""The one error type raised while signing or verifying a principal token."""

from __future__ import annotations


class PrincipalTokenError(Exception):
    """A principal token could not be signed or could not be trusted.

    One type on purpose. Every failure — no key configured, a bad signature, an
    expired token, the wrong audience or issuer — gets the same answer from a
    receiving service: a fixed ``401``. Telling the failures apart in a response
    would tell a forger which part of their token to fix next, so the
    distinction lives in the message, which is for the log only.

    Services may subclass this to give it their own name; anything catching
    this type then catches theirs too.
    """
