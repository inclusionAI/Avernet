"""``${BOT_*}`` substitution — the whitelist, and the resolver behind it.

Two halves of one contract, kept in one module so they cannot disagree: the set
a document may name, and the values those names resolve to. A whitelist that
some other module resolves is a whitelist that will one day admit a variable
nothing can substitute.

**The names are ``BOT_*``, not ``OCB_*``.** ``manifest-schema`` §4 was written
with the older prefix; work-items §2.9 renamed them and W1's rule is that a
divergence between this validator and the published schema document is fixed in
the schema document, in the same change. It was — see that file's §4.

**``BOT_ARCH`` resolves today, it is not merely reserved.** Its value is the
constant ``amd64``: every engine this platform provisions runs ``linux/amd64``
(work-items §4, X3). Implementing it now rather than parking the name means that
if the fleet ever becomes mixed, what changes is where the value comes from —
not the schema, and not anybody's document.

Substitution itself belongs to apply (W4/W5), which is why :func:`resolve` takes
its inputs as arguments rather than reading a request context: this module is a
pure function of a document and the bot's deployment context, callable from the
write path (to validate) and from the apply path (to substitute) without either
importing the other's world.
"""
from __future__ import annotations

import re
from typing import Iterator

#: The architecture every provisioned engine runs on today (work-items §4, X3).
BOT_ARCH_VALUE = "amd64"

#: Every placeholder a v1 document may name. Anything else is refused at write
#: time — an unknown ``${...}`` that survived to apply would either substitute
#: nothing (fetching a URL with a literal ``${TYPO}`` in it) or be silently
#: dropped, and both are worse than a refusal the author can read.
#:
#: **There is deliberately no ``BOT_ID``.** Every name here is a property of the
#: *fleet* — the environment, the tenant, the engine, the architecture — which is
#: what makes one document reusable across bots. A bot id is not: it is minted at
#: creation time (``generate_bot_id`` — a date plus eight random characters) and
#: is not something the caller chooses, so an author preparing content in a git
#: repository cannot know it. A document that interpolated one would have to be
#: written *after* the bot existed and could then only ever describe that bot,
#: which is the opposite of what substitution is for. Anything genuinely
#: per-bot belongs in that bot's own manifest, written literally.
ALLOWED_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "BOT_ENGINE_TYPE",
        "BOT_ENV",
        "BOT_TENANT",
        "BOT_ARCH",
    }
)

#: ``${NAME}`` only. Bare ``$NAME`` is deliberately not a placeholder: a script
#: body is full of shell variables and treating those as manifest placeholders
#: would make ``$HOME`` a validation error. The braces are what distinguishes a
#: platform substitution from the shell's own expansion.
_PLACEHOLDER_RE = re.compile(r"\$\{([^}]*)\}")


def iter_placeholders(text: str) -> Iterator[str]:
    """Yield every ``${NAME}`` name in ``text``, in order, including repeats."""
    for match in _PLACEHOLDER_RE.finditer(text):
        yield match.group(1)


def unknown_placeholders(text: str) -> list[str]:
    """Names used in ``text`` that are not in :data:`ALLOWED_PLACEHOLDERS`.

    De-duplicated and ordered by first appearance, so a document repeating one
    typo reports it once and a document with two typos reports both in the
    order a reader will find them.
    """
    seen: dict[str, None] = {}
    for name in iter_placeholders(text):
        if name not in ALLOWED_PLACEHOLDERS:
            seen.setdefault(name, None)
    return list(seen)


def resolve(
    text: str,
    *,
    engine_type: str,
    env: str,
    tenant: str,
) -> str:
    """Substitute every allowed placeholder in ``text``.

    Unknown names are left **untouched** rather than replaced or dropped. They
    cannot reach here through the public API — the write path refuses them — so
    the only way one arrives is through a caller that skipped validation, and
    leaving it visible makes that bug findable in a fetch URL or a report
    instead of turning it into a plausible-looking empty string.
    """
    values = {
        "BOT_ENGINE_TYPE": engine_type,
        "BOT_ENV": env,
        "BOT_TENANT": tenant,
        "BOT_ARCH": BOT_ARCH_VALUE,
    }

    def _substitute(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))

    return _PLACEHOLDER_RE.sub(_substitute, text)
