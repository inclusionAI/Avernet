"""AICoding transfer configuration; no DI or configuration-file dependencies."""
from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True)
class FastdiskPublishConfig:
    detect_timeout_seconds: int = 30
    upload_timeout_seconds: int | None = None
    request_timeout_seconds: int = 30
    mist_secret_name: str = ""
    endpoint: str = ""

    def __post_init__(self):
        for value in (self.detect_timeout_seconds, self.request_timeout_seconds):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError('fastdisk publish timeouts must be positive integers')
        if self.upload_timeout_seconds is not None and (type(self.upload_timeout_seconds) is not int or self.upload_timeout_seconds <= 0):
            raise ValueError('upload timeout must be positive or None')
        if not isinstance(self.mist_secret_name, str) or self.mist_secret_name != self.mist_secret_name.strip():
            raise ValueError('invalid deployment credential name')


def parse_fastdisk_publish_config(raw: Any) -> FastdiskPublishConfig:
    """Map the deployment block, preserving existing defaults and validation."""
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("fastdisk_publish must be a mapping")
    block = {} if raw is None else dict(raw)
    allowed = {field.name for field in fields(FastdiskPublishConfig)}
    if set(block) - allowed:
        raise ValueError("unknown fastdisk_publish configuration key")
    return FastdiskPublishConfig(**block)
