"""The switch that keeps this surface out of sight until apply exists.

**Why the routes ship hidden.** They are public, and until W8 wires ``PUT`` to
take effect (work-items §2.6) an accepted manifest just sits there: a caller
would write one, get a 200, and watch nothing happen to their bot. Before W6
lands there is a sharper case — a document declaring ``resources`` is accepted
with no materializer behind that category at all.

**It lifts at W8, not at W5.** The alternative was a gate per unfinished
category and per unfinished trigger point; one flag held until the last of them
is simpler than a set of them retired one at a time, and it cannot be partially
forgotten.

**It is not the rule that keeps this honest.** The flag hides the surface; the
capability resolver is what refuses constructs nothing can apply, and that
refusal has to keep working after the flag is gone. A flag alone would leave W1
parsing the whole v1 vocabulary with only part of it implemented — see
``capabilities.py``.

Read on every call rather than cached in a module global. A cached flag needs a
reset hook for tests and, in a deployment whose config is pushed rather than
baked, cannot see a change without a restart. One ``os.environ`` lookup on a
request that is about to touch the database is not a cost worth engineering away.
"""
from __future__ import annotations

import os

#: Environment variable (injected by the config centre in deployments that have
#: one) that opens the surface. Off unless it is exactly "true", case-insensitive
#: — a fail-closed default, so a deployment that has never heard of this feature
#: does not serve it.
CONFIG_MANIFEST_ENABLED_ENV = "BOT_CONFIG_MANIFEST_ENABLED"


def config_manifest_surface_enabled() -> bool:
    """Whether the ``/config-manifest`` routes answer at all."""
    return os.environ.get(CONFIG_MANIFEST_ENABLED_ENV, "false").strip().lower() == "true"
