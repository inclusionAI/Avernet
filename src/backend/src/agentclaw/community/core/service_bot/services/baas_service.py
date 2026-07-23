"""BaaS Service — bot lifecycle + ws_info/http_info lookup over HttpClient.

A plain core service (not a plugin): the per-environment difference lives
entirely in the injected ``HttpClient[baas]`` transport, so there is a
single implementation here. The DI provider
(``ServiceBotModule.baas_service``) supplies ``baas_api_base`` from config
and the baas-qualified ``HttpClient``; ``baas_api_base`` does not route any
request — it only shapes the ``baas_base_url`` field returned by
``get_ws_info``.

Adapters depend on ``BaasServiceProtocol`` in ``agentclaw.community.api.baas_service``;
this concrete class conforms to it structurally (verified by
``tests/architecture/test_service_api_conformance.py``).

The dataclasses (``BotWsConnectionInfoResponse``, ``HttpConnectionInfo``,
``Storage``, ``BotDeployConfig``, ``MountPointEntry``, ``BotConfig``) and the
``BaasServiceError`` exception are defined here and imported from ~15 call
sites.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import json
import time

import httpx
from agentclaw.community.core.caller_identity.credential import (
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_OUTBOUND_INVALID,
    CALLER_OUTBOUND_UPDATE_FAILED,
    CALLER_TARGET_AMBIGUOUS,
    CALLER_TARGET_NOT_FOUND,
    CallerCredentialError,
    CallerToken,
)
from agentclaw.community.core.bot_management.repository.protocol import BotLookupAmbiguousError

from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    DEFAULT_DEVICE_PROVIDER,
    resolve_device_provider,
)
from agentclaw.community.core.service_bot.types import PublishStage, is_editable_bot
from agentclaw.community.log import get_logger
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxProvider, EngineSandboxRegistry

from agentclaw.community.core.devices.protocols import StoragePathProtocol
from agentclaw.community.core.devices.services.sandbox_overrides import (
    InvalidSandboxOverridesError,
    SandboxOverrides,
)

from agentclaw.community.kernel.device_dto import (
    OutBoundOperationRule,
    ResourceSpecification,
)

if TYPE_CHECKING:
    from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
    from agentclaw.community.core.service_bot.repository.bot_publish_repository import BotPublishRepositoryProtocol
    from agentclaw.community.core.system_config.service import SystemConfigService
    from agentclaw.community.core.common_config import CommonWhiteListService

logger = get_logger()


ENGINE_DIR_MOUNT_WHITELIST_BUSINESS_CODE = "nas_mount"
ENGINE_DIR_MOUNT_WHITELIST_PARAM_CODE = "engine_dir_mount_whitelist"


# 默认只读规则常量（deprecated）。
# 新链路请使用 EngineSandboxProvider.get_default_read_only_rules()。
# 保留该常量仅为渐进迁移期兼容，不应再被新的业务逻辑直接依赖。
DEFAULT_READ_ONLY_RULES = [
    {
        "path": "/home/admin/.openclaw/openclaw.json",
        "rule_type": "file",
    },
    {
        "path": "/home/admin/.openclaw/workspace/config/mcporter.json",
        "rule_type": "file",
    },
    {
        "path": "/home/admin/.openclaw/workspace/*.md",
        "rule_type": "glob",
    },
    {
        "path": "/home/admin/.mcporter/mcporter.json",
        "rule_type": "file",
    },
    {
        "path": "/home/admin/.openclaw/agents/*/agent/models.json",
        "rule_type": "glob",
    },
]


class BaasServiceError(Exception):
    """BaaS service error."""
    pass


@dataclass
class BotWsConnectionInfoResponse:
    """BAAS WebSocket connection info.

    Fields:
        ws_url: WebSocket 连接地址（仅 backend grt_chat / ws 链路使用）
        token: 认证 token
        target: 目标地址（agentclawproxy proxypass target，过渡期保留）
        expires_at: token 过期时间
        paas_device_id: PaaS device ID（= ac_entity_device_binding.device_id），
                        device 级 invoke-http 链路使用
        baas_base_url: BaaS HTTP API base URL，invoke-http 链路必需
        engine_port: VM 内 engine 服务端口（默认 20003），invoke-http
                     链路必需；relay 链路会传 18900
        tenant: 租户名称（透传自 effective_tenant），bot 级 invoke-http URL 构造必需
        bot_uuid: BaaS Bot UUID（= ac_entity_device_binding.device_id），
                  bot 级 invoke-http 链路必需；与 paas_device_id 来源相同，
                  但语义不同：bot_uuid 是逻辑 Bot 标识，paas_device_id 是
                  PaaS 设备实例标识
    """

    ws_url: str
    token: str
    target: str
    expires_at: str
    paas_device_id: str = ""
    baas_base_url: str = ""
    engine_port: int = 20003
    tenant: str = ""
    bot_uuid: str = ""


@dataclass
class HttpConnectionInfo:
    """BaaS resolve_http_connection_info 解码后的结构。

    与 BotWsConnectionInfoResponse 等价但专用于 HTTP（不带 ws_url/expires_at），
    用于 plugin 直连 container adapter 的 file/skill/mcp/session/health 业务请求。
    plan-01 引入；BaaS 端 endpoint 见 BaaS commit 9d4622c1e。
    """

    http_url: str
    """完整 HTTP URL（如 "http://10.0.0.1:20010"），已含 scheme + host + port。"""
    token: str
    """adapter 校验用的 openclawToken。"""
    target: str = ""
    """agentclawproxy proxypass target，格式 ``{TYPE}_{device_id}@{template}:{port}``
    （如 ``TECLAW_b_01...@4:20003``）。teclaw 投递从中取 device_id 作为容器内 bot_id。"""


@dataclass
class Storage:
    """
    Sandbox storage.
    """

    type: str
    """
    Storage type.
    """
    path: str
    """
    Storage path.
    """
    storage_id: str
    """
    Storage id.
    """
    quota: str
    """
    Storage quota, such as "1Gi".
    """
    permission: str

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        return {
            "type": self.type,
            "path": self.path,
            "storage_id": self.storage_id,
            "quota": self.quota,
            "permission": self.permission,
        }


@dataclass
class BotDeployConfig:
    """Bot 部署配置。

    Args:
        after_create_cmd_hook: Lifecycle hook: shell script executed after PaaS device creation
        after_create_hook_wait_seconds: Wait time for after_create_cmd_hook execution in seconds
        before_destroy_cmd_hook: Lifecycle hook: shell script executed before PaaS device destruction
        before_destroy_hook_wait_seconds: Wait time for before_destroy_cmd_hook execution in seconds
        mount_points: OSS 挂载点配置列表
        teclaw_bot_config: Composed bot config artifact (BotConfigArtifact,
            serialized) for the teclaw (pull-based) container. Rides inside
            ``deploy_config`` so secbaas reads it as
            ``DeployConfig.teclaw_bot_config`` and forwards it to the external
            container (non-mount delivery). ``None`` for ARCA/baas bots, which
            deliver config via NAS mount instead. (Wire field name confirmed
            with the BaaS owner 2026-06-08: ``teclaw_bot_config``, not
            ``config_artifact`` — the internal value is still ``config_artifact``.)
        user_id: 创建者 user_id (= owner_id),BaaS 侧按 user_id+tc_bot_id 唯一确定 workspace 目录
        tc_bot_id: TeamClaw bot_id (= bolt_id),BaaS 侧按 user_id+tc_bot_id 唯一确定 workspace 目录
    """
    after_create_cmd_hook: str | None = None
    after_create_hook_wait_seconds: int = 300
    before_destroy_cmd_hook: str | None = None
    before_destroy_hook_wait_seconds: int = 300
    mount_points: List["MountPointEntry"] | None = None
    ttl_in_minutes: int = 1440
    outbound_operation_rule: OutBoundOperationRule | None = None
    storage: Storage | None = None
    teclaw_bot_config: Dict[str, Any] | None = None
    user_id: str | None = None
    tc_bot_id: str | None = None
    envs: Dict[str, Any] | None = None
    engine_type: str | None = None
    resource_spec: ResourceSpecification | None = None
    docker_image: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        result = {
            "after_create_cmd_hook": self.after_create_cmd_hook,
            "after_create_hook_wait_seconds": self.after_create_hook_wait_seconds,
            "before_destroy_cmd_hook": self.before_destroy_cmd_hook,
            "before_destroy_hook_wait_seconds": self.before_destroy_hook_wait_seconds,
        }
        if self.mount_points:
            result["mount_points"] = [mp.to_dict() for mp in self.mount_points]
        if self.ttl_in_minutes is not None:
            result["ttl_in_minutes"] = self.ttl_in_minutes
        if self.outbound_operation_rule is not None:
            # OutBoundOperationRule 转 dict
            result["outbound_operation_rule"] = {
                "header_operation_rules": [
                    {
                        "domains": rule.domains,
                        "action": rule.action,
                        "header_name": rule.header_name,
                        "value": rule.value,
                        "placeholder": getattr(rule, 'placeholder', None),
                    }
                    for rule in self.outbound_operation_rule.header_operation_rules
                ]
            }
        if self.storage is not None:
            result["storage"] = self.storage.to_dict()
        if self.teclaw_bot_config is not None:
            # Teclaw container delivery: the composed artifact rides inside
            # deploy_config so secbaas reads it as DeployConfig.teclaw_bot_config
            # (a top-level create_bot field would be dropped by extra="ignore").
            result["teclaw_bot_config"] = self.teclaw_bot_config
        if self.user_id is not None:
            result["user_id"] = self.user_id
        if self.tc_bot_id is not None:
            result["tc_bot_id"] = self.tc_bot_id
        if self.envs is not None:
            result["envs"] = self.envs
        if self.resource_spec is not None:
            spec_dict = {
                "cpu": self.resource_spec.cpu,
                "memory": self.resource_spec.memory,
            }
            if self.resource_spec.disk is not None:
                spec_dict["disk"] = self.resource_spec.disk
            result["resource_spec"] = spec_dict
        if self.docker_image is not None:
            result["docker_image"] = self.docker_image
        if self.engine_type is not None:
            result["engine_type"] = self.engine_type
        return result


@dataclass
class MountPointEntry:
    """OSS mount point configuration for DeployConfig.

        Platform-agnostic representation, converted to Arca MountPoint when needed.
        Keeps domain model independent of Arca SDK per D-01.
        Field names match Arca MountPoint for clarity (id, remote_dir, local_dir, permission).
        """
    remote_dir: str
    local_dir: str
    permission: str

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        return {
            "remote_dir": self.remote_dir,
            "local_dir": self.local_dir,
            "permission": self.permission,
        }


@dataclass
class BotConfig:
    """Bot 配置。

    Args:
        entity_id: 实体 ID
        entity_type: 实体类型
        deploy_config: 部署配置
    """
    entity_id: str = ""
    entity_type: str = "staff"
    auto_approve_publish: bool = False
    deploy_config: Optional[BotDeployConfig] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式。"""
        result = {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "auto_approve_publish": self.auto_approve_publish,
        }
        if self.deploy_config:
            result["deploy_config"] = self.deploy_config.to_dict()
        return result


