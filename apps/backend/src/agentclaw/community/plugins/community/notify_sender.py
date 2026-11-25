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
from collections.abc import Callable
from dataclasses import dataclass

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

log = get_logger()


class DingTalkCredentialHolder:
    """运行时钉钉凭证 holder — 允许通过 API 动态注入凭证，无需重启 backend。

    优先级：API holder > YAML holder > env > 空（skip）。
    """

    _creds: dict[str, str] = {}

    @classmethod
    def set(
        cls, ak_id: str, ak_secret: str, robot_code: str, card_template_id: str
    ) -> None:
        cls._creds = {
            "ak_id": ak_id,
            "ak_secret": ak_secret,
            "robot_code": robot_code,
            "card_template_id": card_template_id,
        }
        log.info("[DingTalkCredentialHolder] credentials injected at runtime")

    @classmethod
    def get(cls, key: str) -> str:
        return cls._creds.get(key, "")

    @classmethod
    def clear(cls) -> None:
        cls._creds = {}


class DingTalkYamlHolder:
    """YAML 配置钉钉凭证 holder — 由 DI 模块在启动时从 application-*.yaml 的
    ``task_discovery_dingtalk`` 块读取并注入。

    优先级低于 API holder（运行时注入可覆盖 YAML 配置），高于 env 变量。
    """

    _creds: dict[str, str] = {}

    @classmethod
    def set(cls, creds: dict[str, str]) -> None:
        cls._creds = {k: v for k, v in creds.items() if v}
        if cls._creds:
            log.info(
                "[DingTalkYamlHolder] credentials loaded from YAML (keys=%s)",
                sorted(cls._creds.keys()),
            )

    @classmethod
    def get(cls, key: str) -> str:
        return cls._creds.get(key, "")

    @classmethod
    def clear(cls) -> None:
        cls._creds = {}


def _env(name: str, fallback: str = "") -> str:
    """Read a deployment credential from its configured environment name."""
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


@dataclass(frozen=True)
class DingTalkSenderConfig:
    """Instance-owned DingTalk account configuration.

    A callback holds the secret credential so no member of this dataclass ever
    stores a literal secret. Each configured sender owns its account and is
    isolated from the task-discovery global holder/environment keys.
    """

    app_key: str
    read_app_secret: Callable[[], str]
    robot_code: str
    card_template_id: str


