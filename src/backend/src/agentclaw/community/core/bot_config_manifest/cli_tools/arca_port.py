"""ARCA delivery: one call to the engine's CLI endpoints, and nothing else.

An ARCA bot boots from a start command and takes everything else as writes
into a live container, so a CLI tool has to reach the running engine. This
port is the whole of that: resolve the bot's device, POST to the engine, turn
a refusal into :class:`CliToolPlacementError`. It fetches nothing, verifies
nothing, records nothing and places nothing — those belong to the service and
to the engine respectively.

**The engine owns everything about placement.** ``install`` carries the
semantics "make this the bot's ``name`` command", so the directory, the
executable bit and exposure to the agent are all decided inside that one call.
There is no directory constant in this module, no executable-bit call, and no
shell command — and therefore no user-supplied name to quote into one. The
directory each ARCA engine uses is recorded in
``docs/bot-config-manifest/engine-requirements.zh-CN.md``, deliberately not
here: a platform-side copy of it would become a second answer to a question the
engine already answers, and a stale one the first time an engine moved it.

**The channel.** :class:`DeviceAdapterTransport` is the one engine channel
core can reach — the same one the runtime-layout probe, the cron relay and the
session-resources service use — and its ``invoke`` takes a JSON body. So the
bytes ride base64-encoded, which costs about a third again in memory over an
already-buffered binary (the fetch pipeline hands the service ``bytes``, not a
stream).

Both alternative shapes were worse. The multipart channel the per-file writes
use (``BaasTransport.post_multipart``) is reached by constructing a device
*filesystem*, whose ARCA branch is corp-only — ``DefaultDeviceFileSystemResolver``
raises for ``provider == "arca"`` and the corp resolver overrides it — so a
multipart install could not be built, bound or tested from this repository at
all. Handing the engine a presigned object-store URL instead assumes a
container egress path to the bucket that nothing here demonstrates. The adapter
transport, by contrast, has a community implementation and an in-memory one, so
this port runs and is exercised outside the corp profile. If the engine later
grows a pull-from-URL install it is an additive field on the same endpoint, and
only this module changes.

The port is deliberately **provider-agnostic**: it asks the resolver for the
bot's device context and POSTs to whatever ``conn_info`` describes. Engine
family is not device provider — an ARCA-family engine may be bound through
``baas`` or ``arca`` — and nothing here needs to know which.

**Failures raise.** A tool the engine could not install must never be recorded
as installed, so every refusal — a 404 from an engine with no CLI endpoints, a
non-2xx, an envelope reporting failure, a timeout — leaves as
:class:`CliToolPlacementError` with the engine's own error inside it.
"""
from __future__ import annotations

import base64
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.delivery_port import (
    CliToolDeliveryError,
    CliToolDeliveryPort,
    CliToolPlacementError,
    DeliverableCliTool,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTransport,
)
from agentclaw.community.log import get_logger

logger = get_logger()

@runtime_checkable
class BotDeviceContextPort(Protocol):
    """``DeviceContextResolver.resolve_for_bot``, as a type key.

    The move ``core/ports/resource_file_port.py`` records, for the same reason: importing
    the real resolver here pulls ``core.devices.services.__init__``, which
    reaches the DI container at import time and closes a cycle back into this
    package. So this names the one method the port calls and nothing else; the
    bound object is the real singleton, by structural typing, with no adapter
    and no second implementation.
    """

    def resolve_for_bot(self, bot_id: str, user_id: str) -> Any: ...


#: The engine's CLI-tool endpoints. Name-addressed, all four of them.
INSTALL_PATH = "/api/cli/install"
DELETE_PATH = "/api/cli/delete"
LIST_PATH = "/api/cli/list"
#: The whole-set endpoint a manifest apply uses: the body *is* the desired set,
#: so a name the engine has and the body does not name is removed. One round
#: trip for a set of any size (spec D-13).
REPLACE_PATH = "/api/cli/replace"

#: A delete is POSTed rather than sent as ``DELETE``: the name travels in a
#: body, and a DELETE carrying one is refused or silently stripped by enough
#: proxies that it is not worth the elegance.
_DELETE_METHOD = "POST"

