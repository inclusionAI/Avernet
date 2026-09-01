"""Business callbacks that must succeed before a work-order decision is stored."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import quote

from agentclaw.community.core.work_orders.errors import (
    WorkOrderCallbackError,
    WorkOrderInvalidEventError,
)
from agentclaw.community.core.work_orders.models import (
    FRIEND_APPROVAL_EVENT_TYPES,
    WorkOrderApprovalContext,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderEventType,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import HttpClient


logger = get_logger()
_RESPONSE_BODY_LOG_LIMIT = 16 * 1024

# The forwarded identity header, lowercased for the case-insensitive comparisons
# below — HTTP header names are case-insensitive and the inbound spelling is
# whatever the gateway and the ASGI server happened to use.
_PRINCIPAL_HEADER = "x-avernet-principal"


def _principal_fingerprint(headers: Mapping[str, str]) -> str | None:
    """Return a safe correlation fingerprint for the forwarded Principal.

    The JWT itself is never logged; the short digest lets operators compare the
    approval request credential with the BCN callback credential.

    Since the callback credential is **re-addressed** before it is sent (see
    :meth:`FriendDecisionCallbackHandler.handle`), the two no longer share a
    digest — a re-signed token is a different token. Both are logged, under
    ``principal_fingerprint`` for what went to BCN and
    ``source_principal_fingerprint`` for what arrived here, so the hop is still
    correlatable from one log line to the next.
    """
    values = [
        value for key, value in headers.items() if key.lower() == _PRINCIPAL_HEADER
    ]
    if not values:
        return None
    return hashlib.sha256(values[0].encode("utf-8")).hexdigest()[:16]


def _principal_header_count(headers: Mapping[str, str]) -> int:
    return sum(key.lower() == _PRINCIPAL_HEADER for key in headers)


def _is_successful_bcn_response(payload: dict[str, object] | None) -> bool:
    """Accept the legacy and OpenAPI BCN success envelopes.

    The legacy friend-connections route returns ``{"success": true, ...}``,
    while the OpenAPI route returns the common envelope with ``code=20000``.
    Both are valid responses for this callback during the route migration.
    """
    if payload is None:
        return False
    if payload.get("success") is True:
        return True
    return payload.get("code") == 20_000


@dataclass(frozen=True)
class WorkOrderCallbackCredential:
    """Caller credentials explicitly allowed across the Backend-to-BCN boundary."""

    headers: Mapping[str, str]


def friend_request_id(biz_data: dict[str, object] | None) -> str:
    """Return the authoritative BCN request id or reject malformed friend data."""

    if not isinstance(biz_data, dict):
        raise WorkOrderInvalidEventError("friend work order requires biz_data")
    request_ids = biz_data.get("request_ids")
    if not isinstance(request_ids, list) or not request_ids:
        raise WorkOrderInvalidEventError(
            "friend work order requires non-empty request_ids"
        )
    request_id = request_ids[0]
    if not isinstance(request_id, str) or not request_id.strip():
        raise WorkOrderInvalidEventError(
            "friend work order requires a non-empty request id"
        )
    return request_id.strip()


def validate_friend_approval_event(
    *,
    biz_type: str,
    event_type: str,
    biz_data: dict[str, object] | None,
) -> None:
    """Validate the creation contract for friend approval work orders."""

    try:
        typed_event = WorkOrderEventType(event_type)
    except ValueError:
        return
    if typed_event not in FRIEND_APPROVAL_EVENT_TYPES:
        return
    if biz_type != WorkOrderBizType.BOT_FRIEND.value:
        raise WorkOrderInvalidEventError(
            "friend approval event requires BOT_FRIEND biz_type"
        )
    friend_request_id(biz_data)


class FriendDecisionCallbackHandler:
    """Apply a friend-request decision to BCN before local persistence.

    The decision arrives on ``/openapi/v1`` carrying the gateway-signed
    ``X-Avernet-Principal`` the backend just verified, and applying it means
    calling BCN as that same caller. The header cannot simply be relayed: the
    gateway addresses each token to one upstream, so the one we hold says
    ``aud=backend`` and BCN's verifier refuses it — the callback failed its
    audience check on every friend approval, and no retry could have fixed it.

    So the credential is **re-addressed** before it is forwarded:
    ``resign_principal`` re-signs the verified claims with the shared key under
    the ``iss``/``aud``/``kid`` BCN requires, leaving the identities and the
    token's lifetime untouched (``core/gateway_principal/signer.py``). BCN then
    authorizes the same caller the backend did, which is the whole point — the
    approval is applied *as the approver*, not as the backend.
    """

    def __init__(
        self,
        http_client: HttpClient,
        resign_principal: Callable[[str], str],
        timeout: float = 10.0,
    ) -> None:
        self._http = http_client
        self._resign_principal = resign_principal
        self._timeout = timeout

    def _readdressed_headers(
        self, headers: Mapping[str, str], *, work_order_id: int
    ) -> dict[str, str]:
        """Return ``headers`` with the forwarded Principal re-addressed to BCN.

        Every other header is passed through untouched — this seam forwards a
        credential, it does not compose a request.

        A **missing or blank** Principal is left alone rather than raised on:
        there is nothing to re-address, and the call then fails at BCN exactly
        as it did before, with ``has_principal=False`` already on the log line
        below naming why. A Principal that is present but cannot be
        re-addressed is a different matter — the token did not verify, or this
        deployment has no signing key — and there is no point sending a
        credential we know BCN will refuse, so the callback fails closed and
        the decision is not stored.
        """
        readdressed = dict(headers)
        for key in [k for k in readdressed if k.lower() == _PRINCIPAL_HEADER]:
            token = readdressed[key].strip()
            if not token:
                continue
            try:
                readdressed[key] = self._resign_principal(token)
            except Exception as exc:
                # The reason, never the token. A verification failure names the
                # contract and the key fingerprint this side judged the token
                # against, which is what an operator needs; the digest
                # correlates this line with the request that carried the
                # credential, since no re-addressed one exists to log.
                logger.warning(
                    "friend work-order BCN callback credential could not be "
                    "re-addressed: work_order_id=%s "
                    "source_principal_fingerprint=%s "
                    "exception_type=%s reason=%s",
                    work_order_id,
                    _principal_fingerprint(headers),
                    type(exc).__name__,
                    exc,
                    extra={
                        "work_order_id": work_order_id,
                        "source_principal_fingerprint": _principal_fingerprint(
                            headers
                        ),
                        "exception_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
                raise WorkOrderCallbackError(
                    "BCN callback credential could not be re-addressed"
                ) from exc
        return readdressed

    def handle(
        self,
        *,
        context: WorkOrderApprovalContext,
        decision: WorkOrderDecision,
        review_remark: str | None,
        credential: WorkOrderCallbackCredential,
    ) -> None:
        if context.work_order.biz_type != WorkOrderBizType.BOT_FRIEND.value:
            raise WorkOrderInvalidEventError(
                "friend approval event requires BOT_FRIEND biz_type"
            )
        try:
            biz_data = (
                json.loads(context.work_order.biz_data)
                if context.work_order.biz_data is not None
                else None
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise WorkOrderInvalidEventError(
                "friend work order biz_data must be a JSON object"
            ) from exc
        request_id = friend_request_id(biz_data)
        action = "accept" if decision is WorkOrderDecision.APPROVED else "reject"
        path = (
            "/openapi/v1/collaboration/friend-connections/requests/"
            f"{quote(request_id, safe='')}/{action}"
        )
        body = (
            None
            if decision is WorkOrderDecision.APPROVED
            else {"reason": review_remark}
        )
        source_principal_fingerprint = _principal_fingerprint(credential.headers)
        callback_headers = self._readdressed_headers(
            credential.headers, work_order_id=context.work_order.id
        )
        lowered_headers = {key.lower(): value for key, value in callback_headers.items()}
        has_principal = "x-avernet-principal" in lowered_headers
        has_authorization = "authorization" in lowered_headers
        principal_header_count = _principal_header_count(callback_headers)
        principal_fingerprint = _principal_fingerprint(callback_headers)
        principal_length = (
            len(lowered_headers["x-avernet-principal"])
            if has_principal
            else None
        )
        logger.info(
            "BCN callback auth headers: has_principal=%s "
            "principal_header_count=%s principal_fingerprint=%s "
            "source_principal_fingerprint=%s "
            "principal_length=%s has_authorization=%s",
            has_principal,
            principal_header_count,
            principal_fingerprint,
            source_principal_fingerprint,
            principal_length,
            has_authorization,
        )
        logger.info(
            "friend work-order BCN callback request: "
            "work_order_id=%s callback_path=%s action=%s "
            "has_principal=%s principal_header_count=%s "
            "principal_fingerprint=%s source_principal_fingerprint=%s "
            "principal_length=%s "
            "has_authorization=%s x_request_id=%s x_trace_id=%s",
            context.work_order.id,
            path,
            action,
            has_principal,
            principal_header_count,
            principal_fingerprint,
            source_principal_fingerprint,
            principal_length,
            has_authorization,
            lowered_headers.get("x-request-id"),
            lowered_headers.get("x-trace-id"),
            extra={
                "work_order_id": context.work_order.id,
                "event_type": context.source_event_type,
                "request_id": request_id,
                "action": action,
                "callback_path": path,
                "request_body": body,
                "has_authorization": has_authorization,
                "has_x_avernet_principal": has_principal,
                "principal_header_count": principal_header_count,
                "principal_fingerprint": principal_fingerprint,
                "source_principal_fingerprint": source_principal_fingerprint,
                "principal_length": principal_length,
                "x_request_id": lowered_headers.get("x-request-id"),
                "x_trace_id": lowered_headers.get("x-trace-id"),
            },
        )
        response = None
        response_body_raw = ""
        response_payload: dict[str, object] | None = None
        callback_started = time.perf_counter()
        try:
            response = self._http.post(
                path,
                json=body,
                headers=callback_headers,
                timeout=self._timeout,
            )
            response_body_raw = response.text
            logged_response_body = response_body_raw
            if len(logged_response_body) > _RESPONSE_BODY_LOG_LIMIT:
                logged_response_body = (
                    logged_response_body[:_RESPONSE_BODY_LOG_LIMIT] + "...<truncated>"
                )
            try:
                parsed = json.loads(response_body_raw)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                response_payload = parsed
            duration_ms = (time.perf_counter() - callback_started) * 1000
            response_code = response_payload.get("code") if response_payload else None
            response_message = (
                response_payload.get("message") if response_payload else None
            )
            response_request_id = (
                response_payload.get("request_id") if response_payload else None
            )
            logger.info(
                "friend work-order BCN callback response: "
                "http_status=%s response_code=%s response_message=%s "
                "response_request_id=%s response_body_raw=%s duration_ms=%.1f",
                response.status_code,
                response_code,
                response_message,
                response_request_id,
                logged_response_body,
                duration_ms,
                extra={
                    "work_order_id": context.work_order.id,
                    "event_type": context.source_event_type,
                    "request_id": request_id,
                    "action": action,
                    "http_status": response.status_code,
                    "response_code": response_code,
                    "response_message": response_message,
                    "response_request_id": response_request_id,
                    "duration_ms": duration_ms,
                    "response_body_raw": logged_response_body,
                },
            )
            response.raise_for_status()
            if not _is_successful_bcn_response(response_payload):
                raise ValueError("BCN callback did not report success")
        except Exception as exc:
            failure_duration_ms = (time.perf_counter() - callback_started) * 1000
            http_status = response.status_code if response is not None else None
            response_code = (
                response_payload.get("code") if response_payload else None
            )
            response_message = (
                response_payload.get("message") if response_payload else None
            )
            response_request_id = (
                response_payload.get("request_id") if response_payload else None
            )
            logged_failure_body = (
                response_body_raw[:_RESPONSE_BODY_LOG_LIMIT] + "...<truncated>"
                if len(response_body_raw) > _RESPONSE_BODY_LOG_LIMIT
                else response_body_raw
            )
            logger.warning(
                "friend work-order decision callback failed: "
                "work_order_id=%s callback_path=%s action=%s "
                "http_status=%s response_code=%s response_message=%s "
                "response_request_id=%s response_body_raw=%s duration_ms=%.1f "
                "exception_type=%s principal_header_count=%s "
                "principal_fingerprint=%s source_principal_fingerprint=%s "
                "principal_length=%s",
                context.work_order.id,
                path,
                action,
                http_status,
                response_code,
                response_message,
                response_request_id,
                logged_failure_body,
                failure_duration_ms,
                type(exc).__name__,
                principal_header_count,
                principal_fingerprint,
                source_principal_fingerprint,
                principal_length,
                extra={
                    "work_order_id": context.work_order.id,
                    "event_type": context.source_event_type,
                    "request_id": request_id,
                    "action": action,
                    "http_status": http_status,
                    "response_code": response_code,
                    "response_message": response_message,
                    "response_request_id": response_request_id,
                    "principal_header_count": principal_header_count,
                    "principal_fingerprint": principal_fingerprint,
                    "source_principal_fingerprint": source_principal_fingerprint,
                    "principal_length": principal_length,
                    "duration_ms": failure_duration_ms,
                    "exception_type": type(exc).__name__,
                    "response_body_raw": logged_failure_body,
                },
                exc_info=True,
            )
            raise WorkOrderCallbackError("BCN callback failed") from exc


class WorkOrderDecisionCallbackDispatcher:
    """Dispatch only explicitly registered approval events; all others are no-op.

    ``resign_principal`` is handed in by the composition root
    (``di/modules/work_orders_module.py``) rather than resolved here: it closes
    over the process-wide signing key, which is deployment configuration this
    layer must stay transport- and config-agnostic about (Rule 7). See
    :class:`FriendDecisionCallbackHandler` for what it is for.
    """

    def __init__(
        self,
        http_client: HttpClient,
        resign_principal: Callable[[str], str],
    ) -> None:
        friend_handler = FriendDecisionCallbackHandler(
            http_client, resign_principal=resign_principal
        )
        self._handlers = {
            WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value: friend_handler,
            WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value: friend_handler,
        }

    def requires_callback(self, event_type: str | None) -> bool:
        return event_type in self._handlers

    def dispatch(
        self,
        *,
        context: WorkOrderApprovalContext,
        decision: WorkOrderDecision,
        review_remark: str | None,
        credential: WorkOrderCallbackCredential,
    ) -> None:
        handler = self._handlers.get(context.source_event_type)
        if handler is None:
            return
        handler.handle(
            context=context,
            decision=decision,
            review_remark=review_remark,
            credential=credential,
        )