class DingTalkNotifySender(NotifySenderPlugin):
    """钉钉交互卡片通道 —— 装饰一个 ``inner`` 通道再额外投递一张钉钉交互卡片。

    设计要点：
      - 这是 ``NotifySenderPlugin`` 的一个通道实现。``DiscoveryService`` 依赖
        ``NotifyMessagesProvider`` 端口(DI alias 到本实例),由 DI 在钉钉凭证就绪时
        绑定本类（凭证缺失则回退到纯 ``CommunityNotifySender``）—— 不把钉钉 SDK
        漏进 ``DiscoveryService``。
      - 默认凭证来自部署配置（``TASK_DISCOVERY_DINGTALK_*`` 与
        ``TASK_DISCOVERY_CARD_TEMPLATE_ID``）；传入 ``DingTalkSenderConfig``
        时改用实例级配置。``strict_card=True`` 只接受真实卡片结果。
      - corp 钉钉 SDK ``alipay_antdingopensdk_client`` **惰性导入**（在 ``send`` 内），
        未配置凭证时根本不导入；满足 ``send()`` 永不抛异常的 Protocol 约定。
      - 卡片载荷来自 ``NotifyMessage.extra``（``card_template_id`` / ``card_biz_id`` /
        ``card_data`` / ``session_url``），由 ``DiscoveryService._send_notification`` 填好。
    """

    def __init__(
        self,
        inner: NotifySenderPlugin,
        *,
        config: DingTalkSenderConfig | None = None,
        strict_card: bool = False,
    ) -> None:
        if strict_card and config is None:
            raise ValueError("strict_card requires DingTalkSenderConfig")

        self._inner = inner
        self._config = config
        self._strict_card = strict_card

    @property
    def channels(self) -> frozenset[str]:
        if self._strict_card:
            return frozenset({"tc_card"})
        return self._inner.channels

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        log.info(
            "[task_discovery] → DingTalkNotifySender.send(title=%r, recipient=%s, channel=%s)",
            message.title[:80],
            message.recipient,
            channel,
        )
        if self._strict_card:
            if channel != "tc_card":
                log.warning(
                    "[DingTalkNotifySender] strict-card sender rejects channel=%s; "
                    "supported=tc_card",
                    channel,
                )
                return None
            try:
                return self._send_dingtalk_card(message)
            except Exception as exc:  # 永不抛异常 —— strict card 结果就是发送结果。
                log.warning(
                    "[DingTalkNotifySender] strict DingTalk card failed "
                    "(recipient=%s, title=%r): %s",
                    message.recipient,
                    message.title[:80],
                    exc,
                )
                return None

        # 先照常走 inner 通道（日志/兜底），再额外投递钉钉卡片。两者同时进行。
        msg_id = self._inner.send(message, channel=channel)
        try:
            self._send_dingtalk_card(message)
        except Exception as exc:  # 永不抛异常 —— 钉钉失败不影响通知主流程
            extra_keys = sorted(message.extra.keys()) if message.extra else []
            log.warning(
                "[DingTalkNotifySender] dingtalk card failed "
                "(recipient=%s, title=%r, extra keys=%s): %s",
                message.recipient,
                message.title[:80],
                extra_keys,
                exc,
                exc_info=True,
            )
        return msg_id

    def _resolve(self, key: str, env_name: str, env_fallback: str) -> str:
        """优先级：实例 config 或 API holder > YAML holder > env。"""
        if self._config is not None:
            if key == "ak_secret":
                return self._config.read_app_secret()
            if key == "ak_id":
                return self._config.app_key
            return str(getattr(self._config, key, "") or "")

        log.info("[task_discovery] → DingTalkNotifySender._resolve(key=%s)", key)
        val = DingTalkCredentialHolder.get(key)
        if val:
            return val
        val = DingTalkYamlHolder.get(key)
        if val:
            return val
        return _env(env_name, env_fallback)

    #: 凭证 key → (env_name, env_fallback) 映射，用于 _configured 诊断日志
    _CRED_KEYS: list[tuple[str, str, str]] = [
        ("ak_id", "TASK_DISCOVERY_DINGTALK_AK_ID", ""),
        ("ak_secret", "TASK_DISCOVERY_DINGTALK_AK_SECRET", ""),
        ("robot_code", "TASK_DISCOVERY_DINGTALK_ROBOT_CODE", ""),
        ("card_template_id", "TASK_DISCOVERY_CARD_TEMPLATE_ID", ""),
    ]

    def _resolve_source(self, key: str) -> str:
        """Return the source name that resolved the credential (api/yaml/env/empty)."""
        if self._config is not None:
            return "sender_config"

        val = DingTalkCredentialHolder.get(key)
        if val:
            return "api"
        val = DingTalkYamlHolder.get(key)
        if val:
            return "yaml"
        return "env" if _env(*self._CRED_KEYS_MAP[key]) else "empty"

    _CRED_KEYS_MAP: dict[str, tuple[str, str]] = {
        k: (e1, e2) for k, e1, e2 in _CRED_KEYS
    }

    def _configured(self) -> bool:
        return all(self._resolve(k, e1, e2) for k, e1, e2 in self._CRED_KEYS)

    def _recipient_account_id(self, message: NotifyMessage) -> str:
        """Resolve the DingTalk recipient account for the active sender mode."""
        if self._config is not None:
            return message.recipient
        return _env("TASK_DISCOVERY_DINGTALK_ACCOUNT_ID") or message.recipient

    def _send_dingtalk_card(self, message: NotifyMessage) -> str | None:
        """Send one interactive card and return its business ID on success."""
        log.info("[task_discovery] → DingTalkNotifySender._send_dingtalk_card()")
        if not self._configured():
            # 诊断：逐个检出哪个凭证缺失及其来源
            missing: list[str] = []
            for key, env_name, env_fallback in self._CRED_KEYS:
                src = self._resolve_source(key)
                if src == "empty":
                    missing.append(
                        f"{key} (api={bool(DingTalkCredentialHolder.get(key))}, "
                        f"yaml={bool(DingTalkYamlHolder.get(key))}, "
                        f"env={env_name}/{env_fallback})"
                    )
            log.warning(
                "[DingTalkNotifySender] dingtalk card SKIPPED — "
                "credentials not configured. Missing: %s. "
                "Sources checked: API holder=%s, YAML holder=%s, env vars. "
                "Recipient=%s, task title=%r",
                ", ".join(missing) if missing else "(none?)",
                bool(DingTalkCredentialHolder._creds),
                bool(DingTalkYamlHolder._creds),
                message.recipient,
                message.title[:80],
            )
            return None
        ak_id = self._resolve("ak_id", "TASK_DISCOVERY_DINGTALK_AK_ID", "")
        ak_secret = self._resolve("ak_secret", "TASK_DISCOVERY_DINGTALK_AK_SECRET", "")
        robot_code = self._resolve(
            "robot_code", "TASK_DISCOVERY_DINGTALK_ROBOT_CODE", ""
        )
        template_id = self._resolve(
            "card_template_id", "TASK_DISCOVERY_CARD_TEMPLATE_ID", ""
        )
        extra = message.extra or {}
        account_id = self._recipient_account_id(message)
        card_biz_id = extra.get("card_biz_id") or f"discover_things_{int(time.time())}"
        # card_data 由 _send_notification 填好（已含 click/session_url/workitem_*…）
        card_data = extra.get("card_data")
        if not card_data:
            log.warning(
                "[DingTalkNotifySender] dingtalk card SKIPPED — "
                "card_data missing from message.extra "
                "(recipient=%s, title=%r, extra keys=%s)",
                message.recipient,
                message.title[:80],
                sorted(extra.keys()) if extra else "[]",
            )
            return None

        # 惰性导入 corp 钉钉 SDK
        log.info(
            "[DingTalkNotifySender] sending card (recipient=%s, template=%s, "
            "robot=%s, card_biz_id=%s, account_id=%s, "
            "card_data_len=%d, credential_sources: ak_id=%s, ak_secret=%s, "
            "robot_code=%s, template_id=%s)",
            account_id,
            template_id,
            robot_code,
            card_biz_id,
            account_id,
            len(card_data),
            self._resolve_source("ak_id"),
            self._resolve_source("ak_secret"),
            self._resolve_source("robot_code"),
            self._resolve_source("card_template_id"),
        )
        from alibabacloud_tea_openapi import models as open_api_models  # type: ignore[import-not-found]
        from alibabacloud_tea_util import models as util_models  # type: ignore[import-not-found]
        from alipay_antdingopensdk_client import (  # type: ignore[import-not-found]
            models as antding_models,
        )
        from alipay_antdingopensdk_client.client import (  # type: ignore[import-not-found]
            Client as AntDingClient,
        )

        # alibabacloud-tea 包不提供 Tea.__version__，但 UtilClient 拼 User-Agent 时读它
        import Tea  # type: ignore[import-not-found]

        if not hasattr(Tea, "__version__"):
            Tea.__version__ = "0.4.3"

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
        # 检查钉钉返回码（ret_code 非 0 表示业务失败）
        ret_code = resp_map.get("retCode") or resp_map.get("ret_code")
        if ret_code and str(ret_code) != "0":
            log.warning(
                "[DingTalkNotifySender] card sent but BUSINESS FAILURE "
                "(ret_code=%s, recipient=%s, template=%s, robot=%s, "
                "card_biz_id=%s) -> %s",
                ret_code,
                account_id,
                template_id,
                robot_code,
                card_biz_id,
                json.dumps(resp_map, ensure_ascii=False),
            )
            return None
        log.info(
            "[DingTalkNotifySender] card sent OK (recipient=%s, template=%s, "
            "robot=%s, card_biz_id=%s) -> %s",
            account_id,
            template_id,
            robot_code,
            card_biz_id,
            json.dumps(resp_map, ensure_ascii=False),
        )
        return str(card_biz_id)
