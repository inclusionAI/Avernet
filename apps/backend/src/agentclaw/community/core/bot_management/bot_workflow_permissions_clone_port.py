"""Service API Protocol for cloning a bot's workflow-permission grants.

The ``task_nav`` ``bot_config`` kind converges by asking clawweb to clone the
source bot's workflow-permission grants (the ``can_view``/``can_execute``/
``can_edit`` matrix clawweb's ``bot-workflow-permissions`` repository owns)
onto the destination bot. This is the seam the handler reaches — a Protocol
rather than an ``httpx`` call inline so the apply engine never imports HTTP
transport, and so a test injects a no-network double.

This is the first **cross-service** ``bot_config`` kind: ``render_screen``'s
handler reaches an in-process service (``RenderScreenService``), this one
reaches clawweb over HTTP. The env switch is therefore not three Port impls
but one — the env-selected :class:`~agentclaw.community.plugin_api.http_client.HttpClient`
(the ``clawweb`` qualifier, bound per-deployment to prod / pre / the
simulator's in-cluster clawweb service, and to a no-network
:class:`~agentclaw.community.plugins.local.http_client.LocalHttpClient` in
test) is injected here, so a deploy swaps hosts by editing one yaml line,
never by branching in code (Rule 14) and never by name in the handler.

The clawweb ``/api/internal/bot-workflow-permissions/clone`` endpoint is
idempotent (an upsert with a widest-rule-wins merge plus an
``already_covered`` skip), so this Port is a single clone call — the handler
does not read the destination's current grants and does not guard repeats.
No token or internal-service header is sent: the endpoint is reached over
the internal mesh and accepts the call as-is (verified against the live
pre/prod endpoint); if clawweb later adds an auth gate, only this impl
changes.
"""
from __future__ import annotations

from typing import Annotated

from agentclaw.community.plugin_api.http_client import (
    HttpClient,
    QUALIFIER_CLAWWEB,
)

#: The clone path, relative to the clawweb ``base_url`` the ``clawweb``
#: HttpClient qualifier is bound to. ``/api/internal/`` is clawweb's
#: service-to-service surface (never browser-exposed).
_CLONE_PATH = "/api/internal/bot-workflow-permissions/clone"


class BotWorkflowPermissionsClonePort:
    """Clones a source bot's workflow-permission grants onto a destination bot.

    Reusable for both the v1 ``type: "bot"`` whole-bot clone (the only shape
    the manifest admits today) and a future ``type: "item"`` named-workflow
    clone, which will pass the same call through with a ``workflow_id``
    filter (added as one optional body field, never a new method).
    """

    def clone(
        self,
        *,
        source_bot_id: str,
        source_owner_id: str,
        target_bot_id: str,
        target_owner_id: str,
        scope: str,
        dry_run: bool,
    ) -> None:
        """POST one clone request; the caller owns failure handling.

        Returns nothing: the ``task_nav`` handler is fire-and-log — it does
        not read the response body or map rows into the apply report. A
        non-2xx status is surfaced as an :class:`httpx.HTTPStatusError` via
        ``raise_for_status`` so the handler's ``except`` logs it (the apply
        still completes with the kind marked ``unchanged``); a transport
        error propagates the same way.
        """
        raise NotImplementedError


# ``BotWorkflowPermissionsClonePort`` is written as a concrete base rather than
# a ``Protocol`` because its single impl is the whole point — there is no
# second implementation for the runtime to distinguish by structural shape, so
# a runtime_checkable Protocol would be ceremony for nothing. The class is the
# seam: a test injects a subclass that records the call and raises on demand,
# the handler depends on the base, and the prod impl below is the only one DI
# binds. This is the same single-impl-seam shape ``BcnService`` keeps.


class HttpBotWorkflowPermissionsClonePort(BotWorkflowPermissionsClonePort):
    """The one real impl — POSTs the clone request through the clawweb HttpClient.

    The :class:`HttpClient` injected here is the env-selected clawweb binding:
    ``HttpxClient(base_url=<clawweb host>)`` in prod / pre / singlebox, and
    ``LocalHttpClient`` in ``test`` / ``corp_test`` (the profile override), so
    this class never branches on environment — Rule 14's "no scattered
    ``if is_local_mode()``".
    """

    def __init__(self, http: Annotated[HttpClient, QUALIFIER_CLAWWEB]) -> None:
        self._http = http

    def clone(
        self,
        *,
        source_bot_id: str,
        source_owner_id: str,
        target_bot_id: str,
        target_owner_id: str,
        scope: str,
        dry_run: bool,
    ) -> None:
        response = self._http.post(
            _CLONE_PATH,
            json={
                "source_bot_id": source_bot_id,
                "source_owner_id": source_owner_id,
                "target_bot_id": target_bot_id,
                "target_owner_id": target_owner_id,
                "scope": scope,
                "dry_run": dry_run,
            },
        )
        # No token/header is added; no response body is read or mapped. A
        # non-2xx surfaces as an HTTPStatusError the handler's fire-and-log
        # ``except`` records; a transport error propagates the same way.
        response.raise_for_status()


__all__ = [
    "BotWorkflowPermissionsClonePort",
    "HttpBotWorkflowPermissionsClonePort",
]
