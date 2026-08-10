"""Domain errors for bot→app authorizations.

One error, and it exists to keep two failures distinguishable at the HTTP
boundary: withdrawing an authorization that is not there is not the same
outcome as withdrawing one that is, and a caller reconciling its own records
needs to tell them apart.

Nothing here corresponds to "you may not manage this bot". That refusal is not
this module's to raise: it comes from the owner-scoped bot read, which answers
a non-owner exactly as it answers a caller naming a bot that does not exist.
Giving it a distinct error here would be giving it a distinct answer, which is
the disclosure the masked refusal exists to prevent.
"""

from __future__ import annotations


class GrantNotFoundError(Exception):
    """No live authorization matched the scope named."""


class GrantIdentityTooLongError(Exception):
    """A user id is too long for a grant to be stored and later found.

    Raised at consent time rather than letting the write happen. The two user
    columns are capped by the unique key's byte budget, and a value beyond it
    would be rejected on a strict server or **silently truncated** on a
    permissive one — and a truncated identity is worse than a rejected one: the
    row looks live in every listing while no lookup can ever match it, so the
    application is unauthorized forever and nothing says why.

    Distinct from :class:`GrantNotFoundError` because it is not a statement
    about what exists. The caller asked for something this record cannot
    represent, which is a bad request rather than a missing one.
    """
