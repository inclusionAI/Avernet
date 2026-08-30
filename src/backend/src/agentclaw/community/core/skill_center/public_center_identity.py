"""Stable local identity for one SC Public catalogue asset."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PublicCenterSkillIdentity:
    """Map an opaque SC code to a deterministic canonical UUIDv4 wire value."""

    skill_code: str
    locator: str
    skill_uuid: str

    @classmethod
    def derive(
        cls, *, tenant: str, env: str, skill_code: str
    ) -> "PublicCenterSkillIdentity":
        for field, value in {
            "tenant": tenant,
            "env": env,
            "skill_code": skill_code,
        }.items():
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or "\x00" in value
            ):
                raise ValueError(f"{field} must be a non-empty unpadded string")
        name = f"avernet:{tenant}:{env}:center:{skill_code}".encode("utf-8")
        # UUID(bytes=..., version=4) deterministically pins the RFC variant and
        # v4 bits while retaining 122 hash bits.  Canonical Store validates the
        # wire shape as UUIDv4; the hash input provides cross-process convergence.
        skill_uuid = str(UUID(bytes=hashlib.sha256(name).digest()[:16], version=4))
        return cls(
            skill_code=skill_code,
            locator=f"center://{skill_code}",
            skill_uuid=skill_uuid,
        )


__all__ = ["PublicCenterSkillIdentity"]
