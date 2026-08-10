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
