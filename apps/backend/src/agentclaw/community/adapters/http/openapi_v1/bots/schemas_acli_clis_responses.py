"""OpenAPI documentation for the acli CLI writes: how they run, and worked samples.

Add, remove and both cancels answer in one of two shapes, so each route declares
both with a sample; add and remove also sample the `409` naming the running operation. Kept apart
from the routes so the handlers stay readable.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_ACCEPTED,
    EXAMPLE_TRACE_ID,
    Envelope,
)

from .schemas_acli_clis import AcliCliOperation, CliOperationConflict

#: Appended to every write's description: the part a caller needs to read a reply.
HOW_IT_RUNS = """

**How it runs.** The change is an operation run in the background; the call
only decides how long to wait for it.

* Without `async`, the call waits up to 30 s. If the operation ends in that
  time, it answers `200` with the result (or the error it ended with);
  otherwise it answers `202` with the operation.
* With `async=true`, it answers `202` with the operation at once.

After a `202`, poll `GET /openapi/v1/bots/{bot_id}/cli-operations/{operation_id}`
until `status` is no longer `running`. Do not resubmit to learn the outcome: a
resubmit is a new request.

Only one add or remove runs in a runtime at a time: another one answers `409`
with the running operation in `data.operation`. A cancel is accepted while its
operation runs; it answers `409` only when nothing of that kind is running."""

_OPERATION = {
    "operation_id": "577b4819b33a48369bf279af5fbf251e",
    "kind": "operation",
    "action": "install",
    "cli_code": "yuque-cli",
    "stage": "draft",
    "status": "running",
    "step": "installing",
    "error_code": None,
    "error_message": None,
}


def _sample(data: Any, *, code: int = 200000, message: str = "OK") -> dict[str, Any]:
    return {
        "content": {
            "application/json": {
                "example": {
                    "code": code, "message": message, "data": data,
                    "request_id": EXAMPLE_TRACE_ID,
                }
            }
        }
    }


def _write_responses(done: dict[str, Any], *, operation: dict[str, Any]) -> dict[int | str, Any]:
    return {
        200: {"description": "The operation ended in time; its result.", **_sample(done)},
        202: {
            "model": Envelope[AcliCliOperation],
            "description": "Still running (`async`, or past the 30 s wait); poll "
            "`…/cli-operations/{operation_id}`.",
            **_sample(operation, code=CODE_ACCEPTED, message="Accepted"),
        },
    }


#: An add or remove refused because another one is running.
_BUSY = {
    409: {
        "model": Envelope[CliOperationConflict],
        "description": "Another add or remove is running in this runtime "
        "(`data.operation`, sampled here). Also answered when the runtime cannot take "
        "the change: no running container (`not_running`) or an engine without the "
        "acli endpoints (`engine_outdated`), with `{code, reason}`; or a default CLI "
        "(remove), with no `data`.",
        **_sample(
            {"operation": _OPERATION},
            code=409000, message="Another CLI operation is in progress; try again later",
        ),
    },
}

ADD_RESPONSES = {
    **_write_responses(
        {"cli_code": "yuque-cli", "command": "yuque", "version": "1.2.0"}, operation=_OPERATION,
    ),
    **_BUSY,
}
REMOVE_RESPONSES = {
    **_write_responses(
        {"deleted": True}, operation={**_OPERATION, "action": "uninstall", "step": "uninstalling"},
    ),
    **_BUSY,
}
_CANCEL = {**_OPERATION, "kind": "cancel"}
CANCEL_RESPONSES = _write_responses(
    {**_CANCEL, "status": "succeeded", "step": None},
    operation={**_CANCEL, "step": "cancelling"},
)

__all__ = ["ADD_RESPONSES", "CANCEL_RESPONSES", "HOW_IT_RUNS", "REMOVE_RESPONSES"]