class BaasService:  # pragma: no cover
    """BaaS 服务 - 与 BaaS 层 API 交互。

    负责 BaaS 层相关的 API 调用，如创建 Bot 等。所有依赖由
    ``ServiceBotModule.baas_service`` 注入；环境差异（本地 vs 线上）由注入的
    ``HttpClient[baas]`` 承载，本类只有一个实现。

    Coverage: ``# pragma: no cover`` — the BaaS HTTP / sandbox-config body is
    real-integration code (binding resolution, payload builders, ARCA
    sandbox shaping) exercised by acceptance / 联调, not CI LOCAL line
    coverage. Carried over verbatim from the pre-demote ``ProdBaasService``
    (``@plugin_impl(Mode.PROD)``), which CI never instantiated. The contract
    is still enforced structurally by
    ``tests/architecture/test_service_api_conformance.py``, and the
    behavior-level unit / injection tests still run (respx-mocked) for
    regression — they just don't count toward CI line coverage.
    """

    def __init__(
        self,
        baas_api_base: str,
        tenant: str,
        template_uuid: str,
        bot_repo: "BotRepository",
        bot_publish_repo: "BotPublishRepositoryProtocol",
        system_config_service: "SystemConfigService",
        storage_path: StoragePathProtocol,
        device_binding_repo: "DeviceBindingRepository",
        default_ttl_minutes: int,
        sandbox_registry: EngineSandboxRegistry,
        http_client: HttpClient,
        general_http_client: HttpClient,
        secret_resolver: SecretResolver,
        common_whitelist_service: "CommonWhiteListService",
        outbound_rule_provider: "OutboundRuleProvider",
        personal_bot_template_uuid: Optional[str] = None,
    ):
        """初始化 BaasService。

        所有依赖均由 ``ServiceBotModule.baas_service`` 注入 — 不再支持
        手动构造时省略参数（DI 已保证一定提供）。

        ``http_client`` is the baas-qualified :class:`HttpClient` (``base_url=<baas gateway>``);
        control-plane calls (lifecycle API against the BaaS gateway) pass **relative paths**
        (e.g. ``/api/v1/bots``); the base_url from the client is prepended automatically.

        ``general_http_client`` is the general-qualified :class:`HttpClient`
        (``base_url=""``); used by ``invoke_http`` to pass the full runtime
        container URL returned by BaaS (host varies per call). It is required —
        callers must inject a distinct general-qualified client; falling back to
        ``http_client`` (baas-qualified, ``base_url=<baas gateway>``) would mix
        two clients with incompatible ``base_url`` semantics when ``invoke_http``
        passes a full absolute URL.

        ``baas_api_base`` is now only used for the ``baas_base_url`` field in
        :class:`BotWsConnectionInfoResponse` — it no longer prefixes request paths.

        ``personal_bot_template_uuid``: poolab personal-bot template UUID;
        used by :meth:`_build_personal_bot_payload`. None 时该方法会主动 raise，
        caller 通过 ``template_uuid`` 参数显式传也可。

        ``secret_resolver``: Mist secret 解析插件 (Rule 20)。DI 装配时由
        ``ServiceBotModule.baas_service`` 注入: prod → ProdSecretResolver (走
        layotto/Mist); singlebox / pytest → LocalSecretResolver (返 None
        → token 退化为空字符串,BaaS LocalPaasService 无视 outbound rule)。
        None (仅旧测试构造时省略) → 走 layotto 老路径,仅 prod 可用。
        """
        self._baas_api_base = baas_api_base
        self._http = http_client
        self._general_http = general_http_client
        self._tenant = tenant
        self._template_uuid = template_uuid
        self._personal_bot_template_uuid = personal_bot_template_uuid
        self._device_binding_repo = device_binding_repo
        self._bot_repo = bot_repo
        self._bot_publish_repo = bot_publish_repo
        self._system_config_service = system_config_service
        self._storage_path = storage_path
        self._default_ttl_minutes = default_ttl_minutes
        self._sandbox_registry = sandbox_registry
        self._secret_resolver = secret_resolver
        self._common_whitelist_service = common_whitelist_service
        self._outbound_rule_provider = outbound_rule_provider

    def post_bots_api(
        self,
        path: str,
        payload: Dict[str, Any],
        action: str,
    ) -> Dict[str, Any]:
        """发送 POST 请求到 BaaS Bot API，处理响应和错误码。

        公开接口，供 DesktopBotService 等外部调用方自行构建 payload 后使用。

        Args:
            path: API 相对路径（base_url 由注入的 HttpClient 提供）
            payload: 请求体
            action: 操作名称，用于日志

        Returns:
            BaaS 层返回的 data 字段

        Raises:
            BaasServiceError: API 返回非零 code
        """
        return self._post_bots_api(path=path, payload=payload, action=action)

    def _get_bots_api(
        self,
        path: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """发送 GET 请求到 BaaS Bot API，处理响应和错误码（内部实现）。

        ``params`` 为额外 query 参数，与 ``{"tenant": ...}`` 合并；不传时
        行为与原先一致（只带 tenant）。
        """
        merged_params: Dict[str, Any] = {"tenant": self._tenant}
        if params:
            merged_params.update(params)
        response = self._http.get(
            path,
            params=merged_params,
            timeout=30.0,
        )
        response.raise_for_status()

        response_data = response.json()

        logger.info(
            f"[BaasService.{action}] BaaS raw response: %s",
            response_data,
        )

        if response_data.get("code") != 0:
            raise BaasServiceError(
                f"BaaS API error: {response_data.get('message', 'Unknown error')}"
            )

        result = response_data.get("data", {})

        logger.info(
            f"[BaasService.{action}] "
            f"Success: {result}"
        )

        return result

    def _post_bots_api(
        self,
        path: str,
        payload: Dict[str, Any],
        action: str,
        tenant: Optional[str] = None,
    ) -> Dict[str, Any]:
        """发送 POST 请求到 BaaS Bot API，处理响应和错误码（内部实现）。

        ``path`` 应为前导斜杠的相对路径（如 ``/api/v1/bots``）；base_url 由
        ``self._http``（baas-qualified HttpClient）自动拼接。``tenant`` 留空时使用
        ``self._tenant``（多实例重启等场景可显式覆盖）。
        """
        response = self._http.post(
            path,
            params={"tenant": tenant or self._tenant},
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()

        response_data = response.json()

        logger.info(
            f"[BaasService.{action}] BaaS raw response: %s",
            response_data,
        )

        if response_data.get("code") != 0:
            raise BaasServiceError(
                f"BaaS API error: {response_data.get('message', 'Unknown error')}"
            )

        result = response_data.get("data", {})

        logger.info(
            f"[BaasService.{action}] "
            f"Success: {result}"
        )

        return result

    @staticmethod
    def _normalize_migration_path_for_mount(
            migration_path: str,
            mount_home_dir_storage: bool,
    ) -> str:
        if mount_home_dir_storage:
            return migration_path.replace("/home/admin", "/opt", 1)
        if migration_path.startswith("/opt"):
            return migration_path.replace("/opt", "/home/admin", 1)
        return migration_path

    def _resolve_service_bot_resource_spec(
        self, ext: dict | None
    ) -> ResourceSpecification | None:
        """从 ac_bots.ext 的 service_bot_config 解析沙箱规格。

        cpu/memory 必须同时成功转 int 才生效；任一缺失/为空/转换失败 → None（整组不传）。
        disk 可选，单独缺失/非法不影响 cpu/memory。
        """
        if not ext or not isinstance(ext, dict):
            return None
        sbc = ext.get("service_bot_config")
        if not isinstance(sbc, dict):
            return None
        try:
            cpu = int(sbc["cpu"])
            memory = int(sbc["memory"])
        except (KeyError, ValueError, TypeError):
            return None
        kwargs: dict = {"cpu": cpu, "memory": memory}
        disk = sbc.get("disk")
        if disk is not None:
            try:
                kwargs["disk"] = int(disk)
            except (ValueError, TypeError):
                pass
        return ResourceSpecification(**kwargs)

    def _build_create_bot_payload(
        self,
        bot: Dict[str, Any],
        owner_id: str,
        request_id: str,
        device_count: int,
        migration_path: str,
        agent_pass_token: str = "",
        mount_path: Optional[str] = None,
        machine_id: Optional[str] = None,
        template_uuid: Optional[str] = None,
        stage: str | None = None,
        version: str = "1",
        auto_approve_publish: bool = True,
        extra_envs: Optional[Dict[str, Any]] = None,
        template_config: Optional[Dict[str, Any]] = None,
        mount_home_dir_storage: bool | None = None,
        ext_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """构建创建 Bot 的请求体。

        包含：
        - 构建 sandbox 成功后执行的命令
        - 构建实例销毁前置hook命令
        - 构建部署配置
        - 构建配置
        - 构建请求体

        Args:
            bot: Bot 信息字典，包含: bot_id, bot_name, entity_id, entity_type, bot_desc, active_engine
            owner_id: 创建者 ID
            request_id: 请求 ID
            device_count: 设备数量
            migration_path: Bot 实例迁移后的目录路径
            mount_path: 用户自定义 NAS 挂载路径（可选）
            machine_id: 目标设备节点 ID（可选，指定部署机器）
            agent_pass_token: Agent 授权码，用于重启后写入沙箱 credentials
            stage: 发布阶段；None 时不向启动脚本传 --stage
            version: 发布版本
            auto_approve_publish: 是否由 BaaS 创建后自动审批发布单
            extra_envs: 追加写入容器的环境变量
            template_config: 上层选择 template 时携带的沙箱覆写配置
            mount_home_dir_storage: 是否使用 home 目录 NAS；None 时由底层挂载逻辑按白名单解析
            ext_info: Optional[Dict[str, Any]] = None,

        Returns:
            请求体字典
        """
        # 从 bot 对象提取信息
        bot_id = bot.get("bot_id", "")
        name = bot.get("bot_name", bot.get("bot_id", ""))
        entity_id = bot.get("entity_id", "")
        entity_type = bot.get("entity_type", "staff")
        description = bot.get("bot_desc")
        engine = bot.get("active_engine", DEFAULT_ENGINE_TYPE)
        bot_type = bot.get("bot_type", "personal")

        # 根据白名单选择挂载本地 session 目录或 home 目录到远端 NAS。
        # 整条 payload 链路只查询一次白名单，start_cmd 与 storage 共用同一结果；
        # 未命中或读取异常时默认沿用 session 目录，保证灰度切换安全。
        if migration_path:
            mount_home_dir_storage = self._should_mount_home_dir_storage(
                owner_id=owner_id,
                bot_id=bot_id,
            )
            migration_path = self._normalize_migration_path_for_mount(
                migration_path=migration_path,
                mount_home_dir_storage=mount_home_dir_storage,
            )

        # 构建 sandbox 成功后执行的命令
        start_up_cmd = self._get_start_cmd(
            bot_id=bot_id,
            owner_id=owner_id,
            entity_id=entity_id,
            entity_type=entity_type,
            migration_pat=migration_path,
            bot_type=bot_type,
            engine=engine,
            stage=stage,
            version=version,
            mount_home_dir_storage=mount_home_dir_storage,
            ext_info=ext_info,
        )

        # 构建实例销毁前置hook命令
        destroy_cmd = self._get_destroy_cmd()

        # 获取挂载点配置
        mount_points = self._setup_directory(
            entity_id=entity_id,
            entity_type=entity_type,
            bot_id=bot_id,
            engine_type=engine,
            mount_path=mount_path,
            owner_id=owner_id,
            mount_home_dir_storage=mount_home_dir_storage,
        )

        # 获取 bot 的 nas storage 挂载点：命中白名单走 home 目录，
        # 否则沿用 sessions 目录（白名单判定在 _setup_bot_storage 内部完成）。
        storage = self._setup_bot_storage(
            entity_id=entity_id,
            entity_type=entity_type,
            owner_id=owner_id,
            bot_id=bot_id,
            engine_type=engine,
            mount_home_dir_storage=mount_home_dir_storage,
            bot_type=bot_type,
            stage=stage,
        )

        # 构建出站操作规则
        outbound_operation_rule = self._build_outbound_operation_rule(bot_id, owner_id, agent_pass_token)

        # 从 ac_bots.ext.service_bot_config 解析沙箱规格（cpu/memory 缺一则整组不传）
        resource_spec = self._resolve_service_bot_resource_spec(bot.get("ext"))
        if resource_spec is not None:
            logger.info(
                f"[BaasService._build_create_bot_payload] resource_spec applied: "
                f"bot_id={bot_id}, cpu={resource_spec.cpu}, "
                f"memory={resource_spec.memory}, disk={resource_spec.disk}"
            )
        else:
            logger.info(
                f"[BaasService._build_create_bot_payload] no resource_spec: bot_id={bot_id}"
            )

        envs, resource_spec, docker_image = self._resolve_deploy_envs_spec_image(
            engine=engine,
            extra_envs=extra_envs,
            template_config=template_config,
            resource_spec=resource_spec,
        )

        # 构建部署配置
        # user_id / tc_bot_id: BaaS 侧按 user_id+tc_bot_id 唯一确定 workspace 目录
        # (与 desktop_bot_service._build_create_bot_payload 同款姿势)
        deploy_config = BotDeployConfig(
            after_create_cmd_hook=start_up_cmd,
            after_create_hook_wait_seconds=10,
            before_destroy_cmd_hook=destroy_cmd,
            before_destroy_hook_wait_seconds=10,
            mount_points=mount_points,
            ttl_in_minutes=self._default_ttl_minutes,
            outbound_operation_rule=outbound_operation_rule,
            storage=storage,
            user_id=owner_id or None,
            tc_bot_id=bot_id or None,
            envs=envs,
            resource_spec=resource_spec,
            docker_image=docker_image,
        )

        # 构建配置
        config = BotConfig(
            entity_id=entity_id,
            entity_type=entity_type,
            auto_approve_publish=auto_approve_publish,
            deploy_config=deploy_config,
        )

        # 构建请求体
        payload = {
            "name": name,
            "template_uuid": template_uuid if template_uuid is not None else self._template_uuid,
            "device_count": device_count,
            "operator": owner_id,
            "request_id": request_id,
        }

        if machine_id:
            payload["machine_id"] = machine_id

        if description:
            payload["description"] = description

        # 添加 config
        payload["config"] = config.to_dict()

        return payload

    def _resolve_deploy_envs_spec_image(
        self,
        *,
        engine: str,
        extra_envs: Optional[Dict[str, Any]],
        template_config: Optional[Dict[str, Any]],
        resource_spec: Any,
    ) -> tuple[Dict[str, Any], Any, Optional[str]]:
        """Resolve the deploy config's ``(envs, resource_spec, docker_image)``.

        Base envs carry the engine; ``extra_envs`` and any validated
        ``template_config`` sandbox overrides (image/spec/envs) layer on top. Old
        Arca used ``template_config`` to override the template's default image,
        spec, and envs; the BaaS Docker template's image-override field is
        ``docker_image``. Returns the possibly-overridden triple.
        """
        envs = {"AGENTCLAW_ENGINE": engine}
        if extra_envs:
            envs.update(extra_envs)

        docker_image = None
        overrides = SandboxOverrides.from_template_config(template_config)
        if not overrides.is_empty():
            try:
                overrides.validate()
            except InvalidSandboxOverridesError as e:
                raise BaasServiceError(f"沙箱覆写参数校验失败: {e}") from e

            envs = overrides.merged_envs(envs)
            if overrides.resource_spec is not None:
                resource_spec = overrides.resource_spec
            if overrides.image is not None:
                docker_image = overrides.image

        return envs, resource_spec, docker_image

    def create_bot(
        self,
        bot: Dict[str, Any],
        owner_id: str,
        request_id: str,
        device_count: int = 1,
        migration_path: Optional[str] = None,
        agent_pass_token: str = "",
        mount_path: Optional[str] = None,
        machine_id: Optional[str] = None,
        template_uuid: Optional[str] = None,
        stage: str = PublishStage.ONLINE.value,
        version: str = "1",
        auto_approve_publish: bool = True,
        ext_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """调用 BaaS 层 API 创建 Bot。

        参考: /Users/pingwu/teamclaw/secbaas/docs/API.md

        Args:
            bot: Bot 信息字典，包含: bot_name, entity_id, entity_type, bot_desc, active_engine
            owner_id: 创建者 ID
            request_id: 请求 ID（32-64字符，字母/数字/连字符/下划线）
            device_count: 设备数量
            migration_path: Bot 实例迁移后的目录路径
            mount_path: 用户自定义 NAS 挂载路径（可选）
            machine_id: 目标设备节点 ID（可选，指定部署机器）
            template_uuid: 模板 UUID（可选，不传则使用默认模板）
            agent_pass_token: Agent 授权码，用于重启后写入沙箱 credentials
            stage: str = PublishStage.ONLINE.value,
            version: str = "1",发布版本
            auto_approve_publish: 是否由 BaaS 创建后自动审批发布单
            ext_info: Bot 额外信息

        Returns:
            BaaS 层返回的 Bot 信息，包含：
            - bot_uuid: Bot UUID
            - publish_id: 发布工作流 ID

        Raises:
            BaasServiceError: 创建失败
        """
        # migration_path 兼容性处理（2026-06-05 singlebox 改造）：
        # - 服务 bot caller（bot_build_service.release / upgrade）将 migration_path 声明为
        #   必填位置参数，且来源是 build() 生成的非空 NAS 路径 /home/admin/nfs/bot-data/{ver}/...，
        #   永远非空，不受本块影响。
        # - singlebox 个人 bot caller（local_device_service._do_allocate）不传此参数，
        #   默认 None,本地无"上一版本数据迁移"概念。
        # 处理策略:
        #   * None  → 转为空字符串透传，由容器内 start_service.sh 兼容空 --source_dir 跳过迁移
        #     （兼容由 BaaS 同学在容器侧脚本里做）
        #   * ""    → 视为调用方 bug（显式传空字符串），主动 raise 报错
        if migration_path is None:
            migration_path = ""
        elif not migration_path:
            # 只可能是显式传了非 None 但 falsy 的值（如空字符串以外的奇怪类型）
            raise BaasServiceError(
                "migration_path is empty; pass None to skip or a real path string"
            )

        name = bot.get("bot_name", bot.get("bot_id", ""))
        logger.info(
            f"[BaasService.create_bot] "
            f"Creating bot in BaaS: name={name}, owner_id={owner_id}, request_id={request_id}"
        )

        # 构建请求体
        payload = self._build_create_bot_payload(
            bot=bot,
            owner_id=owner_id,
            request_id=request_id,
            device_count=device_count,
            migration_path=migration_path,
            agent_pass_token=agent_pass_token,
            mount_path=mount_path,
            machine_id=machine_id,
            template_uuid=template_uuid,
            stage=stage,
            version=version,
            auto_approve_publish=auto_approve_publish,
            ext_info=ext_info,
        )

        logger.info(
            f"[BaasService.create_bot] "
            f"Upgrading bot in BaaS: operator={owner_id}, request_id={request_id}, payload={payload}"
        )

        try:
            return self._post_bots_api(
                path="/api/v1/bots",
                payload=payload,
                action="create_bot",
            )

        except httpx.HTTPStatusError as e:
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BaasService.create_bot] "
                f"Failed to create bot in BaaS: {e}"
            )
            raise BaasServiceError(f"Failed to create bot in BaaS: {e}")

    def create_teclaw_bot(
        self,
        bot: Dict[str, Any],
        owner_id: str,
        request_id: str,
        config_artifact: Dict[str, Any],
        *,
        template_uuid: str,
        device_count: int = 1,
        ttl_in_minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Provision a **teclaw** (pull-based, non-mount) container via BaaS.

        Unlike :meth:`create_bot` (ARCA: requires ``migration_path`` + NAS mount
        points + boot hooks), teclaw delivers config through
        ``deploy_config.teclaw_bot_config`` — BaaS forwards the composed
        ``BotConfigArtifact`` to the external container (non-mount) and the
        container boots from it (owner-side contract). No NAS, no migration_path.

        Args:
            bot: Bot info dict (``bot_id`` / ``bot_name`` / ``entity_id`` /
                ``entity_type`` / ``bot_desc``).
            owner_id: Creator user id (operator).
            request_id: Request id (32-64 chars).
            config_artifact: Composed ``BotConfigArtifact`` (serialized) to hand
                to the container via ``deploy_config.teclaw_bot_config`` (the
                wire field; the param keeps the internal name).
            template_uuid: The **teclaw** template uuid (the container type is
                decided by secbaas from this template's config). Required — there
                is no ARCA-style default fallback for teclaw.
            device_count: Number of devices to provision.
            ttl_in_minutes: Optional device lifetime; defaults to the configured
                ARCA default ttl.
        Returns:
            BaaS response containing ``bot_uuid`` and ``publish_id``.

        Raises:
            BaasServiceError: Creation failed.
        """
        logger.info(
            "[BaasService.create_teclaw_bot] Creating teclaw bot in BaaS: "
            "name=%s, owner_id=%s, template_uuid=%s, request_id=%s",
            bot.get("bot_name", bot.get("bot_id", "")),
            owner_id,
            template_uuid,
            request_id,
        )
        payload = self._build_teclaw_payload(
            bot,
            owner_id,
            request_id,
            config_artifact,
            template_uuid=template_uuid,
            device_count=device_count,
            ttl_in_minutes=ttl_in_minutes,
        )
        return self._post_teclaw(
            path="/api/v1/bots",
            payload=payload,
            action="create_teclaw_bot",
        )

    def update_teclaw_bot(
        self,
        bot_uuid: str,
        bot: Dict[str, Any],
        owner_id: str,
        request_id: str,
        config_artifact: Dict[str, Any],
        *,
        template_uuid: str,
        device_count: int = 1,
        ttl_in_minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Re-deliver a new frozen artifact to an existing **teclaw** container.

        The teclaw analogue of :meth:`upgrade_bot` (ARCA, ``migration_path``):
        POSTs ``/api/v1/bots/{bot_uuid}/update`` with the composed artifact in
        ``deploy_config.teclaw_bot_config`` (non-mount). Used by the publish
        re-publish / restart path. Returns ``{bot_uuid, publish_id}``.
        """
        logger.info(
            "[BaasService.update_teclaw_bot] Updating teclaw bot in BaaS: "
            "bot_uuid=%s, owner_id=%s, request_id=%s",
            bot_uuid,
            owner_id,
            request_id,
        )
        payload = self._build_teclaw_payload(
            bot,
            owner_id,
            request_id,
            config_artifact,
            template_uuid=template_uuid,
            device_count=device_count,
            ttl_in_minutes=ttl_in_minutes,
        )
        return self._post_teclaw(
            path=f"/api/v1/bots/{bot_uuid}/update",
            payload=payload,
            action="update_teclaw_bot",
        )

    def _build_teclaw_payload(
        self,
        bot: Dict[str, Any],
        owner_id: str,
        request_id: str,
        config_artifact: Dict[str, Any],
        *,
        template_uuid: str,
        device_count: int,
        ttl_in_minutes: Optional[int],
    ) -> Dict[str, Any]:
        """Shape the create/update body for a teclaw (non-mount) container.

        Only the artifact rides in ``deploy_config`` — no mount_points /
        migration_path / boot hooks (those are ARCA-specific).
        """
        deploy_config = BotDeployConfig(
            ttl_in_minutes=ttl_in_minutes or self._default_ttl_minutes,
            teclaw_bot_config=config_artifact,
        )
        config = BotConfig(
            entity_id=bot.get("entity_id", ""),
            entity_type=bot.get("entity_type", "staff"),
            # All-auto approval (#197): the teclaw create/update payloads used to
            # omit this (BotConfig default False), making the client-side approve
            # load-bearing. Under all-auto, BaaS approves server-side and every
            # client approve call is removed, so this must be True for both the
            # teclaw create and update paths (both build through here).
            auto_approve_publish=True,
            deploy_config=deploy_config,
        )
        payload: Dict[str, Any] = {
            "name": bot.get("bot_name", bot.get("bot_id", "")),
            "template_uuid": template_uuid,
            "device_count": device_count,
            "operator": owner_id,
            "request_id": request_id,
            "config": config.to_dict(),
        }
        description = bot.get("bot_desc")
        if description:
            payload["description"] = description
        return payload

    def _post_teclaw(
        self, *, path: str, payload: Dict[str, Any], action: str
    ) -> Dict[str, Any]:
        """POST a teclaw create/update payload, normalizing errors to
        :class:`BaasServiceError`."""
        try:
            return self._post_bots_api(path=path, payload=payload, action=action)
        except httpx.HTTPStatusError as e:
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error("[BaasService.%s] failed: %s", action, e)
            raise BaasServiceError(f"{action} failed: {e}")

    def destroy_bot(
        self,
        bot_uuid: str,
        operator: str,
        request_id: str,
    ) -> Dict[str, Any]:
        """调用 BaaS 层 API 销毁 Bot。

        参考: /Users/pingwu/teamclaw/secbaas/docs/API.md

        Args:
            bot_uuid: Bot UUID
            operator: 操作者用户 ID
            request_id: 请求 ID（32-64字符，字母/数字/连字符/下划线）

        Returns:
            BaaS 层返回的信息，包含：
            - bot_uuid: Bot UUID
            - publish_id: 销毁工作流 ID
            - request_id: 请求 ID

        Raises:
            BaasServiceError: 销毁失败
        """
        logger.info(
            f"[BaasService.destroy_bot] "
            f"Destroying bot in BaaS: bot_uuid={bot_uuid}, operator={operator}, request_id={request_id}"
        )

        payload = {
            "operator": operator,
            "request_id": request_id,
            # All-auto approval (#197): destroy previously relied on a client-side
            # approve after the call; under all-auto BaaS approves the DESTROY
            # workflow server-side and the client approve is removed.
            "auto_approve_publish": True,
        }

        logger.info(
            f"[BaasService.destroy_bot] "
            f"Upgrading bot in BaaS: bot_uuid={bot_uuid}, operator={operator}, request_id={request_id}, payload={payload}"
        )

        try:
            result = self._post_bots_api(
                path=f"/api/v1/bots/{bot_uuid}/destroy",
                payload=payload,
                action="destroy_bot",
            )
            logger.info(
                f"[BaasService.destroy_bot] "
                f"Bot destroy initiated: bot_uuid={bot_uuid}, publish_id={result.get('publish_id')}"
            )
            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.destroy_bot] "
                f"HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BaasService.destroy_bot] "
                f"Failed to destroy bot in BaaS: {e}"
            )
            raise BaasServiceError(f"Failed to destroy bot in BaaS: {e}")

    def get_bot(
        self,
        bot_uuid: str,
        health_check: bool = False,
        engine_type: str = "",
    ) -> Dict[str, Any]:
        """查询 BaaS 层 bot 详情 / 状态。

        Args:
            bot_uuid: bot 唯一标识
            health_check: True 时 BaaS 经 PaaS 层实时探活，刷新 devices 的
                status/health；False（默认）返回 DB 态，行为与原先一致。
            engine_type: 当前 bot 引擎类型；非空时作为 query 透传，留空则不传。

        Returns:
            BaaS 层返回的 data 字段（含 ``devices[]``、``bot_uuid``、``status`` 等）。
        """
        if not bot_uuid:
            raise BaasServiceError("bot_uuid is required for get_bot")

        logger.info(
            f"[BaasService.get_bot] Getting bot in BaaS: bot_uuid={bot_uuid}, "
            f"tenant={self._tenant}, health_check={health_check}, engine_type={engine_type}"
        )

        params: Optional[Dict[str, Any]] = None
        extra: Dict[str, Any] = {}
        if health_check:
            extra["health_check"] = "true"
        if engine_type:
            extra["engine_type"] = engine_type
        if extra:
            params = extra

        try:
            return self._get_bots_api(
                path=f"/api/v1/bots/{bot_uuid}",
                action="get_bot",
                params=params,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(
                    f"[BaasService.get_bot] Bot not found, fallback to RELEASED: bot_uuid={bot_uuid}, tenant={self._tenant}"
                )
                return {"status": "RELEASED"}

            logger.error(
                f"[BaasService.get_bot] HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BaasService.get_bot] Failed to get bot in BaaS: {e}"
            )
            raise BaasServiceError(f"Failed to get bot in BaaS: {e}")

    def stop_bot(
        self,
        bot_uuid: str,
        operator: str,
        request_id: str,
        auto_approve_publish: bool = True,
    ) -> Dict[str, Any]:
        """调用 BaaS 层 API 销毁 Bot。

        参考: /Users/pingwu/teamclaw/secbaas/docs/API.md

        Args:
            bot_uuid: Bot UUID
            operator: 操作者用户 ID
            request_id: 请求 ID（32-64字符，字母/数字/连字符/下划线）
            auto_approve_publish: bool = True,

        Returns:
            BaaS 层返回的信息，包含：
            - bot_uuid: Bot UUID
            - publish_id: 销毁工作流 ID
            - request_id: 请求 ID

        Raises:
            BaasServiceError: 销毁失败
        """
        logger.info(
            f"[BaasService.stop_bot] "
            f"Stopping bot in BaaS: bot_uuid={bot_uuid}, operator={operator}, "
            f"request_id={request_id}, tenant={self._tenant}, "
            f"auto_approve_publish={auto_approve_publish}"
        )

        payload = {
            "operator": operator,
            "request_id": request_id,
            "auto_approve_publish": auto_approve_publish,
        }

        logger.info(
            f"[BaasService.stop_bot] "
            f"Upgrading bot in BaaS: bot_uuid={bot_uuid}, operator={operator}, request_id={request_id}, payload={payload}"
        )

        try:
            result = self._post_bots_api(
                path=f"/api/v1/bots/{bot_uuid}/stop",
                payload=payload,
                action="stop_bot",
            )
            logger.info(
                f"[BaasService.stop_bot] "
                f"Bot destroy initiated: bot_uuid={bot_uuid}, publish_id={result.get('publish_id')}"
            )
            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.stop_bot] "
                f"HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BaasService.stop_bot] "
                f"Failed to destroy bot in BaaS: {e}"
            )
            raise BaasServiceError(f"Failed to stop bot in BaaS: {e}")

    def scale_bot(
        self,
        bot_uuid: str,
        owner_id: str,
        request_id: str,
        target_count: int,
        auto_approve_publish: bool = False,
    ) -> Dict[str, Any]:
        """调用 BaaS 层 API 扩缩容 Bot。

        调用 POST /api/v1/bots/{bot_uuid}/scale

        Args:
            bot_uuid: Bot UUID
            owner_id: 操作者用户 ID
            request_id: 请求 ID
            target_count: 目标设备数量

        Returns:
            BaaS 层返回的信息，通常包含：
            - bot_uuid
            - target_count
            - publish_id
            - request_id

        Raises:
            BaasServiceError: 扩缩容失败
        """
        if not bot_uuid:
            raise BaasServiceError("bot_uuid is required for scaling bot")
        if not owner_id:
            raise BaasServiceError("owner_id is required for scaling bot")
        if not request_id:
            raise BaasServiceError("request_id is required for scaling bot")
        if target_count < 1:
            raise BaasServiceError("target_count must be greater than 0")

        logger.info(
            f"[BaasService.scale_bot] "
            f"Scaling bot in BaaS: bot_uuid={bot_uuid}, operator={owner_id}, "
            f"request_id={request_id}, target_count={target_count}"
        )

        payload = {
            "target_count": target_count,
            "operator": owner_id,
            "request_id": request_id,
            "auto_approve_publish": auto_approve_publish,
        }

        logger.info(
            f"[BaasService.scale_bot] "
            f"Scaling bot in BaaS: bot_uuid={bot_uuid}, operator={owner_id}, "
            f"request_id={request_id}, payload={payload}"
        )

        try:
            return self._post_bots_api(
                path=f"/api/v1/bots/{bot_uuid}/scale",
                payload=payload,
                action="scale_bot",
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.scale_bot] HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(
                f"[BaasService.scale_bot] "
                f"Failed to scale bot in BaaS: {e}"
            )
            raise

    def _build_personal_bot_payload(
        self,
        *,
        bot_id: str,
        bot_name: str,
        bot_desc: str | None,
        entity_id: str,
        entity_type: str,
        owner_id: str,
        request_id: str,
        envs: Dict[str, str] | None = None,
        image_id: str | None = None,
        tenant_id: str | None = None,
        template_uuid: str | None = None,
    ) -> Dict[str, Any]:
        """构建 personal bot（poolab template）创建请求体。

        与 ``_build_create_bot_payload``（service bot / desktop bot）不同：
        poolab template 的 DeployConfig 使用 ``poolab_`` 前缀字段，
        由 BaaS 端 ``PoolabCreateConfig`` 定义并通过白名单
        ``_POOLAB_ALLOWED_OVERRIDE_FIELDS`` 过滤。

        Args:
            bot_id: ocb bot_id
            bot_name: bot 名称
            bot_desc: bot 描述（可选）
            entity_id: 实体 ID（用户/团队）
            entity_type: 实体类型
            owner_id: 创建者 ID（一般 == entity_id）
            request_id: BaaS 幂等 ID（32-64 字符）
            envs: 容器环境变量（透传 BotService 计算的 extra_envs）
            image_id: 容器镜像 ID（不传则使用 template 默认 image）
            tenant_id: poolab 租户 ID（可选）
            template_uuid: 显式指定 template uuid；不传则使用注入的
                ``personal_bot_template_uuid`` 配置

        Returns:
            BaaS POST /api/v1/bots 的请求体

        Raises:
            BaasServiceError: 未配置 ``personal_bot_template_uuid`` 且未显式传入
        """
        effective_template_uuid = template_uuid or self._personal_bot_template_uuid
        if not effective_template_uuid:
            raise BaasServiceError(
                "personal_bot_template_uuid not configured; "
                "set baas.personal_bot_template_uuid in config"
            )

        deploy_config: Dict[str, Any] = {"poolab_user_id": owner_id}
        if envs:
            deploy_config["poolab_envs"] = dict(envs)
        if image_id:
            deploy_config["poolab_image_id"] = image_id
        if tenant_id:
            deploy_config["poolab_tenant_id"] = tenant_id

        config: Dict[str, Any] = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "deploy_config": deploy_config,
        }

        payload: Dict[str, Any] = {
            "name": bot_name or bot_id,
            "template_uuid": effective_template_uuid,
            "device_count": 1,
            "operator": owner_id,
            "request_id": request_id,
            "config": config,
        }
        if bot_desc:
            payload["description"] = bot_desc

        logger.info(
            "[BaasService._build_personal_bot_payload] "
            "bot_id=%s owner_id=%s template_uuid=%s envs_keys=%s image_id=%s tenant_id=%s",
            bot_id, owner_id, effective_template_uuid,
            list(envs.keys()) if envs else [],
            image_id, tenant_id,
        )
        return payload

    def exec_command_on_bot(
        self,
        *,
        bot_uuid: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Execute a shell command inside a BaaS bot container.

        Calls ``POST /api/v1/bots/{tenant}/{bot_uuid}/execute-command``
        (secbaas bot-level endpoint — secbaas picks the device automatically).

        Args:
            bot_uuid: BaaS bot UUID.
            cmd: Shell command to execute.
            env: Optional environment variables dict.
            timeout_seconds: Command execution timeout (default 30s).

        Returns:
            ``{"exit_code": int, "stdout": str, "stderr": str,
              "execution_time_ms": int}``

        Raises:
            BaasServiceError: on HTTP or API-level error.
        """
        logger.info(
            "[BaasService.exec_command_on_bot] bot_uuid=%s cmd=%.120s timeout=%s",
            bot_uuid, cmd, timeout_seconds,
        )

        payload: dict[str, Any] = {
            "cmd": cmd,
            "timeout_seconds": timeout_seconds,
        }
        if env:
            payload["env"] = env

        try:
            response = self._http.post(
                f"/api/v1/bots/{self._tenant}/{bot_uuid}/execute-command",
                json=payload,
                timeout=float(timeout_seconds + 10),
            )
            response.raise_for_status()

            response_data = response.json()

            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS exec_command error: {response_data.get('message', 'Unknown error')}"
                )

            return response_data.get("data", {})

        except httpx.HTTPStatusError as e:
            logger.error(
                "[BaasService.exec_command_on_bot] HTTP error: %s - %s",
                e.response.status_code, e.response.text,
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error("[BaasService.exec_command_on_bot] Failed: %s", e)
            raise BaasServiceError(f"Failed to execute command on bot: {e}")

    def restart_bot(
        self,
        bot_uuid: str,
        operator: str,
        request_id: str,
        agent_code: str = "",
    ) -> Dict[str, Any]:
        """调用 BaaS 层 API 重启 Bot。

        参考: /Users/pingwu/teamclaw/secbaas/docs/API.md

        Args:
            bot_uuid: Bot UUID
            operator: 操作者用户 ID
            request_id: 请求 ID（32-64字符，字母/数字/连字符/下划线）
            agent_code: Agent 授权码，用于重启后写入沙箱 credentials

        Returns:
            BaaS 层返回的信息，包含：
            - bot_uuid: Bot UUID
            - publish_id: 重启工作流 ID
            - request_id: 请求 ID

        Raises:
            BaasServiceError: 重启失败
        """
        logger.info(
            f"[BaasService.restart_bot] "
            f"Restarting bot in BaaS: bot_uuid={bot_uuid}, operator={operator}, request_id={request_id}"
        )

        payload = {
            "operator": operator,
            "request_id": request_id,
        }

        logger.info(
            f"[BaasService.restart_bot] "
            f"Upgrading bot in BaaS: bot_uuid={bot_uuid}, operator={operator}, request_id={request_id}, payload={payload}"
        )

        try:
            response = self._http.post(
                f"/api/v1/bots/{bot_uuid}/restart",
                params={"tenant": self._tenant},
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()

            response_data = response.json()

            logger.info(
                "[BaasService.restart_bot] BaaS raw response: %s",
                response_data,
            )

            # 检查响应码
            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            result = response_data.get("data", {})

            publish_id = result.get("publish_id")

            logger.info(
                f"[BaasService.restart_bot] "
                f"Bot restart initiated: bot_uuid={bot_uuid}, publish_id={publish_id}"
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.restart_bot] "
                f"HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            logger.error(
                f"[BaasService.restart_bot] "
                f"Failed to restart bot in BaaS: {e}"
            )
            raise BaasServiceError(f"Failed to restart bot in BaaS: {e}")

    def open_folder_bot(
        self,
        bot_uuid: str,
        folder_path: str | None = None,
    ) -> dict[str, Any]:
        """调用 BaaS 层 API 打开 Bot 工作目录。

        调用 POST /api/v1/bots/{tenant}/{bot_uuid}/open-folder

        Args:
            bot_uuid: Bot UUID
            folder_path: 要打开的目录路径，None 则由 BaaS 使用默认路径

        Returns:
            BaaS 层返回的信息

        Raises:
            BaasServiceError: 调用失败
        """
        logger.info(
            "[BaasService.open_folder_bot] bot_uuid=%s folder_path=%s",
            bot_uuid, folder_path,
        )

        payload: dict[str, Any] = {}
        if folder_path is not None:
            payload["folder_path"] = folder_path

        try:
            response = self._http.post(
                f"/api/v1/bots/{self._tenant}/{bot_uuid}/open-folder",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()

            response_data = response.json()

            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            return response_data.get("data", {})

        except httpx.HTTPStatusError as e:
            logger.error(
                "[BaasService.open_folder_bot] HTTP error: %s - %s",
                e.response.status_code, e.response.text,
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error("[BaasService.open_folder_bot] Failed: %s", e)
            raise BaasServiceError(f"Failed to open folder in BaaS: {e}")

    def get_publish_progress(
        self,
        publish_id: int,
        include_devices: bool = False,
    ) -> Dict[str, Any]:
        """调用 BaaS 层 API 获取发布进度。

        参考: /Users/pingwu/teamclaw/secbaas/docs/API.md

        Args:
            publish_id: 发布工作流 ID
            include_devices: 是否包含设备详情，默认 False

        Returns:
            BaaS 层返回的发布进度信息，包含：
            - publish_id: 发布 ID
            - status: 发布状态 (INIT, PENDING, ACTIVE, APPROVING, REJECTED, FAILED, SUCCESS, REVOKED)
            - current_stage: 当前阶段
            - overall_progress: 整体进度
            - stages: 阶段列表
            - device_details: 设备详情（可选）
            - failed_devices: 失败设备列表

        Raises:
            BaasServiceError: 查询失败
        """
        logger.info(
            f"[BaasService.get_publish_progress] "
            f"Getting publish progress: publish_id={publish_id}, include_devices={include_devices}"
        )

        params = {"tenant": self._tenant}
        if include_devices:
            params["include_devices"] = "true"

        try:
            response = self._http.get(
                f"/api/v1/publishes/{publish_id}/progress",
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()

            response_data = response.json()

            logger.info(
                "[BaasService.get_publish_progress] BaaS raw response: %s",
                response_data,
            )

            # 检查响应码
            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            result = response_data.get("data", {})

            status = result.get("status")
            current_stage = result.get("current_stage")
            progress_percentage = result.get("overall_progress", {}).get("progress_percentage", 0)

            logger.info(
                f"[BaasService.get_publish_progress] "
                f"Publish progress: publish_id={publish_id}, status={status}, "
                f"current_stage={current_stage}, progress={progress_percentage}%"
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.get_publish_progress] "
                f"HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            logger.error(
                f"[BaasService.get_publish_progress] "
                f"Failed to get publish progress: {e}"
            )
            raise BaasServiceError(f"Failed to get publish progress: {e}")

    def get_ws_info(
        self,
        bind_id: int,
        port: int = 20003,
        path: str = "api/openclaw/ws",
        tenant: str = "",
        device_affinity: Optional[str] = None,
        device_uuid: Optional[str] = None,
        ws_conn_mode: Optional[str] = None,
    ) -> BotWsConnectionInfoResponse:
        """获取 WebSocket 连接信息.

        调用 GET /api/v1/bots/{device_id}/ws-info?tenant={tenant}&port={port}&path={path}

        Args:
            bind_id: 设备绑定 ID
            port: 目标端口，默认 20003
            path: WebSocket 路径，默认 api/openclaw/ws
            tenant: 租户名称；空则回落到 self._tenant（BaasConfig.tenant，各部署配置）
            device_affinity: 设备亲和性标识，用于指定目标设备（可选）
            device_uuid: 多实例场景锁定特定实例（可选）；不传则 BaaS 自动选活跃实例
            ws_conn_mode: WebSocket 连接模式透传（可选）；不传则不覆盖

        Returns:
            BotWsConnectionInfoResponse: WebSocket 连接信息

        Raises:
            BaasServiceError: 获取失败或绑定记录不存在
        """
        # 从 DeviceBindingRepository 获取 device_id
        binding = self._device_binding_repo.get_by_id(bind_id)
        if binding is None:
            raise BaasServiceError(f"Device binding not found: bind_id={bind_id}")

        return self.get_ws_info_by_bot_uuid(
            bot_uuid=binding.device_id,
            port=port,
            path=path,
            tenant=tenant,
            device_affinity=device_affinity,
            device_uuid=device_uuid,
            ws_conn_mode=ws_conn_mode,
        )

    def get_ws_info_by_bot_uuid(
        self,
        bot_uuid: str,
        port: int = 20003,
        path: str = "api/openclaw/ws",
        tenant: str = "",
        device_affinity: Optional[str] = None,
        device_uuid: Optional[str] = None,
        ws_conn_mode: Optional[str] = None,
    ) -> BotWsConnectionInfoResponse:
        """获取 WebSocket 连接信息（通过 bot_uuid 直接查询）.

        调用 GET /api/v1/bots/{bot_uuid}/ws-info?tenant={tenant}&port={port}&path={path}

        Args:
            bot_uuid: Bot UUID（= device_id）
            port: 目标端口，默认 20003
            path: WebSocket 路径，默认 api/openclaw/ws
            tenant: 租户名称；空则回落到 self._tenant（BaasConfig.tenant，各部署配置）
            device_affinity: 设备亲和性标识，用于指定目标设备（可选）
            device_uuid: 多实例场景锁定特定实例（可选）；不传则 BaaS 自动选活跃实例
            ws_conn_mode: WebSocket 连接模式透传（可选）；不传则不覆盖

        Returns:
            BotWsConnectionInfoResponse: WebSocket 连接信息

        Raises:
            BaasServiceError: 获取失败
        """
        effective_tenant = tenant or self._tenant
        logger.info(
            f"[BaasService.get_ws_info_by_bot_uuid] "
            f"Getting ws info: bot_uuid={bot_uuid}, tenant={effective_tenant}, "
            f"port={port}, path={path}, device_affinity={device_affinity}, device_uuid={device_uuid}"
        )

        params = {
            "tenant": effective_tenant,
            "port": port,
            "path": path,
        }
        if device_affinity:
            params["device_affinity"] = device_affinity
        if device_uuid:
            params["device_uuid"] = device_uuid
        if ws_conn_mode:
            params["ws_conn_mode"] = ws_conn_mode

        try:
            response = self._http.get(
                f"/api/v1/bots/{bot_uuid}/ws-info",
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()

            response_data = response.json()

            # 检查响应码
            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            data = response_data.get("data", {})
            result = BotWsConnectionInfoResponse(
                ws_url=data["ws_url"],
                token=data["token"],
                target=data["target"],
                expires_at=data["expires_at"],
                paas_device_id=bot_uuid,
                baas_base_url=self._baas_api_base,
                tenant=effective_tenant,
                bot_uuid=bot_uuid,
                engine_port=port,
            )

            logger.info(
                f"[BaasService.get_ws_info_by_bot_uuid] "
                f"Got ws info: bot_uuid={bot_uuid}, target={result.target}"
            )

            return result

        except httpx.HTTPStatusError as e:
            # WARNING, not ERROR: every HTTP error here is re-raised as
            # BaasServiceError and the callers (connection handler, device
            # plugin, identity) all catch and degrade gracefully. The common
            # cases — 404 BOT_NOT_FOUND (bot released/expired) and 503
            # NO_ACTIVE_DEVICES (device still coming up) — are expected,
            # self-healing business states, not faults; logging them as ERROR
            # only floods the alarm/ticket pipeline. Whether a failure is truly
            # alarm-worthy is the caller's call, made where the business context
            # is known.
            #
            # The BaaS app only ever answers this endpoint with JSON
            # (200/404/503/500); a 3xx status here is injected by the fronting
            # gateway (Spanner) before the request reaches BaaS. Its ``Location``
            # says where the request is being redirected (login/SSO host vs a
            # moved route), and bot_uuid/tenant/device_affinity let us cluster
            # intermittent redirects by bot/tenant/target device — the data
            # needed to tell a partial-instance/routing fault apart from a
            # blanket auth requirement.
            location = e.response.headers.get("location")
            logger.warning(
                f"[BaasService.get_ws_info_by_bot_uuid] "
                f"HTTP error: {e.response.status_code} - "
                f"bot_uuid={bot_uuid}, tenant={effective_tenant}, "
                f"device_affinity={device_affinity}, location={location!r} - "
                f"{e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            logger.error(
                f"[BaasService.get_ws_info_by_bot_uuid] "
                f"Failed to get ws info: {e}"
            )
            raise BaasServiceError(f"Failed to get ws info: {e}")

    def list_bots(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> tuple[int, list[dict]]:
        """调用 BaaS 层 API 获取 Bot 列表。

        参考 BAAS API: GET /api/v1/bots

        Args:
            page: 页码，从 1 开始
            page_size: 每页数量
            status: 按状态过滤（可选）

        Returns:
            (total, items) 元组，items 是 Bot 信息字典列表

        Raises:
            BaasServiceError: 查询失败
        """
        params: dict[str, Any] = {
            "tenant": self._tenant,
            "page": page,
            "page_size": page_size,
        }
        if status:
            params["status"] = status

        logger.info(
            f"[BaasService.list_bots] "
            f"Listing bots: page={page}, page_size={page_size}, status={status}"
        )

        try:
            response = self._http.get(
                "/api/v1/bots",
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()

            response_data = response.json()

            # 检查响应码
            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            data = response_data.get("data", {})
            total = data.get("total", 0)
            items = data.get("items", [])

            logger.info(
                f"[BaasService.list_bots] "
                f"Listed bots: total={total}, returned={len(items)}"
            )

            return total, items

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.list_bots] "
                f"HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            logger.error(
                f"[BaasService.list_bots] "
                f"Failed to list bots: {e}"
            )
            raise BaasServiceError(f"Failed to list bots: {e}")

    def list_bot_publishes(self, bot_uuid: str) -> List[Dict[str, Any]]:
        """List every publish workflow associated with a bot_uuid (including
        terminal ones), newest first.

        Maps to BaaS ``GET /api/v1/bots/{bot_uuid}/publishes``. Used by the
        idempotent-recovery adopt-by-query differencing: the returned workflow ids
        are differenced against the ids the local ledger has already claimed, and an
        unclaimed one is this operation's in-doubt workflow.

        Each element carries: ``id`` (workflow id), ``bot_id``, ``publish_type``,
        ``status``, ``gmt_create``.

        A 404 (bot_uuid unknown) is deliberately mapped to ``[]`` rather than
        raised: this endpoint is bot-scoped, so a 404 means "this bot has no
        publish workflows" — which is exactly the "no candidate to adopt" signal
        adopt-by-query needs. It is load-bearing for the destroyed-bot cases: e.g.
        an upgrade whose target bot is gone snapshots an empty baseline here and
        then falls back to a first-release on the ``BOT_NOT_FOUND`` from the issue;
        raising on the 404 would break that fallback. Non-404 failures still raise.

        Raises:
            BaasServiceError: on any non-404 call failure.
        """
        try:
            data = self._get_bots_api(
                f"/api/v1/bots/{bot_uuid}/publishes",
                action="list_bot_publishes",
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info(
                    "[BaasService.list_bot_publishes] bot_uuid=%s not found (404) -> []",
                    bot_uuid,
                )
                return []
            logger.error(
                "[BaasService.list_bot_publishes] HTTP error: %s - %s",
                e.response.status_code, e.response.text,
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        # A list `data` deserializes straight through _get_bots_api's data extract.
        return data if isinstance(data, list) else []

    def get_bind_id(
        self,
        bot_id: str,
        owner_id: str,
        bot_type: str,
        publish_status: Optional[str] = None,
    ) -> Optional[int]:
        """根据发布状态获取绑定 ID。

        Args:
            bot_id: Bot ID
            owner_id: 所有者 ID
            bot_type: Bot 类型（service 表示服务型 Bot）
            publish_status: 发布状态（PublishStatus 枚举值）

        Returns:
            绑定 ID，如果不存在返回 None

        Raises:
            BaasServiceError: 获取失败
        """
        # 延迟导入避免循环依赖
        from agentclaw.community.core.service_bot.repository.models import (
            PublishStatus,
            select_stage_bind_id,
        )

        logger.info(
            f"[BaasService.get_bind_id] bot_id={bot_id}, owner_id={owner_id}, "
            f"bot_type={bot_type}, publish_status={publish_status}"
        )

        # DRAFT 状态或无状态：从 bot 数据中获取 bind_id
        if publish_status == PublishStatus.DRAFT.value or publish_status is None:
            bot = self._bot_repo.get_by_id_and_owner(bot_id=bot_id, owner_id=owner_id)
            if bot is None:
                logger.warning(f"[BaasService.get_bind_id] Bot not found: bot_id={bot_id}")
                return None
            bind_id = bot.get("bind_id") or bot.get("binding_id")
            if bind_id:
                logger.info(f"[BaasService.get_bind_id] Got bind_id from bot: {bind_id}")
            return bind_id

        # VALIDATING/SUCCESS 等状态：从发布单扩展字段获取
        from agentclaw.community.utils.env_utils import get_current_env
        env = get_current_env()
        publish_record = self._bot_publish_repo.get_by_publish_bot_id(
            publish_bot_id=bot_id,
            owner_id=owner_id,
            env=env,
            publish_status=publish_status,
        )
        if publish_record is None:
            logger.warning(f"[BaasService.get_bind_id] Publish record not found: bot_id={bot_id}")
            return None

        ext = publish_record.ext or {}
        binding_info = ext.get("binding", {})

        # 根据状态选择对应的 binding
        bind_id = select_stage_bind_id(binding_info, publish_status)

        if bind_id:
            logger.info(f"[BaasService.get_bind_id] Got bind_id from publish ext: {bind_id}")
        return bind_id

    def approve_publish(
        self,
        publish_id: int,
        operator: str,
        request_id: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用 BaaS 层 API 审批发布单当前阶段。

        参考: POST /api/v1/publishes/{publish_id}/approve

        Args:
            publish_id: BaaS 层发布工作流 ID
            operator: 审批操作者用户 ID
            request_id: 请求 ID，用于幂等性控制
            comment: 可选审批备注

        Returns:
            BaaS 层返回的发布单信息，包含：
            - publish_id: 发布 ID
            - status: 发布状态
            - 其他发布单字段

        Raises:
            BaasServiceError: 审批失败
        """
        logger.info(
            f"[BaasService.approve_publish] "
            f"Approving publish: publish_id={publish_id}, operator={operator}, request_id={request_id}"
        )

        payload = {
            "operator": operator,
            "request_id": request_id,
        }
        if comment:
            payload["comment"] = comment

        try:
            response = self._http.post(
                f"/api/v1/publishes/{publish_id}/approve",
                params={"tenant": self._tenant},
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()

            response_data = response.json()

            logger.info(
                "[BaasService.approve_publish] BaaS raw response: %s",
                response_data,
            )

            # 检查响应码
            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            result = response_data.get("data", {})

            logger.info(
                f"[BaasService.approve_publish] "
                f"Publish approved: publish_id={publish_id}, status={result.get('status')}"
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.approve_publish] "
                f"HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )

        except Exception as e:
            logger.error(
                f"[BaasService.approve_publish] "
                f"Failed to approve publish: {e}"
            )
            raise BaasServiceError(f"Failed to approve publish: {e}")

    def _get_start_cmd(
            self,
            bot_id: str,
            owner_id: str,
            entity_id: str,
            entity_type: str,
            migration_pat: str,
            bot_type: str,
            engine: str,
            stage: str | None = PublishStage.ONLINE.value,
            version: str | None = "1",
            mount_home_dir_storage: bool = False,
            ext_info: Optional[Dict[str, Any]] = None,
    ):
        # 1、Bootstrap 补偿脚本
        bootstrap_cmp = self._get_bootstrap_cmp()

        # 2、安装引擎
        install_engine_cmd = self._get_install_engine_cmd()

        # 3、设置同步服务
        # setup_cmd = self._get_setup_sync_service_cmd(engine)

        # 4、 确保引擎目录存在
        # mkdir_cmd = self._get_mkdir_engine_dir_cmd(engine)

        # 5、 启动同步服务
        start_cmd = self._get_start_sandbox_service_cmd(
            engine, migration_pat, bot_type, bot_id, owner_id, entity_id, entity_type, stage, version, mount_home_dir_storage, ext_info
        )

        # 6、 Start watchdog
        watchdog_cmd = self._get_start_watchdog_cmd()

        return (
            f"{bootstrap_cmp} && ({install_engine_cmd}) && "
            f" {start_cmd} && {watchdog_cmd}"
        )

    def _get_destroy_cmd(
            self
    ):

        # 检查是否开启销毁前调用脚本功能
        invoke_enabled = False
        if self._system_config_service:
            try:
                from agentclaw.community.utils import env_utils
                current_env = env_utils.get_current_env()
                invoke_rsync = self._system_config_service.get_config(
                    config_key="invoke_rsync_before_release",
                    category="system",
                    env=current_env,
                )
                invoke_enabled = str(invoke_rsync).lower() == "true" if invoke_rsync else False
            except Exception as e:
                logger.warning(f"[BaasService.destroy_bot] Failed to get invoke_rsync_before_release config: {e}")
                invoke_enabled = False

        if not invoke_enabled:
            return None

        return "supervisorctl stop sync"

    def _get_bootstrap_cmp(self):
        """执行 bootstrap 补偿脚本。"""
        return "su admin -c 'bash /home/admin/bin/bootstrap_minimal.sh'"

    def _get_setup_sync_service_cmd(self, engine: str = ""):
        """执行 setup_supervisor_sync_service.sh 脚本。"""
        setup_cmd = f"bash /home/admin/bin/setup_supervisor_sync_service.sh {engine}"
        logger.info(f"[_get_setup_sync_service_cmd] Executing cmd: {setup_cmd}")
        return setup_cmd

    def _get_install_engine_cmd(self):
        """执行 install_engine.sh 脚本。

        install_engine.sh 是从 start_service.sh 拆分出来的，
        负责安装/更新引擎二进制并落盘 marker 文件，setup_supervisor_sync_service.sh
        会等待该 marker 才继续。BaaS 路径通过 && 串联各步，install_engine
        同步执行即可，无需 nohup；存在性保护用于 backend 先发版而 daas-script
        旧镜像尚无该脚本的窗口。
        """
        script_path = "/home/admin/bin/install_engine.sh"
        log_path = "/home/admin/logs/install_engine.log"
        install_cmd = (
            f"if [ -f {script_path} ]; then "
            f"bash {script_path} >> {log_path} 2>&1; "
            f"else echo '[install_engine] {script_path} not found, skip'; fi"
        )
        logger.info(f"[_get_install_engine_cmd] Executing cmd: {install_cmd}")
        return install_cmd

    def _get_start_sandbox_service_cmd(
        self,
        engine: str,
        migration_path: str,
        bot_type: str,
        bot_id: str | None,
        owner_id: str | None,
        entity_id: str | None,
        entity_type: str | None,
        stage: str | None = PublishStage.ONLINE.value,
        version: str | None = "1",
        mount_home_dir_storage: bool = False,
        ext_info: Optional[Dict[str, Any]] = None,
    ):
        """启动沙箱服务。"""
        # 保留 {token} 和 {client_id} 占位符，供后续替换
        start_service_cmd = (
            f"/home/admin/bin/start_service.sh --token {{token}} --client_id {{client_id}} --bot_type {bot_type} --engine {engine}"
        )

        # 个人 Bot 和服务 Bot 草稿没有迁移目录；此时不传 --source_dir，
        # 避免 start_service.sh 把后面的 --bot_id 当成 source_dir 的值。
        if migration_path:
            start_service_cmd += f" --source_dir {migration_path}"

        if bot_id:
            start_service_cmd += f" --bot_id {bot_id}"
        if owner_id:
            start_service_cmd += f" --owner_id {owner_id}"

        if entity_id and entity_type:
            start_service_cmd += f" --entity_id {entity_id} --entity_type {entity_type}"

        if stage:
            stage_str = stage
            ext_info = ext_info or {}
            if ext_info.get("biz_id"):
                stage_str += f"-{ext_info.get("biz_id")}"
            start_service_cmd += f" --stage {stage_str}"

        if version:
            start_service_cmd += f" --version V{version}"

        # 命中 home 目录挂载白名单时，通知容器内启动脚本使用 NAS home 目录。
        start_service_cmd += f" --useNas {str(mount_home_dir_storage).lower()}"

        read_only_rules = self._get_set_read_only_rule(
            bot_id=bot_id, owner_id=owner_id,
            bot_type=bot_type, stage=stage or PublishStage.ONLINE.value,
        )
        start_service_cmd += read_only_rules

        start_cmd = f"""su admin -c 'nohup {start_service_cmd} >> /home/admin/start.log 2>&1'"""

        logger.info(f"[_get_start_sandbox_service_cmd] Executing cmd: {start_cmd}")
        return start_cmd

    def _resolve_sandbox_provider(
        self,
        bot_id: str = "",
        owner_id: str = "",
        engine: str = "",
    ) -> EngineSandboxProvider:
        """解析引擎对应的 sandbox provider。"""
        engine_type = engine or resolve_engine_for_bot(bot_id, owner_id, bot_repo=self._bot_repo)
        try:
            return self._sandbox_registry.resolve(engine_type)
        except Exception as e:
            logger.warning(
                "[_resolve_sandbox_provider] resolve failed for engine=%s, fallback to default: %s",
                engine_type,
                e,
            )
            return self._sandbox_registry.resolve(DEFAULT_ENGINE_TYPE)

    def _parse_bot_ext(self, bot: Dict[str, Any] | None) -> Dict[str, Any]:
        if not bot:
            return {}
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                return {}
        return ext if isinstance(ext, dict) else {}

    def _materialize_rule_path(self, path: str, base_path: str) -> str:
        if not path:
            return path
        if path.startswith("/"):
            return path
        return f"{base_path.rstrip('/')}/{path}"

    def _materialize_default_rules(
        self,
        provider: EngineSandboxProvider,
    ) -> list[dict[str, str]]:
        base_path = provider.get_base_path()
        result: list[dict[str, str]] = []
        for rule in provider.get_default_read_only_rules():
            result.append({
                "path": self._materialize_rule_path(rule.path, base_path),
                "rule_type": rule.rule_type,
            })
        return result

    def _normalize_custom_read_only_rules(
        self,
        rules: Any,
        *,
        base_path: str,
    ) -> list[dict[str, str]]:
        if not isinstance(rules, list):
            return []

        result: list[dict[str, str]] = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not path or not isinstance(path, str):
                continue
            rule_type = item.get("rule_type", "file")
            result.append({
                "path": self._materialize_rule_path(path, base_path),
                "rule_type": rule_type,
            })
        return result

    def _dedupe_read_only_rules(self, rules: list[dict[str, str]]) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        result: list[dict[str, str]] = []
        for rule in rules:
            key = (rule.get("path", ""), rule.get("rule_type", "file"))
            if key in seen:
                continue
            seen.add(key)
            result.append(rule)
        return result

    def _get_set_read_only_rule(self, bot_id: str = "", owner_id: str = "", engine: str = "",
                                bot_type: str = "service", stage: str = "online"):
        """返回只读规则，拼接为 --set_read_only 参数。

        可编辑(personal / service草稿)不锁:容器内仍需写 mcporter 等配置;
        只有 service 发布 online 才锁。判定收口 is_editable_bot。
        """
        if is_editable_bot(bot_type, stage):
            return ""
        provider = self._resolve_sandbox_provider(bot_id=bot_id, owner_id=owner_id, engine=engine)
        base_path = provider.get_base_path()
        default_rules = self._materialize_default_rules(provider)

        custom_rules: list[dict[str, str]] = []
        if bot_id and owner_id:
            try:
                bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
                ext = self._parse_bot_ext(bot)
                custom_rules = self._normalize_custom_read_only_rules(
                    ext.get("read_only_rules", []),
                    base_path=base_path,
                )
            except Exception as e:
                logger.warning(
                    "[_get_set_read_only_rule] Failed to query custom rules: %s", e
                )

        all_rules = self._dedupe_read_only_rules(default_rules + custom_rules)
        all_paths = [r["path"] for r in all_rules if r.get("path")]
        if not all_paths:
            return ""
        return f" --set_read_only {','.join(all_paths)}"

    def _get_start_watchdog_cmd(
            self,
    ):
        # Start watchdog
        # 保留 {token} 和 {client_id} 占位符，供后续替换
        watchdog_cmd = "/home/admin/bin/starting_watchdog.sh --token {token} --client_id {client_id}"
        exec_watchdog_cmd = (
            f"""su admin -c 'nohup {watchdog_cmd} >> /home/admin/logs/starting_watchdog.log 2>&1'"""
        )
        logger.info(f"[_get_start_watchdog_cmd] Executing cmd: {exec_watchdog_cmd}")
        return exec_watchdog_cmd

    def _get_mkdir_engine_dir_cmd(self, engine: str) -> str:
        """确保引擎目录存在，兼容旧设备缺少引擎级 NAS 挂载的情况。

        对于旧设备，通过 symlink 将 /home/admin/.{engine} 指向
        /home/admin/nfs/bot-data/{engine}（通用 NAS 挂载下的子目录）。
        """
        engine_dir = f"/home/admin/.{engine}"
        nfs_engine_dir = f"/home/admin/nfs/bot-data/{engine}"
        cmd = (
            f"test -d {engine_dir} || "
            f"(mkdir -p {nfs_engine_dir} && ln -sfn {nfs_engine_dir} {engine_dir}) ; "
        )
        logger.info(f"[_ensure_engine_dirs] Executing cmd: {cmd}")
        return cmd

    def _setup_directory(
        self,
        entity_id: str,
        entity_type: str,
        bot_id: str,
        engine_type: str = DEFAULT_ENGINE_TYPE,
        mount_path: Optional[str] = None,
        owner_id: str = "",
        mount_home_dir_storage: bool | None = None,
    ) -> list[MountPointEntry]:
        """使用 OSS 创建用户目录结构，返回 Arca MountPoint 配置。

        Args:
            entity_id: 实体 ID
            entity_type: 实体类型
            bot_id: Bot ID
            engine_type: 引擎类型，默认 "openclaw"
            mount_path: 用户自定义 NAS 挂载路径（可选，追加到 mount_points）
        """
        sp = self._storage_path
        bolt_data = sp.get_bolt_data_path(entity_type=entity_type, entity_id=entity_id, bot_id=bot_id)
        skill_repo = sp.get_skills_repo_path()

        # 引擎感知：通过 EngineSandboxProvider 动态解析 skills 挂载本地路径
        # （base_path/{skill_target_relpath}/skills-repo），避免硬编码 openclaw
        provider = self._resolve_sandbox_provider(engine=engine_type)
        base_path = provider.get_base_path()
        build_plan = provider.get_build_plan()
        skills_local_dir = f"{base_path}/{build_plan.skill_target_relpath}/skills-repo"

        # OSS 挂载点必须位于 /home/admin/nfs/ 下；引擎专用目录
        # （/home/admin/.{engine}、/home/admin/.config/{engine}）由
        # _ensure_engine_dirs() 在沙箱内通过 symlink 指向 bot-data 子目录。

        mount_points = [
            # agentclaw-sys 挂载
            MountPointEntry(
                remote_dir="/agentclaw-sys",
                local_dir="/mnt/sys",
                permission="READ_ONLY",
            ),
        ]

        # skill repo独立挂载
        # bolt 配置数据独立挂载
        if mount_home_dir_storage is None:
            mount_home_dir_storage = bool(owner_id) and self._should_mount_home_dir_storage(
                owner_id=owner_id,
                bot_id=bot_id,
            )

        if mount_home_dir_storage:
            mount_points.append(
                MountPointEntry(
                    remote_dir=f"/{bolt_data}",
                    local_dir="/opt/nfs/bot-data",
                    permission="READ_WRITE",
                ),
            )
        else:
            mount_points.extend(
                [
                    MountPointEntry(
                        remote_dir=f"/{bolt_data}",
                        local_dir="/home/admin/nfs/bot-data",
                        permission="READ_WRITE",
                    ),
                    MountPointEntry(
                        remote_dir=f"/{skill_repo}",
                        local_dir=skills_local_dir,
                        permission="READ_ONLY",
                    ),
                ]
            )

        # 用户自定义挂载路径
        if mount_path:
            mount_points.append(
                MountPointEntry(
                    remote_dir=mount_path,
                    local_dir=mount_path,
                    permission="READ_WRITE",
                ),
            )
            logger.info(
                f"[BaasService._setup_directory] Added custom mount_path: {mount_path}"
            )

        return mount_points

    def _setup_bot_storage(
            self,
            entity_id: str,
            entity_type: str,
            owner_id: str,
            bot_id: str,
            engine_type: str = DEFAULT_ENGINE_TYPE,
            mount_home_dir_storage: bool | None = None,
            bot_type: str = "",
            stage: str = "",
    ) -> Storage:
        """根据通用白名单配置选择 Bot 的 NAS storage 挂载目录。"""
        if mount_home_dir_storage is None:
            mount_home_dir_storage = self._should_mount_home_dir_storage(
                owner_id=owner_id,
                bot_id=bot_id,
            )

        if mount_home_dir_storage:
            logger.info(
                "[BaasService._setup_bot_storage] Use home dir storage: "
                "owner_id=%s, bot_id=%s, engine_type=%s",
                owner_id,
                bot_id,
                engine_type,
            )
            return self._setup_home_dir_storage(
                entity_id=entity_id,
                entity_type=entity_type,
                bot_id=bot_id,
                engine_type=engine_type,
                device_scoped_home_storage=self._requires_device_scoped_home_storage(
                    bot_type=bot_type,
                    stage=stage,
                ),
            )

        logger.info(
            "[BaasService._setup_bot_storage] Use sessions dir storage: "
            "owner_id=%s, bot_id=%s, engine_type=%s",
            owner_id,
            bot_id,
            engine_type,
        )
        return self._setup_sessions_dir(
            entity_id=entity_id,
            entity_type=entity_type,
            bot_id=bot_id,
            engine_type=engine_type,
        )

    def _should_mount_home_dir_storage(self, *, owner_id: str, bot_id: str) -> bool:
        """判断当前 bot 是否切换到 home 目录挂载。

        白名单配置读取失败时 fail closed，保持原 session 目录挂载。
        """
        try:
            from agentclaw.community.utils.env_utils import get_current_env

            return self._common_whitelist_service.is_bot_feature_enabled(
                business_code=ENGINE_DIR_MOUNT_WHITELIST_BUSINESS_CODE,
                param_code=ENGINE_DIR_MOUNT_WHITELIST_PARAM_CODE,
                owner_id=owner_id,
                bot_id=bot_id,
                env=get_current_env(),
                default=False,
            )
        except Exception as exc:
            logger.warning(
                "[BaasService._should_mount_home_dir_storage] "
                "Failed to check whitelist, fallback to sessions dir: "
                "owner_id=%s, bot_id=%s, error=%s",
                owner_id,
                bot_id,
                exc,
            )
            return False

    def _setup_sessions_dir(
            self,
            entity_id: str,
            entity_type: str,
            bot_id: str,
            engine_type: str = DEFAULT_ENGINE_TYPE,
    ) -> Storage:

        # sessions NAS 远端路径（{device_uuid} 占位符由 BaaS 层赋值，service bot 多副本按设备隔离）。
        # 重启不丢 session 改由原地 restart 保证（device_uuid 不变），不靠去掉后缀。
        from agentclaw.community.core.workspace.path_factory import get_bot_nas_storage_id
        nas_storage_id = get_bot_nas_storage_id(
            entity_id=entity_id, bot_id=bot_id, engine_type=engine_type, entity_type=entity_type,
        )
        sessions_storage_id = f"{nas_storage_id}_{{device_uuid}}"

        # 引擎感知：sessions 目录由 EngineSandboxProvider 自描述,
        # BaasService 不再拼接引擎相关的子路径约定。
        provider = self._resolve_sandbox_provider(engine=engine_type)
        sessions_dir = provider.get_sessions_dir()

        storage = Storage(
            type="nas",
            storage_id=sessions_storage_id,
            quota="1Gi",
            permission="0777",
            path=sessions_dir,
        )

        return storage

    def _setup_home_dir_storage(
            self,
            entity_id: str,
            entity_type: str,
            bot_id: str,
            engine_type: str = DEFAULT_ENGINE_TYPE,
            device_scoped_home_storage: bool = False,
    ) -> Storage:

        # 个人 Bot / 草稿服务 Bot 只有一个运行态来源，home 目录复用 bot 级 NAS。
        # 预发/生产服务 Bot 支持多实例，每台 BaaS 设备要隔离自己的 home NAS。
        from agentclaw.community.core.workspace.path_factory import get_bot_nas_storage_id
        nas_storage_id = get_bot_nas_storage_id(
            entity_id=entity_id, bot_id=bot_id, engine_type=engine_type, entity_type=entity_type,
        )
        if device_scoped_home_storage:
            nas_storage_id = f"{nas_storage_id}_{{device_uuid}}"

        storage = Storage(
            type="nas",
            storage_id=nas_storage_id,
            quota="1Gi",
            permission="0777",
            path="/home/admin",
        )

        return storage

    @staticmethod
    def _requires_device_scoped_home_storage(*, bot_type: str, stage: str) -> bool:
        """预发/生产服务 Bot 支持多实例，home NAS 需要按 BaaS device_uuid 隔离。"""
        return (
            (bot_type or "").strip().lower() == "service"
            and (stage or "").strip().lower()
            in {PublishStage.VERIFY.value, PublishStage.ONLINE.value, PublishStage.EVAL.value}
        )

    def _resolve_bot_type(self, bot_id: str, owner_id: str) -> str | None:
        """根据 bot_id + owner_id 查询 bot_type（供 outbound rule 选择 secret）。"""
        bot = self._bot_repo.get_by_id_and_owner(
            bot_id=bot_id, owner_id=owner_id
        )
        return (bot or {}).get("bot_type")

    def _build_outbound_operation_rule(
        self,
        bot_id: str,
        owner_id: str,
        agent_pass_token: str = "",
        agent_code: str = "",
    ) -> OutBoundOperationRule:
        """构建出站操作规则 — 委托给注入的 ``OutboundRuleProvider`` (Rule 20)。

        - corp → ProdOutboundRuleProvider (AntGroup 网关域名 + Mist secret);
        - community / singlebox / pytest → 空规则 (无出站 header 注入)。

        各 profile 的行为完全由其 provider 实现决定,core 不做任何 None 回退。
        """
        return self._outbound_rule_provider.build_rule(
            bolt_id=bot_id,
            device_id="",
            owner_id=owner_id,
            agent_pass_token=agent_pass_token,
            agent_code=agent_code,
            bot_type_resolver=self._resolve_bot_type,
        )

    def _build_teclaw_outbound_operation_rule(
        self,
        *,
        agent_pass_token: str = "",
    ) -> OutBoundOperationRule | None:
        """Teclaw 出站规则 — 委托给注入的 ``OutboundRuleProvider``。

        是否返回 ``None`` 由 provider 实现决定(community plugin 返 ``None``)。
        """
        return self._outbound_rule_provider.build_agentpass_rule(
            agent_pass_token=agent_pass_token,
        )

    def update_teclaw_outbound_rule_by_bot_uuid(
        self,
        bot_uuid: str,
        *,
        agent_pass_token: str = "",
    ) -> list[dict[str, Any]]:
        """创建/发布后按 BaaS bot_uuid 更新 Teclaw PaaS 设备出站规则。"""
        outbound_rule = self._build_teclaw_outbound_operation_rule(
            agent_pass_token=agent_pass_token,
        )
        if outbound_rule is None:
            return []

        devices = self.list_devices_by_bot_uuid(bot_uuid)
        if not devices:
            logger.warning(
                "[BaasService.update_teclaw_outbound_rule_by_bot_uuid] No devices found: bot_uuid=%s",
                bot_uuid,
            )
            return []

        updated_devices: list[dict[str, Any]] = []
        for device in devices:
            paas_device_id = device.get("provider_device_id")
            device_uuid = device.get("device_uuid", "")
            if not paas_device_id:
                logger.warning(
                    "[BaasService.update_teclaw_outbound_rule_by_bot_uuid] Missing provider_device_id: "
                    "bot_uuid=%s, device_uuid=%s",
                    bot_uuid,
                    device_uuid,
                )
                continue
            self.update_device_outbound_rule(paas_device_id, outbound_rule)
            updated_devices.append({
                "device_uuid": device_uuid,
                "paas_device_id": paas_device_id,
            })

        return updated_devices

    @staticmethod
    def _outbound_rule_to_dict(outbound_rule: OutBoundOperationRule) -> dict[str, Any]:
        """将 OutBoundOperationRule 转为可序列化的 dict。"""
        return {
            "header_operation_rules": [
                {
                    "domains": rule.domains,
                    "action": rule.action,
                    "header_name": rule.header_name,
                    "value": rule.value,
                    "placeholder": getattr(rule, "placeholder", None),
                    "separator": getattr(rule, "separator", None),
                }
                for rule in outbound_rule.header_operation_rules
            ]
        }

    def get_device_by_uuid(self, device_uuid: str) -> dict[str, Any]:
        """根据 device_uuid 查询 BaaS 设备信息。

        调用 GET /api/v1/devices/{device_uuid}

        Args:
            device_uuid: BaaS 设备 UUID（带 DEVICE- 前缀）

        Returns:
            设备信息字典，包含 provider_device_id 等字段

        Raises:
            BaasServiceError: 查询失败
        """
        logger.info(f"[BaasService.get_device_by_uuid] Querying device: device_uuid={device_uuid}")

        try:
            response = self._http.get(
                f"/api/v1/devices/{device_uuid}", timeout=30.0
            )
            response.raise_for_status()
            response_data = response.json()

            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            data = response_data.get("data", {})
            logger.info(
                f"[BaasService.get_device_by_uuid] Success: device_uuid={device_uuid}, "
                f"provider_device_id={data.get('provider_device_id')}"
            )
            return data

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.get_device_by_uuid] HTTP error: "
                f"{e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )

    def list_devices_by_bot_uuid(
        self,
        bot_uuid: str,
        tenant: str = "",
        *,
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        """根据 bot_uuid 查询该逻辑 Bot 下的所有 BaaS 设备。

        调用 GET /api/v1/bots/{bot_uuid}/devices?tenant={tenant}

        Args:
            bot_uuid: BaaS Bot UUID（即 binding.device_id）
            tenant: 租户名称，默认使用 self._tenant

        Returns:
            设备信息字典列表

        Raises:
            BaasServiceError: 查询失败
        """
        effective_tenant = tenant or self._tenant
        logger.info(
            f"[BaasService.list_devices_by_bot_uuid] Querying devices: "
            f"bot_uuid={bot_uuid}, tenant={effective_tenant}"
        )

        try:
            response = self._http.get(
                f"/api/v1/bots/{bot_uuid}/devices",
                params={"tenant": effective_tenant},
                timeout=timeout,
            )
            response.raise_for_status()
            response_data = response.json()

            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            # 返回值结构：data[0]["items"]
            data = response_data.get("data", [{}])
            items = data[0].get("items", []) if data else []
            logger.info(
                f"[BaasService.list_devices_by_bot_uuid] Success: "
                f"bot_uuid={bot_uuid}, total={len(items)}"
            )
            return items

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.list_devices_by_bot_uuid] HTTP error: "
                f"{e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            # Transport-layer errors (httpx.ConnectError/TimeoutException/...) are
            # not HTTPStatusError; normalize to BaasServiceError so callers (e.g.
            # resolve_container_provider) can fall back instead of leaking a raw
            # httpx error into the publish flow.
            logger.error(
                f"[BaasService.list_devices_by_bot_uuid] Request error: {e}"
            )
            raise BaasServiceError(
                f"Failed to list devices by bot_uuid: {e}"
            )

    def restart_devices(
        self,
        bot_uuid: str,
        device_uuids: list[str],
        *,
        operator: str,
        request_id: str,
        tenant: str = "",
    ) -> dict[str, Any]:
        """重启指定设备（多实例场景）。

        调用 POST /api/v1/bots/{bot_uuid}/update-devices
        body: {"device_uuids": [...], "operator": ..., "request_id": ...,
        "auto_approve_publish": True}

        Args:
            bot_uuid: BaaS Bot UUID
            device_uuids: 要重启的设备 UUID 列表
            operator: 操作者身份（必填，BaaS 契约要求）
            request_id: 调用方提供的确定性关联 id（#197：取代原 uuid4，
                重试同一逻辑重启时保持稳定，便于日志追踪；BaaS 侧仅作关联，非去重键）
            tenant: 租户名称，默认使用 self._tenant

        Returns:
            BaaS API 返回的 data dict，含 publish_id

        Raises:
            BaasServiceError: 重启失败
        """
        if not request_id:
            raise BaasServiceError("request_id is required for restarting devices")
        effective_tenant = tenant or self._tenant
        logger.info(
            f"[BaasService.restart_devices] Restarting devices: "
            f"bot_uuid={bot_uuid}, device_uuids={device_uuids}, "
            f"operator={operator}, request_id={request_id}, "
            f"tenant={effective_tenant}"
        )

        payload: dict[str, Any] = {
            "device_uuids": device_uuids,
            "operator": operator,
            "request_id": request_id,
            "auto_approve_publish": True,
        }

        try:
            data = self._post_bots_api(
                path=f"/api/v1/bots/{bot_uuid}/update-devices",
                payload=payload,
                action="restart_devices",
                tenant=effective_tenant,
            )
            logger.info(
                f"[BaasService.restart_devices] Success: "
                f"bot_uuid={bot_uuid}, publish_id={data.get('publish_id')}"
            )
            return data

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.restart_devices] HTTP error: "
                f"{e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BaasService.restart_devices] Request error: {e}"
            )
            raise BaasServiceError(
                f"Failed to restart devices: {e}"
            )

    def resolve_container_provider(
        self,
        bot: Dict[str, Any],
        *,
        default_provider: str = DEFAULT_DEVICE_PROVIDER,
    ) -> str:
        """Resolve a bot's ``device_provider`` from its engine.

        A bot's container type follows its engine: a ``teclaw`` engine runs in a
        teclaw (pull-based external) container, everything else uses the default
        (``baas``). arca and baas both route to ``ArcaSnapshotProducer``, so
        non-teclaw bots are byte-for-byte unchanged.

        This is the creation-time intent (the engine is the single source of
        truth) — baas is **not** queried for the bot's container. Once draft
        creation is migrated onto baas, this can become a real container query
        against the bot's provisioned device.

        Args:
            bot: Bot info dict; its ``active_engine`` decides the container.
            default_provider: Token for non-teclaw bots.

        Returns:
            A ``device_provider`` token (``teclaw`` or ``default_provider``).
        """
        active_engine = bot.get("active_engine") if bot else None
        return resolve_device_provider(
            active_engine, default_provider=default_provider
        )

    def upgrade_bot(
        self,
        bot_uuid: str,
        bot: Dict[str, Any],
        owner_id: str,
        request_id: str,
        migration_path: Optional[str],
        device_count: int = 1,
        stage: str | None = None,
        version: str = "1",
        template_uuid: Optional[str] = None,
        auto_approve_publish: bool = True,
        mount_home_dir_storage: bool | None = None,
        extra_envs: Optional[Dict[str, Any]] = None,
        template_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """调用 BaaS 层 API 升级 Bot。

        调用 POST /api/v1/bots/{bot_uuid}/update

        内部通过 _build_create_bot_payload 构建 config（与 create_bot 一致）。

        Args:
            bot_uuid: Bot UUID
            owner_id: 操作者用户 ID
            request_id: 请求 ID（32-64字符，字母/数字/连字符/下划线）
            bot: Bot 信息字典，包含: bot_id, entity_id, entity_type, bot_name, bot_desc, active_engine
            migration_path: 发布态迁移源目录；普通重启可传 None
            device_count: int = 1, 拉起设备实例数
            stage: 发布阶段；None 时不向启动脚本传 --stage
            version: str = "1", 发布版本
            template_uuid: 模板 UUID（可选，不传则使用默认模板）
            mount_home_dir_storage: 是否使用 home 目录 NAS；None 时由底层挂载逻辑按白名单解析
            extra_envs: 追加写入容器的环境变量。原地重启时由上层按 applicationCoding/
                personalCoding + 引擎门控构造（BOT_TYPE / RELAY_DEFAULT_MODEL /
                RELAY_DEFAULT_RUNTIME 等），与 create 路径同口径透传，缺省 None 时
                envs 退化为 AGENTCLAW_ENGINE 单值，service bot 升级链路零差异。
            template_config: 上层选择 template 时携带的沙箱覆写配置（镜像/规格/envs），
                与 create_bot 一致；缺省 None 时不触发 SandboxOverrides。

        Returns:
            BaaS 层返回的信息，包含：
            - bot_uuid: Bot UUID
            - publish_id: 升级工作流 ID（如果触发了 UPDATE 发布）
            - request_id: 请求 ID

        Raises:
            BaasServiceError: 升级失败
        """
        # 发布态 service upgrade 必须有迁移源；草稿重启不迁移，允许为空。
        if (
            bot.get("bot_type") == "service"
            and stage in {PublishStage.VERIFY.value, PublishStage.ONLINE.value}
            and not migration_path
        ):
            raise BaasServiceError("migration_path is required for upgrading bot")

        logger.info(
            f"[BaasService.upgrade_bot] "
            f"Upgrading bot in BaaS: bot_uuid={bot_uuid}, operator={owner_id}, request_id={request_id}"
        )

        # 构建请求体
        payload = self._build_create_bot_payload(
            bot=bot,
            owner_id=owner_id,
            request_id=request_id,
            device_count=device_count,
            migration_path=migration_path or "",
            stage=stage,
            version=version,
            template_uuid=template_uuid,
            auto_approve_publish=auto_approve_publish,
            mount_home_dir_storage=mount_home_dir_storage,
            extra_envs=extra_envs,
            template_config=template_config,
        )

        logger.info(
            f"[BaasService.upgrade_bot] "
            f"Upgrading bot in BaaS: bot_uuid={bot_uuid}, operator={owner_id}, request_id={request_id}, payload={payload}"
        )

        # 调用 BaaS 层 API
        try:
            return self._post_bots_api(
                path=f"/api/v1/bots/{bot_uuid}/update",
                payload=payload,
                action="upgrade_bot",
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.upgrade_bot] HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(
                f"[BaasService.upgrade_bot] "
                f"Failed to upgrade bot in BaaS: {e}"
            )
            raise

    def update_device_outbound_rule(
        self, paas_device_id: str, outbound_rule: OutBoundOperationRule
    ) -> bool:
        """更新 PaaS 设备的出站 header 规则。

        调用 PUT /api/v1/paas/devices/{paas_device_id}/outbound-rule

        Args:
            paas_device_id: PaaS 设备 ID（即 provider_device_id）
            outbound_rule: 出站规则对象

        Returns:
            是否更新成功

        Raises:
            BaasServiceError: 更新失败
        """
        logger.info(
            f"[BaasService.update_device_outbound_rule] Updating outbound rule: "
            f"paas_device_id={paas_device_id}"
        )

        payload = self._outbound_rule_to_dict(outbound_rule)

        try:
            response = self._http.put(
                f"/api/v1/paas/devices/{paas_device_id}/outbound-rule",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            response_data = response.json()

            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            logger.info(
                f"[BaasService.update_device_outbound_rule] Success: "
                f"paas_device_id={paas_device_id}"
            )
            return True

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.update_device_outbound_rule] HTTP error: "
                f"{e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )

    def append_caller_outbound_rule(
        self,
        paas_device_id: str,
        caller_rule: OutBoundOperationRule,
    ) -> bool:
        """Append one validated Caller overlay without replacing base rules."""
        payload = self._outbound_rule_to_dict(caller_rule)
        logger.info(
            "caller_outbound_append_started rule_count=%s",
            len(caller_rule.header_operation_rules),
        )
        try:
            response = self._http.put(
                f"/api/v1/paas/devices/{paas_device_id}/outbound-rule?mode=append",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("code") != 0:
                logger.warning("caller_outbound_append_rejected")
                raise BaasServiceError("BaaS Caller outbound append rejected")
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "caller_outbound_append_http_failed status_code=%s",
                exc.response.status_code,
            )
            raise BaasServiceError("BaaS Caller outbound append failed") from exc
        except BaasServiceError:
            raise
        except Exception as exc:
            logger.warning(
                "caller_outbound_append_failed error_type=%s",
                type(exc).__name__,
            )
            raise BaasServiceError("BaaS Caller outbound append failed") from exc
        logger.info("caller_outbound_append_succeeded")
        return True

    def update_caller_identity(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        caller_user_id: str,
        caller_token: CallerToken,
        stage: str,
        publish_id: int | None,
        entity_id: str | None = None,
        binding_id: int | None = None,
        is_test_exchange: bool = False,
    ) -> None:
        """Install one Caller-token overlay on the Bot's current BaaS device."""
        if (
            not caller_token.access_token
            or caller_token.subject_user_id != caller_user_id
        ):
            raise CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)

        if entity_id is not None:
            bot = self._bot_repo.get_by_id_and_entity(bot_id, entity_id)
        else:
            try:
                # COSEC: do not select an arbitrary duplicate Bot when callers
                # omit entity_id; the caller credential target must be unique.
                bot = self._bot_repo.get_unique_by_id(bot_id)
            except BotLookupAmbiguousError as exc:
                logger.warning(
                    "caller_identity_update_rejected_ambiguous_bot bot_id=%s",
                    bot_id,
                )
                raise CallerCredentialError(CALLER_TARGET_AMBIGUOUS) from exc
        # COSEC: an entity-scoped lookup is not authorization; require the
        # resolved Bot owner to match the identity already resolved upstream.
        if (
            not bot
            or (not is_test_exchange and bot.get("bot_type") != "service")
            or bot.get("status") != "ACTIVE"
            or str(bot.get("owner_id") or "") != owner_user_id
        ):
            raise CallerCredentialError(CALLER_TARGET_NOT_FOUND)

        use_supplied_binding_id = self._is_valid_caller_binding_id(binding_id)
        resolved_binding_id = (
            binding_id
            if use_supplied_binding_id
            else self._resolve_caller_binding_id(
                bot=bot,
                bot_id=bot_id,
                owner_user_id=owner_user_id,
                stage=stage,
                publish_id=publish_id,
            )
        )
        binding = self._device_binding_repo.get_by_id(resolved_binding_id)
        if (
            binding is None
            or str(getattr(binding, "status", "")).upper() != "ACTIVE"
            or not str(getattr(binding, "device_id", ""))
        ):
            raise CallerCredentialError(CALLER_TARGET_NOT_FOUND)

        devices = self.list_devices_by_bot_uuid(str(binding.device_id), timeout=3.0)
        if not devices:
            raise CallerCredentialError(CALLER_TARGET_NOT_FOUND)
        if len(devices) != 1:
            raise CallerCredentialError(CALLER_TARGET_AMBIGUOUS)
        paas_device_id = devices[0].get("provider_device_id")
        if not self._is_valid_paas_device_id(paas_device_id):
            raise CallerCredentialError(CALLER_TARGET_NOT_FOUND)

        caller_rule = self._outbound_rule_provider.build_caller_rule(
            caller_token=caller_token.access_token,
        )
        if not self._is_valid_caller_rule(caller_rule, caller_token.access_token):
            raise CallerCredentialError(CALLER_OUTBOUND_INVALID)
        assert caller_rule is not None
        logger.info(
            "caller_outbound_update_started bot_id=%s stage=%s rule_count=%s "
            "entity_scoped=%s supplied_binding_id=%s test_exchange=%s",
            bot_id,
            stage,
            len(caller_rule.header_operation_rules),
            entity_id is not None,
            use_supplied_binding_id,
            is_test_exchange,
        )
        try:
            updated = self.append_caller_outbound_rule(paas_device_id, caller_rule)
        except Exception as exc:
            logger.warning(
                "caller_outbound_update_failed bot_id=%s stage=%s error_type=%s "
                "entity_scoped=%s supplied_binding_id=%s test_exchange=%s",
                bot_id,
                stage,
                type(exc).__name__,
                entity_id is not None,
                use_supplied_binding_id,
                is_test_exchange,
            )
            raise CallerCredentialError(CALLER_OUTBOUND_UPDATE_FAILED) from exc
        if not updated:
            raise CallerCredentialError(CALLER_OUTBOUND_UPDATE_FAILED)
        logger.info(
            "caller_outbound_update_succeeded bot_id=%s stage=%s entity_scoped=%s "
            "supplied_binding_id=%s test_exchange=%s",
            bot_id,
            stage,
            entity_id is not None,
            use_supplied_binding_id,
            is_test_exchange,
        )

    @staticmethod
    def _is_valid_caller_binding_id(binding_id: object) -> bool:
        return (
            isinstance(binding_id, int)
            and not isinstance(binding_id, bool)
            and binding_id > 0
        )

    def _resolve_caller_binding_id(
        self,
        *,
        bot: Dict[str, Any],
        bot_id: str,
        owner_user_id: str,
        stage: str,
        publish_id: int | None,
    ) -> int:
        if stage == PublishStage.DRAFT.value:
            binding_id = bot.get("binding_id")
        elif stage in {PublishStage.VERIFY.value, PublishStage.ONLINE.value}:
            if (
                not isinstance(publish_id, int)
                or isinstance(publish_id, bool)
                or publish_id <= 0
            ):
                raise CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)
            record = self._bot_publish_repo.get_by_id(publish_id)
            if (
                record is None
                or getattr(record, "source_bot_id", None) != bot_id
                or getattr(record, "owner_id", None) != owner_user_id
            ):
                raise CallerCredentialError(CALLER_TARGET_NOT_FOUND)
            ext = getattr(record, "ext", None)
            binding_id = (
                (ext.get("binding") or {}).get(stage)
                if isinstance(ext, dict)
                else None
            )
        else:
            raise CallerCredentialError(CALLER_CREDENTIAL_REQUEST_INVALID)
        if not self._is_valid_caller_binding_id(binding_id):
            raise CallerCredentialError(CALLER_TARGET_NOT_FOUND)
        return binding_id

    @staticmethod
    def _is_valid_caller_rule(
        caller_rule: OutBoundOperationRule | None,
        access_token: str,
    ) -> bool:
        if caller_rule is None:
            return False
        return any(
            rule.header_name == "x-caller-token"
            and rule.action == "set"
            and rule.value == access_token
            for rule in caller_rule.header_operation_rules
        )

    @staticmethod
    def _is_valid_paas_device_id(value: object) -> bool:
        if not isinstance(value, str) or not value or len(value) > 256:
            return False
        device_id, separator, template_id = value.partition("@")
        if not (device_id and separator and template_id and "@" not in template_id):
            return False
        # COSEC: the BaaS client interpolates this database-sourced identifier
        # into a fixed relative URL, so reject path/control characters first.
        safe_chars = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
        )
        return all(char in safe_chars for char in device_id + template_id)

    def get_sandbox_id_from_publish_record(
        self,
        record,
        user_id: str,
    ) -> str | None:
        """从发布记录获取 sandbox_id。

        从 record.ext.binding 中获取 bind_id，再通过 get_ws_info 获取设备信息，
        最后解析 sandbox_id。

        Args:
            record: BotPublishRecord 发布记录
            user_id: 用户 ID，用于设备亲和性路由

        Returns:
            sandbox_id，如果无法获取则返回 None
        """
        try:
            # 从 ext 扩展字段按发布状态获取 bind_id
            from agentclaw.community.core.service_bot.repository.models import select_stage_bind_id

            ext = record.ext or {}
            binding_info = ext.get("binding", {})
            bind_id = select_stage_bind_id(binding_info, record.status)

            if not bind_id:
                logger.warning(
                    f"[BaasService.get_sandbox_id_from_publish_record] "
                    f"publish_id={record.id} bind_id not found in ext"
                )
                return None

            ws_info = self.get_ws_info(bind_id=bind_id, device_affinity=user_id)
            target = ws_info.target
            # target 格式: ARCA_{sandbox_id}:{port}
            if not target or not target.startswith("ARCA_"):
                logger.warning(
                    f"[BaasService.get_sandbox_id_from_publish_record] "
                    f"publish_id={record.id} target is not Arca: {target}"
                )
                return None

            target_without_prefix = target[5:]  # 去掉 "ARCA_"
            sandbox_id = target_without_prefix.rsplit(":", 1)[0]
            logger.info(
                f"[BaasService.get_sandbox_id_from_publish_record] "
                f"Got sandbox_id={sandbox_id} for publish_id={record.id}"
            )
            return sandbox_id

        except Exception as e:
            logger.error(
                f"[BaasService.get_sandbox_id_from_publish_record] "
                f"Failed for publish_id={record.id}: {e}"
            )
            return None

    def get_http_info(
        self,
        *,
        bind_id: int,
        port: int,
        path: str = "",
        tenant: Optional[str] = None,
        device_affinity: Optional[str] = None,
        device_uuid: Optional[str] = None,
        ws_conn_mode: Optional[str] = None,
        timeout: float = 5.0,
    ) -> HttpConnectionInfo:
        """获取容器 HTTP 连接信息。

        调用 GET /api/v1/bots/{device_id}/http-info?tenant=&port=&path=&device_affinity=&device_uuid=&ws_conn_mode=
        BaaS 端 endpoint：commit 9d4622c1e 引入（plan-01 接入）。

        Args:
            bind_id: 设备绑定 ID（ac_entity_device_binding.id）
            port: 目标 HTTP 端口（adapter 通常 20010-20099）
            path: HTTP path（用于 BaaS 审计 / 日志；BaaS 不解释）
            tenant: 租户名称，默认走 self._tenant
            device_affinity: 设备亲和性标识，用于一致性哈希粘性选择
            device_uuid: 多实例 service bot 场景锁定特定实例；不传则 BaaS 自动选
                活跃实例。与 ``get_ws_info`` 的 ``device_uuid`` 参数对称 —— 文件
                读写等经 invoke_http 的链路用它把实例选择传达到 BaaS /http-info。
            timeout: HTTP 请求超时（秒）

        Returns:
            HttpConnectionInfo: HTTP 连接信息 (http_url, token)

        Raises:
            BaasServiceError: BaaS 不可达 / 404 / 5xx / 网络 / 超时 / 业务 code≠0
        """
        # 从 DeviceBindingRepository 获取 device_id
        device_binding_repo = self._device_binding_repo
        binding = device_binding_repo.get_by_id(bind_id)
        if binding is None:
            raise BaasServiceError(f"Device binding not found: bind_id={bind_id}")

        device_id = binding.device_id
        effective_tenant = tenant or self._tenant
        logger.info(
            f"[BaasService.get_http_info] "
            f"Getting http info: bind_id={bind_id}, device_id={device_id}, "
            f"tenant={effective_tenant}, port={port}, path={path}, "
            f"device_affinity={device_affinity}, device_uuid={device_uuid}"
        )

        params: dict[str, Any] = {
            "tenant": effective_tenant,
            "port": port,
            "path": path,
        }
        if device_affinity:
            params["device_affinity"] = device_affinity
        if device_uuid:
            params["device_uuid"] = device_uuid
        if ws_conn_mode:
            params["ws_conn_mode"] = ws_conn_mode

        try:
            response = self._http.get(
                f"/api/v1/bots/{device_id}/http-info",
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()

            response_data = response.json()

            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            data = response_data.get("data", {})
            result = HttpConnectionInfo(
                http_url=data["http_url"],
                token=data["token"],
                target=data["target"],
            )

            logger.info(
                f"[BaasService.get_http_info] "
                f"Got http info: bind_id={bind_id}, device_id={device_id}, "
                f"http_url={result.http_url}"
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.get_http_info] "
                f"HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error(
                f"[BaasService.get_http_info] "
                f"Failed to get http info: {e}"
            )
            raise BaasServiceError(f"Failed to get http info: {e}")

    def invoke_http(
        self,
        *,
        bind_id: int,
        port: int,
        path: str,
        method: str = "POST",
        json: Any | None = None,
        files: Any | None = None,
        data: Any | None = None,
        params: Any | None = None,
        tenant: Optional[str] = None,
        device_affinity: Optional[str] = None,
        device_uuid: Optional[str] = None,
        auth_header: str = "openclawToken",
        timeout: float | None = None,
    ) -> httpx.Response:
        """容器内 API 统一出口：封装 get_http_info + 直传完整 http_url。

        调用方无需手动拼 invoke-http URL 或管理 token，只需传 bind_id / port / path，
        本方法负责：
          1. 调 get_http_info 获取完整 http_url + token
          2. 直接把完整 http_url 传给 self._general_http（httpx 遇到绝对 URL 会忽略 base_url）
          3. 携带 token（header 名由 ``auth_header`` 决定）发出请求

        Args:
            bind_id: 设备绑定 ID（ac_entity_device_binding.id）
            port: 目标 HTTP 端口（adapter 通常 20010-20099）
            path: 容器内 HTTP 路径（如 "/api/file/read"）
            method: HTTP 方法，默认 "POST"
            json: POST/PUT 请求体（dict，可选）
            files: multipart 上传的 files（写文件链路用，POST 时与 json 互斥）
            data: multipart 上传的表单字段（写文件链路用，配合 files）
            params: query 参数（dict，可选），透传给底层 get/post/put/delete
            tenant: 租户名称，默认走 self._tenant
            device_affinity: 设备亲和性标识，用于一致性哈希粘性选择
            device_uuid: 多实例 service bot 场景锁定特定实例；透传给
                ``get_http_info`` 作为 BaaS /http-info 的 ``device_uuid`` query 参数。
                不传则 BaaS 自动选活跃实例（单实例 bot 忽略）。
            auth_header: token 注入的 header 名。默认 ``"openclawToken"``（secbaas
                invoke-http 隧道）；走 **agentclawproxy** ``/proxypass`` 网关的链路
                （teclaw/arca，``http_url`` 形如 ``{base}/proxypass/{target}{path}``）
                需传 ``"x-proxypass-token"`` —— 该网关用此 header 鉴权，传 openclawToken
                会 401。``info.token`` 是网关对应的 token，header 名不同而已。
            timeout: 获取连接信息并调用容器 API 的总超时秒数；不传时沿用各请求默认值。

        Returns:
            httpx.Response — 来自 self._general_http 的原始响应，供调用方自行处理

        Raises:
            BaasServiceError: get_http_info 失败（404/503/网络超时等）
        """
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")

        started_at = time.monotonic() if timeout is not None else None
        http_info_timeout_kwargs = (
            {"timeout": min(timeout, 5.0)} if timeout is not None else {}
        )
        info = self.get_http_info(
            bind_id=bind_id,
            port=port,
            path=path,
            tenant=tenant,
            device_affinity=device_affinity,
            device_uuid=device_uuid,
            **http_info_timeout_kwargs,
        )

        request_timeout_kwargs: dict[str, float] = {}
        if timeout is not None and started_at is not None:
            request_timeout_kwargs["timeout"] = max(
                timeout - (time.monotonic() - started_at),
                0.001,
            )

        # 直接传完整 http_url 给 general_http（base_url=""）：httpx.Client 收到绝对
        # URL 时不拼 base_url；local MockSeam 按 method 记录调用，path 参数存入 calls。
        headers = {auth_header: info.token}

        m = method.upper()
        if m == "GET":
            return self._general_http.get(
                info.http_url, params=params, headers=headers, **request_timeout_kwargs
            )
        elif m == "PUT":
            return self._general_http.put(
                info.http_url, json=json, params=params, headers=headers, **request_timeout_kwargs
            )
        elif m == "DELETE":
            return self._general_http.delete(
                info.http_url, params=params, headers=headers, **request_timeout_kwargs
            )
        elif files is not None:
            # 写文件等 multipart 链路：files+data 走 httpx multipart，json 必为空。
            return self._general_http.post(
                info.http_url, files=files, data=data, json=json, params=params,
                headers=headers, **request_timeout_kwargs
            )
        else:
            # POST（默认）或任何其他方法走 POST
            return self._general_http.post(
                info.http_url, json=json, params=params, headers=headers,
                **request_timeout_kwargs
            )

    def get_bot_start_progress(
        self,
        bot_uuid: str,
        tenant: str = "",
        device_affinity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """查询 Bot 设备启动进度。

        调用 GET /api/v1/bots/{bot_uuid}/start-progress

        Args:
            bot_uuid: Bot UUID
            tenant: 租户名称，默认使用 self._tenant
            device_affinity: 设备亲和性标识，用于一致性哈希粘性选择

        Returns:
            BaaS 返回的启动进度信息，包含 progress 等字段

        Raises:
            BaasServiceError: 查询失败
        """
        effective_tenant = tenant or self._tenant
        logger.info(
            f"[BaasService.get_bot_start_progress] Querying start progress: "
            f"bot_uuid={bot_uuid}, tenant={effective_tenant}, "
            f"device_affinity={device_affinity!r}"
        )

        params: Dict[str, Any] = {"tenant": effective_tenant}
        if device_affinity:
            params["device_affinity"] = device_affinity

        try:
            response = self._http.get(
                f"/api/v1/bots/{bot_uuid}/start-progress",
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()
            response_data = response.json()

            if response_data.get("code") != 0:
                raise BaasServiceError(
                    f"BaaS API error: {response_data.get('message', 'Unknown error')}"
                )

            data = response_data.get("data", {})
            logger.info(
                f"[BaasService.get_bot_start_progress] Success: "
                f"bot_uuid={bot_uuid}, progress={data.get('progress')}"
            )
            return data

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[BaasService.get_bot_start_progress] HTTP error: "
                f"{e.response.status_code} - {e.response.text}"
            )
            raise BaasServiceError(
                f"BaaS API error: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            logger.error(
                f"[BaasService.get_bot_start_progress] Request error: {e}"
            )
            raise BaasServiceError(
                f"Failed to get bot start progress: {e}"
            )


__all__ = [
    "BaasService",
    "BaasServiceError",
    "BotWsConnectionInfoResponse",
    "HttpConnectionInfo",
    "Storage",
    "BotDeployConfig",
    "MountPointEntry",
    "BotConfig",
    "DEFAULT_READ_ONLY_RULES",
]
