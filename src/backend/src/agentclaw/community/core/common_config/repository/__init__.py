"""Common config repository layer."""

from .common_config_repository import CommonConfigRepository
from .protocol import CommonConfigRepositoryProtocol

__all__ = [
    "CommonConfigRepository",
    "CommonConfigRepositoryProtocol",
]