#: An install can carry a binary as large as the ``cli_tools`` fetch cap
#: (200 MiB, ``fetch/limits.py``) plus base64 overhead, over a link that is not
#: local. The control calls carry a name and nothing else.
INSTALL_TIMEOUT_SECONDS = 300.0
CONTROL_TIMEOUT_SECONDS = 30.0

#: A replacement carries the whole set, so its budget scales with the set
#: rather than borrowing one install's — but is capped, so a pathological
#: declaration cannot hold a worker for an hour.
REPLACE_TIMEOUT_CAP_SECONDS = 1800.0


def replace_timeout_for(count: int) -> float:
    """The budget for a whole-set call carrying ``count`` tools."""
    return min(INSTALL_TIMEOUT_SECONDS * max(count, 1), REPLACE_TIMEOUT_CAP_SECONDS)


class ArcaCliToolPort(CliToolDeliveryPort):
    """Calls the ARCA engine's CLI endpoints. Nothing else."""

    def __init__(
        self,
        *,
        resolver: BotDeviceContextPort,
        transport: DeviceAdapterTransport,
    ) -> None:
        self._resolver = resolver
        self._transport = transport

    # ── the one call each ────────────────────────────────────────────────

    async def install(
        self, ctx: CliToolContext, *, name: str, data: bytes
    ) -> None:
        logger.info(
            "[cli_tools/arca] install bot=%s name=%s size=%d",
            ctx.bot_id, name, len(data),
        )
        await self._invoke(
            ctx,
            "POST",
            INSTALL_PATH,
            body={
                "name": name,
                "size_bytes": len(data),
                "content_b64": base64.b64encode(data).decode("ascii"),
            },
            timeout=INSTALL_TIMEOUT_SECONDS,
            what=f"install {name!r}",
        )

    async def delete(self, ctx: CliToolContext, *, name: str) -> None:
        logger.info("[cli_tools/arca] delete bot=%s name=%s", ctx.bot_id, name)
        await self._invoke(
            ctx,
            _DELETE_METHOD,
            DELETE_PATH,
            body={"name": name},
            timeout=CONTROL_TIMEOUT_SECONDS,
            what=f"delete {name!r}",
        )

    async def list(self, ctx: CliToolContext) -> list[str]:
        payload = await self._invoke(
            ctx,
            "GET",
            LIST_PATH,
            body=None,
            timeout=CONTROL_TIMEOUT_SECONDS,
            what="list",
        )
        return _names_in(payload)

    # ── the whole set, in one call ───────────────────────────────────────

    async def replace_all(
        self, ctx: CliToolContext, tools: Sequence[DeliverableCliTool]
    ) -> Mapping[str, str]:
        """POST the desired set. The engine removes whatever it is not sent.

        An **empty** set is a real call, not a skip: ``cli_tools: []`` in a
        manifest means "this bot has no tools", and the engine has to be told.
        """
        logger.info(
            "[cli_tools/arca] replace bot=%s tools=%d bytes=%d",
            ctx.bot_id, len(tools), sum(len(t.data) for t in tools),
        )
        payload = await self._invoke(
            ctx,
            "POST",
            REPLACE_PATH,
            # TODO(W9): every tool's bytes ride on every replacement, including
            # the ones that did not change — an apply that edits one tool of
            # four uploads all four. Accepted for v1 (spec rev 8, Open
            # Questions): a *no-op* apply never reaches here at all, because
            # the service converges on ``(digest, subpath)`` first, so the cost
            # falls only on an apply that changes something. The fix is an
            # engine-contract change: carry the full set as ``(name, digest)``
            # and bytes only for what the engine says it lacks.
            body={
                "tools": [
                    {
                        "name": tool.name,
                        "size_bytes": len(tool.data),
                        "content_b64": base64.b64encode(tool.data).decode("ascii"),
                    }
                    for tool in tools
                ]
            },
            timeout=replace_timeout_for(len(tools)),
            what=f"replace the tool set ({len(tools)} tool(s))",
        )
        return _failures_in(payload, expected=[tool.name for tool in tools])

    # ── the boundary ─────────────────────────────────────────────────────

    async def _invoke(
        self,
        ctx: CliToolContext,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None,
        timeout: float,
        what: str,
    ) -> Any:
        """One engine call, with every failure shaped the same way.

        The envelope is checked as well as the status: an engine that answers
        200 with ``{"success": false}`` has still refused, and treating that as
        a success is exactly how a tool gets recorded that the bot does not
        have.
        """
        try:
            context = self._resolver.resolve_for_bot(ctx.bot_id, ctx.owner_id)
            response = await self._transport.invoke(
                context.conn_info, method, path, body=body, timeout=timeout,
            )
        except DeviceAdapterEndpointNotFoundError as error:
            # The honest reading of a 404 here: this engine build has no CLI
            # endpoints. Not "the tool is missing" — the platform never asked
            # about a tool by path, and there is no other route to try.
            raise CliToolPlacementError(
                f"cli_tools: engine for bot {ctx.bot_id} exposes no CLI endpoint "
                f"({path}); cannot {what}"
            ) from error
        except DeviceAdapterHTTPStatusError as error:
            raise CliToolPlacementError(
                f"cli_tools: engine refused to {what} for bot {ctx.bot_id}: {error}"
            ) from error
        except Exception as error:
            raise CliToolPlacementError(
                f"cli_tools: could not {what} for bot {ctx.bot_id}: {error}"
            ) from error

        if isinstance(response, dict) and response.get("success") is False:
            raise CliToolPlacementError(
                f"cli_tools: engine refused to {what} for bot {ctx.bot_id}: "
                f"{response.get('message') or response.get('error') or response}"
            )
        return response


