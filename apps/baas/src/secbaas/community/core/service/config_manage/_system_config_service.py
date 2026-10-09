"""
Default implementation of SystemConfigManageService.

Moved from domain/service/system_config_service.py as part of Phase 3 refactoring.
"""

from __future__ import annotations

from secbaas.community.api.config_manage import (
    SystemConfigCreate,
    SystemConfigListResponse,
    SystemConfigManageService,
    SystemConfigResponse,
    SystemConfigUpdate,
)
from secbaas.community.core.repository.system_config import (
    SystemConfigRecord,
    SystemConfigRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


def _record_to_response(record: SystemConfigRecord) -> SystemConfigResponse:
    """Convert SystemConfigRecord to SystemConfigResponse."""
    return SystemConfigResponse(
        id=record.id,
        conf_key=record.conf_key,
        conf_value=record.conf_value,
        env=record.env,
        name=record.name,
        description=record.description,
        creator=record.creator,
        modifier=record.modifier,
        gmt_create=record.gmt_create,
        gmt_modified=record.gmt_modified,
    )


class DefaultSystemConfigManageService(SystemConfigManageService):
    """System config business service implementation."""

    def __init__(self, repository: SystemConfigRepository) -> None:
        self._repository = repository

    def create_config(self, data: SystemConfigCreate) -> SystemConfigResponse:
        """Create a new system config."""
        env = get_current_env()
        logger.info("Creating system config: conf_key=%s, env=%s", data.conf_key, env)

        repo = self._repository
        if data.operator is None:
            raise ValueError("operator is required")
        record_id = repo.insert_config(
            conf_key=data.conf_key,
            conf_value=data.conf_value,
            env=env,
            name=data.name,
            description=data.description,
            creator=data.operator,
            modifier=data.operator,
        )

        logger.info("System config created successfully: record_id=%s", record_id)

        # Query back to get complete record
        record = repo.get_by_id(record_id)
        assert record is not None, f"Record not found after insert: id={record_id}"
        return _record_to_response(record)

    def get_config(self, conf_key: str) -> SystemConfigResponse | None:
        """Get config by conf_key."""
        env = get_current_env()
        logger.info("Getting system config: env=%s, conf_key=%s", env, conf_key)

        repo = self._repository
        record = repo.get_by_env_and_key(env, conf_key)

        if record:
            return _record_to_response(record)
        return None

    def update_config(
        self, conf_key: str, data: SystemConfigUpdate
    ) -> SystemConfigResponse | None:
        """Update system config."""
        env = get_current_env()
        logger.info("Updating system config: env=%s, conf_key=%s", env, conf_key)

        repo = self._repository
        record = repo.get_by_env_and_key(env, conf_key)

        if not record:
            return None

        # Build update fields
        update_fields = {}
        if data.conf_value is not None:
            update_fields["conf_value"] = data.conf_value
        if data.name is not None:
            update_fields["name"] = data.name
        if data.description is not None:
            update_fields["description"] = data.description

        if update_fields:
            repo.update_config(
                config_id=record.id, modifier=data.operator, **update_fields
            )
            logger.info("System config %s/%s updated successfully", env, conf_key)

        # Return updated record
        record = repo.get_by_env_and_key(env, conf_key)
        assert record is not None, (
            f"Record not found after update: env={env}, key={conf_key}"
        )
        return _record_to_response(record)

    def delete_config(self, conf_key: str) -> bool:
        """Delete system config."""
        env = get_current_env()
        logger.info("Deleting system config: env=%s, conf_key=%s", env, conf_key)

        repo = self._repository
        record = repo.get_by_env_and_key(env, conf_key)

        if not record:
            return False

        repo.delete_config(config_id=record.id)
        logger.info("System config %s/%s deleted successfully", env, conf_key)
        return True

    def list_configs(
        self, page: int = 1, page_size: int = 20
    ) -> SystemConfigListResponse:
        """List system configs."""
        env = get_current_env()
        logger.info(
            "Listing system configs: env=%s, page=%s, page_size=%s",
            env,
            page,
            page_size,
        )

        repo = self._repository
        total, records = repo.list_configs(env=env, page=page, page_size=page_size)

        items = [_record_to_response(r) for r in records]
        return SystemConfigListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
