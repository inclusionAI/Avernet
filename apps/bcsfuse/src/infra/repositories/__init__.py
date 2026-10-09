"""
Infrastructure Repositories

存储接口定义。

已定义的接口：
- FusedProfileRepository: 融合结果存储接口

内存实现位于 src.infra.adapters：
- InMemoryFusedProfileStore: 融合结果内存存储

NOTE: InMemoryWorkerRepository 和 InMemoryFusedProfileStore 是 PLACEHOLDER 实现，不用于生产环境。
"""

from src.infra.repositories.in_memory_worker_repository import InMemoryWorkerRepository
from src.infra.repositories.fused_profile_repository import FusedProfileRepository

__all__ = [
    "InMemoryWorkerRepository",
    "FusedProfileRepository",
]