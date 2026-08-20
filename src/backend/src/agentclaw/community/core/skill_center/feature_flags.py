"""SkillCenter feature flags — 统一读取入口。

所有 skill_center 相关 feature flag 必须通过本模块读取，禁止硬编码或直接读环境变量。
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SkillCenterFlags:
    """SkillCenter feature flags (immutable)."""
    nas_sync_enabled: bool
    center_uri_enabled: bool
    propagation_enabled: bool
    symlink_verify_auto_fix: bool
    write_skill_uuid: bool  # 双写控制，不关

    @classmethod
    def from_env(cls) -> "SkillCenterFlags":
        """从环境变量（ConfigCenter 注入）读取 flag 值。"""
        return cls(
            nas_sync_enabled=os.environ.get("SC_NAS_SYNC_ENABLED", "false").lower() == "true",
            center_uri_enabled=os.environ.get("SC_CENTER_URI_ENABLED", "false").lower() == "true",
            propagation_enabled=os.environ.get("SC_PROPAGATION_ENABLED", "false").lower() == "true",
            symlink_verify_auto_fix=os.environ.get("SC_SYMLINK_AUTO_FIX", "false").lower() == "true",
            write_skill_uuid=os.environ.get("SC_WRITE_SKILL_UUID", "true").lower() == "true",
        )

    def any_blocking_enabled(self) -> bool:
        """任一核心 flag 开启（用于日志采样等）。"""
        return self.nas_sync_enabled or self.center_uri_enabled or self.propagation_enabled


# 全局单例（延迟初始化）
_flags: "SkillCenterFlags | None" = None


def get_skill_center_flags() -> SkillCenterFlags:
    """获取当前 SkillCenter feature flags（全局单例）。"""
    global _flags
    if _flags is None:
        _flags = SkillCenterFlags.from_env()
    return _flags
