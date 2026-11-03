"""System-owned expert provenance, carried only by trusted creation contexts."""

from dataclasses import asdict, dataclass
import json
from typing import Any


@dataclass(frozen=True)
class ExpertOrigin:
    source_repo_id: str
    expert_id: str
    expert_name: str
    source_commit: str
    content_digest: str
    manifest_digest: str
    digest_encoding: str = "sha256-json-v1"
    creation_task_key: str = ""

    def as_payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, value: dict) -> "ExpertOrigin":
        return cls(**value)


class ExpertOriginReadOnlyError(ValueError):
    """Ordinary metadata callers cannot write expert creation facts."""


def expert_name_from_ext(ext: Any) -> str | None:
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except (ValueError, TypeError):
            return None
    if not isinstance(ext, dict):
        return None
    origin = ext.get("expert_origin")
    name = origin.get("expert_name") if isinstance(origin, dict) else None
    return name if isinstance(name, str) else None


def reject_expert_origin_update(ext: dict | None) -> None:
    if ext is not None and "expert_origin" in ext:
        raise ExpertOriginReadOnlyError(
            "expert_origin is system-owned and cannot be modified"
        )