def _names_in(payload: Any) -> list[str]:
    """The command names in a ``list`` envelope, whichever shape it arrived in.

    Read leniently and on purpose: this feeds the drift comparison, never a
    write. An envelope nobody can parse reads as "the engine reports nothing",
    which shows up as drift — the observable outcome — rather than as an
    exception on a read that decides nothing.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        data = data.get("tools")
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for item in data:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def _failures_in(payload: Any, *, expected: Sequence[str]) -> Mapping[str, str]:
    """The per-name failures in a ``replace`` envelope.

    **Read strictly, unlike :func:`_names_in`**, and the difference is the
    point: that one feeds a drift comparison that decides nothing, while this
    one decides what the platform reports as installed. A name the engine did
    not answer for is not an implicit success — reporting silence as success is
    exactly how a tool the bot does not have ends up in a green apply report.

    So every expected name must come back with a verdict, or the whole call is
    unreadable and raises. Names the platform did not send are ignored: an
    engine listing something extra is drift for ``list`` to surface, not a
    failure of this call.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    results = data.get("results") if isinstance(data, dict) else None
    if results is None and isinstance(payload, dict):
        results = payload.get("results")
    if not isinstance(results, list):
        raise CliToolDeliveryError(
            "cli_tools: the engine's replace response carries no per-tool "
            f"results, so nothing can be reported as installed: {payload!r}"
        )

    verdicts: dict[str, str | None] = {}
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = item["name"]
        if item.get("success") is True:
            verdicts[name] = None
            continue
        verdicts[name] = str(
            item.get("message") or item.get("error") or "the engine refused it"
        )

    unanswered = [name for name in expected if name not in verdicts]
    if unanswered:
        raise CliToolDeliveryError(
            "cli_tools: the engine's replace response says nothing about "
            f"{', '.join(repr(n) for n in unanswered)}; refusing to record "
            "them as installed"
        )
    return {name: why for name, why in verdicts.items() if why is not None}


__all__ = [
    "BotDeviceContextPort",
    "CONTROL_TIMEOUT_SECONDS",
    "DELETE_PATH",
    "INSTALL_PATH",
    "INSTALL_TIMEOUT_SECONDS",
    "LIST_PATH",
    "REPLACE_PATH",
    "REPLACE_TIMEOUT_CAP_SECONDS",
    "ArcaCliToolPort",
    "replace_timeout_for",
]
