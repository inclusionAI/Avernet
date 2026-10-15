"""CLI tools router — the five endpoints the platform's apply engine calls.

Marshals HTTP↔service and nothing else; placement lives in the engine's
``CliToolsService``. Contract:
``apps/backend/docs/bot-config-manifest/engine-requirements.zh-CN.md`` §4 A2.
Caller: ``ArcaCliToolPort`` (``…/bot_config_manifest/cli_tools/arca_port.py``).

**Three response conventions the platform depends on**, each chosen because the
obvious alternative loses information the platform needs:

* **A refusal is never silent.** Non-2xx *or* ``200`` with ``success: false``
  both read as refusal on the platform side; what must never happen is a tool
  the engine could not place being reported as placed.
* **``404`` is reserved** for "this engine build has no CLI endpoints" — the
  platform never asks about a tool by path, so it reads a 404 as a missing
  *endpoint*. An unknown tool therefore answers ``200`` with
  ``success: false, error: "not_found"``; a 404 there would make one bad name
  look like a permanently broken engine.
* **A partial failure is an ordinary ``200``.** ``replace`` reports per name,
  and some names failing while others succeed is a normal outcome, not an
  error status.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os

from engine.community.api.caps import check_capability
from engine.community.api.cli.schemas import (
    AcliInstallRequest,
    AcliUninstallRequest,
    DeleteRequest,
    InstallRequest,
    ReplaceRequest,
)
from engine.community.api.response import ApiResponse
from engine.community.core.acli.runner import AcliError, AcliRunner
from engine.community.core.cli_tools.models import CliToolPayload
from engine.community.core.cli_tools.service import InvalidCliToolNameError
from engine.community.core.engine.capability import Capability
from engine.community.manager import EngineManager
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/cli", tags=["cli"])
log = logging.getLogger("api-cli")


def _service():
    return EngineManager.get_instance().cli_tools


def _decode(content_b64: str, *, name: str) -> bytes:
    """Decode a payload, refusing malformed base64 rather than writing junk.

    ``validate=True`` matters: without it Python discards non-alphabet
    characters silently, so a corrupted body would install a *truncated*
    binary and report success.
    """
    try:
        return base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail=f"cli tool {name!r}: content_b64 is not valid base64: {error}",
        ) from error


@router.post("/install", response_model=ApiResponse)
async def install_cli_tool(request: InstallRequest) -> ApiResponse:
    """Make ``name`` this bot's command, leaving every other one alone."""
    warning = check_capability(Capability.CLI_INSTALL)
    data = _decode(request.content_b64, name=request.name)
    try:
        await _service().install(request.name, data)
    except InvalidCliToolNameError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001 — a refusal, never a success
        log.warning("[api-cli] install failed name=%s: %s", request.name, error)
        return ApiResponse(success=False, message=str(error), warning=warning)
    return ApiResponse(
        success=True, data={"name": request.name, "size_bytes": len(data)},
        warning=warning,
    )


@router.post("/delete", response_model=ApiResponse)
async def delete_cli_tool(request: DeleteRequest) -> ApiResponse:
    """Remove the command. A command that was never there is success."""
    warning = check_capability(Capability.CLI_DELETE)
    try:
        await _service().delete(request.name)
    except InvalidCliToolNameError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001
        log.warning("[api-cli] delete failed name=%s: %s", request.name, error)
        return ApiResponse(success=False, message=str(error), warning=warning)
    return ApiResponse(success=True, data={"name": request.name}, warning=warning)


@router.post("/replace", response_model=ApiResponse)
async def replace_cli_tools(request: ReplaceRequest) -> ApiResponse:
    """Make this set **the** command set; anything unnamed is removed.

    Returns one verdict per requested name. The platform parses this strictly
    — a name it sent that is missing from ``results`` makes the whole response
    unreadable to it, deliberately, because silence read as success is how a
    tool the bot does not have ends up in a green apply report. So every
    requested name is answered here, including the ones that failed to decode.
    """
    warning = check_capability(Capability.CLI_REPLACE)

    payloads: list[CliToolPayload] = []
    # A name whose payload cannot even be decoded still owes a verdict, so it
    # is failed here rather than 400-ing the whole call and losing the
    # per-name detail for every other tool in the set.
    undecodable: list[dict[str, object]] = []
    for item in request.tools:
        try:
            data = base64.b64decode(item.content_b64, validate=True)
        except (binascii.Error, ValueError) as error:
            undecodable.append(
                {"name": item.name, "success": False,
                 "message": f"content_b64 is not valid base64: {error}"}
            )
            continue
        payloads.append(CliToolPayload(name=item.name, data=data))

    # An undecodable entry never reaches the service, so without this the
    # prune would read its name as dropped from the desired set and delete a
    # perfectly good installed tool — turning "your update failed" into "your
    # tool is gone". A failed entry degrades to unchanged, exactly as one that
    # failed to install does.
    outcome = await _service().replace_all(
        payloads, also_keep=[str(entry["name"]) for entry in undecodable]
    )
    body = [
        {"name": r.name, "success": r.success}
        if r.success
        else {"name": r.name, "success": False, "message": r.message}
        for r in outcome.results
    ]
    # Pruning touches names the request did not carry, so a prune failure has
    # no verdict slot. Reporting it here keeps the response from implying the
    # bot is clean when a tool the manifest dropped is still callable.
    message = None
    if outcome.prune_failures:
        message = (
            "some tools could not be removed and are still installed: "
            + "; ".join(outcome.prune_failures)
        )
    return ApiResponse(success=True, data={"results": body + undecodable},
                       message=message, warning=warning)


