import json
from datetime import date

from injector import inject

from agentclaw.community.core.access.repository import PolicyRepository
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

_POLICY_ON = json.dumps({"policy": "on"})
_POLICY_OFF = json.dumps({"policy": "off"})

# 用户类型和状态常量
USER_TYPE_COMPETE = "COMPETE"
USER_STATUS_ACCESS = "ACCESS"
USER_STATUS_REFUSE = "REFUSE"

logger = get_logger()


class PolicyService:
    @inject
    def __init__(self, repository: PolicyRepository) -> None:
        self._repo = repository

    def check(self, *, entity_id: str, entity_type: str) -> bool:
        """检查 entity 是否有访问权限。

        流程：
        1. 检查白名单（ac_access_control_policy表，entity_type='staff'）
           - policy=on: 返回 true
           - policy=off: 直接返回 false（禁止访问，不进入竞争）
        2. 检查竞争用户表（ac_user_info表，user_type=COMPETE，status=ACCESS）
        3. 否则进入竞争逻辑，判断是否可以获得竞争名额

        Returns:
            True if allowed, False otherwise.
        """
        # 1. 检查白名单
        policy_record = self._repo.get_by_entity(entity_id=entity_id, entity_type="staff")
        if policy_record:
            if self._is_policy_on(policy_record.policy):
                logger.info("[check] entity_id=%s in whitelist, allowed", entity_id)
                return True
            if self._is_policy_off(policy_record.policy):
                logger.info("[check] entity_id=%s in blacklist with policy=off, denied", entity_id)
                return False

        # 2. 检查竞争用户表
        user_record = self._repo.get_user_info(user_id=entity_id, user_type=USER_TYPE_COMPETE)
        if user_record and user_record.status == USER_STATUS_ACCESS:
            logger.info("[check] entity_id=%s in compete list with status=ACCESS, allowed", entity_id)
            return True

        # 3. 进入竞争逻辑
        logger.info("[check] entity_id=%s not allowed, entering compete logic", entity_id)
        return self._try_compete(entity_id=entity_id)

    def _is_policy_on(self, policy: str | None) -> bool:
        """检查policy是否为on。"""
        if policy is None:
            return False
        try:
            data = json.loads(policy)
            return data.get("policy") == "on"
        except (json.JSONDecodeError, AttributeError):
            return False

    def _is_policy_off(self, policy: str | None) -> bool:
        """检查policy是否为off。"""
        if policy is None:
            return False
        try:
            data = json.loads(policy)
            return data.get("policy") == "off"
        except (json.JSONDecodeError, AttributeError):
            return False

    def _parse_policy_json(self, policy: str | None) -> dict:
        """解析 policy JSON 字符串，解析失败返回空 dict。"""
        if policy is None:
            return {}
        try:
            return json.loads(policy)
        except (json.JSONDecodeError, AttributeError):
            return {}

    def get_bots_ceiling(self, *, entity_id: str, default: int = 5) -> int:
        """获取指定用户的 BOT 数量上限。

        读取 ``ac_access_control_policy.policy`` 中的 ``bots_ceiling`` 字段：
        - 无该用户记录 → 返回 default
        - policy 字段为 NULL → 返回 default
        - 无 ``bots_ceiling`` key → 返回 default
        - ``bots_ceiling`` 非数字 → 返回 default
        - ``bots_ceiling`` <= 0 → 返回 default
        - ``bots_ceiling`` > 0 → 返回该值
        """
        record = self._repo.get_by_entity(entity_id=entity_id, entity_type="staff")
        if not record or not record.policy:
            return default

        data = self._parse_policy_json(record.policy)
        raw_ceiling = data.get("bots_ceiling")
        if raw_ceiling is None:
            return default

        try:
            ceiling = int(raw_ceiling)
        except (ValueError, TypeError):
            logger.warning(
                "[get_bots_ceiling] invalid bots_ceiling value for entity_id=%s: %r",
                entity_id, raw_ceiling,
            )
            return default

        if ceiling <= 0:
            return default

        return ceiling

    def set_bots_ceiling(self, *, entity_id: str, ceiling: int) -> None:
        """设置指定用户的 BOT 数量上限（merge 进现有 policy JSON）。

        保留现有 policy 中的其他 key（如 ``policy``），仅更新 ``bots_ceiling``。
        如果用户尚无 policy 记录，则创建一条。
        """
        record = self._repo.get_by_entity(entity_id=entity_id, entity_type="staff")
        existing = self._parse_policy_json(record.policy) if record else {}

        updated = {**existing, "bots_ceiling": str(ceiling)}
        self._repo.upsert_policy(
            entity_id=entity_id,
            entity_type="staff",
            policy=json.dumps(updated, ensure_ascii=False),
        )
        logger.info(
            "[set_bots_ceiling] entity_id=%s, ceiling=%d", entity_id, ceiling,
        )

    def _try_compete(self, *, entity_id: str) -> bool:
        """尝试竞争名额。"""
        env = get_current_env()

        # 获取有效配额
        effective_quota = self._get_effective_quota(env)
        if effective_quota <= 0:
            logger.info("[_try_compete] effective_quota <= 0, compete failed")
            return False

        # 获取更新时间配置
        update_time_record = self._repo.get_config_by_key(config_key="daily_container_update_time", category="system", env=env)
        if not update_time_record:
            logger.warning("[_try_compete] missing update_time_record")
            return False

        # 构建今天更新时间点的datetime字符串
        update_time_str = update_time_record.config_value.strip()  # 格式如 "09:00"
        today_str = date.today().strftime("%Y-%m-%d")
        start_datetime = f"{today_str} {update_time_str}:00"
        logger.info("[_try_compete] effective_quota=%s, start_datetime=%s", effective_quota, start_datetime)

        # 统计已竞争成功的用户数量
        current_count = self._repo.count_compete_users_after_time(start_time=start_datetime)
        logger.info("[_try_compete] current_count=%s, effective_quota=%s", current_count, effective_quota)

        # 判断是否可以获得名额
        if current_count < effective_quota:
            # 获得名额，更新或新增记录到用户表
            self._repo.upsert_user_info(user_id=entity_id, user_type=USER_TYPE_COMPETE, status=USER_STATUS_ACCESS)
            logger.info("[_try_compete] entity_id=%s compete success, new_count=%s", entity_id, current_count + 1)
            return True
        else:
            logger.info("[_try_compete] entity_id=%s compete failed, quota exhausted", entity_id)
            return False

    def _get_effective_quota(self, env: str) -> int:
        """计算有效配额：min(daily_container_quota, total_container_limit - 当前占用)。"""
        # 获取配额配置
        quota_record = self._repo.get_config_by_key(config_key="daily_container_quota", category="system", env=env)
        total_limit_record = self._repo.get_config_by_key(config_key="total_container_limit", category="system", env=env)

        if not quota_record:
            logger.warning("[_get_effective_quota] missing quota_record")
            return 0

        try:
            daily_quota = int(quota_record.config_value)
        except (ValueError, TypeError):
            logger.warning("[_get_effective_quota] invalid daily_quota value: %s", quota_record.config_value)
            return 0

        # 如果没有总量限制配置，直接返回日配额
        if not total_limit_record:
            logger.info("[_get_effective_quota] no total_limit_record, using daily_quota=%s", daily_quota)
            return daily_quota

        try:
            total_limit = int(total_limit_record.config_value)
        except (ValueError, TypeError):
            logger.warning("[_get_effective_quota] invalid total_limit value: %s", total_limit_record.config_value)
            return daily_quota

        # 统计当前占用机器数
        active_count = self._repo.count_active_devices(env=env)
        available_slots = total_limit - active_count

        # 有效配额 = min(日配额, 总量-当前占用)
        effective_quota = min(daily_quota, available_slots)
        logger.info(
            "[_get_effective_quota] daily_quota=%s, total_limit=%s, active_count=%s, available_slots=%s, effective_quota=%s",
            daily_quota, total_limit, active_count, available_slots, effective_quota
        )
        return max(0, effective_quota)

    def allow(self, *, entity_id: str, entity_type: str) -> None:
        """将 entity 加入白名单（设置 policy=on，merge 模式保留其他 key）。"""
        record = self._repo.get_by_entity(entity_id=entity_id, entity_type=entity_type)
        existing = self._parse_policy_json(record.policy) if record else {}
        updated = {**existing, "policy": "on"}
        self._repo.upsert_policy(
            entity_id=entity_id,
            entity_type=entity_type,
            policy=json.dumps(updated, ensure_ascii=False),
        )

    def disallow(self, *, entity_id: str, entity_type: str) -> None:
        """将 entity 移出白名单（设置 policy=off，merge 模式保留其他 key）。"""
        record = self._repo.get_by_entity(entity_id=entity_id, entity_type=entity_type)
        existing = self._parse_policy_json(record.policy) if record else {}
        updated = {**existing, "policy": "off"}
        self._repo.upsert_policy(
            entity_id=entity_id,
            entity_type=entity_type,
            policy=json.dumps(updated, ensure_ascii=False),
        )

    def get_quota(self) -> dict:
        """获取容器配额配置。

        Returns:
            包含 quota、updateTime、effectiveQuota 等信息的字典。
        """
        env = get_current_env()
        logger.info("[get_quota] start querying config, env=%s", env)

        # 获取配置
        quota_record = self._repo.get_config_by_key(config_key="daily_container_quota", category="system", env=env)
        total_limit_record = self._repo.get_config_by_key(config_key="total_container_limit", category="system", env=env)
        update_record = self._repo.get_config_by_key(config_key="daily_container_update_time", category="system", env=env)

        logger.info("[get_quota] quota_record: %s, total_limit_record: %s, update_record: %s", quota_record, total_limit_record, update_record)

        # 计算各项值
        daily_quota = int(quota_record.config_value) if quota_record else 0
        total_limit = int(total_limit_record.config_value) if total_limit_record else 0
        active_count = self._repo.count_active_devices(env=env)
        effective_quota = self._get_effective_quota(env)

        result = {
            "quota": daily_quota,
            "totalLimit": total_limit,
            "activeCount": active_count,
            "effectiveQuota": effective_quota,
            "updateTime": update_record.config_value if update_record else "",
        }
        logger.info("[get_quota] result: %s", result)
        return result
