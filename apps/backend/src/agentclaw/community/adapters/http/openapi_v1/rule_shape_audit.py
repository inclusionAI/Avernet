"""Boot-time self-audit of the openapi authorization rule table's shape.

``authorization_rules.py`` invokes :func:`assert_member_writes_name_explicit_origin`
at the bottom of its table, so the rule shape asserts itself while it imports:
a row that would publish a Space-synthesized MEMBER into the edit domain fails
the boot instead of lurking until a permission review notices.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentclaw.community.core.bot_collaborator.models import PermissionLevel

from .models_authorization import Authorization, Check

#: Methods that never mutate, so a MEMBER rule row on them names no explicit
#: origin: reads are exactly where the Space-synthesized member belongs.
_READ_METHODS = ("GET", "HEAD", "OPTIONS")


def assert_member_writes_name_explicit_origin(
    authorization: Mapping[tuple[str, str], Authorization],
) -> None:
    """Every non-read ``Check`` row at MEMBER must name its explicit origin.

    Instruction #2551: the explicit source-mark is rule-shaped, not decor
    — a MEMBER write or operations row that silently misses it publishes the
    Space-synthesized MEMBER into the edit domain, re-opening exactly the
    hole the flag exists to close, with no test that can notice an omission
    (an unmarked row is indistinguishable from a read row). So the table
    asserts its own shape: a new write row either names its origin, or
    fails the boot loudly.
    """
    offenders = sorted(
        f"{method} {path}"
        for (method, path), rule in authorization.items()
        if method not in _READ_METHODS
        and isinstance(rule, Check)
        and rule.level is PermissionLevel.MEMBER
        and not rule.explicit
    )
    if offenders:
        raise ValueError(
            "MEMBER write/operations rows must name their explicit origin "
            "(Check …, explicit=True) — Space-synthesized members are "
            "refused there by design; found unmarked rows:\n  "
            + "\n  ".join(offenders)
        )


__all__ = ["assert_member_writes_name_explicit_origin"]
