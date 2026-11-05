"""Production BCN collaboration-metadata plugin for one-shot repair.

The one-shot collaboration repair sends a Bot's committed metadata
snapshots through the Plugin Protocol declared in
``plugin_api/bcs_collaboration_metadata.py``; the existing delivery path
is the *synchronous* ``BcnService`` over the bcn-qualified ``HttpClient``
up-link. This adapter is the only place that joins them: Core never
imports ``BcnService`` (the repair service consumes the Protocol), and
``BcnService`` never learns about repair snapshots. Like the ownership
and managers community plugins, this plugin owns only delivery and
transport classification; the composition root injects its borrowed
``BcnService`` and request-scoped headers.

Exactly ONE bounded transport send happens per Plugin method call: no
retry, no new URL or endpoint, no ``register_provider_bot`` (the up-link
``onboard`` is the only profile write, mirroring the daily metadata path
in ``services/bot_service.py``), without modifying caller configuration.

Threading. Because each Plugin method issues exactly one send, the
caller's ``timeout_seconds`` IS the remaining budget for that send and
is forwarded to the transport, which enforces it on the HTTP client
itself (``timeout_seconds`` plumbing from Task 2). The synchronous
chain runs via ``asyncio.to_thread`` so the delivery loop never blocks
on httpx. It mirrors the established Core pattern (``bot_collaborator``
probes and ``manager_sync`` read facts the same way). A cancelled ``await`` cannot
interrupt the in-flight synchronous send (thread cancellation is not
possible), which is why the transport keeps the remaining timeout
instead of relying on argument-level cancellation; after a cancelled
await this adapter starts no further send, because no method owns more
than one.

Lifecycle. The injected ``BcnService`` (and the shared bcn ``HttpClient``
pool under it) is a borrowed injector singleton owned by the
BotManagementModule bindings; ``aclose`` is a documented no-op so a
completed repair never tears the daily BCN path's transport down mid
flight.

Transport → plugin mapping (typed end to end, never ``str(exc)`` parsing).
Every ``code`` is a member of the kernel's finite
``MetadataDeliveryErrorCode`` vocabulary: transport-shaped codes
(``http_403``, ``http_500``, ``http_unknown``) are bucketed so the
public report carries only stable, sanitized reasons.

| transport outcome | plugin outcome |
| --- | --- |
| ``BcnBotMissingError`` (404) | ``exists`` → ``False`` — the only False path; on the write paths → ``MetadataDeliveryError(REJECTED, collaboration_bot_not_found)`` |
| ``BcnUnavailableError`` (503) | ``MetadataDeliveryError(UNKNOWN, collaboration_sync_unavailable)`` |
| ``BcnRequestError``, code ``timeout`` | ``MetadataDeliveryError(UNKNOWN, delivery_timeout)`` — the transport's kind is kept |
| ``BcnRequestError``, kind ``REJECTED`` (other 4xx) | ``MetadataDeliveryError(REJECTED, collaboration_rejected)`` — the 4xx bucket |
| ``BcnRequestError``, kind ``UNKNOWN`` (5xx / no response) | ``MetadataDeliveryError(UNKNOWN, collaboration_upstream_error)`` — the 5xx bucket |
| ``BcnRequestError``, kind ``NOT_SENT`` | ``MetadataDeliveryError(NOT_SENT, collaboration_sync_unavailable)`` — defensive; the transport does not raise it today |
| other ``BcnServiceError`` | ``MetadataDeliveryError(UNKNOWN, unknown)`` |
| result body ``{"skipped": True}`` | ``RepairPreconditionError(collaboration_sync_unavailable)`` — an environment/credential gate is unavailability to start, never a fabricated absence (today only ``get_attributes`` gates this way; the onboard branch is handled uniformly for the same transport family) |
| 2xx onboard body with ``onboarded is False`` | ``MetadataDeliveryError(REJECTED, collaboration_bot_not_found)`` — the definitive per-domain rejection BCN reports without an exception |
| 2xx body failing the documented success shape (non-dict, missing/non-bool ``onboarded``, non-string or mismatched ``bot_uuid``) | ``MetadataDeliveryError(UNKNOWN, collaboration_response_malformed)`` |

``exists`` failures surface as ``MetadataDeliveryError`` with
precondition-shaped codes (``collaboration_sync_unavailable`` /
``delivery_timeout``); the repair service maps an exception from
``exists`` to a PRECONDITION (never a per-item ``unknown``). The
``exists`` probe reads attributes through the transport, which already
normalizes a non-dict body to ``{}``, so its ``collaboration_response_malformed``
guard is defence-in-depth for other transport implementations rather
than the live path.

``bot_uuid`` in the onboard confirmation is the requested stable
composite identity itself (the endpoint echoes the ``bot_id`` it was
addressed by), so a confirmation naming anything else describes a
different bot and is never treated as delivery success. Onboard's
``avatar_url`` is forwarded verbatim — the explicit ``""`` clear is a
valid payload.

Logs carry only the operation, the stable BCS address and the machine
classification — never full request headers (which carry the operating
user's credentials) and never raw response bodies.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import NoReturn

from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnBotMissingError,
    BcnRequestError,
    BcnService,
    BcnServiceError,
    BcnUnavailableError,
)
from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.kernel.collaboration_repair import (
    MetadataDeliveryError,
    MetadataDeliveryErrorCode,
    ProfileSnapshot,
    RepairPreconditionError,
    RepairPreconditionErrorCode,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.bcs_collaboration_metadata import (
    BcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl

logger = get_logger()

_SKIPPED_KEY = "skipped"
_ONBOARDED_KEY = "onboarded"
_BOT_UUID_KEY = "bot_uuid"

# Delivery-level machine reason vocabulary: the kernel owns it
# (``MetadataDeliveryErrorCode``); transport-shaped codes are bucketed so
# nothing status-shaped reaches the public report.
_CODE_UNTYPED = MetadataDeliveryErrorCode.UNKNOWN
_CODE_RESPONSE_MALFORMED = MetadataDeliveryErrorCode.COLLABORATION_RESPONSE_MALFORMED
_CODE_TIMEOUT = MetadataDeliveryErrorCode.DELIVERY_TIMEOUT
_CODE_REJECTED = MetadataDeliveryErrorCode.COLLABORATION_REJECTED
_CODE_UPSTREAM = MetadataDeliveryErrorCode.COLLABORATION_UPSTREAM_ERROR
_CODE_UNAVAILABLE = MetadataDeliveryErrorCode.COLLABORATION_SYNC_UNAVAILABLE
_CODE_NOT_FOUND = MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND
# The started-or-not family is separate: an environment/credential skip
# refuses to start the repair, so it carries the precondition code.
_PRECONDITION_UNAVAILABLE = (
    RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
)


@plugin_impl(mode=Mode.PROD, rationale="BCN up-link collaboration metadata delivery")
class BcnCollaborationMetadataAdapter(BcsCollaborationMetadataPlugin):
    """Adapt the existing synchronous BCN up-link onto the Plugin Protocol."""

    def __init__(
        self,
        bcn_service: BcnService,
        profile_headers: Mapping[str, str],
    ) -> None:
        self._bcn_service = bcn_service
        # Defensive copy: this mapping carries the operating user's
        # credentials, and the adapter must not observe later mutation.
        self._profile_headers = dict(profile_headers)

    async def exists(self, bcs_bot_id: str, *, timeout_seconds: float) -> bool:
        """One provider-scoped attributes GET decides remote presence."""
        try:
            result = await asyncio.to_thread(
                self._bcn_service.get_attributes,
                bot_uuid=bcs_bot_id,
                timeout_seconds=timeout_seconds,
            )
        except BcnBotMissingError:
            # 404 is the documented missing-bot signal and the ONLY False
            # path — every other failure keeps its typed classification
            # instead of collapsing into a fabricated absence.
            logger.info(
                "collaboration_metadata operation=exists bcs_bot_id=%s outcome=absent",
                bcs_bot_id,
            )
            return False
        except BcnServiceError as exc:
            self._raise_delivery_failure("exists", bcs_bot_id, exc)
        # The transport normalizes a non-dict attributes body to ``{}``, so
        # the mapping guard below is defence-in-depth for other
        # implementations rather than the live path.
        body = self._require_mapping("exists", bcs_bot_id, result)
        self._reject_skipped("exists", bcs_bot_id, body)
        logger.info(
            "collaboration_metadata operation=exists bcs_bot_id=%s outcome=present",
            bcs_bot_id,
        )
        return True

    async def update_profile(
        self,
        snapshot: ProfileSnapshot,
        *,
        timeout_seconds: float,
    ) -> None:
        """One onboard send of the committed name/summary/avatar projection."""
        # The daily up-link omits falsy request_headers, so an empty
        # profile_headers keeps the historical payload shape exactly.
        send_kwargs: dict[str, object] = {
            "avatar_url": snapshot.avatar_url,
            "timeout_seconds": timeout_seconds,
        }
        if self._profile_headers:
            send_kwargs["request_headers"] = self._profile_headers
        try:
            result = await asyncio.to_thread(
                self._bcn_service.onboard_bot,
                bot_id=snapshot.bcs_bot_id,
                name=snapshot.name,
                summary=snapshot.summary,
                **send_kwargs,
            )
        except BcnServiceError as exc:
            self._raise_delivery_failure("profile", snapshot.bcs_bot_id, exc)
        # Blank summaries retain BCS Keep semantics. A valid acknowledgement
        # confirms this operation; success does not imply a summary clear.
        self._confirm_onboard(snapshot.bcs_bot_id, result)
        logger.info(
            "collaboration_metadata operation=profile bcs_bot_id=%s outcome=confirmed",
            snapshot.bcs_bot_id,
        )

    async def aclose(self) -> None:
        """Release nothing: the transport singletons are borrowed, not owned.

        ``BcnService`` and the bcn-qualified ``HttpClient`` pool stay alive
        for the daily BCN path; their lifecycle belongs to the injector
        bindings that constructed them, not to one repair invocation.
        """
        return None

    def _require_mapping(
        self, operation: str, bcs_bot_id: str, result: object
    ) -> Mapping[str, object]:
        """Reject a 2xx body that is not an object; nothing is inferred from it."""
        if not isinstance(result, dict):
            self._raise_malformed(operation, bcs_bot_id)
        return result

    def _reject_skipped(
        self, operation: str, bcs_bot_id: str, body: Mapping[str, object]
    ) -> None:
        """An environment/credential gate is unavailability, not an answer."""
        if body.get(_SKIPPED_KEY) is True:
            self._raise_unavailable(operation, bcs_bot_id)

    def _confirm_onboard(self, bcs_bot_id: str, result: object) -> None:
        """Verify BCN's 2xx onboard confirmation names this stable bot."""
        body = self._require_mapping("profile", bcs_bot_id, result)
        self._reject_skipped("profile", bcs_bot_id, body)
        onboarded = body.get(_ONBOARDED_KEY)
        if onboarded is False:
            logger.warning(
                "collaboration_metadata operation=profile bcs_bot_id=%s "
                "outcome=failed kind=%s code=%s",
                bcs_bot_id,
                DeliveryFailure.REJECTED.value,
                _CODE_NOT_FOUND,
            )
            raise MetadataDeliveryError(DeliveryFailure.REJECTED, _CODE_NOT_FOUND)
        if onboarded is not True:
            self._raise_malformed("profile", bcs_bot_id)
        bot_uuid = body.get(_BOT_UUID_KEY)
        if not isinstance(bot_uuid, str) or bot_uuid != bcs_bot_id:
            # The endpoint echoes the requested stable identity; anything
            # else means the confirmation describes a different bot.
            self._raise_malformed("profile", bcs_bot_id)

    @staticmethod
    def _raise_delivery_failure(
        operation: str,
        bcs_bot_id: str,
        exc: BcnServiceError,
    ) -> NoReturn:
        """Bucket a typed transport failure into the finite delivery vocabulary.

        The transport's ``kind`` is the authority for "rejected" versus
        "unconfirmed"; its detail ``code`` (``http_403``, ``http_unknown``,
        …) never propagates, because only the kernel's
        ``MetadataDeliveryErrorCode`` members may later appear on a
        ``RepairItem``.
        """
        if isinstance(exc, BcnRequestError):
            if exc.code == "timeout":
                # The send may have landed: keep the transport's UNKNOWN kind.
                kind = exc.kind
                code = _CODE_TIMEOUT
            elif exc.kind is DeliveryFailure.REJECTED:
                kind = DeliveryFailure.REJECTED
                code = _CODE_REJECTED
            elif exc.kind is DeliveryFailure.NOT_SENT:
                # Defensive: the transport does not raise NOT_SENT today.
                kind = DeliveryFailure.NOT_SENT
                code = _CODE_UNAVAILABLE
            else:
                kind = DeliveryFailure.UNKNOWN
                code = _CODE_UPSTREAM
        elif isinstance(exc, BcnUnavailableError):
            kind = DeliveryFailure.UNKNOWN
            code = _CODE_UNAVAILABLE
        elif isinstance(exc, BcnBotMissingError):
            kind = DeliveryFailure.REJECTED
            code = _CODE_NOT_FOUND
        else:
            kind = DeliveryFailure.UNKNOWN
            code = _CODE_UNTYPED
        error = MetadataDeliveryError(kind, code)
        logger.warning(
            "collaboration_metadata operation=%s bcs_bot_id=%s outcome=failed "
            "kind=%s code=%s error_type=%s",
            operation,
            bcs_bot_id,
            kind.value,
            code,
            type(exc).__name__,
        )
        raise error from exc

    @staticmethod
    def _raise_unavailable(operation: str, bcs_bot_id: str) -> NoReturn:
        """Treat an environment/credential skip as failed-to-start."""
        logger.warning(
            "collaboration_metadata operation=%s bcs_bot_id=%s outcome=skipped "
            "code=%s",
            operation,
            bcs_bot_id,
            _PRECONDITION_UNAVAILABLE,
        )
        raise RepairPreconditionError(_PRECONDITION_UNAVAILABLE)

    @staticmethod
    def _raise_malformed(operation: str, bcs_bot_id: str) -> NoReturn:
        """A 2xx body that fails the success shape confirms nothing."""
        logger.warning(
            "collaboration_metadata operation=%s bcs_bot_id=%s outcome=unknown "
            "code=%s",
            operation,
            bcs_bot_id,
            _CODE_RESPONSE_MALFORMED,
        )
        raise MetadataDeliveryError(
            DeliveryFailure.UNKNOWN, _CODE_RESPONSE_MALFORMED
        )
