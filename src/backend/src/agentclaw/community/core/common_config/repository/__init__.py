"""Common config repository layer."""

from .common_config_repository import CommonConfigRepository
from agentclaw.community.core.repository.protocols.config import CommonConfigRepositoryProtocol

__all__ = [
    "CommonConfigRepository",
    "CommonConfigRepositoryProtocol",
]
