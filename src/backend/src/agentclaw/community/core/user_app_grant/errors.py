"""Domain errors for user→app account-level authorizations.

Two errors, each keeping a distinct outcome distinguishable at the HTTP
boundary. Neither corresponds to "this application may not act as this user"
on a governed operation: that refusal is the admission seam's, and it answers
with the same masked 404 a nonexistent bot receives — see
``adapters/http/openapi_v1/principal.py::require_granted_user``.
"""

from __future__ import annotations


class UserGrantNotFoundError(Exception):
    """No live account-level authorization matched the scope named.

    Raised by a withdrawal that found nothing to withdraw, so the adapter can
    answer 404 distinctly from a successful withdrawal.
    """


class UserGrantIdentityTooLongError(Exception):
    """A user id is too long for a grant to be stored and later found.

    Raised at consent time rather than letting the write happen: the column is
    in the unique key, and a value beyond its width would be rejected on a
    strict server or silently truncated on a permissive one — and a truncated
    identity is worse than a rejected one, because the row then looks live in
    every listing while no lookup can ever match it.
    """
