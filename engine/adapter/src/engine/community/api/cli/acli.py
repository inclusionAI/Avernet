"""``/api/cli/acli/*`` — acli market tools, as operations.

Contract: ``docs/specs/2026-10-21-acli-cli-operations/spec.md`` (engine
contract). Caller: the platform's ``ArcaAcliPort``.

Every install and uninstall is an operation (``AcliOperations``), returned in
``data.operation``:

* ``200`` — the operation has ended: ``success`` is whether it succeeded,
  ``error`` its code otherwise.
* ``202`` — still running (``async``, or a sync call that waited its 30 s).
  Read it later with ``GET /api/cli/acli/operations/{id}``.
* ``409`` — ``engine_busy`` (another operation holds the container, returned
  with it) or ``not_active`` (nothing of that kind to cancel).
* ``400`` — bad input; nothing started.

``GET /api/cli/acli/tools`` lists what acli has installed, outside the slot.

``404`` stays reserved for "this engine build has no such endpoint", except
``operations/{id}`` answering ``operation_not_found`` inside the envelope.
"""
from __future__ import annotations

import logging
import os

from engine.community.api.caps import check_capability
from engine.community.api.cli.schemas import AcliOperationRequest
from engine.community.api.response import ApiResponse
from engine.community.config import ACLI_INSTALL_DIR
from engine.community.core.acli.operations import (
    INSTALL,
    UNINSTALL,
    AcliOperation,
    AcliOperations,
    OperationBusyError,
    OperationNotActiveError,
)
from engine.community.core.acli.runner import AcliError, AcliRunner
from engine.community.core.engine.capability import Capability
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/acli", tags=["cli"])
log = logging.getLogger("api-cli")

#: How long a sync call waits for its operation before answering ``202``.
SYNC_WAIT_SECONDS = 30.0

# This module wires the runner: it hands acli the process environment and the
# engine's install directory, so neither is chosen by core code or by a caller.
_ACLI_RUNNER = AcliRunner(environ=os.environ, install_dir=ACLI_INSTALL_DIR)
_ACLI_OPERATIONS = AcliOperations(_ACLI_RUNNER)


def _operations() -> AcliOperations:
    return _ACLI_OPERATIONS


def _envelope(
    status: int, *, success: bool, op: AcliOperation | None = None,
    error: str | None = None, message: str | None = None, warning: str | None = None,
) -> JSONResponse:
    body = ApiResponse(
        success=success,
        data={"operation": op.public()} if op is not None else None,
        error=error, message=message, warning=warning,
    )
    return JSONResponse(status_code=status, content=body.model_dump())


def _answer(op: AcliOperation, warning: str | None) -> JSONResponse:
    if op.active:
        return _envelope(202, success=True, op=op, warning=warning)
    if op.state == "succeeded":
        return _envelope(200, success=True, op=op, warning=warning)
    error = op.error or {"code": op.state, "message": f"the operation was {op.state}"}
    return _envelope(
        200, success=False, op=op, error=error["code"], message=error["message"],
        warning=warning,
    )


async def _start(action: str, request: AcliOperationRequest, warning: str | None) -> JSONResponse:
    try:
        op = _operations().submit(action, request.name)
    except AcliError as error:
        if error.code == "invalid_input":
            raise HTTPException(status_code=400, detail=error.message) from error
        return _envelope(200, success=False, error=error.code, message=error.message,
                         warning=warning)
    except OperationBusyError as busy:
        return _envelope(409, success=False, op=busy.operation, error="engine_busy",
                         message=str(busy), warning=warning)
    if not request.run_async:
        await AcliOperations.wait(op, SYNC_WAIT_SECONDS)
    return _answer(op, warning)


async def _cancel(action: str, request: AcliOperationRequest, warning: str | None) -> JSONResponse:
    try:
        op = _operations().cancel(action, request.name)
    except OperationNotActiveError as error:
        return _envelope(409, success=False, error="not_active", message=str(error),
                         warning=warning)
    if not request.run_async:
        await AcliOperations.wait(op, SYNC_WAIT_SECONDS)
    return _answer(op, warning)


@router.post("/install", response_model=ApiResponse)
async def install_acli_tool(request: AcliOperationRequest) -> JSONResponse:
    """Start installing one acli market tool into the engine's install directory."""
    warning = check_capability(Capability.CLI_ACLI_INSTALL)
    return await _start(INSTALL, request, warning)


@router.post("/uninstall", response_model=ApiResponse)
async def uninstall_acli_tool(request: AcliOperationRequest) -> JSONResponse:
    """Start uninstalling an acli tool. One acli never installed here is success."""
    warning = check_capability(Capability.CLI_ACLI_UNINSTALL)
    return await _start(UNINSTALL, request, warning)


@router.post("/install/cancel", response_model=ApiResponse)
async def cancel_acli_install(request: AcliOperationRequest) -> JSONResponse:
    """Stop the running install of ``name`` and remove whatever it installed."""
    warning = check_capability(Capability.CLI_ACLI_CANCEL)
    return await _cancel(INSTALL, request, warning)


@router.post("/uninstall/cancel", response_model=ApiResponse)
async def cancel_acli_uninstall(request: AcliOperationRequest) -> JSONResponse:
    """Stop the running uninstall of ``name`` and reinstall it if it was removed."""
    warning = check_capability(Capability.CLI_ACLI_CANCEL)
    return await _cancel(UNINSTALL, request, warning)


@router.get("/tools", response_model=ApiResponse)
async def list_acli_tools() -> ApiResponse:
    """What acli has installed in this container. Read-only; runs beside an operation."""
    warning = check_capability(Capability.CLI_ACLI_LIST)
    try:
        tools = await _ACLI_RUNNER.list_tools()
    except AcliError as error:
        return ApiResponse(success=False, error=error.code, message=error.message,
                           warning=warning)
    return ApiResponse(
        success=True,
        data={"tools": [
            {"name": t.name, "command": t.command, "description": t.description,
             "version": t.version}
            for t in tools
        ]},
        warning=warning,
    )


@router.get("/operations/{operation_id}", response_model=ApiResponse)
async def get_acli_operation(operation_id: str) -> JSONResponse:
    """One operation, running or ended. Read-only."""
    warning = check_capability(Capability.CLI_ACLI_OPERATION_GET)
    op = _operations().get(operation_id)
    if op is None:
        return _envelope(404, success=False, error="operation_not_found",
                         message=f"no acli operation {operation_id}", warning=warning)
    return _answer(op, warning)


__all__ = ["router"]