@router.get("/list", response_model=ApiResponse)
async def list_cli_tools() -> ApiResponse:
    """What this bot has **on disk** right now. Empty is ``[]``, not a 404."""
    warning = check_capability(Capability.CLI_LIST)
    tools = await _service().list_tools()
    return ApiResponse(
        success=True,
        data={
            "tools": [
                {"name": t.name, "md5": t.md5, "size_bytes": t.size_bytes}
                for t in tools
            ]
        },
        warning=warning,
    )


@router.get("/download", response_model=ApiResponse)
async def download_cli_tool(name: str = Query(...)) -> ApiResponse:
    """One command's bytes, base64-encoded.

    A verification and troubleshooting bypass, not part of the delivery path —
    the platform keeps its own copy of every byte it sent.
    """
    warning = check_capability(Capability.CLI_DOWNLOAD)
    try:
        tool = await _service().read_tool(name)
    except InvalidCliToolNameError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if tool is None:
        # NOT a 404 — that status means "this engine build has no CLI
        # endpoints", and reusing it here would make one unknown name
        # indistinguishable from a permanently endpoint-less engine.
        return ApiResponse(
            success=False,
            error="not_found",
            message=f"no such tool: {name}",
            warning=warning,
        )
    return ApiResponse(
        success=True,
        data={
            "name": tool.name,
            "size_bytes": tool.size_bytes,
            "md5": tool.md5,
            "content_b64": base64.b64encode(tool.data).decode("ascii"),
        },
        warning=warning,
    )


# ── acli market tools ─────────────────────────────────────────────────────
#
# Same conventions as above: bad input is a 400, anything acli refused is a
# 200 with ``success: false`` and ``error`` set to the runner's code, and 404
# stays reserved for "this engine build has no such endpoint".

# This module wires the runner, so it hands acli the process environment.
_ACLI_RUNNER = AcliRunner(environ=os.environ)


def _acli() -> AcliRunner:
    return _ACLI_RUNNER


def _acli_refusal(error: AcliError, warning: str | None) -> ApiResponse:
    if error.code == "invalid_input":
        raise HTTPException(status_code=400, detail=error.message) from error
    return ApiResponse(
        success=False, error=error.code, message=error.message, warning=warning
    )


@router.post("/acli/install", response_model=ApiResponse)
async def install_acli_tool(request: AcliInstallRequest) -> ApiResponse:
    """Install one acli market tool into ``install_dir``."""
    warning = check_capability(Capability.CLI_ACLI_INSTALL)
    try:
        tool = await _acli().install(request.name, request.install_dir)
    except AcliError as error:
        return _acli_refusal(error, warning)
    return ApiResponse(
        success=True,
        data={
            "name": tool.name,
            "command": tool.command,
            "description": tool.description,
            "version": tool.version,
        },
        warning=warning,
    )


@router.post("/acli/uninstall", response_model=ApiResponse)
async def uninstall_acli_tool(request: AcliUninstallRequest) -> ApiResponse:
    """Uninstall an acli tool. One acli never installed here is success."""
    warning = check_capability(Capability.CLI_ACLI_UNINSTALL)
    try:
        result = await _acli().uninstall(request.name, request.install_dir)
    except AcliError as error:
        return _acli_refusal(error, warning)
    return ApiResponse(
        success=True,
        data={"name": result.name, "uninstalled": result.uninstalled},
        warning=warning,
    )


@router.get("/acli/market", response_model=ApiResponse)
async def list_acli_market(
    keyword: str = Query(""),
    page_no: int = Query(1),
    page_size: int = Query(20),
) -> ApiResponse:
    """One page of the acli market, filtered by ``keyword``."""
    warning = check_capability(Capability.CLI_ACLI_MARKET)
    try:
        page = await _acli().market(keyword, page_no, page_size)
    except AcliError as error:
        return _acli_refusal(error, warning)
    return ApiResponse(
        success=True,
        data={
            "items": [
                {
                    "name": t.name,
                    "command": t.command,
                    "description": t.description,
                    "version": t.version,
                }
                for t in page.items
            ],
            "total": page.total,
            "has_more": page.has_more,
        },
        warning=warning,
    )


__all__ = ["router"]
