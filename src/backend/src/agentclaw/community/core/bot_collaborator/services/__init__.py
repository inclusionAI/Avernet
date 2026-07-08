"""Bot 协作者服务层。"""
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
    CollaboratorServiceError,
    PermissionDeniedError,
    CollaboratorNotFoundError,
    CollaboratorAlreadyExistsError,
    CannotRemoveSelfError,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    CollaboratorLockService,
    CollaboratorLockServiceError,
    LockNotHeldError,
    LockReleaseDeniedError,
)

__all__ = [
    # CollaboratorService
    "CollaboratorService",
    "CollaboratorServiceError",
    "PermissionDeniedError",
    "CollaboratorNotFoundError",
    "CollaboratorAlreadyExistsError",
    "CannotRemoveSelfError",
    # CollaboratorLockService
    "CollaboratorLockService",
    "CollaboratorLockServiceError",
    "LockNotHeldError",
    "LockReleaseDeniedError",
]