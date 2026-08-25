"""Service API Protocol for bot collaborator management."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, runtime_checkable

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
    PermissionLevel,
)


@runtime_checkable
class CollaboratorServiceProtocol(Protocol):
    """Service API for managing bot collaborators."""

    def add_collaborator(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        operator_id: str,
        user_name: Optional[str] = None,
        role: str = CollaboratorRole.ADMIN,
        env: Optional[str] = None,
    ) -> CollaboratorRecord: ...

    def list_collaborators(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        role: Optional[str] = None,
        env: Optional[str] = None,
    ) -> List[CollaboratorRecord]: ...

    def batch_list_collaborators(
        self,
        bot_ids: list[str],
        user_id: str,
        role: Optional[str] = None,
        env: Optional[str] = None,
    ) -> List[CollaboratorRecord]: ...

    def update_collaborator(
        self,
        collaborator_id: int,
        operator_id: str,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        role: Optional[str] = None,
        env: Optional[str] = None,
    ) -> CollaboratorRecord: ...

    def remove_collaborator(
        self,
        collaborator_id: int,
        operator_id: str,
        env: Optional[str] = None,
    ) -> bool: ...

    def leave_collaboration(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        env: Optional[str] = None,
    ) -> bool: ...

    def check_collaborator_permission(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        required_level: PermissionLevel,
        env: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def check_permission(
        self,
        bot_pk: int,
        user_id: str,
        owner_id: str,
        required_level: PermissionLevel,
        env: Optional[str] = None,
    ) -> None: ...

    def get_permission_level(
        self,
        bot_pk: int,
        user_id: str,
        owner_id: str,
        env: Optional[str] = None,
    ) -> PermissionLevel: ...

    def get_operable_permission_level(
        self,
        *,
        bot: Mapping[str, Any],
        user_id: str,
        env: Optional[str] = None,
    ) -> PermissionLevel: ...

    def get_operable_permission_levels(
        self,
        *,
        bots: Sequence[Mapping[str, Any]],
        user_id: str,
        env: Optional[str] = None,
    ) -> Dict[int, PermissionLevel]: ...

    def list_user_collaborations(
        self,
        user_id: str,
        env: Optional[str] = None,
    ) -> List[CollaboratorRecord]: ...

    def add_editor(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        operator_id: str,
        user_name: Optional[str] = None,
        role: str = CollaboratorRole.MEMBER,
        env: Optional[str] = None,
    ) -> CollaboratorRecord: ...

    def list_editors(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        role: Optional[str] = None,
        env: Optional[str] = None,
    ) -> List[CollaboratorRecord]: ...

    def update_editor(
        self,
        bot_id: str,
        owner_id: str,
        collaborator_id: int,
        operator_id: str,
        role: str,
        env: Optional[str] = None,
    ) -> CollaboratorRecord: ...

    def remove_editor(
        self,
        bot_id: str,
        owner_id: str,
        collaborator_id: int,
        operator_id: str,
        env: Optional[str] = None,
    ) -> bool: ...

    def leave_editors(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        env: Optional[str] = None,
    ) -> bool: ...

    def on_collaboration_changed(
        self,
        bot_id: str,
        owner_id: str,
        env: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...
