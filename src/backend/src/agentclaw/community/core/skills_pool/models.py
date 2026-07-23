"""Skills Pool 激活闭环使用的领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenClawPoolPaths:
    """OpenClaw P3 的容器视角路径契约。"""

    active: str = "/home/admin/.openclaw/workspace/skills"
    legacy_local: str = "/home/admin/.openclaw/workspace/skills/skills-local"
    pool_local: str = (
        "/home/admin/.openclaw/workspace/skills-pool/skills-local"
    )
    pool_repo: str = (
        "/home/admin/.openclaw/workspace/skills-pool/skills-repo"
    )


@dataclass(frozen=True, slots=True)
class RegisteredSkillAsset:
    """Backend 中属于一个 Bot 的技能来源记录。"""

    skill_id: int
    name: str
    git_path: str


@dataclass(frozen=True, slots=True)
class PoolSkillMapping:
    """显式以目标 Pool layout 构造的运行时 mapping。"""

    source: str
    target: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "target": self.target}


__all__ = [
    "OpenClawPoolPaths",
    "PoolSkillMapping",
    "RegisteredSkillAsset",
]
