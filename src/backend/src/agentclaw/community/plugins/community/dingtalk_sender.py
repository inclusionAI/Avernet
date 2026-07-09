"""DingTalk 消息发送服务 — community implementation (migrated from corp).

Provides two sender classes for DingTalk robot messaging:

  - ``DingTalkMarkdownSender`` — sends via
    ``POST /v1.0/robot/oToMessages/batchSend`` with
    ``msgKey=sampleMarkdown``.

  - ``DingTalkTcCardSender`` — sends via
    ``POST /v1.0/card/instances/createAndDeliver`` (TC card),
    with ``send_markdown()`` as a degradation fallback.

Both are **leaf components** with no service-level dependencies.
Uses stdlib ``http.client`` instead of ``httpx`` to avoid:

  - ``SSL: CERTIFICATE_VERIFY_FAILED`` — httpx bundles the certifi
    CA bundle, which lacks corporate proxy CA certificates; stdlib
    http.client uses the OS certificate store (which includes them).
  - ``sofa_tracer`` monkey-patch on ``httpx.Client.send`` — breaks
    in SpawnProcess contexts where the hook is inherited but
    incomplete.

This module is dependency-neutral (stdlib only) and therefore lives in the
community package: the community/singlebox profile can dispatch real DingTalk
notifications when credentials are configured, without importing the corp
package (B11 corp-free boundary preserved). The corp package re-exports these
symbols from its historical path (``agentclaw.corp.plugins.prod.dingtalk_sender``)
so existing corp call sites are unchanged.

AccessToken lifecycle:
  - Fetched via ``POST /v1.0/oauth2/accessToken``.
  - Cached in-memory with a 2-hour TTL (minus a 5-minute safety margin).
  - Lazy refresh on next ``send_*()`` call after expiry.

Error handling:
  - All exceptions are caught and logged; ``send_*()`` **never
    raises** — it returns ``None`` on failure so callers can mark the
    notification for retry.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DingTalk API endpoints
# ---------------------------------------------------------------------------

_DINGTALK_HOST = "api.dingtalk.com"
_GET_TOKEN_PATH = "/v1.0/oauth2/accessToken"
_SEND_ROBOT_MSG_PATH = "/v1.0/robot/oToMessages/batchSend"
_CREATE_CARD_PATH = "/v1.0/card/instances/createAndDeliver"

# HTTP timeout for DingTalk API calls (seconds)
_API_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# HTTP transport (stdlib http.client)
# ---------------------------------------------------------------------------


def _dingtalk_post(
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    """POST to DingTalk API via stdlib ``http.client``.

    Uses stdlib http.client instead of httpx to avoid:

      - ``SSL: CERTIFICATE_VERIFY_FAILED`` — httpx bundles the certifi
        CA bundle, which lacks corporate proxy CA certificates.
      - ``sofa_tracer`` monkey-patch on ``httpx.Client.send`` that
        breaks in SpawnProcess contexts.

    Args:
        path: API path (e.g. ``/v1.0/oauth2/accessToken``).
        body: JSON-serializable request body.
        headers: Extra request headers (Content-Type is set automatically).

    Returns:
        ``(status_code, response_body_text)``
    """
    import http.client

    conn = http.client.HTTPSConnection(_DINGTALK_HOST, timeout=_API_TIMEOUT)
    hdrs: dict[str, str] = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    conn.request(
        "POST",
        path,
        body=json.dumps(body) if body else "",
        headers=hdrs,
    )
    resp = conn.getresponse()
    resp_body = resp.read().decode()
    conn.close()
    return resp.status, resp_body


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DingTalkSenderConfig:
    """DingTalk application credentials for robot messaging.

    All fields are injected via DI (SecretResolver or environment variables).
    The dataclass is ``frozen`` to make it safely shareable across threads.
    """

    app_key: str
    app_secret: str
    robot_code: str


# ---------------------------------------------------------------------------
# Access Token Manager
# ---------------------------------------------------------------------------


class _AccessTokenManager:
    """Manage DingTalk accessToken with in-memory caching (2h TTL).

    Thread-safety: not thread-safe by itself. Current usage is within
    the scan lock (single-threaded), so no locking is needed. If future
    callers invoke from multiple threads, add a ``threading.Lock``.
    """

    def __init__(self, app_key: str, app_secret: str) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._access_token: str = ""
        self._expire_time: float = 0.0

    def get_token(self) -> str:
        """Return a valid accessToken, refreshing if expired.

        Raises:
            RuntimeError: If the token request fails.
        """
        if self._access_token and _now() < self._expire_time:
            return self._access_token

        status, resp_body = _dingtalk_post(
            _GET_TOKEN_PATH,
            body={
                "appKey": self._app_key,
                "appSecret": self._app_secret,
            },
        )

        if status != 200:
            raise RuntimeError(
                f"accessToken request failed: HTTP {status} {resp_body[:300]}"
            )

        data = json.loads(resp_body)
        token = data.get("accessToken")
        expire_in = data.get("expireIn", 7200)
        if not token:
            raise RuntimeError(f"accessToken missing in response: {data}")

        self._access_token = token
        # Refresh 5 minutes early to avoid edge-case expiry
        self._expire_time = _now() + expire_in - 300
        return self._access_token


def _now() -> float:
    """Return current time as epoch seconds (testable)."""
    import time
    return time.time()


# ---------------------------------------------------------------------------
# DingTalkMarkdownSender
# ---------------------------------------------------------------------------


class DingTalkMarkdownSender:
    """Send DingTalk robot messages via batchSend (sampleMarkdown).

    This is the Markdown-channel sender.  It is a leaf component
    — stdlib ``http.client`` via ``_dingtalk_post``, no DI dependencies
    beyond the ``DingTalkSenderConfig`` it receives at construction.

    Usage::

        sender = DingTalkMarkdownSender(config)
        msg_id = sender.send_markdown("staffId123", "通知标题", "# Hello")
        if msg_id:
            print("sent:", msg_id)
        else:
            print("failed, will retry next scan")
    """

    def __init__(self, config: DingTalkSenderConfig) -> None:
        self._config = config
        self._token_mgr = _AccessTokenManager(config.app_key, config.app_secret)

    def send_markdown(
        self,
        user_id: str,
        title: str,
        content: str,
    ) -> str | None:
        """Send a Markdown notification to a DingTalk user.

        Args:
            user_id: Recipient staffId (DingTalk ``userIds[]``).
            title: Message title (``sampleMarkdown.title``).
            content: Markdown body (``sampleMarkdown.text``).

        Returns:
            External message ID on success, ``None`` on failure.
            **Never raises** — all errors are caught and logged.
        """
        try:
            token = self._token_mgr.get_token()
        except Exception:
            logger.exception(
                "[DingTalkMarkdownSender] Failed to get accessToken",
            )
            return None

        payload: dict[str, Any] = {
            "robotCode": self._config.robot_code,
            "userIds": [uid.strip() for uid in user_id.split(",")],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps(
                {"title": title, "text": content},
                ensure_ascii=False,
            ),
        }

        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }

        try:
            status, resp_body = _dingtalk_post(
                _SEND_ROBOT_MSG_PATH,
                body=payload,
                headers=headers,
            )

            if status != 200:
                logger.error(
                    "[DingTalkMarkdownSender] batchSend failed: "
                    "status=%d body=%s user_id=%s",
                    status, resp_body[:512], user_id,
                )
                return None

            result = json.loads(resp_body)
        except Exception:
            logger.exception(
                "[DingTalkMarkdownSender] HTTP error during batchSend, user_id=%s",
                user_id,
            )
            return None

        # Extract external message ID for tracking.
        # DingTalk batchSend response body: ``{}`` on success with
        # HTTP 200, but the meaningful ID is in ``process_query_key``
        # when available.  Fall back to a UUID-based sentinel if the
        # response is empty (common for batchSend).
        msg_id = (
            result.get("process_query_key")
            or result.get("message_id")
            or ""
        )
        if not msg_id:
            # batchSend typically returns an empty body on success;
            # use a placeholder so we can distinguish sent vs not-sent.
            msg_id = f"dingtalk-{uuid.uuid4().hex[:12]}"

        logger.info(
            "[DingTalkMarkdownSender] Sent markdown to user_id=%s, "
            "external_message_id=%s",
            user_id, msg_id,
        )
        return msg_id

    def send_tc_card(
        self,
        user_id: str,
        reason: str,
        detail_link: str,
        bot_id: str,
        card_id: str,
        notification_data: dict[str, Any],
        out_track_id_prefix: str = "dingtalk",
    ) -> str | None:
        """Markdown sender cannot send TC cards — always returns ``None``.

        This method exists to satisfy the ``GovernanceNotifySender``
        protocol.  The ``DingTalkMarkdownSender`` only supports the
        Markdown channel; TC card dispatching is handled by
        ``DingTalkTcCardSender``.
        """
        logger.warning(
            "[DingTalkMarkdownSender] send_tc_card called but this sender "
            "only supports Markdown channel — returning None. "
            "Use DingTalkTcCardSender for TC card dispatch.",
        )
        return None


# ---------------------------------------------------------------------------
# TC Card Sender (createAndDeliver)
# ---------------------------------------------------------------------------


class DingTalkTcCardSender:
    """Send DingTalk TC card messages via createAndDeliver.

    This is the TC-card-channel sender.  It sends a card shell
    with:
      - ``reason``: Markdown content rendered by DDRichTextView
      - ``detailLink``: 3-layer nested DingTalk deep link that opens
        a teamclaw preview iframe for full detail + feedback form

    It also supports ``send_markdown()`` as a fallback (degradation)
    so that a single sender can handle both channels transparently.

    Leaf component — stdlib ``http.client`` via ``_dingtalk_post``,
    no DI dependencies beyond the ``DingTalkSenderConfig``
    it receives at construction.

    Usage::

        # card_template_id and card_id both come from config
        # (EconomyGovernanceConfig.tc_card_template_id / tc_card_id), never
        # hardcoded — community source ships no corp card identifiers.
        sender = DingTalkTcCardSender(config, card_template_id=template_id)
        msg_id = sender.send_tc_card(
            "staffId123", reason, detail_link, "bot_123", card_id, data
        )
    """

    def __init__(
        self,
        config: DingTalkSenderConfig,
        card_template_id: str = "",
    ) -> None:
        self._config = config
        self._card_template_id = card_template_id
        self._token_mgr = _AccessTokenManager(config.app_key, config.app_secret)

    def send_tc_card(
        self,
        user_id: str,
        reason: str,
        detail_link: str,
        bot_id: str,
        card_id: str,
        notification_data: dict[str, Any],
        out_track_id_prefix: str = "dingtalk",
    ) -> str | None:
        """Send a TC card notification to a DingTalk user.

        Uses ``POST /v1.0/card/instances/createAndDeliver``.

        The card shell renders ``reason`` via DDRichTextView, and the
        ``detailLink`` opens a teamclaw preview iframe.

        Uses ``callbackType=STREAM`` — feedback is handled by the iframe
        React component via fetch POST, not by DingTalk platform callbacks.

        Args:
            user_id: Recipient staffId.
            reason: Markdown content rendered in card shell.
            detail_link: DingTalk deep link (3-layer nested encoding).
            bot_id: Bot ID for outTrackId generation.
            card_id: Aix card component ID.
            notification_data: Full structured data (unused in the API
                call directly — it's already encoded in detail_link;
                accepted for protocol conformance).
            out_track_id_prefix: Prefix for the ``outTrackId`` field.
                Business callers should pass their own prefix (e.g.
                ``"gov-notify"`` for governance notifications).
                Defaults to ``"dingtalk"``.

        Returns:
            External message ID on success, ``None`` on failure.
            **Never raises** — all errors are caught and logged.
        """
        if not self._card_template_id:
            logger.warning(
                "[DingTalkTcCardSender] No card_template_id configured — "
                "cannot send TC card, returning None. user_id=%s",
                user_id,
            )
            return None

        try:
            token = self._token_mgr.get_token()
        except Exception:
            logger.exception(
                "[DingTalkTcCardSender] Failed to get accessToken",
            )
            return None

        out_track_id = f"{out_track_id_prefix}-{uuid.uuid4().hex[:12]}"

        # cardParamMap values must all be strings
        card_param_map: dict[str, str] = {
            "session_id": f"tc_{uuid.uuid4().hex[:8]}",
            "bot_id": bot_id,
            "reason": reason,
            "detailLink": detail_link,
        }

        payload: dict[str, Any] = {
            "userId": user_id,
            "userIdType": 1,  # 1 = staffId
            "cardTemplateId": self._card_template_id,
            "outTrackId": out_track_id,
            "callbackType": "STREAM",  # iframe fetch POST handles feedback
            "openSpaceId": f"dtv1.card//im_robot.{user_id}",
            "cardData": {"cardParamMap": card_param_map},
            "imGroupOpenSpaceModel": {"supportForward": False},
            "imGroupOpenDeliverModel": {
                "robotCode": self._config.robot_code,
                "recipients": [user_id],
            },
            "imRobotOpenSpaceModel": {"supportForward": False},
            "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
        }

        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }

        try:
            status, resp_body = _dingtalk_post(
                _CREATE_CARD_PATH,
                body=payload,
                headers=headers,
            )

            if status != 200:
                logger.error(
                    "[DingTalkTcCardSender] createAndDeliver failed: "
                    "status=%d body=%s user_id=%s",
                    status, resp_body[:512], user_id,
                )
                return None

            result = json.loads(resp_body)
        except Exception:
            logger.exception(
                "[DingTalkTcCardSender] HTTP error during createAndDeliver, "
                "user_id=%s",
                user_id,
            )
            return None

        # Extract external identifier for tracking
        msg_id = (
            result.get("outTrackId")
            or result.get("process_query_key")
            or result.get("cardInstId")
            or out_track_id
        )

        logger.info(
            "[DingTalkTcCardSender] Sent TC card to user_id=%s, "
            "external_message_id=%s, outTrackId=%s",
            user_id, msg_id, out_track_id,
        )
        return msg_id

    def send_markdown(
        self,
        user_id: str,
        title: str,
        content: str,
    ) -> str | None:
        """Send a Markdown notification as fallback (degradation).

        Uses the same DingTalk batchSend API as ``DingTalkMarkdownSender``.
        This allows a single ``DingTalkTcCardSender`` instance to handle
        both channels transparently when degradation is needed.
        """
        try:
            token = self._token_mgr.get_token()
        except Exception:
            logger.exception(
                "[DingTalkTcCardSender] Failed to get accessToken for markdown fallback",
            )
            return None

        payload: dict[str, Any] = {
            "robotCode": self._config.robot_code,
            "userIds": [uid.strip() for uid in user_id.split(",")],
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps(
                {"title": title, "text": content},
                ensure_ascii=False,
            ),
        }

        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }

        try:
            status, resp_body = _dingtalk_post(
                _SEND_ROBOT_MSG_PATH,
                body=payload,
                headers=headers,
            )

            if status != 200:
                logger.error(
                    "[DingTalkTcCardSender] Markdown fallback failed: "
                    "status=%d body=%s user_id=%s",
                    status, resp_body[:512], user_id,
                )
                return None

            result = json.loads(resp_body)
        except Exception:
            logger.exception(
                "[DingTalkTcCardSender] HTTP error during markdown fallback, user_id=%s",
                user_id,
            )
            return None

        msg_id = (
            result.get("process_query_key")
            or result.get("message_id")
            or ""
        )
        if not msg_id:
            msg_id = f"dingtalk-md-{uuid.uuid4().hex[:12]}"

        logger.info(
            "[DingTalkTcCardSender] Sent markdown fallback to user_id=%s, "
            "external_message_id=%s",
            user_id, msg_id,
        )
        return msg_id