"""
Worker Profile Source Protocol

Worker Profile Ingestion Baseline

定义 Worker Profile 来源的抽象接口。

日志增强规范：
====================

所有实现类必须遵循以下日志规范：

1. 错误日志（使用 ❌ 标记）：
   - Profile 加载失败
   - Profile 格式错误
   - Profile 数据缺失（必需字段）
   - 文件/资源访问错误
   - 解析错误

   示例：
   ```python
   logger.error("❌ [ProfileSource] Failed to load profile: {profile_key}, reason: {error_msg}")
   logger.error("❌ [ProfileSource] Profile format error: {profile_key}, field={field_name}, expected={expected}")
   logger.error("❌ [ProfileSource] Profile data missing: {profile_key}, missing_fields={fields}")
   ```

2. 警告日志（使用 ⚠️ 标记）：
   - Profile 可选字段缺失
   - Profile 数据质量警告
   - 性能警告
   - 兼容性警告

   示例：
   ```python
   logger.warning("⚠️ [ProfileSource] Profile field deprecated: {profile_key}, field={field}")
   logger.warning("⚠️ [ProfileSource] Profile quality warning: {profile_key}, issue={issue}")
   logger.warning("⚠️ [ProfileSource] Slow profile load: {profile_key}, elapsed={elapsed_ms}ms")
   ```

3. 成功日志（使用 ✅ 标记）：
   - 成功加载 profile
   - 成功扫描 profiles
   - 关键操作完成

   示例：
   ```python
   logger.info("✅ [ProfileSource] Successfully loaded profile: {profile_key}, staff_id={staff_id}")
   logger.info("✅ [ProfileSource] Profile scan completed, profiles_count={count}, elapsed={elapsed_ms}ms")
   ```

上下文信息要求：
=================

所有日志必须包含以下上下文信息：
- profile_key: Profile 的唯一标识
- staff_id: 员工 ID
- source_type: 来源类型（file/api/registry）
- 错误详情：具体的错误原因和字段信息
- 性能指标：操作耗时
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.domain.models.worker_profile import (
    WorkerProfile,
    WorkerProfileScanResult,
)


@runtime_checkable
class WorkerProfileSource(Protocol):
    """
    Worker Profile 来源接口

    定义 Worker Profile 数据来源的基本操作。

    使用 Protocol 而非 ABC，允许 duck typing，
    但仍能通过 isinstance 检查。

    实现可以是：
    - FileWorkerProfileSource: 文件系统来源
    - ApiWorkerProfileSource: API 来源（未来）
    - RegistryWorkerProfileSource: 注册中心来源（未来）
    """

    def scan(self) -> WorkerProfileScanResult:
        """
        扫描并返回所有 WorkerProfile

        实现类日志要求：
        - 成功：logger.info("✅ [ProfileSource] Profile scan completed, profiles_count={count}, source_type={type}")
        - 警告：logger.warning("⚠️ [ProfileSource] Profile scan warning, issue={issue}, affected_profiles={keys}")
        - 错误：logger.error("❌ [ProfileSource] Profile scan failed, error={error}, source_type={type}")
        - 空：logger.warning("⚠️ [ProfileSource] No profiles found after scan, source_type={type}, source_root={root}")

        Returns:
            WorkerProfileScanResult: 扫描结果，包含：
                - profiles: 扫描到的 WorkerProfile 列表
                - scan_warnings: 扫描层面的警告
                - source_roots: 扫描的源根目录列表
        """
        ...

    def get_profile(
        self, staff_id: str, profile_id: str
    ) -> Optional[WorkerProfile]:
        """
        获取指定 WorkerProfile

        实现类日志要求：
        - 成功：logger.info("✅ [ProfileSource] Profile loaded, profile_key={key}, staff_id={staff_id}")
        - 未找到：logger.warning("⚠️ [ProfileSource] Profile not found, profile_key={key}, staff_id={staff_id}")
        - 错误：logger.error("❌ [ProfileSource] Failed to load profile, profile_key={key}, staff_id={staff_id}, error={error}")
        - 加载失败：logger.error("❌ [ProfileSource] Profile load failed, profile_key={key}, reason={reason}")

        Args:
            staff_id: 员工 ID
            profile_id: 画像 ID

        Returns:
            WorkerProfile 或 None
        """
        ...

    def get_profiles_by_staff(self, staff_id: str) -> list[WorkerProfile]:
        """
        获取指定员工的所有 WorkerProfile

        实现类日志要求：
        - 成功且有结果：logger.info("✅ [ProfileSource] Profiles loaded by staff, staff_id={staff_id}, count={count}")
        - 成功但无结果：logger.warning("⚠️ [ProfileSource] No profiles found for staff, staff_id={staff_id}")
        - 错误：logger.error("❌ [ProfileSource] Failed to load profiles by staff, staff_id={staff_id}, error={error}")

        Args:
            staff_id: 员工 ID

        Returns:
            WorkerProfile 列表
        """
        ...


__all__ = ["WorkerProfileSource"]