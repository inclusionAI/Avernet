"""What started an apply — the vocabulary of ``ApplyReport.trigger``.

Every value is a plain lowercase string, stored verbatim in the apply record's
``trigger`` column and echoed unchanged in the report payload.

========================  =================================================
Value                     Who produces it
========================  =================================================
``explicit``              ``POST …/config-manifest/apply``, through
                          ``adapters/http/…/bots/config_manifest_apply``.
                          Also ``start_apply``'s default, so any caller
                          naming no trigger records this one.
``put``                   ``PUT …/config-manifest`` on an existing bot,
                          through
                          ``adapters/http/…/bots/config_manifest_support``.
``create:pre_container``  The creation job's first phase, through
                          ``creation.ManifestCreationSteps`` and
                          ``create_job``. Applies ``PRE_CONTAINER`` only.
``create:on_container``   The creation job's second phase, same producers.
                          Applies ``ON_CONTAINER`` only.
========================  =================================================

Example::

    ApplyReport(..., trigger="create:pre_container", ...)

One module so a trigger is spelled once. The two creation triggers stay in
``creation.py``, which owns the creation job's recognition of its own phases;
they are re-exported here for readers, not redefined.

On teclaw with the platform-managed switch on, only ``create:pre_container``
occurs: the whole manifest is delivered before the container exists.

Restart and republish are **not** triggers: nothing previously applied is lost
on either path, so a re-apply there was deferred. The column is ``String(32)``;
every value here fits.
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

#: Every trigger a report may carry, for tests and readers::
#:
#:     ("explicit", "put", "create:pre_container", "create:on_container")
#:
#: Not enforced anywhere — ``start_apply`` takes a plain ``str``. This is the
#: closed list a reader and a test can check against.
ALL_TRIGGERS: tuple[str, ...] = (
    EXPLICIT,
    PUT,
    CREATE_PRE_CONTAINER,
    CREATE_ON_CONTAINER,
)

#: The apply record's ``trigger`` column width (``apply_models.py``), in
#: characters. ``"create:pre_container"`` is the longest value at 20, so every
#: trigger above fits with room to spare.
TRIGGER_COLUMN_WIDTH = 32

__all__ = [
    "ALL_TRIGGERS",
    "CREATE_ON_CONTAINER",
    "CREATE_PRE_CONTAINER",
    "EXPLICIT",
    "PUT",
    "TRIGGER_COLUMN_WIDTH",
]
