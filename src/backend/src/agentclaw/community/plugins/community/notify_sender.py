"""Community ``NotifySenderPlugin`` — log-only notification sender.

A real, deployable impl (not a MockSeam test double). The community build
ships no DingTalk / Slack / Email channel — notifications are delivered to
the standard logger via ``get_logger``. Since writing to the log IS the
delivery, ``send()`` returns a log-based message ID (not ``None``).

Corp deployments bind ``DingTalkNotifySender`` instead for real delivery.

Mirrors ``CommunityDRMReader`` (degenerate but real, every flag is unset)
and ``NoApprovalWorkflow`` (unavailable, tells callers so). Not a
``MockSeam`` — bound directly by ``CommunityNotifyModule``.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

log = get_logger(__name__)


def _env(name: str, fallback: str = "") -> str:
    """读取 env，带 SINGLEBOX_* 兼容回退（便于复用 e2e 既有的钉钉凭证）。"""
    return (os.environ.get(name) or os.environ.get(fallback) or "").strip()


class CommunityNotifySender(NotifySenderPlugin):
    """Community profile: log-only notification sender.

    ``send()`` writes the notification to the standard logger and returns
    a ``log-<uuid>`` message ID — writing to the log IS the delivery,
    so the caller treats this as a successful send.
    """

    @property
    def channels(self) -> frozenset[str]:
        return frozenset({"log"})

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        msg_id = f"log-{uuid.uuid4().hex[:12]}"
        log.info(
            "[CommunityNotifySender] send(channel=%s → log, recipient=%s, "
            "title=%r, deep_link=%s) → %s",
            channel,
            message.recipient,
            message.title[:80],
            message.deep_link[:60] if message.deep_link else "",
            msg_id,
        )
        log.debug(
            "[CommunityNotifySender] message body:\n%s",
            message.body[:500] if message.body else "",
        )
        return msg_id


class DingTalkNotifySender(NotifySenderPlugin):
    """钉钉交互卡片通道 —— 装饰一个 ``inner`` 通道再额外投递一张钉钉交互卡片。

    设计要点：
      - 这是 ``NotifySenderPlugin`` 的一个通道实现。``DiscoveryService`` 仍只依赖
        ``notify_sender``，由 DI 在钉钉凭证就绪时绑定本类（凭证缺失则回退到纯
        ``CommunityNotifySender``）—— 不把钉钉 SDK 漏进 ``DiscoveryService``。
      - 复用 e2e 既有的钉钉凭证 env（``TASK_DISCOVERY_DINGTALK_*``，回退
        ``SINGLEBOX_DINGTALK_*``）；``card_template_id`` 复用
        ``TASK_DISCOVERY_CARD_TEMPLATE_ID``（回退 ``SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID``）。
      - corp 钉钉 SDK ``alipay_antdingopensdk_client`` **惰性导入**（在 ``send`` 内），
        未配置凭证时根本不导入；满足 ``send()`` 永不抛异常的 Protocol 约定。
      - 卡片载荷来自 ``NotifyMessage.extra``（``card_template_id`` / ``card_biz_id`` /
        ``card_data`` / ``session_url``），由 ``DiscoveryService._send_notification`` 填好。
    """

    def __init__(self, inner: NotifySenderPlugin) -> None:
        self._inner = inner

    @property
    def channels(self) -> frozenset[str]:
        return self._inner.channels

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        # 先照常走 inner 通道（日志/兜底），再额外投递钉钉卡片。两者同时进行。
        msg_id = self._inner.send(message, channel=channel)
        try:
            self._send_dingtalk_card(message)
        except Exception as exc:  # 永不抛异常 —— 钉钉失败不影响通知主流程
            log.warning(
                "[DingTalkNotifySender] dingtalk card failed "
                "(recipient=%s): %s",
                message.recipient,
                exc,
            )
        return msg_id

    @staticmethod
    def _configured() -> bool:
        return all(
            [
                _env("TASK_DISCOVERY_DINGTALK_AK_ID", "SINGLEBOX_DINGTALK_AK_ID"),
                _env(
                    "TASK_DISCOVERY_DINGTALK_AK_SECRET",
                    "SINGLEBOX_DINGTALK_AK_SECRET",
                ),
                _env(
                    "TASK_DISCOVERY_DINGTALK_ROBOT_CODE",
                    "SINGLEBOX_DINGTALK_ROBOT_CODE",
                ),
                _env(
                    "TASK_DISCOVERY_CARD_TEMPLATE_ID",
                    "SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID",
                ),
            ]
        )

    def _send_dingtalk_card(self, message: NotifyMessage) -> None:
        if not self._configured():
            return  # 未配置凭证 → 跳过，仅 inner 通道（ CommunityNotifySender 日志）
        ak_id = _env("TASK_DISCOVERY_DINGTALK_AK_ID", "SINGLEBOX_DINGTALK_AK_ID")
        ak_secret = _env(
            "TASK_DISCOVERY_DINGTALK_AK_SECRET", "SINGLEBOX_DINGTALK_AK_SECRET"
        )
        robot_code = _env(
            "TASK_DISCOVERY_DINGTALK_ROBOT_CODE", "SINGLEBOX_DINGTALK_ROBOT_CODE"
        )
        template_id = _env(
            "TASK_DISCOVERY_CARD_TEMPLATE_ID", "SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID"
        )
        extra = message.extra or {}
        # account_id 默认用 recipient（owner），可用 env 单独覆盖
        account_id = _env(
            "TASK_DISCOVERY_DINGTALK_ACCOUNT_ID", "SINGLEBOX_DINGTALK_ACCOUNT_ID"
        ) or message.recipient
        card_biz_id = extra.get("card_biz_id") or f"discover_things_{int(time.time())}"
        # card_data 由 _send_notification 填好（已含 click/session_url/workitem_*…）
        card_data = extra.get("card_data")
        if not card_data:
            return

        # 惰性导入 corp 钉钉 SDK
        from alibabacloud_tea_openapi import models as open_api_models  # type: ignore[import-not-found]
        from alibabacloud_tea_util import models as util_models  # type: ignore[import-not-found]
        from alipay_antdingopensdk_client import (  # type: ignore[import-not-found]
            models as antding_models,
        )
        from alipay_antdingopensdk_client.client import (  # type: ignore[import-not-found]
            Client as AntDingClient,
        )

        config = open_api_models.Config()
        config.access_key_id = ak_id
        config.access_key_secret = ak_secret
        client = AntDingClient(config)
        headers = antding_models.HttpHeader()
        headers.account_context = antding_models.AccountContext(account_id=account_id)
        req = antding_models.SendRobotInteractiveCardRequest()
        req.card_template_id = template_id
        req.robot_code = robot_code
        req.card_biz_id = card_biz_id
        req.card_data = card_data
        req.user_id = account_id
        resp = client.send_robot_interactive_card_with_options(
            req, headers, util_models.RuntimeOptions()
        )
        biz = resp.body
        resp_map = biz.to_map() if hasattr(biz, "to_map") else {"raw": str(biz)}
        log.info(
            "[DingTalkNotifySender] card sent (recipient=%s, template=%s, "
            "robot=%s, card_biz_id=%s) -> %s",
            account_id,
            template_id,
            robot_code,
            card_biz_id,
            json.dumps(resp_map, ensure_ascii=False),
        )