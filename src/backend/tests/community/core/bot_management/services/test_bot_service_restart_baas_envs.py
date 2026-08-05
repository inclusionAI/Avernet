"""单元测试：BaaS 原地重启（``_restart_bot_baas``）透传 extra_envs / template_config。

覆盖本分支修复点：applicationCoding / personalCoding + (claude_code | aicoding) 引擎
门控命中时，``upgrade_bot`` 必须收到带 ``BOT_TYPE`` / ``RELAY_DEFAULT_MODEL`` /
``RELAY_DEFAULT_RUNTIME`` 的 ``extra_envs``；无论该门控是否命中，
``template_config``（含 template_uid 上下文）都必须透传，确保 envs/image/resource_spec
沙箱覆写在重启时不丢失。

门控口径与 ``_allocate_device_async``（create / arca-restart）严格对齐：
- ``active_engine ∈ {claude_code, aicoding}``
- ``template_type ∈ {applicationCoding, personalCoding}``
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
    BAAS_RESTART_PUBLISH_POLL_TASK,
)


# ---------------------------------------------------------------------------
# 脚手架（与 test_bot_service_restart_idempotency 同款 _make_service 风格）
# ---------------------------------------------------------------------------


class _FakeRestartLockRepo:
    """In-memory restart-lock repo honoring UNIQUE(env, entity_id, bot_id)."""

    def __init__(self) -> None:
        self.rows: dict[tuple, SimpleNamespace] = {}

    def acquire(self, env, entity_id, bot_id, holder_user_id):
        key = (env, entity_id, bot_id)
        if key in self.rows:
            return None
        rec = SimpleNamespace(lock_token="tok-1")
        self.rows[key] = rec
        return rec

    def release(self, env, entity_id, bot_id, lock_token):
        self.rows.pop((env, entity_id, bot_id), None)
        return True


def _make_service(
    *,
    bot_template_type: str | None = "applicationCoding",
    active_engine: str = "aicoding",
    template_config: dict | None = None,
    bot_type: str = "personal",
) -> tuple[BotService, MagicMock, MagicMock]:
    """造一个直接调 ``_restart_bot_baas`` 的 BotService。

    返回 (svc, baas, device_service) 三个 mock，便于断言 upgrade_bot 入参。
    """
    svc = BotService.__new__(BotService)
    svc._restart_lock_repo = _FakeRestartLockRepo()
    svc._repository = MagicMock()
    svc._repository.get_by_id_and_owner.return_value = {
        "bot_id": "bot001",
        "owner_id": "user001",
        "status": "ACTIVE",
    }
    svc._bot_publish_provider = lambda: MagicMock()
    svc._teclaw_provision_provider = lambda: SimpleNamespace(
        is_teclaw=lambda engine: engine == "teclaw"
    )
    svc._oss_record_repo = MagicMock()
    svc._skill_set_factory = MagicMock()
    svc._device_binding_repo = MagicMock()
    svc._bot_publish_repo = MagicMock()
    # template_service.get_template_config 返回读回的 template_config（含 model/runtime/token）
    svc._template_service = MagicMock()
    svc._template_service.get_template_config.return_value = template_config

    # device_service.get_device 返回 binding，device_id == bot_uuid
    device_service = MagicMock()
    device_service.get_device.return_value = SimpleNamespace(
        id=42, device_provider="baas", device_id="BOT-uuid-9",
    )
    svc._device_service_provider = lambda: device_service

    baas = MagicMock()
    baas.upgrade_bot.return_value = {"publish_id": 100}
    svc._baas_service_provider = lambda: baas

    # _attach_template_uid_context 走真实方法会访问 _baas_template_resolver；
    # 这里只测透传，patch 成透传器即可。
    svc._baas_template_resolver = None
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None
    svc._bcn_service = MagicMock()

    svc._task_queue_service = MagicMock()
    return svc, baas, device_service


def _make_bot(
    *,
    bot_type: str = "personal",
    active_engine: str = "aicoding",
    template_type: str = "applicationCoding",
) -> dict:
    return {
        "bot_id": "bot001",
        "owner_id": "user001",
        "status": "ACTIVE",
        "entity_id": "staff_user001",
        "entity_type": "staff",
        "bot_type": bot_type,
        "active_engine": active_engine,
        "template_type": template_type,
        "bot_name": "TestBot",
        "binding_id": 42,
    }


# ===========================================================================
# extra_envs / template_config 透传
# ===========================================================================


class TestRestartBaasImagePolicy:
    def test_low_level_restart_uses_explicit_pin_without_mutating_template_snapshot(self):
        template_config = {"image": "registry/arka:v1", "envs": {"A": "1"}}
        svc, baas, _ = _make_service(
            template_config=template_config,
            bot_type="service",
            active_engine="openclaw",
        )
        bot = {
            **_make_bot(bot_type="service", active_engine="openclaw"),
            "ext": {
                "sbot_pin_image": True,
                "sbot_docker_image": "registry/arka:v2",
            },
        }

        svc._restart_bot_baas(
            bot_id="bot001", user_id="user001", binding_id=42, bot=bot
        )

        assert baas.upgrade_bot.call_args.kwargs["template_config"] == {
            "image": "registry/arka:v2",
            "envs": {"A": "1"},
        }
        assert template_config["image"] == "registry/arka:v1"

    def test_restart_failure_does_not_persist_default_marker(self):
        svc, baas, _ = _make_service(
            template_config={},
            bot_type="service",
            active_engine="openclaw",
        )
        baas.upgrade_bot.side_effect = RuntimeError("baas rejected")
        bot = {
            **_make_bot(bot_type="service", active_engine="openclaw"),
            "ext": {
                "sbot_pin_image": True,
                "sbot_docker_image": "registry/arka:v2",
            },
        }

        with pytest.raises(RuntimeError, match="baas rejected"):
            svc._restart_bot_baas(
                bot_id="bot001", user_id="user001", binding_id=42, bot=bot
            )

        svc._repository.update_by_owner.assert_not_called()
        svc._bot_publish_repo.update_status_with_ext.assert_not_called()

    @pytest.mark.parametrize(
        ("bot_type", "active_engine"),
        [("personal", "openclaw"), ("service", "teclaw")],
    )
    def test_default_persistence_skips_non_arka_service_bot(
        self,
        bot_type: str,
        active_engine: str,
    ):
        svc, _, _ = _make_service(
            template_config={},
            bot_type=bot_type,
            active_engine=active_engine,
        )
        bot = {
            **_make_bot(bot_type=bot_type, active_engine=active_engine),
            "ext": {
                "sbot_pin_image": True,
                "sbot_docker_image": "registry/arka:v2",
            },
        }

        svc._persist_service_bot_default_image(bot, user_id="user001")

        svc._repository.update_by_owner.assert_not_called()
        svc._bot_publish_repo.get_draft_by_publish_bot_id.assert_not_called()
        svc._bot_publish_repo.update_status_with_ext.assert_not_called()

    def test_restart_submit_defers_default_marker_until_poll_success(self):
        svc, _, _ = _make_service(
            template_config={},
            bot_type="service",
            active_engine="openclaw",
        )
        bot = {
            **_make_bot(bot_type="service", active_engine="openclaw"),
            "ext": {
                "service_bot_config": {"device_count": 1},
                "sbot_use_default_image": True,
            },
        }

        svc._restart_bot_baas(
            bot_id="bot001", user_id="user001", binding_id=42, bot=bot
        )

        svc._bot_publish_repo.update_status_with_ext.assert_not_called()
        payload = svc._task_queue_service.enqueue.call_args.args[1]
        assert payload["image_policy_on_success"] == "default"
        pending_update = svc._repository.update_by_owner.call_args.args[2]
        assert "sbot_use_default_image" not in pending_update.get("ext", {})


class TestRestartBaasEnvInjection:
    def test_restart_records_current_publish_id_and_clears_stale_baas_failure(self):
        """BaaS 原地重启进入新 publish 轮次时，持久化当前 publish_id，
        并清理上一轮 BaaS publish 失败 marker。"""
        svc, baas, _ = _make_service(template_config={})
        baas.upgrade_bot.return_value = {"publish_id": 12372}
        bot = {
            **_make_bot(active_engine="openclaw", template_type=None),
            "ext": {
                "start_status": "FAILED",
                "start_message": "BaaS publish FAILED: publish_id=10377",
                "service_bot_config": {"device_count": 1},
            },
        }
        svc._repository.get_by_id_and_owner.return_value = bot

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        svc._device_binding_repo.update_device_props.assert_called_once_with(
            binding_id=42,
            props={
                "publish_id": "12372",
                "restart_publish_id": "12372",
                "restart_request_id": baas.upgrade_bot.call_args.kwargs["request_id"],
            },
        )
        svc._repository.update_by_owner.assert_called_with(
            "bot001",
            "user001",
            {
                "status": "PENDING",
                "ext": {
                    "service_bot_config": {"device_count": 1},
                    "restart_publish_id": "12372",
                },
            },
        )
        svc._task_queue_service.enqueue.assert_called_once()
        enqueue_args = svc._task_queue_service.enqueue.call_args
        assert enqueue_args.args[0] == BAAS_RESTART_PUBLISH_POLL_TASK
        assert enqueue_args.kwargs["deadline_seconds"] == 86400

    def test_restart_baas_records_restart_publish_id_and_enqueues_task(self):
        svc, baas, device_service = _make_service(template_config={})
        baas.upgrade_bot.return_value = {"publish_id": 12372}
        bot = _make_bot(active_engine="openclaw", template_type=None)

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        request_id = baas.upgrade_bot.call_args.kwargs["request_id"]
        svc._device_binding_repo.update_device_props.assert_called_once_with(
            binding_id=42,
            props={
                "publish_id": "12372",
                "restart_publish_id": "12372",
                "restart_request_id": request_id,
            },
        )
        started_at_epoch_s = svc._task_queue_service.enqueue.call_args.args[1][
            "started_at_epoch_s"
        ]
        svc._task_queue_service.enqueue.assert_called_once_with(
            BAAS_RESTART_PUBLISH_POLL_TASK,
            {
                "binding_id": 42,
                "bot_id": "bot001",
                "owner_id": "user001",
                "publish_id": 12372,
                "started_at_epoch_s": started_at_epoch_s,
                "bot_uuid": "BOT-uuid-9",
            },
            deadline_seconds=86400,
        )

    def test_restart_baas_marks_pending_before_enqueue(self):
        svc, baas, device_service = _make_service(template_config={})
        order: list[str] = []
        svc._repository.update_by_owner.side_effect = lambda *args, **kwargs: order.append("bot_pending")
        svc._device_binding_repo.update_status.side_effect = lambda *args, **kwargs: order.append("binding_pending")
        svc._task_queue_service.enqueue.side_effect = lambda *args, **kwargs: order.append("enqueue")
        bot = _make_bot(active_engine="openclaw", template_type=None)

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        assert order.index("bot_pending") < order.index("enqueue")
        assert order.index("binding_pending") < order.index("enqueue")

    def test_application_coding_injects_bot_type_model_runtime(self):
        """applicationCoding + aicoding → upgrade_bot 收到 extra_envs 含
        BOT_TYPE=application / RELAY_DEFAULT_MODEL / RELAY_DEFAULT_RUNTIME。"""
        svc, baas, _ = _make_service(
            template_config={
                "model": "antchat/Ling-2.6-1T",
                "runtime": "python",
            },
        )
        bot = _make_bot(active_engine="aicoding", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        baas.upgrade_bot.assert_called_once()
        kwargs = baas.upgrade_bot.call_args.kwargs
        envs = kwargs["extra_envs"]
        assert envs is not None
        assert envs["BOT_TYPE"] == "application"
        assert envs["RELAY_DEFAULT_MODEL"] == "antchat/Ling-2.6-1T"
        assert envs["RELAY_DEFAULT_RUNTIME"] == "python"
        # template_config 透传到 BaaS device 层（供 SandboxOverrides 覆写镜像/规格）
        assert kwargs["template_config"] is not None

    def test_personal_coding_injects_bot_type_personal_and_relay_defaults(self):
        """personalCoding + claude_code → BOT_TYPE=personal，并写 RELAY_DEFAULT_*。"""
        svc, baas, _ = _make_service(
            active_engine="claude_code",
            template_config={"model": "m1", "runtime": "py"},
        )
        bot = _make_bot(active_engine="claude_code", template_type="personalCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        kwargs = baas.upgrade_bot.call_args.kwargs
        envs = kwargs["extra_envs"]
        assert envs is not None
        assert envs["BOT_TYPE"] == "personal"
        assert envs["RELAY_DEFAULT_MODEL"] == "m1"
        assert envs["RELAY_DEFAULT_RUNTIME"] == "py"

    def test_claude_code_engine_injects_envs(self):
        """claude_code 引擎同样命中门控（与 aicoding 等价）。"""
        svc, baas, _ = _make_service(
            active_engine="claude_code",
            template_config={"model": "m-claude", "runtime": "node"},
        )
        bot = _make_bot(active_engine="claude_code", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        envs = baas.upgrade_bot.call_args.kwargs["extra_envs"]
        assert envs["BOT_TYPE"] == "application"
        assert envs["RELAY_DEFAULT_MODEL"] == "m-claude"
        assert envs["RELAY_DEFAULT_RUNTIME"] == "node"


    def test_claude_code_other_template_type_consumes_template_config(self):
        """claude_code + 其它 template_type 也要消费 template_config。

        这类模板可能不是 applicationCoding/personalCoding，但必须带
        template_key/template_uid 模板身份；重启时应把 model/runtime 注入
        extra_envs，并继续透传 template_config 给 BaaS 沙箱覆写。
        """
        template_config = {
            "template_key": "customCC",
            "template_uid": "tpl-custom-cc",
            "model": "  antchat/custom  ",
            "runtime": "  codefuse-antcc  ",
            "envs": {"FOO": "bar"},
            "image": "registry.example.com/custom:latest",
            "resource_spec": {"cpu": 4, "memory": 8192},
        }
        svc, baas, _ = _make_service(
            active_engine="claude_code",
            template_config=template_config,
        )
        bot = _make_bot(active_engine="claude_code", template_type="customCC")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        kwargs = baas.upgrade_bot.call_args.kwargs
        envs = kwargs["extra_envs"]
        assert envs is not None
        assert envs["BOT_TYPE"] == "customCC"
        assert envs["RELAY_DEFAULT_MODEL"] == "antchat/custom"
        assert envs["RELAY_DEFAULT_RUNTIME"] == "codefuse-antcc"
        assert kwargs["template_config"] == template_config

    def test_claude_code_other_template_type_without_template_identity_does_not_consume_envs(self):
        """claude_code + 其它 template_type 缺 template_key/template_uid 时不消费 env 策略。"""
        template_config = {
            "model": "antchat/custom",
            "runtime": "codefuse-antcc",
            "envs": {"FOO": "bar"},
        }
        svc, baas, _ = _make_service(
            active_engine="claude_code",
            template_config=template_config,
        )
        bot = _make_bot(active_engine="claude_code", template_type="customCC")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        kwargs = baas.upgrade_bot.call_args.kwargs
        assert kwargs["extra_envs"] is None
        # 沙箱覆写仍按重启链路透传；只是引擎策略不消费 model/runtime/token。
        assert kwargs["template_config"] == template_config

    def test_non_coding_engine_keeps_template_config_overrides(self):
        """非 claude_code/aicoding 引擎门控不命中 → extra_envs=None，
        但 template_config.envs/image/resource_spec 仍要透传给 BaaS /update。"""
        sandbox_overrides = {
            "model": "m1",  # 不应被 extra_envs 消费
            "envs": {"FOO": "bar"},
            "image": "registry.example.com/custom:latest",
            "resource_spec": {"cpu": 4, "memory": 8},
        }
        svc, baas, _ = _make_service(
            active_engine="moltis",
            template_config=sandbox_overrides,
        )
        bot = _make_bot(active_engine="moltis", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        kwargs = baas.upgrade_bot.call_args.kwargs
        assert kwargs["extra_envs"] is None
        assert kwargs["template_config"] == sandbox_overrides

    def test_template_uid_context_failure_keeps_template_config_overrides(self):
        """template_uid 上下文解析异常不应阻断 BaaS 重启或丢失沙箱覆写。"""
        sandbox_overrides = {
            "envs": {"FOO": "bar"},
            "image": "registry.example.com/custom:latest",
            "resource_spec": {"cpu": 4, "memory": 8},
        }
        svc, baas, _ = _make_service(
            active_engine="moltis",
            template_config=sandbox_overrides,
        )
        svc._attach_template_uid_context = MagicMock(side_effect=RuntimeError("resolver down"))
        bot = _make_bot(active_engine="moltis", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        kwargs = baas.upgrade_bot.call_args.kwargs
        assert kwargs["extra_envs"] is None
        assert kwargs["template_config"] == sandbox_overrides

    def test_non_coding_template_type_no_envs(self):
        """非 applicationCoding/personalCoding 的 template_type 门控不命中。"""
        svc, baas, _ = _make_service(
            active_engine="aicoding",
            template_config={"model": "m1"},
        )
        bot = _make_bot(active_engine="aicoding", template_type="other")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        kwargs = baas.upgrade_bot.call_args.kwargs
        assert kwargs["extra_envs"] is None
        assert kwargs["template_config"] == {"model": "m1"}

    def test_missing_model_runtime_still_injects_bot_type(self):
        """applicationCoding + 无 model/runtime → 仍注入 BOT_TYPE，不写 RELAY_DEFAULT_*。"""
        svc, baas, _ = _make_service(
            template_config={},
        )
        bot = _make_bot(active_engine="aicoding", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        envs = baas.upgrade_bot.call_args.kwargs["extra_envs"]
        assert envs == {"BOT_TYPE": "application"}

    def test_get_template_config_failure_does_not_block_restart(self):
        """template_service.get_template_config 抛异常 → 仅 warning，不阻断重启，
        extra_envs 仍至少包含 BOT_TYPE（template_config=None 时 build_aix_extra_envs
        只产 BOT_TYPE）。"""
        svc, baas, _ = _make_service(template_config={})
        svc._template_service.get_template_config.side_effect = RuntimeError("db down")
        bot = _make_bot(active_engine="aicoding", template_type="applicationCoding")

        # 不应抛出
        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        baas.upgrade_bot.assert_called_once()
        envs = baas.upgrade_bot.call_args.kwargs["extra_envs"]
        # template_config=None → build_aix_extra_envs 仍返回 {"BOT_TYPE": "application"}
        assert envs == {"BOT_TYPE": "application"}


# ===========================================================================
# restart queue payload 不透传 codefuse token；handler 成功后会自行重读 template_config
# ===========================================================================


class TestRestartBaasCodefuseTokenPassthrough:
    def test_application_coding_queue_payload_does_not_include_token(self):
        """applicationCoding + 有 token → queue payload 仍不带 token，只带 bot_uuid。"""
        svc, _, device_service = _make_service(
            template_config={"token": "enc:v1:ciphertext", "model": "m1"},
        )
        bot = _make_bot(active_engine="aicoding", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        payload = svc._task_queue_service.enqueue.call_args.args[1]
        assert payload["bot_uuid"] == "BOT-uuid-9"
        assert "codefuse_token" not in payload
        assert "token" not in payload

    def test_personal_coding_queue_payload_does_not_include_token(self):
        """personalCoding 同样不在 queue payload 中传 token。"""
        svc, _, device_service = _make_service(
            template_config={"token": "enc:v1:cipher", "model": "m1"},
        )
        bot = _make_bot(active_engine="aicoding", template_type="personalCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        payload = svc._task_queue_service.enqueue.call_args.args[1]
        assert "codefuse_token" not in payload

    def test_application_coding_without_token_still_enqueues_without_token_field(self):
        """applicationCoding 但无 token → payload 不带 token 字段，bot_uuid 仍保留。"""
        svc, _, device_service = _make_service(template_config={})
        bot = _make_bot(active_engine="aicoding", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        payload = svc._task_queue_service.enqueue.call_args.args[1]
        assert "codefuse_token" not in payload
        assert payload["bot_uuid"] == "BOT-uuid-9"

    def test_non_coding_engine_queue_payload_does_not_include_token(self):
        """非 coding 引擎门控不命中 → payload 不带 token。"""
        svc, _, device_service = _make_service(
            active_engine="moltis",
            template_config={"token": "enc:v1:cipher"},
        )
        bot = _make_bot(active_engine="moltis", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        payload = svc._task_queue_service.enqueue.call_args.args[1]
        assert "codefuse_token" not in payload

    def test_get_template_config_failure_does_not_block_restart_enqueue(self):
        """template_service.get_template_config 抛异常 → 不阻断 restart，payload 仍不带 token。"""
        svc, _, device_service = _make_service(template_config={"token": "enc:v1:cipher"})
        svc._template_service.get_template_config.side_effect = RuntimeError("db down")
        bot = _make_bot(active_engine="aicoding", template_type="applicationCoding")

        svc._restart_bot_baas(bot_id="bot001", user_id="user001", binding_id=42, bot=bot)

        payload = svc._task_queue_service.enqueue.call_args.args[1]
        assert "codefuse_token" not in payload
