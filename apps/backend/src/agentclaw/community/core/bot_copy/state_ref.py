"""The ``copy`` state reference for the apply engine.

``copy_state(operation_id)`` is the ``StateRef("copy", operation_id)`` a CopyCompiler
(Stage B) registers under and consumes. Landing it in Stage A lets the
``POST /openapi/v1/bots/copy`` handler build the create_job payload's
``state_ref`` before the compiler exists — create_job does not process
``state_ref`` until Stage B's threading lands.
"""

from __future__ import annotations

from agentclaw.community.core.apply_engine.compiler import StateRef

#: The kind the CopyCompiler registers under in the ``StateCompilers`` multibinding.
COPY_STATE_KIND = "copy"


def copy_state(operation_id: str) -> StateRef:
    """The state reference that converges a dst bot from a copy operation.

    ``operation_id`` is the FE polling correlation id and the `ac_bot_copy` UNIQUE
    key; the CopyCompiler resolves it back to the source bot + excludes at compile
    time.
    """
    if not operation_id:
        raise ValueError("a copy state reference needs an operation id")
    return StateRef(COPY_STATE_KIND, operation_id)
