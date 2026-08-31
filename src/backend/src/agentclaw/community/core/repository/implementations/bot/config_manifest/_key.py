"""The bounded surrogate for ``(env, entity_id, bot_id)``.

Its own module because the reason it is length-prefixed is a correctness
argument, not a formatting choice, and it should be read before anyone
"simplifies" it back to a join.
"""
from __future__ import annotations

import hashlib


def manifest_key(*, env: str, entity_id: str, bot_id: str) -> str:
    """Return the row key for one bot's manifest.

    The logical key is ``(env, entity_id, bot_id)``; ``entity_id`` alone is 1024
    utf8mb4 characters, which is 4096 bytes — past InnoDB's 3072-byte index-key
    cap before the other two are counted. Hashing gives a fixed 64-character key
    while the real columns keep their true widths.

    **Length-prefixed, not delimiter-joined.** A separator only disambiguates
    while it cannot occur inside a component, and nothing enforces that:
    ``create_bot`` takes caller-supplied ``bot_id`` / ``entity_id`` and neither
    is validated against control characters. Under a NUL-joined form
    ``(entity_id="a\\0b", bot_id="c")`` and ``(entity_id="a", bot_id="b\\0c")``
    hash identically — one bot's write landing on another bot's row. That was a
    real defect in ``ac_bot_startup_script`` before it was fixed there; starting
    from the fixed shape here is the whole point of stating it.

    Prefixing each component with its length is injective for *every* input, so
    the key does not depend on an invariant nobody upholds.

    Every read filters on this rather than on the three columns it is built
    from: once the uniqueness key moved here, ``(env, entity_id, bot_id)`` had no
    index behind it at all, so filtering on the surrogate is what keeps a lookup
    on the one index the table has.
    """
    joined = "".join(f"{len(part)}:{part}" for part in (env, entity_id, bot_id))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
