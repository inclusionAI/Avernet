"""
Fuse Utility Functions - 融合相关工具函数

提供融合操作所需的通用工具函数，包括：
- fusion_id 生成
- Profile 内容哈希计算
- 参与者 ID 解析和格式化

根据 fusion-storage-design.md 规范，工具函数集中管理于此。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from src.domain.enums.fuse_enums import FusionMode
    from src.domain.models.profile_fusion import FusionContext


def generate_fusion_id(
    fusion_mode: str,
    ctx: "FusionContext | None" = None,
) -> str:
    """
    生成融合唯一标识 fusion_id。

    根据融合模式采用不同策略：
    - G9 模式 (bot_profile_fuse): 基于 FusionContext 中的参数生成MD5哈希，
      相同输入产生相同ID，支持结果复用
    - G1/G2/G5 模式: 生成UUID，每次请求独立

    Args:
        fusion_mode: 融合模式，参见 FusionMode 枚举
        ctx: 融合上下文（G9模式必需），包含 participant_ids, profiles_dict, group_id, driver_bot_id

    Returns:
        fusion_id: 32位hex字符串

    Examples:
        >>> # G9 模式（内容哈希）
        >>> from src.domain.models import FusionContext
        >>> ctx = FusionContext(
        ...     group_id="group_123",
        ...     driver_bot_id="bot_001",
        ...     participant_ids=["wrk_arch:default", "wrk_dba:default"],
        ...     profiles_dict=[{"soul_md": "...", "skills": [...]}],
        ... )
        >>> fusion_id = generate_fusion_id("bot_profile_fuse", ctx=ctx)
        >>> len(fusion_id)
        32

        >>> # G1/G2/G5 模式（UUID）
        >>> fusion_id = generate_fusion_id(fusion_mode="agent")
        >>> len(fusion_id)
        32
    """
    # G9 模式：使用内容哈希作为ID，支持去重复用
    if fusion_mode == "bot_profile_fuse":
        if ctx is None or not ctx.participant_ids or not ctx.profiles_dict or not ctx.group_id:
            # 参数不足时降级为 UUID
            return uuid.uuid4().hex

        # 1. 排序确保顺序无关
        sorted_ids = sorted(ctx.participant_ids)

        # 2. 计算每个 profile 关键内容的 hash（取前8位）
        content_hashes = []
        for profile in ctx.profiles_dict:
            content_hash = calculate_profile_content_hash(profile)
            content_hashes.append(content_hash)

        # 3. 组合生成 ID（包含 group_id 和 driver_bot_id）
        # 格式: driver_bot_id@group_id:sorted_ids:content_hashes
        driver_prefix = f"{ctx.driver_bot_id}@" if ctx.driver_bot_id else ""
        key_str = f"{driver_prefix}{ctx.group_id}:{'+'.join(sorted_ids)}:{','.join(sorted(content_hashes))}"
        return hashlib.md5(key_str.encode()).hexdigest()

    # G1/G2/G5 模式：每次请求生成唯一ID
    return uuid.uuid4().hex


def calculate_profile_content_hash(profile: dict) -> str:
    """
    计算单个 Profile 的内容哈希。

    基于 Profile 的核心内容字段计算 MD5 哈希，用于：
    - G9 模式的 fusion_id 生成
    - 判断 Profile 内容是否变化

    使用字段（按优先级）：
    - soul_md 或 soul：核心身份定位
    - identity_md 或 identity：身份信息
    - memory_md 或 memory：经验知识
    - skills：技能列表

    Args:
        profile: Profile 字典，包含 soul/soul_md, identity/identity_md,
                 memory/memory_md, skills 等字段

    Returns:
        8位 hex 哈希字符串

    Examples:
        >>> profile = {
        ...     "soul_md": "我是一名系统架构师",
        ...     "identity_md": "10年分布式系统经验",
        ...     "memory_md": "曾主导过多个高可用系统设计",
        ...     "skills": ["code_review", "deployment"]
        ... }
        >>> calculate_profile_content_hash(profile)
        'a1b2c3d4'
    """
    # 提取关键字段，优先使用 _md 版本
    soul = profile.get("soul_md", "") or profile.get("soul", "") or ""
    identity = profile.get("identity_md", "") or profile.get("identity", "") or ""
    memory = profile.get("memory_md", "") or profile.get("memory", "") or ""

    # skills 需要排序确保顺序无关
    skills_data = profile.get("skills", [])
    if isinstance(skills_data, list):
        skills = ",".join(sorted(str(s) for s in skills_data))
    else:
        skills = ""

    # 组合内容并计算哈希
    content = f"{soul}|{identity}|{memory}|{skills}"
    return hashlib.md5(content.encode()).hexdigest()[:8]


def parse_participant_ids(participant_ids_str: str) -> list[str]:
    """
    解析逗号分隔的参与者ID字符串。

    将数据库中存储的逗号分隔字符串转换为列表。

    Args:
        participant_ids_str: 逗号分隔的参与者ID字符串，
                             如 "wrk_arch:default,wrk_dba:default"

    Returns:
        参与者ID列表，如 ["wrk_arch:default", "wrk_dba:default"]

    Examples:
        >>> parse_participant_ids("wrk_arch:default,wrk_dba:default")
        ['wrk_arch:default', 'wrk_dba:default']
        >>> parse_participant_ids("")
        []
        >>> parse_participant_ids(None)
        []
    """
    if not participant_ids_str:
        return []
    return [pid.strip() for pid in participant_ids_str.split(",") if pid.strip()]


def format_participant_ids(participant_ids: list[str]) -> str:
    """
    将参与者ID列表格式化为逗号分隔字符串（按字母序排序）。

    用于将列表存储到数据库的 TEXT 字段。

    Args:
        participant_ids: 参与者ID列表

    Returns:
        排序后的逗号分隔字符串

    Examples:
        >>> format_participant_ids(["wrk_dba:default", "wrk_arch:default"])
        'wrk_arch:default,wrk_dba:default'
        >>> format_participant_ids([])
        ''
    """
    if not participant_ids:
        return ""
    return ",".join(sorted(participant_ids))


def safe_json_serialize(obj: object, ensure_ascii: bool = False) -> str:
    """
    安全的 JSON 序列化。

    处理 datetime 等标准库类型，确保可序列化。

    Args:
        obj: 要序列化的对象
        ensure_ascii: 是否确保 ASCII 输出

    Returns:
        JSON 字符串
    """
    import json
    from datetime import datetime

    def default_handler(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    return json.dumps(obj, ensure_ascii=ensure_ascii, default=default_handler)


def safe_json_deserialize(json_str: str) -> dict | list | None:
    """
    安全的 JSON 反序列化。

    Args:
        json_str: JSON 字符串

    Returns:
        反序列化后的对象，解析失败返回 None
    """
    import json

    if not json_str:
        return None

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def get_current_timestamp() -> str:
    """
    获取当前时间戳（ISO 8601 格式）。

    Returns:
        ISO 8601 格式的时间戳字符串
    """
    from datetime import datetime
    return datetime.now().isoformat()


def worker_profile_to_dict(profile) -> dict:
    """
    将 WorkerProfileContent 转换为字典格式（用于存储和 fusion_id 计算）

    Args:
        profile: WorkerProfileContent 对象

    Returns:
        包含关键字段的字典
    """
    return {
        "worker_id": profile.worker_id,
        "soul_md": profile.soul_md or "",
        "identity_md": profile.contents.get("identity.md", "") if profile.contents else "",
        "memory_md": profile.contents.get("memory.md", "") if profile.contents else "",
        "skills": [s.name for s in profile.skill_sets] if profile.skill_sets else [],
        "display_name": profile.display_name or "",
        "description": profile.description or "",
    }


__all__ = [
    "generate_fusion_id",
    "calculate_profile_content_hash",
    "parse_participant_ids",
    "format_participant_ids",
    "safe_json_serialize",
    "safe_json_deserialize",
    "get_current_timestamp",
    "worker_profile_to_dict",
]