"""What started an apply — the vocabulary of ``ApplyReport.trigger`` (W8).

One module so a trigger is spelled once. The two creation triggers stay in
``creation.py``, which owns the creation job's recognition of its own phases;
they are re-exported here for readers, not redefined.

The vocabulary in iteration 1:

* ``explicit`` — ``POST …/config-manifest/apply``.
* ``put`` — ``PUT …/config-manifest`` on an existing bot (§2.6).
* ``create:pre_container`` / ``create:on_container`` — W13's creation job. On
  teclaw with the platform-managed switch on only the first occurs: the whole
  manifest is delivered before the container exists.

Restart and republish are **not** triggers in this iteration: nothing
previously applied is lost on either path, so a re-apply there was deferred
(spec D-1). The column is ``String(32)``; every value here fits.
"""
from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)

EXPLICIT = "explicit"
PUT = "put"
CREATE_PRE_CONTAINER = CREATE_PRE_CONTAINER_TRIGGER
CREATE_ON_CONTAINER = CREATE_ON_CONTAINER_TRIGGER

#: Every trigger a report may carry, for tests and readers.
ALL_TRIGGERS: tuple[str, ...] = (
    EXPLICIT,
    PUT,
    CREATE_PRE_CONTAINER,
    CREATE_ON_CONTAINER,
)

#: The apply record's ``trigger`` column width (``apply_models.py``).
TRIGGER_COLUMN_WIDTH = 32

__all__ = [
    "ALL_TRIGGERS",
    "CREATE_ON_CONTAINER",
    "CREATE_PRE_CONTAINER",
    "EXPLICIT",
    "PUT",
    "TRIGGER_COLUMN_WIDTH",
]
