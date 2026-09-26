"""
Profile Key Canonicalizer

profile_key 规范化工具，解决不同格式的 profile_key 匹配问题。

问题背景：
- 传入的 participant 格式: `wrk_test_reg_g5_security_audit_expert:default`
- 系统扫描的 profile_key 格式: `wrk_test_reg_g1_architect:default`
- 直接字符串匹配会失败

规范化策略：
1. 优先通过 binding store 查询映射
2. 如果没有 binding，尝试兼容性规范化
   - worker_id 前缀匹配 (wrk_*, bot_*)
   - profile_id 后缀匹配 (:default, :v1)

使用方式：
```python
canonicalizer = ProfileKeyCanonicalizer(binding_store)
canonical_keys = canonicalizer.canonicalize(raw_keys, available_keys)
```
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_profile_binding_store_adapter import WorkerProfileBindingStoreAdapter

logger = logging.getLogger(__name__)


class ProfileKeyCanonicalizer:
    """
    profile_key 规范化器

    将不同格式的 profile_key 规范化为统一格式，以便正确匹配。

    Attributes:
        _binding_store: Worker Profile 绑定存储（可选）
    """

    # 已知的前缀模式
    KNOWN_PREFIXES = [
        "staff_",  # staff 前缀 (file scanner: staff_xxx directories)
        "wrk_",  # worker 前缀
        "bot_",  # bot 前缀
    ]

    def __init__(
        self,
        binding_store: Optional["WorkerProfileBindingStoreAdapter"] = None,
    ):
        """
        初始化规范化器

        Args:
            binding_store: Worker Profile 绑定存储（可选，用于精确映射）
        """
        self._binding_store = binding_store

    def canonicalize(
        self,
        raw_keys: list[str],
        available_keys: Optional[set[str]] = None,
    ) -> dict[str, str]:
        """
        将原始 profile_keys 规范化

        Args:
            raw_keys: 原始 profile_keys 列表
            available_keys: 可用的 profile_keys 集合（用于匹配）

        Returns:
            dict[str, str]: 原始 key -> 规范化 key 的映射
        """
        result: dict[str, str] = {}

        if not raw_keys:
            return result

        logger.info("[CANONICALIZER] 开始规范化 profile_keys")
        logger.info("[CANONICALIZER]   raw_keys 数量: %d", len(raw_keys))
        logger.info("[CANONICALIZER]   available_keys 数量: %s",
                   len(available_keys) if available_keys else "None")

        for raw_key in raw_keys:
            canonical_key = self._canonicalize_single(raw_key, available_keys)
            if canonical_key:
                result[raw_key] = canonical_key
                if raw_key != canonical_key:
                    logger.debug("[CANONICALIZER]   %s -> %s", raw_key, canonical_key)
            else:
                # 无法规范化，保留原始 key
                result[raw_key] = raw_key
                logger.warning("[CANONICALIZER]   无法规范化: %s，保留原值", raw_key)

        # 统计
        matched_count = sum(1 for k, v in result.items() if k != v)
        logger.info("[CANONICALIZER] 规范化完成: %d/%d 个 key 需要映射",
                   matched_count, len(raw_keys))

        return result

    def _canonicalize_single(
        self,
        raw_key: str,
        available_keys: Optional[set[str]] = None,
    ) -> Optional[str]:
        """
        规范化单个 profile_key

        策略优先级：
        1. 精确匹配（已经是规范格式）
        2. Binding store 查询
        3. 前缀兼容匹配（wrk_*, bot_*）
        4. 后缀兼容匹配

        Args:
            raw_key: 原始 profile_key
            available_keys: 可用的 profile_keys 集合

        Returns:
            规范化后的 profile_key，如果无法规范化则返回原始 raw_key
        """
        if not raw_key:
            return None

        # 1. 精确匹配
        if available_keys and raw_key in available_keys:
            return raw_key

        # 2. Binding store 查询
        # 使用 binding 中的 worker_id 信息重新构建 possible profile_keys
        if self._binding_store:
            binding = self._binding_store.get_binding_by_profile_key(raw_key)
            if binding:
                # 尝试多种格式匹配
                # Profile Source 生成的 profile_key 格式: {worker_id}:{profile_id}
                # 注意: worker_id 本身可能包含冒号，所以 profile_id 是最后一部分
                worker_id = binding.worker_id
                profile_id = raw_key.split(":")[-1] if ":" in raw_key else "default"

                # 尝试多种可能的格式
                possible_keys = [
                    binding.profile_key,  # 原始 binding 中的 profile_key
                    f"{worker_id}:{profile_id}",  # wrk_xxx:default
                ]

                for candidate in possible_keys:
                    if available_keys and candidate in available_keys:
                        logger.debug("[CANONICALIZER]     Binding 匹配: %s -> %s (via worker_id=%s)",
                                   raw_key, candidate, worker_id)
                        return candidate

        # 3. 如果没有 available_keys，直接返回原始 key
        if not available_keys:
            return raw_key

        # 4. 前缀兼容匹配
        # 尝试添加/移除已知前缀
        for prefix in self.KNOWN_PREFIXES:
            # 尝试添加前缀
            if not raw_key.startswith(prefix):
                candidate = f"{prefix}{raw_key}"
                if candidate in available_keys:
                    logger.debug("[CANONICALIZER]     前缀匹配: %s -> %s", raw_key, candidate)
                    return candidate

            # 尝试移除前缀
            if raw_key.startswith(prefix):
                stripped = raw_key[len(prefix):]
                if stripped in available_keys:
                    logger.debug("[CANONICALIZER]     移除前缀: %s -> %s", raw_key, stripped)
                    return stripped

                # 尝试移除前缀后再添加其他前缀
                for other_prefix in self.KNOWN_PREFIXES:
                    if other_prefix != prefix:
                        candidate = f"{other_prefix}{stripped}"
                        if candidate in available_keys:
                            logger.debug("[CANONICALIZER]     前缀替换: %s -> %s", raw_key, candidate)
                            return candidate

        # 5. 后缀兼容匹配
        # 尝试匹配 worker_id 部分（忽略前缀差异）
        # 注意: worker_id 本身可能包含冒号，所以 profile_id 是最后一部分
        key_parts = raw_key.split(":")
        if len(key_parts) >= 2:
            # worker_id 是除最后一部分外的所有内容，profile_id 是最后一部分
            profile_id = key_parts[-1]
            worker_id = ":".join(key_parts[:-1])

            for prefix in self.KNOWN_PREFIXES:
                # 尝试匹配去掉前缀后的 worker_id
                if worker_id.startswith(prefix):
                    core_id = worker_id[len(prefix):]

                    for available_key in available_keys:
                        avail_parts = available_key.split(":")
                        if len(avail_parts) >= 2:
                            # 同样处理 available_key
                            avail_profile_id = avail_parts[-1]
                            avail_worker_id = ":".join(avail_parts[:-1])

                            # 检查 core_id 和 profile_id 是否匹配
                            for avail_prefix in self.KNOWN_PREFIXES:
                                if avail_worker_id.startswith(avail_prefix):
                                    avail_core_id = avail_worker_id[len(avail_prefix):]
                                    if core_id == avail_core_id and profile_id == avail_profile_id:
                                        logger.debug(
                                            "[CANONICALIZER]     核心匹配: %s -> %s (core=%s)",
                                            raw_key, available_key, core_id
                                        )
                                        return available_key

        # 6. 尝试添加默认 profile_id 后缀
        # 当传入纯 worker_id (如 wrk_algorithm_expert)，尝试匹配 wrk_algorithm_expert:default
        if ":" not in raw_key:
            # 尝试添加 :default 后缀
            candidate = f"{raw_key}:default"
            if candidate in available_keys:
                logger.debug("[CANONICALIZER]     添加默认后缀: %s -> %s", raw_key, candidate)
                return candidate

            # 尝试添加其他常见后缀
            for suffix in ["v1", "latest", "active"]:
                candidate = f"{raw_key}:{suffix}"
                if candidate in available_keys:
                    logger.debug("[CANONICALIZER]     添加后缀: %s -> %s", raw_key, candidate)
                    return candidate

            # 尝试匹配任何以 raw_key 为前缀的 profile_key
            for available_key in available_keys:
                if available_key.startswith(raw_key + ":"):
                    logger.debug("[CANONICALIZER]     前缀匹配: %s -> %s", raw_key, available_key)
                    return available_key

        # 无法规范化，保留原始 key
        logger.debug("[CANONICALIZER]     无法匹配，保留原始 key: %s", raw_key)
        return raw_key

    def extract_worker_id(self, profile_key: str) -> Optional[str]:
        """
        从 profile_key 提取 worker_id

        Args:
            profile_key: profile_key (格式: "worker_id:profile_id")
            注意: worker_id 本身可能包含冒号

        Returns:
            worker_id 或 None
        """
        if not profile_key:
            return None

        parts = profile_key.split(":")
        if len(parts) > 1:
            # worker_id 可能包含冒号，取除最后一部分外的所有
            return ":".join(parts[:-1])
        return profile_key

    def extract_profile_id(self, profile_key: str) -> str:
        """
        从 profile_key 提取 profile_id

        Args:
            profile_key: profile_key (格式: "worker_id:profile_id")
            注意: worker_id 本身可能包含冒号，所以 profile_id 是最后一部分

        Returns:
            profile_id (默认 "default")
        """
        if not profile_key:
            return "default"

        parts = profile_key.split(":")
        if len(parts) >= 2:
            # worker_id 可能包含冒号，profile_id 是最后一部分
            return parts[-1]
        return "default"


__all__ = ["ProfileKeyCanonicalizer"]