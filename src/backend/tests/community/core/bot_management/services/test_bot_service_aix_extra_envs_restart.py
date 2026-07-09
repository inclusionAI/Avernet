"""Unit tests for AIX coding bot restart 注入 extra_envs 的逻辑。

回归 9f37c43c1：``_allocate_device_async`` 在 restart AIX 编码 bot 时必须
通过 ``TemplateService.get_template_config(bot_id)`` 获取模板配置，而不是
``get_template().get("template_config")`` —— 后者返回的 TemplateModel 字典
里根本没有 ``template_config`` 字段（实际存在 ``ext`` 里），会让
``build_aix_extra_envs`` 拿到 ``None``，导致 ``GIT_ADDRESSES`` /
``AIX_DEVFLOW_INFO`` 缺失，restart 注入到 Arca 容器的环境变量与 create 不一致。

进一步回归 b2531b570：``_allocate_device_async`` 在所有 restart / start-bot
路径都必须通过 ``get_template_config`` 读取 template_config 并透传给
``apply_device``，而不是从 ``bot_record.get("template_config")`` 读取（ac_bots
表没有 template_config 列，值存在 ac_templates.ext 里）。否则沙箱覆写参数
（image / command / envs / resource_spec）在重启时全部丢失。

这些测试覆盖：
- applicationCoding restart 时 ``apply_device`` 收到的 ``extra_envs``
  同时带 ``BOT_TYPE`` / ``AIX_DEVFLOW_INFO`` / ``GIT_ADDRESSES``。
- personalCoding restart 时退化为只带 ``BOT_TYPE``。
- 非 AIX 模板（如 ``personal``）restart 时 ``extra_envs=None``，但
  ``get_template_config`` 仍被调用以读取沙箱覆写参数。
- 非 ``claude_code`` 引擎即使 template_type=applicationCoding 也不注入 extra_envs。
- ``get_template_config`` 抛错时被 ``except`` 兜住、``extra_envs=None``、
  分配继续走 —— restart 不应因为 template 缺失就整体失败。
- 防御回归：直接调 ``get_template`` 的旧代码已不再使用（间接通过
  ``apply_device`` 接收到的 envs 是齐全的）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.models import DeviceBindingStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SyncThread:
    """同步执行 target 的 threading.Thread 替身，方便测试断言同步发生的副作用。"""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


def _make_aix_template_config(
    *,
    devflow: str | dict | None = "devflow/path.yaml",
    backend_repos: list[str] | None = None,
    frontend_repos: list[str] | None = None,
    lib_repos: list[str] | None = None,
) -> dict:
    """构造 AIX 模板的 template_config（即 TemplateModel.ext 的反序列化形态）。"""
    cfg: dict = {}
    if devflow is not None:
        cfg["devflow_workflow"] = devflow
    if backend_repos:
        cfg["backend_repo"] = [{"repo_url": u} for u in backend_repos]
    if frontend_repos:
        cfg["frontend_repo"] = [{"repo_url": u} for u in frontend_repos]
    if lib_repos:
        cfg["lib_repo"] = [{"repo_url": u} for u in lib_repos]
    return cfg


def _make_service() -> BotService:
    """构造一个绕过 __init__ 的 BotService，只装填本测试关心的依赖。"""
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._restart_lock_repo = MagicMock()
    svc._oss_record_repo = MagicMock()
    # OSS 迁移名单查询不命中
    svc._oss_record_repo.get_record.return_value = None
    svc._template_service = MagicMock()
    # symlink 分支这里不是重点，给一个空 mappings 即可
    skill_set_svc = MagicMock()
    skill_set_svc.get_symlink_mappings.return_value = []
    svc._skill_set_factory = MagicMock()
    svc._skill_set_factory.create.return_value = skill_set_svc

    # device service 默认成功分配（ACTIVE），后面单测可重置
    device_svc = MagicMock()
    device_svc.apply_device.return_value = SimpleNamespace(
        id="bind-1",
        device_id="dev-1",
        device_provider="arca",
        status=DeviceBindingStatus.ACTIVE.value,
    )
    svc._device_service_provider = lambda: device_svc

    # owner_id 查询返回空 → 不影响 apply_device 参数
    svc._query_admin_worknos = MagicMock(return_value=None)
    svc._baas_template_resolver = None
    return svc


def _make_bot_record(
    *,
    bot_id: str = "bot001",
    template_type: str | None = "applicationCoding",
    bot_type: str = "personal",
) -> dict:
    """构造 ``get_by_id_and_owner`` 返回的 bot_record。

    desktop bot 在 _allocate_device_async 入口就 early-return，所以这里
    默认 ``bot_type="personal"`` 让流程走到 AIX extra_envs 分支。
    """
    return {
        "bot_id": bot_id,
        "owner_id": "user001",
        "entity_id": "staff_user001",
        "entity_type": "staff",
        "bot_type": bot_type,
        "template_type": template_type,
    }


def _run_allocate(
    svc: BotService,
    *,
    bot_id: str = "bot001",
    active_engine: str = "claude_code",
    device_provider: str | None = None,
) -> MagicMock:
    """同步触发 _allocate_device_async，返回 apply_device mock 便于断言。"""
    device_svc = svc._device_service_provider()
    with patch(
        "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
        _SyncThread,
    ), patch(
        "agentclaw.community.core.bot_management.services.bot_service.BotService._is_new_bot_use_nas",
        return_value=False,
    ):
        svc._allocate_device_async(
            bot_id=bot_id,
            user_id="user001",
            nick_name="user001",
            entity_id="staff_user001",
            entity_type="staff",
            engine_types=["claude_code"],
            bot_name="aix_bot",
            active_engine=active_engine,
            owner_id="user001",
            device_provider=device_provider,
            restart_lock_key=None,
        )
    return device_svc.apply_device


# ===========================================================================
# AIX extra_envs injection on restart
# ===========================================================================


class TestAixExtraEnvsOnRestart:
    def test_application_coding_restart_uses_get_template_config(self):
        """applicationCoding restart：走 get_template_config 拿到的 template_config
        必须 round-trip 进 apply_device 的 extra_envs，BOT_TYPE/AIX_DEVFLOW_INFO/
        GIT_ADDRESSES 三件套齐全。这是 9f37c43c1 修复的核心场景。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="applicationCoding",
        )
        template_config = _make_aix_template_config(
            devflow="devflow/app.yaml",
            backend_repos=["git@code.teamclaw.com:foo/backend.git"],
            frontend_repos=["git@code.teamclaw.com:foo/frontend.git"],
        )
        svc._template_service.get_template_config.return_value = template_config

        apply_device = _run_allocate(svc)

        # 防御 9f37c43c1 之前的 bug：get_template 不应再被 _allocate_device_async 用来
        # 提取 template_config（旧代码读 template["template_config"] 永远 None）。
        svc._template_service.get_template.assert_not_called()
        svc._template_service.get_template_config.assert_called_once_with("bot001")

        # apply_device 拿到的 extra_envs 必须三件套齐全。
        _, kwargs = apply_device.call_args
        extra_envs = kwargs["extra_envs"]
        assert extra_envs is not None, (
            "applicationCoding restart 必须注入 extra_envs，否则 Arca 容器拿不到 "
            "GIT_ADDRESSES/AIX_DEVFLOW_INFO，回到 9f37c43c1 之前的故障现象"
        )
        assert extra_envs["BOT_TYPE"] == "application"
        assert extra_envs["AIX_DEVFLOW_INFO"] == "devflow/app.yaml"
        assert json.loads(extra_envs["GIT_ADDRESSES"]) == [
            "git@code.teamclaw.com:foo/backend.git",
            "git@code.teamclaw.com:foo/frontend.git",
        ]
        # template_type 也透传给 device service，避免下游再次 mapping。
        assert kwargs["template_type"] == "applicationCoding"

    def test_aicoding_engine_restart_injects_extra_envs(self):
        """门控对齐 create（:997）后：aicoding 引擎的 applicationCoding bot 重启也注入
        extra_envs（含 model→RELAY_DEFAULT_MODEL）。修复前 restart 门控只判 claude_code，
        aicoding bot 重启会丢 RELAY_DEFAULT_MODEL/BOT_TYPE。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="applicationCoding",
        )
        cfg = _make_aix_template_config(devflow="devflow/app.yaml")
        cfg["model"] = "antchat/Ling-2.6-1T"
        svc._template_service.get_template_config.return_value = cfg

        apply_device = _run_allocate(svc, active_engine="aicoding")

        _, kwargs = apply_device.call_args
        extra_envs = kwargs["extra_envs"]
        assert extra_envs is not None, "aicoding bot 重启必须注入 extra_envs（门控已含 aicoding）"
        assert extra_envs["BOT_TYPE"] == "application"
        assert extra_envs["RELAY_DEFAULT_MODEL"] == "antchat/Ling-2.6-1T"

    def test_personal_coding_restart_only_bot_type(self):
        """personalCoding 通常没有 devflow / repos —— restart 时只注入 BOT_TYPE，
        不应误造 AIX_DEVFLOW_INFO / GIT_ADDRESSES。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="personalCoding",
        )
        # 模板存在但 ext 是空 dict（personalCoding 的典型形态）
        svc._template_service.get_template_config.return_value = {}

        apply_device = _run_allocate(svc)

        svc._template_service.get_template_config.assert_called_once_with("bot001")
        _, kwargs = apply_device.call_args
        extra_envs = kwargs["extra_envs"]
        assert extra_envs == {"BOT_TYPE": "personal"}

    def test_non_aix_template_skips_extra_envs(self):
        """非 AIX 模板（如 template_type=None 或 'personal'）：跳过 extra_envs
        分支，但 get_template_config 仍被调用以读取沙箱覆写参数（image 等）。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="personal",  # 非 AIX 编码类型
        )

        apply_device = _run_allocate(svc)

        # get_template_config 仍被调用（用于读取沙箱覆写参数），但 AIX extra_envs 不注入
        svc._template_service.get_template_config.assert_called_once_with("bot001")
        _, kwargs = apply_device.call_args
        assert kwargs["extra_envs"] is None

    def test_non_claude_code_engine_skips_extra_envs(self):
        """active_engine != claude_code：哪怕 template_type=applicationCoding 也
        不注入 extra_envs —— AIX 编排只在 claude_code 引擎上有意义。
        但 get_template_config 仍被调用（沙箱覆写参数不限于 AIX 场景）。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="applicationCoding",
        )

        apply_device = _run_allocate(svc, active_engine="moltis")

        # get_template_config 仍被调用（用于读取沙箱覆写参数），但 AIX extra_envs 不注入
        svc._template_service.get_template_config.assert_called_once_with("bot001")
        _, kwargs = apply_device.call_args
        assert kwargs["extra_envs"] is None

    def test_template_lookup_failure_does_not_break_allocation(self):
        """get_template_config 抛错时被外层 except 兜住：template_config 退化为 None，
        但 BOT_TYPE 仍由 template_type 决定（不依赖 template_config），所以
        extra_envs 仍有 BOT_TYPE；device 分配继续完成 —— restart 不应被 template
        故障级联拖垮。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="applicationCoding",
        )
        svc._template_service.get_template_config.side_effect = RuntimeError(
            "template repo blew up"
        )

        apply_device = _run_allocate(svc)

        # 异常被吞，apply_device 仍被调用一次
        apply_device.assert_called_once()
        _, kwargs = apply_device.call_args
        # template_config=None 导致 AIX_DEVFLOW_INFO/GIT_ADDRESSES 缺失，
        # 但 BOT_TYPE 仍由 template_type 映射而来
        assert kwargs["extra_envs"] == {"BOT_TYPE": "application"}
        # 而且 bot 状态仍被推进到 ACTIVE（device_status=ACTIVE → final=ACTIVE）
        svc._repository.update_by_owner.assert_called_once()
        _, upd_kwargs = svc._repository.update_by_owner.call_args
        # update_by_owner(bot_id, user_id, dict)
        assert svc._repository.update_by_owner.call_args.args[2]["status"] == "ACTIVE"

    def test_template_returns_none_yields_only_bot_type(self):
        """get_template_config 返回 None（例如 template 行根本不存在）：
        build_aix_extra_envs 在 template_config=None 时只会写 BOT_TYPE。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="applicationCoding",
        )
        svc._template_service.get_template_config.return_value = None

        apply_device = _run_allocate(svc)

        _, kwargs = apply_device.call_args
        # 只有 BOT_TYPE，没有 AIX_DEVFLOW_INFO / GIT_ADDRESSES
        assert kwargs["extra_envs"] == {"BOT_TYPE": "application"}

    def test_devflow_dict_format_extracts_path(self):
        """devflow_workflow 字段允许是 dict（``{"path": "..."}``）：
        build_aix_extra_envs 必须从里面抽 path，不能整个 dict 透传。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="applicationCoding",
        )
        svc._template_service.get_template_config.return_value = (
            _make_aix_template_config(
                devflow={"path": "devflow/v2/app.yaml", "extra": "ignored"},
                backend_repos=["git@x/y.git"],
            )
        )

        apply_device = _run_allocate(svc)

        _, kwargs = apply_device.call_args
        extra_envs = kwargs["extra_envs"]
        assert extra_envs["AIX_DEVFLOW_INFO"] == "devflow/v2/app.yaml"
        assert json.loads(extra_envs["GIT_ADDRESSES"]) == ["git@x/y.git"]


# ===========================================================================
# template_config 透传给 apply_device 的回归测试（b2531b570）
# ===========================================================================


class TestTemplateConfigPassthroughOnRestart:
    """回归 b2531b570：_allocate_device_async 必须通过 get_template_config
    读取 template_config 并透传给 apply_device，而不是从 bot_record 读取
    （ac_bots 表没有 template_config 列，值存在 ac_templates.ext 里）。

    这些测试专注于 template_config 参数的透传，与 AIX extra_envs 无关。
    """

    def test_template_config_passed_to_apply_device(self):
        """get_template_config 返回的值必须原样透传给 apply_device 的
        template_config 参数——这是沙箱覆写（image/command/envs/resource_spec）
        生效的前提。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="personal",  # 非 AIX 类型，排除 extra_envs 干扰
        )
        sandbox_overrides = {
            "image": "registry.example.com/custom:latest",
            "command": "python /app/main.py",
            "envs": {"FOO": "bar"},
            "resource_spec": {"cpu": 4, "memory": 8},
        }
        svc._template_service.get_template_config.return_value = sandbox_overrides

        apply_device = _run_allocate(svc)

        _, kwargs = apply_device.call_args
        # template_config 必须原样透传，不能是 None 或 bot_record 里的值
        assert kwargs["template_config"] == sandbox_overrides, (
            "template_config from ac_templates.ext must be passed through to "
            "apply_device; None means sandbox overrides are lost on restart"
        )

    def test_template_config_none_when_no_template(self):
        """get_template_config 返回 None（bot 没有 template 记录）时，
        apply_device 的 template_config 也应为 None——存量 bot 不受影响。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="personal",
        )
        svc._template_service.get_template_config.return_value = None

        apply_device = _run_allocate(svc)

        _, kwargs = apply_device.call_args
        assert kwargs["template_config"] is None

    def test_baas_restart_resolves_template_uid_before_apply_device(self):
        """历史 provider=baas 的 restart 不重新走 DRM，但上层仍要补 template_uid。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="normalCC",
            bot_type="personal",
        )
        svc._template_service.get_template_config.return_value = None
        resolver = MagicMock()
        resolver.resolve_template_uid.return_value = "openclaw_personal_default"
        svc._baas_template_resolver = resolver

        apply_device = _run_allocate(
            svc,
            active_engine="openclaw",
            device_provider="baas",
        )

        resolver.resolve_template_uid.assert_called_once_with(
            bot_id="bot001",
            user_id="user001",
            env="dev",
            bot_type="personal",
            engine_type="openclaw",
            template_type="normalCC",
            template_config=None,
        )
        _, kwargs = apply_device.call_args
        assert kwargs["device_provider"] == "baas"
        assert kwargs["template_config"] == {
            "template_uid": "openclaw_personal_default"
        }

    def test_template_config_survives_lookup_error(self):
        """get_template_config 抛异常时，resolved_template_config 退化为 None，
        apply_device 仍然被调用（template_config=None），分配不被中断。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="personal",
        )
        svc._template_service.get_template_config.side_effect = RuntimeError("db error")

        apply_device = _run_allocate(svc)

        apply_device.assert_called_once()
        _, kwargs = apply_device.call_args
        assert kwargs["template_config"] is None

    def test_template_config_with_aix_extra_envs_together(self):
        """AIX 场景下 template_config 和 extra_envs 同时正确：
        template_config 来自 get_template_config，extra_envs 来自
        build_aix_extra_envs(resolved_template_config)。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="applicationCoding",
        )
        full_config = _make_aix_template_config(
            devflow="devflow/app.yaml",
            backend_repos=["git@code.teamclaw.com:foo/backend.git"],
        )
        # 加入沙箱覆写参数
        full_config["image"] = "registry.example.com/custom:latest"
        full_config["resource_spec"] = {"cpu": 4, "memory": 8}
        svc._template_service.get_template_config.return_value = full_config

        apply_device = _run_allocate(svc)

        _, kwargs = apply_device.call_args
        # template_config 原样透传（含 image 和 resource_spec）
        assert kwargs["template_config"] == full_config
        # extra_envs 也正确生成
        assert kwargs["extra_envs"]["BOT_TYPE"] == "application"
        assert kwargs["extra_envs"]["AIX_DEVFLOW_INFO"] == "devflow/app.yaml"

    def test_non_claude_code_engine_still_passes_template_config(self):
        """非 claude_code 引擎（如 moltis）不注入 AIX extra_envs，
        但 template_config 仍需透传——沙箱覆写参数不限于 AIX 场景。"""
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _make_bot_record(
            template_type="personal",
        )
        sandbox_overrides = {"image": "registry.example.com/custom:latest"}
        svc._template_service.get_template_config.return_value = sandbox_overrides

        apply_device = _run_allocate(svc, active_engine="moltis")

        _, kwargs = apply_device.call_args
        # 非 AIX 场景：extra_envs 为 None，但 template_config 仍透传
        assert kwargs["extra_envs"] is None
        assert kwargs["template_config"] == sandbox_overrides
