"""解析 template_uid，并映射到 BaaS 创建所需的 template_uuid。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.devices.services.effective_engine_resolver import (
    resolve_effective_engine_for_template,
)
from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    from agentclaw.community.core.system_config import SystemConfigService


logger = get_logger()

BAAS_TEMPLATE_MAPPING_CATEGORY = "system"
BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY = "baas_template_uid_routing_config"
PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY = "personal_bot_test_template_whitelist"


@dataclass(frozen=True)
class BaasTemplateUidResolution:
    template_uid: str
    source: str
    config_version: str = ""


@dataclass(frozen=True)
class BaasTemplateResolution:
    template_uid: str
    template_uuid: str
    source: str
    config_version: str = ""


class BaasTemplateResolveError(DeviceServiceError):
    """provider=baas 无法解析所需 template 时抛出。"""


class BaasTemplateResolverProtocol(Protocol):
    def resolve_template_override(
        self,
        *,
        env: str,
        user_id: str | None,
        bot_type: str,
    ) -> str | None:
        """命中现有工号映射时返回覆盖的 template_uuid。"""

    def resolve_template_uid(
        self,
        *,
        bot_id: str | None = None,
        user_id: str | None = None,
        env: str,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
        template_config: dict | None,
    ) -> str:
        """返回当前业务场景选中的 template_uid。"""

    def resolve_template_uuid(
        self,
        *,
        bot_id: str | None = None,
        env: str,
        template_uid: str,
    ) -> str:
        """将已选中的 template_uid 映射为 BaaS 使用的 template_uuid。"""

    def resolve_template(
        self,
        *,
        bot_id: str | None = None,
        user_id: str | None = None,
        env: str,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
        template_config: dict | None,
    ) -> BaasTemplateResolution:
        """一次性返回业务 template_uid 和 BaaS template_uuid。"""


class SystemConfigBaasTemplateResolver:
    """基于 system_config 管理 template 选择与映射。

    OCB 内部只流转稳定的 template_uid，实际传给 BaaS 的
    template_uuid 统一从 system_config 映射得到，避免业务代码散落
    BaaS 模板 UUID。
    """

    def __init__(self, system_config_service: SystemConfigService) -> None:
        self._system_config_service = system_config_service

    def resolve_template_uid(
        self,
        *,
        bot_id: str | None = None,
        user_id: str | None = None,
        env: str,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
        template_config: dict | None,
    ) -> str:
        """返回设备分配上下文里最终选中的 template_uid。"""
        return self.resolve_template_uid_context(
            bot_id=bot_id,
            user_id=user_id,
            env=env,
            bot_type=bot_type,
            engine_type=engine_type,
            template_type=template_type,
            template_config=template_config,
        ).template_uid

    def resolve_template_uid_context(
        self,
        *,
        bot_id: str | None = None,
        user_id: str | None = None,
        env: str,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
        template_config: dict | None,
    ) -> BaasTemplateUidResolution:
        """解析 template_uid，并返回来源与配置版本用于日志定位。"""
        # 调用方已明确选择 template 时，直接使用该 template_uid。
        # 最终 BaaS template_uuid 仍由统一配置映射得到。
        if template_config is not None and "template_uid" in template_config:
            explicit_uid = template_config.get("template_uid")
            if not isinstance(explicit_uid, str) or not explicit_uid.strip():
                raise BaasTemplateResolveError(
                    "invalid explicit BaaS template_uid in template_config: "
                    f"template_uid={explicit_uid!r}"
                )
            template_uid = explicit_uid.strip()
            logger.info(
                "[template_uid.resolve] bot_id=%s env=%s bot_type=%s engine_type=%s "
                "engine=%s template_type=%s template_uid=%s source=template_config version=",
                bot_id,
                env,
                bot_type,
                engine_type,
                self.normalize_engine_for_template(
                    engine_type=engine_type,
                    template_type=template_type,
                ),
                template_type,
                template_uid,
            )
            return BaasTemplateUidResolution(
                template_uid=template_uid,
                source="template_config",
            )

        # 未显式指定时，根据 engine / bot_type / template_type 等上下文
        # 在 system_config selectors 里选一个业务 template_uid。
        mapping = self._read_mapping(env=env)
        engine = self.normalize_engine_for_template(
            engine_type=engine_type,
            template_type=template_type,
        )
        template_uid = self.select_template_uid(
            mapping=mapping,
            env=env,
            user_id=user_id,
            bot_type=bot_type,
            engine_type=engine_type,
            template_type=template_type,
        )
        version = self._config_version(mapping)
        logger.info(
            "[template_uid.resolve] bot_id=%s env=%s bot_type=%s engine_type=%s "
            "engine=%s template_type=%s template_uid=%s source=system_config version=%s",
            bot_id,
            env,
            bot_type,
            engine_type,
            engine,
            template_type,
            template_uid,
            version,
        )
        return BaasTemplateUidResolution(
            template_uid=template_uid,
            source="system_config",
            config_version=version,
        )

    def resolve_template_uuid(
        self,
        *,
        bot_id: str | None = None,
        env: str,
        template_uid: str,
    ) -> str:
        """将业务侧 template_uid 转换成 BaaS create 接口需要的 template_uuid。"""
        # BaaS create payload 需要 template_uuid；这里是 UID -> UUID 的唯一出口。
        mapping = self._read_mapping(env=env)
        template_uuid = self._extract_template_uuid(
            mapping=mapping,
            env=env,
            template_uid=template_uid,
        )
        logger.info(
            "[template_uuid.resolve] bot_id=%s env=%s template_uid=%s "
            "template_uuid=%s source=system_config version=%s",
            bot_id,
            env,
            template_uid,
            template_uuid,
            self._config_version(mapping),
        )
        return template_uuid

    def resolve_template(
        self,
        *,
        bot_id: str | None = None,
        user_id: str | None = None,
        env: str,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
        template_config: dict | None,
    ) -> BaasTemplateResolution:
        """解析当前业务上下文最终使用的 BaaS template。"""
        override_uuid = self.resolve_template_override(
            env=env,
            user_id=user_id,
            bot_type=bot_type,
        )
        if override_uuid is not None:
            logger.info(
                "[template.whitelist] whitelist override hit: "
                "env=%s config_key=%s user_id=%s template_uuid=%s bot_id=%s",
                env,
                PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY,
                user_id,
                override_uuid,
                bot_id,
            )
            return BaasTemplateResolution(
                template_uid="__whitelist_override__",
                template_uuid=override_uuid,
                source="whitelist",
            )

        uid_resolution = self.resolve_template_uid_context(
            bot_id=bot_id,
            user_id=user_id,
            env=env,
            bot_type=bot_type,
            engine_type=engine_type,
            template_type=template_type,
            template_config=template_config,
        )
        template_uuid = self.resolve_template_uuid(
            bot_id=bot_id,
            env=env,
            template_uid=uid_resolution.template_uid,
        )
        return BaasTemplateResolution(
            template_uid=uid_resolution.template_uid,
            template_uuid=template_uuid,
            source=uid_resolution.source,
            config_version=uid_resolution.config_version,
        )

    def resolve_template_override(
        self,
        *,
        env: str,
        user_id: str | None,
        bot_type: str,
    ) -> str | None:
        """从现有数据库配置解析个人或服务 Bot 的模板覆盖。

        只有配置有效且 owner 工号明确命中时才返回模板。调用方可将
        ``None`` 视为保持原逻辑，包括配置缺失、读取失败和工号未命中。
        """
        normalized_bot_type = (bot_type or "").strip().lower()
        if normalized_bot_type not in {"personal", "service"}:
            return None
        return self._resolve_whitelist_override(env=env, user_id=user_id)

    def select_template_uid(
        self,
        *,
        mapping: dict[str, Any],
        env: str,
        user_id: str | None,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
    ) -> str:
        """根据 selectors 选择最匹配的 template_uid。

        bot_type 只作为可选 selector 维度参与匹配；provider=baas 是否支持
        该 bot_type，由 BaasDeviceService 在真正创建前校验。
        """
        normalized_bot_type = (bot_type or "").strip().lower()
        engine = self.normalize_engine_for_template(
            engine_type=engine_type,
            template_type=template_type,
        )
        selectors = mapping.get("selectors")
        if not isinstance(selectors, list):
            raise BaasTemplateResolveError(
                "invalid BaaS template routing config: missing selectors list"
            )

        # selector 采用“必填 engine + 可选精确维度”的匹配方式：
        # - engine 必须命中；
        # - bot_type / template_type 配了才参与匹配，命中后提高优先级；
        # - legacy 白名单命中属于临时兼容场景，优先级高于普通通用规则。
        matches: list[tuple[int, str]] = []
        for selector in selectors:
            if not isinstance(selector, dict):
                continue
            if selector.get("engine") != engine:
                continue
            score = 0
            selector_bot_type = selector.get("bot_type")
            if selector_bot_type is not None:
                if selector_bot_type != normalized_bot_type:
                    continue
                score += 1
            selector_template_type = selector.get("template_type")
            if selector_template_type is not None:
                if selector_template_type != template_type:
                    continue
                score += 1
            legacy_whitelist = selector.get("legacy_template_whitelist")
            if legacy_whitelist is not None:
                if not self._legacy_template_whitelist_hit(
                    legacy_whitelist,
                    env=env,
                    user_id=user_id,
                ):
                    continue
                score += 10
            template_uid = selector.get("template_uid")
            if not isinstance(template_uid, str) or not template_uid.strip():
                raise BaasTemplateResolveError(
                    "invalid template_uid in BaaS template selector: "
                    f"selector={selector!r}"
                )
            matches.append((score, template_uid.strip()))

        # 没有任何 selector 命中时阻断 BaaS 创建，避免落到错误默认模板。
        if not matches:
            raise BaasTemplateResolveError(
                "BaaS template selector not configured: "
                f"bot_type={normalized_bot_type}, engine={engine}, "
                f"template_type={template_type!r}, user_id={user_id!r}"
            )

        # 同一最高优先级只能收敛到一个 template_uid；否则说明配置语义冲突。
        best_score = max(score for score, _template_uid in matches)
        best_uids = {
            template_uid for score, template_uid in matches if score == best_score
        }
        if len(best_uids) > 1:
            raise BaasTemplateResolveError(
                "ambiguous BaaS template selectors: "
                f"bot_type={normalized_bot_type}, engine={engine}, "
                f"template_type={template_type!r}, user_id={user_id!r}, "
                f"template_uids={sorted(best_uids)}"
            )
        return next(iter(best_uids))

    @staticmethod
    def normalize_engine_for_template(
        *,
        engine_type: str | None,
        template_type: str | None,
    ) -> str:
        """把历史 engine 表达归一成 template 配置里的 engine。"""
        return resolve_effective_engine_for_template(
            engine_type=engine_type,
            template_type=template_type,
        )

    def _legacy_template_whitelist_hit(
        self,
        reference: Any,
        *,
        env: str,
        user_id: str | None,
    ) -> bool:
        """判断用户是否命中旧 system_config 里的临时模板白名单。"""
        # 芝麻模板白名单仍维护在旧配置里，这里只复用名单命中关系，
        # 命中后使用当前 BaaS template 配置里的 template_uid。
        if not isinstance(reference, str) or "/" not in reference:
            logger.warning(
                "[template_uid.resolve] invalid legacy_template_whitelist=%r",
                reference,
            )
            return False
        if not user_id:
            return False

        # reference 采用 category/config_key，便于只把需要兼容的旧白名单接进来。
        category, config_key = reference.split("/", 1)
        category = category.strip()
        config_key = config_key.strip()
        if not category or not config_key:
            logger.warning(
                "[template_uid.resolve] invalid legacy_template_whitelist=%r",
                reference,
            )
            return False

        # 读取旧配置失败不能影响普通 template 选择，白名单 selector 未命中即可。
        try:
            whitelist_config = self._system_config_service.get_config(
                category=category,
                config_key=config_key,
                env=env,
            )
        except Exception as e:
            logger.warning(
                "[template_uid.resolve] failed to read legacy whitelist: "
                "env=%s category=%s config_key=%s error=%s",
                env,
                category,
                config_key,
                e,
            )
            return False

        if not isinstance(whitelist_config, dict):
            return False
        staff_ids = whitelist_config.get("staff_ids")
        if not isinstance(staff_ids, list):
            return False

        # 旧配置里的 staff_id 可能是数字或字符串，这里统一按字符串比较。
        hit = str(user_id) in {str(staff_id) for staff_id in staff_ids}
        if hit:
            logger.info(
                "[template_uid.resolve] legacy whitelist hit: "
                "env=%s category=%s config_key=%s user_id=%s",
                env,
                category,
                config_key,
                user_id,
            )
        return hit

    def _resolve_whitelist_override(
        self,
        *,
        env: str,
        user_id: str | None,
    ) -> str | None:
        """如果 user_id 命中白名单，返回覆盖的 template_uuid，否则返回 None。"""
        if not user_id:
            return None

        try:
            whitelist_config = self._system_config_service.get_config(
                category=BAAS_TEMPLATE_MAPPING_CATEGORY,
                config_key=PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY,
                env=env,
            )
        except Exception as e:
            logger.warning(
                "[template.whitelist] failed to read whitelist config: "
                "env=%s config_key=%s error=%s",
                env,
                PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY,
                e,
            )
            return None

        if not isinstance(whitelist_config, dict):
            return None

        staff_ids = whitelist_config.get("staff_ids")
        if not isinstance(staff_ids, list) or not staff_ids:
            return None

        template_uuid = whitelist_config.get("template_uuid")
        if not isinstance(template_uuid, str) or not template_uuid.strip():
            return None

        template_uuid = template_uuid.strip()
        # 防止错误配置的 UUID 不经格式校验直接传给 BaaS，产生难以排查的下游错误。
        if not template_uuid.startswith("TEMPLATE-"):
            logger.warning(
                "[template.whitelist] invalid template_uuid format in whitelist config: "
                "env=%s config_key=%s template_uuid=%s",
                env,
                PERSONAL_BOT_TEST_TEMPLATE_WHITELIST_CONFIG_KEY,
                template_uuid,
            )
            return None

        if str(user_id) in {str(staff_id) for staff_id in staff_ids}:
            return template_uuid

        return None

    def _read_mapping(self, *, env: str) -> dict[str, Any]:
        """读取当前环境的 BaaS template 路由配置。"""
        # template 路由配置按环境隔离，pre/prod 可配置不同 UUID。
        try:
            mapping = self._system_config_service.get_config(
                category=BAAS_TEMPLATE_MAPPING_CATEGORY,
                config_key=BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY,
                env=env,
            )
        except Exception as e:
            raise BaasTemplateResolveError(
                "failed to read BaaS template mapping from system_config: "
                f"env={env}, config_key={BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY}, error={e}"
            ) from e

        if not isinstance(mapping, dict):
            raise BaasTemplateResolveError(
                "missing or invalid BaaS template mapping in system_config: "
                f"env={env}, config_key={BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY}"
            )
        return mapping

    @staticmethod
    def _extract_template_uuid(
        *,
        mapping: dict[str, Any],
        env: str,
        template_uid: str,
    ) -> str:
        """从配置映射中取出 template_uid 对应的 BaaS template_uuid。"""
        # templates 是 template_uid 到 BaaS template_uuid 的集中映射表。
        templates = mapping.get("templates")
        if not isinstance(templates, dict):
            raise BaasTemplateResolveError(
                "invalid BaaS template mapping: missing templates object, "
                f"env={env}, config_key={BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY}"
            )

        template_entry = templates.get(template_uid)
        if not isinstance(template_entry, dict):
            raise BaasTemplateResolveError(
                "BaaS template uid not configured: "
                f"env={env}, template_uid={template_uid}, "
                f"config_key={BAAS_TEMPLATE_UID_ROUTING_CONFIG_KEY}"
            )

        template_uuid = template_entry.get("template_uuid")
        if not isinstance(template_uuid, str) or not template_uuid.strip():
            raise BaasTemplateResolveError(
                "invalid BaaS template_uuid: "
                f"env={env}, template_uid={template_uid}, "
                f"template_uuid={template_uuid!r}"
            )
        template_uuid = template_uuid.strip()
        if not template_uuid.startswith("TEMPLATE-"):
            raise BaasTemplateResolveError(
                "invalid BaaS template_uuid format: "
                f"env={env}, template_uid={template_uid}, "
                f"template_uuid={template_uuid!r}"
            )
        return template_uuid

    @staticmethod
    def _config_version(mapping: dict[str, Any]) -> str:
        """提取配置版本，便于日志和线上排查对齐。"""
        version = mapping.get("version")
        return version.strip() if isinstance(version, str) else ""
