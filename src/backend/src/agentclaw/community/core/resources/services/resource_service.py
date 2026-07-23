"""
Resource service for managing data sources with multiple resource types support.

This service provides a unified interface for managing different types of resources:
- FILE: Local files and directories
- URL: External URLs (for future use)
- DATABASE: Database connections (for future use)
- API: API endpoints (for future use)

All resource metadata is stored in the main database (SQLite or the corp store).
"""
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Dict, Any

from agentclaw.community.core.resources.services.file_service import DATA_DIR, FileService

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.core.resources.models import (
    Resource,
    ResourceType,
    ResourceStatus,
    create_file_resource,
    create_url_resource,
    create_node_resource,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.log import get_logger

logger = get_logger()


def _get_resource_data_dir(
    path_factory: WorkspacePathFactory,
    user_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    bot_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    entity_type: Optional[str] = None,
) -> Path:
    """Get resource data directory using new bolt directory structure.

    Priority:
    1. If entity_id, bot_id, engine_type provided: use bolt path
    2. If user_id provided: construct entity_id from user_id
    3. Otherwise: use default DATA_DIR

    Args:
        user_id: User ID (used to construct entity_id if entity_id not provided)
        entity_id: Entity ID (e.g., staff_xxx, proj_xxx, team_xxx or pure id)
        bot_id: Bot ID (default: "default")
        engine_type: Engine type (default: "moltis")
        entity_type: Entity type (e.g., staff, proj, team), used when entity_id is pure id

    Returns:
        Path to data directory
    """
    factory = path_factory

    # Priority 1: Use provided entity_id/bot_id/engine_type
    if entity_id and bot_id and engine_type:
        # Parse entity_id to extract entity_type and pure entity_id
        if entity_id.startswith("staff_"):
            effective_entity_type = "staff"
            effective_entity_id = entity_id[6:]  # Remove "staff_" prefix
        elif entity_id.startswith("proj_"):
            effective_entity_type = "proj"
            effective_entity_id = entity_id[5:]  # Remove "proj_" prefix
        elif entity_id.startswith("team_"):
            effective_entity_type = "team"
            effective_entity_id = entity_id[5:]  # Remove "team_" prefix
        else:
            # Pure entity_id, use provided entity_type or default to "staff"
            effective_entity_type = entity_type if entity_type else "staff"
            effective_entity_id = entity_id
        return factory.get_bot_data_dir(effective_entity_id, bot_id, engine_type, effective_entity_type)

    # Priority 2: Construct from user_id
    if user_id:
        effective_entity_type = "staff"
        effective_entity_id = user_id
        effective_bot_id = "default"
        effective_engine = DEFAULT_ENGINE_TYPE
        return factory.get_bot_data_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)

    # Priority 3: Fallback to default DATA_DIR
    return DATA_DIR


class ResourceService:
    """
    Resource service - unified interface for managing resources.

    Supports multiple resource types through unified Resource model:
    - FILE: Local files and directories
    - URL: External URL links
    - NODE: Compute node resources
    """

    def __init__(
        self,
        repository: Any,
        path_factory: WorkspacePathFactory,
        bot_repo: Any = None,
        data_dir: Path = None,
        user_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        entity_type: Optional[str] = None,
    ):
        """
        Args:
            data_dir: Data directory (deprecated, use user_id/entity_id/bot_id/engine_type instead)
            repository: Repository instance (optional)
            user_id: User ID (used to construct path if entity_id not provided)
            entity_id: Entity ID (e.g., staff_xxx, proj_xxx, team_xxx or pure id)
            bot_id: Bot ID (default: "default")
            engine_type: Engine type (default: "moltis")
            entity_type: Entity type (e.g., staff, proj, team), used when entity_id is pure id
        """
        # Store path parameters for later use (e.g., saving to resource metadata)
        self._entity_id = entity_id
        self._bot_id = bot_id
        self._engine_type = engine_type
        self._entity_type = entity_type
        self._path_factory = path_factory

        # Determine data_dir using priority logic
        if data_dir:
            self.data_dir = data_dir
        else:
            self.data_dir = _get_resource_data_dir(
                path_factory=path_factory,
                user_id=user_id,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
                entity_type=entity_type
            )

        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._repo = repository
        self._bot_repo = bot_repo
        self._file_service = FileService(self.data_dir)

        logger.info(f"[ResourceService] Initialized: data_dir={self.data_dir}, repository={type(self._repo).__name__}")

    async def check_name_exists(
        self,
        name: str,
        resource_type: ResourceType,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        exclude_id: Optional[str] = None
    ) -> bool:
        """
        Check if a resource with the same name already exists.

        Uniqueness is determined by (name, resource_type, parent_path, user_id, bolt_id).
        """
        existing = self._repo.list_resources(
            resource_type=resource_type.value if isinstance(resource_type, ResourceType) else resource_type,
            parent_path=parent_path,
            user_id=user_id,
            bolt_id=self._bot_id
        )

        for item in existing:
            if item['name'] == name:
                if exclude_id and item['id'] == exclude_id:
                    continue
                return True

        return False

    def get_resource_by_name(
        self,
        name: str,
        resource_type: ResourceType,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Optional[Resource]:
        """Get a resource by name, type, parent_path and user_id."""
        existing = self._repo.list_resources(
            resource_type=resource_type.value if isinstance(resource_type, ResourceType) else resource_type,
            parent_path=parent_path,
            user_id=user_id,
            bolt_id=self._bot_id
        )

        for item in existing:
            if item['name'] == name:
                return Resource(**item)

        return None

    def list_resources(
        self,
        resource_type: Optional[ResourceType] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[ResourceStatus] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Resource]:
        """List resources with filters."""
        items = self._repo.list_resources(
            resource_type=resource_type.value if isinstance(resource_type, ResourceType) else resource_type,
            parent_path=parent_path,
            user_id=user_id,
            status=status.value if isinstance(status, ResourceStatus) else status,
            bolt_id=self._bot_id
        )

        resources = [Resource(**item) for item in items]

        if limit:
            resources = resources[offset:offset + limit]

        return resources

    # ==================== File Resource Operations ====================

    async def list_file_resources(
        self,
        parent_path: Optional[str] = None,
        flat: bool = False
    ) -> List[Resource]:
        """
        List file resources.

        If flat=True, returns flat list of files in parent_path.
        If flat=False, returns tree structure starting from parent_path.
        """
        if flat:
            return self.list_resources(
                resource_type=ResourceType.FILE,
                parent_path=parent_path or ""
            )
        else:
            return await self._sync_and_build_tree(parent_path or "")

    async def _sync_and_build_tree(self, path: str) -> List[Resource]:
        """Sync filesystem with database and build tree structure."""
        fs_items = await self._file_service.list_files(path)

        resources = []

        def process_node(node_data: dict, parent: str = "") -> Resource:
            """Process a filesystem node and create/update database record."""
            node_path = node_data.get('path', '')
            is_dir = node_data.get('is_dir', False)

            # Check if resource exists in database
            existing = self._repo.get_by_path(node_path, bolt_id=self._bot_id)

            if existing:
                # Update existing resource
                existing['attributes']['is_directory'] = is_dir
                existing['attributes']['size'] = node_data.get('size', 0)
                existing['attributes']['parent_path'] = parent
                existing['gmt_modified'] = datetime.now().isoformat()
                self._repo.update(existing['id'], existing)
                resource = Resource(**existing)
            else:
                # Create new resource
                resource = create_file_resource(
                    name=node_data.get('name', ''),
                    path=node_path,
                    parent_path=parent,
                    is_directory=is_dir,
                    size=node_data.get('size', 0),
                    extension=self._get_extension(node_data.get('name', '')),
                    mime_type=self._guess_mime_type(node_data.get('name', '')),
                    source='filesystem',
                )
                result = self._repo.create(resource.to_dict())
                resource.id = result.get('id')

            # Process children if directory
            if is_dir and 'children' in node_data:
                children = []
                for child_data in node_data['children']:
                    child = process_node(child_data, node_path)
                    children.append(child)
                resource.metadata = {'children': [c.id for c in children if c.id]}

            return resource

        # Process root items
        if 'children' in fs_items:
            for child_data in fs_items['children']:
                resource = process_node(child_data, path)
                resources.append(resource)

        return resources

    async def upload_file(
        self,
        *,
        data: bytes,
        filename: str,
        target_dir: str = "",
        user_id: Optional[str] = None,
        created_by: Optional[str] = None,
        device_fs: "DeviceFileSystem",
        device_path_prefix: str | None = None,
    ) -> Resource:
        """Upload a file and create resource record.

        Args:
            data: Raw file bytes (read at the adapter layer).
            filename: Source filename. May contain '/' for directory uploads.
            device_fs: Device filesystem boundary (resolved by the adapter via
                ``DeviceFilesystemDispatcher.for_bot(...)``) the underlying write
                is delivered through.
        """
        logger.info(f"[ResourceService.upload_file] Start: filename={filename}, target_dir={target_dir}, user_id={user_id}")

        # Check for duplicate name
        file_name = filename or "unnamed"
        if await self.check_name_exists(
            name=file_name,
            resource_type=ResourceType.FILE,
            parent_path=target_dir if target_dir else "",
            user_id=user_id
        ):
            logger.warning(f"[ResourceService.upload_file] Duplicate name detected: {file_name}")
            raise ValueError(f"Resource '{file_name}' already exists")

        # Save file via the device-filesystem boundary
        file_info = await self._file_service.upload_file(
            data=data,
            filename=filename,
            target_dir=target_dir,
            device_fs=device_fs,
            device_path_prefix=device_path_prefix,
        )
        logger.info(f"[ResourceService.upload_file] File saved: {file_info['path']}")

        # Create resource record with path info in metadata for deletion
        metadata = {}
        if self._entity_id:
            metadata['entity_id'] = self._entity_id
        if self._entity_type:
            metadata['entity_type'] = self._entity_type
        if self._bot_id:
            metadata['bot_id'] = self._bot_id
        if self._engine_type:
            metadata['engine_type'] = self._engine_type
        # device_provider/sandbox_id are no longer resolved here (the write
        # goes through the injected device_fs); they were only stored in
        # metadata historically and are not read back on the delete path
        # (delete resolves the device fresh from entity_id/bot_id/engine_type).
        resource = create_file_resource(
            name=file_info['name'],
            path=file_info['path'],
            parent_path=target_dir,
            size=file_info.get('size', 0),
            extension=self._get_extension(file_info['name']),
            mime_type=self._guess_mime_type(file_info['name']),
            is_directory=False,
            source='upload',
            user_id=user_id,
            created_by=created_by,
            metadata=metadata if metadata else None,
            bolt_id=self._bot_id if self._bot_id else 'default',
        )

        result = self._repo.create(resource.to_dict())
        resource.id = result.get('id')
        logger.info(f"[ResourceService.upload_file] Success: resource_id={resource.id}, path={resource.path}")
        return resource

    async def upload_files(
        self,
        files: List[tuple[bytes, str]],
        target_dir: str = "",
        user_id: Optional[str] = None,
        created_by: Optional[str] = None,
        *,
        device_fs: "DeviceFileSystem",
    ) -> Dict[str, Any]:
        """Upload multiple files.

        Args:
            files: List of (data, filename) tuples. Bytes read at the adapter layer.
            device_fs: Device filesystem boundary (resolved by the adapter).
        """
        uploaded = []
        errors = []

        for data, filename in files:
            try:
                resource = await self.upload_file(
                    data=data,
                    filename=filename,
                    target_dir=target_dir,
                    user_id=user_id,
                    created_by=created_by,
                    device_fs=device_fs,
                )
                uploaded.append(resource.to_dict())
            except Exception as e:
                errors.append({
                    'filename': filename,
                    'error': str(e)
                })

        return {
            'uploaded': uploaded,
            'errors': errors,
            'total': len(files),
            'success_count': len(uploaded),
            'error_count': len(errors)
        }

    async def get_file_resource(self, resource_id: str) -> Optional[Resource]:
        """Get file resource by ID."""
        item = self._repo.get_by_id(resource_id)
        if item:
            resource = Resource(**item)
            if resource.is_file:
                return resource
        return None

    async def get_file_resource_by_path(self, path: str) -> Optional[Resource]:
        """Get file resource by path."""
        item = self._repo.get_by_path(path, bolt_id=self._bot_id)
        return Resource(**item) if item else None

    async def delete_file_resource(self, resource_id: str) -> bool:
        """Delete file resource."""
        resource = await self.get_file_resource(resource_id)
        if not resource:
            return False

        # Delete from filesystem
        if resource.path:
            # todo delete use new api
            await self._file_service.delete_item(resource.path)

        # Soft delete from database
        return self._repo.delete(resource_id)

    async def create_directory(
        self,
        path: str,
        user_id: Optional[str] = None,
        created_by: Optional[str] = None,
        device_provider: str | None = None,
    ) -> Resource:
        """Create a directory resource."""
        logger.info(f"[ResourceService.create_directory] Start: path={path}, user_id={user_id}")

        # Extract directory name from path
        dir_name = path.split('/')[-1] if '/' in path else path
        parent_path = '/'.join(path.split('/')[:-1]) if '/' in path else ''

        # Check for duplicate name
        if await self.check_name_exists(
            name=dir_name,
            resource_type=ResourceType.FILE,
            parent_path=parent_path if parent_path else "",
            user_id=user_id
        ):
            logger.warning(f"[ResourceService.create_directory] Duplicate name: {dir_name}")
            raise ValueError(f"Resource '{dir_name}' already exists")

        # Create directory in filesystem
        # todo use new device
        dir_info = {
            'path': 'null'
        }
        if device_provider == 'arac':
            logger.info(f"[ResourceService.create_directory] Arca device: path={path}, skip create dir")
        else:
            dir_info = await self._file_service.create_directory(path)
        logger.info(f"[ResourceService.create_directory] Directory created: {dir_info['path']}")

        # Create resource record with path info in metadata for deletion
        metadata = {}
        if self._entity_id:
            metadata['entity_id'] = self._entity_id
        if self._entity_type:
            metadata['entity_type'] = self._entity_type
        if self._bot_id:
            metadata['bot_id'] = self._bot_id
        if self._engine_type:
            metadata['engine_type'] = self._engine_type

        resource = create_file_resource(
            name=dir_info['name'],
            path=dir_info['path'],
            parent_path=parent_path or '',
            is_directory=True,
            size=0,
            source='upload',
            user_id=user_id,
            created_by=created_by,
            metadata=metadata if metadata else None,
            bolt_id=self._bot_id if self._bot_id else 'default',
        )

        result = self._repo.create(resource.to_dict())
        resource.id = result.get('id')
        logger.info(f"[ResourceService.create_directory] Success: resource_id={resource.id}")
        return resource

    async def move_resource(
        self,
        resource_id: str,
        new_path: str,
        overwrite: bool = False
    ) -> Optional[Resource]:
        """Move resource to new path."""
        resource = await self.get_file_resource(resource_id)
        if not resource:
            return None

        old_path = resource.path

        # Move in filesystem
        try:
            result = await self._file_service.move_item(old_path, new_path, overwrite)
        except ValueError:
            return None

        # Update resource record
        resource_dict = resource.to_dict()
        resource_dict['attributes']['path'] = new_path
        resource_dict['attributes']['parent_path'] = '/'.join(new_path.split('/')[:-1]) if '/' in new_path else ''
        resource_dict['attributes']['name'] = result.get('name', resource.name)
        resource_dict['gmt_modified'] = datetime.now().isoformat()

        self._repo.update(resource_id, resource_dict)
        return Resource(**resource_dict)

    # ==================== URL Resource Operations ====================

    async def create_url_resource(
        self,
        name: str,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> Resource:
        """Create a URL resource."""
        # Check for duplicate name
        if await self.check_name_exists(
            name=name,
            resource_type=ResourceType.URL,
            parent_path=parent_path,
            user_id=user_id
        ):
            raise ValueError(f"Resource '{name}' already exists")

        resource = create_url_resource(
            name=name,
            url=url,
            method=method,
            headers=headers,
            parent_path=parent_path,
            user_id=user_id,
            created_by=created_by,
            source='manual',
            bolt_id=self._bot_id if self._bot_id else 'default',
        )
        result = self._repo.create(resource.to_dict())
        resource.id = result.get('id')
        return resource

    # ==================== Node Resource Operations ====================

    async def create_node_resource(
        self,
        name: str,
        node_address: str,
        path_alias: Optional[str] = None,
        scan_recursive: bool = True,
        parent_path: Optional[str] = None,
        user_id: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> Resource:
        """Create a Node resource."""
        # Check for duplicate name
        if await self.check_name_exists(
            name=name,
            resource_type=ResourceType.NODE,
            parent_path=parent_path,
            user_id=user_id
        ):
            raise ValueError(f"Resource '{name}' already exists")

        resource = create_node_resource(
            name=name,
            node_address=node_address,
            path_alias=path_alias or name,
            scan_recursive=scan_recursive,
            parent_path=parent_path,
            user_id=user_id,
            created_by=created_by,
            source='manual',
            bolt_id=self._bot_id if self._bot_id else 'default',
        )
        result = self._repo.create(resource.to_dict())
        resource.id = result.get('id')
        return resource

    # ==================== Generic Resource Operations ====================

    def get_resource(self, resource_id: str) -> Optional[Resource]:
        """Get any resource by ID."""
        item = self._repo.get_by_id(resource_id)
        return Resource(**item) if item else None

    def count_resources(
        self,
        resource_type: Optional[ResourceType] = None,
        user_id: Optional[str] = None
    ) -> int:
        """Count resources."""
        return self._repo.count_resources(
            resource_type=resource_type.value if isinstance(resource_type, ResourceType) else resource_type,
            user_id=user_id,
            bolt_id=self._bot_id
        )

    def count_children(self, parent_path: str) -> int:
        """Count all child resources (files + urls) under a directory."""
        return self._repo.count_resources(parent_path=parent_path, bolt_id=self._bot_id)

    def delete_resource(
        self,
        resource_id: str,
        device_provider: str | None = None,
        sandbox_id: str | None = None,
        *,
        device_fs: "DeviceFileSystem | None" = None,
    ) -> bool:
        """Delete any resource by ID.

        ``device_fs`` (resolved by the adapter via the dispatcher) is the
        device-filesystem boundary the Arca delete is delivered through.
        """
        item = self._repo.get_by_id(resource_id)
        if not item:
            return False

        resource = Resource(**item)
        data_dir = None
        if resource.is_file and resource.path:
            # Delete from filesystem using correct path
            # Get path info from resource metadata if available
            metadata = resource.metadata or {}
            entity_id = metadata.get('entity_id')
            bot_id = metadata.get('bot_id')
            engine_type = metadata.get('engine_type')

            if entity_id and bot_id and engine_type:
                # Use path factory to get correct data_dir
                factory = self._path_factory
                # Determine entity_type from entity_id format or metadata
                if entity_id.startswith('proj_'):
                    effective_entity_type = 'proj'
                    effective_entity_id = entity_id[5:]
                elif entity_id.startswith('staff_'):
                    effective_entity_type = 'staff'
                    effective_entity_id = entity_id[6:]
                elif entity_id.startswith('team_'):
                    effective_entity_type = 'team'
                    effective_entity_id = entity_id[5:]
                else:
                    # Pure entity_id, use entity_type from metadata or default to staff
                    effective_entity_type = metadata.get('entity_type', 'staff')
                    effective_entity_id = entity_id

                data_dir = factory.get_bot_data_dir(effective_entity_id, bot_id, engine_type, effective_entity_type)
                file_service = FileService(data_dir)
                logger.info(f"[delete_resource] Using data_dir from metadata: {data_dir}")
            else:
                # Fallback to current file_service (for backward compatibility)
                file_service = self._file_service
                logger.info(f"[delete_resource] Using default data_dir (metadata missing)")

            if device_provider == 'arca' and sandbox_id:
                if data_dir is None:
                    logger.error(f"[delete_resource] data_dir is None, cannot delete from arca")
                    return False
                # 处理路径拼接，避免重复的斜杠
                resource_path = resource.path.lstrip('/')
                path = str(data_dir) + "/" + resource_path
                logger.info(f"[delete_resource] Deleting from arca with sandbox_id: {sandbox_id}, path: {path}")
                import asyncio
                asyncio.create_task(device_fs.delete_file(path))
            else:
                import asyncio
                asyncio.create_task(file_service.delete_item(resource.path))

        return self._repo.delete(resource_id)

    # ==================== Helper Methods ====================

    @staticmethod
    def _get_extension(filename: str) -> str:
        """Get file extension."""
        if '.' in filename:
            return filename.split('.')[-1].lower()
        return ''

    @staticmethod
    def _guess_mime_type(filename: str) -> Optional[str]:
        """Guess MIME type from filename."""
        ext = ResourceService._get_extension(filename)

        mime_types = {
            'pdf': 'application/pdf',
            'txt': 'text/plain',
            'md': 'text/markdown',
            'json': 'application/json',
            'yaml': 'application/yaml',
            'yml': 'application/yaml',
            'xml': 'application/xml',
            'html': 'text/html',
            'htm': 'text/html',
            'css': 'text/css',
            'js': 'application/javascript',
            'csv': 'text/csv',
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'svg': 'image/svg+xml',
            'mp4': 'video/mp4',
            'mp3': 'audio/mpeg',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xls': 'application/vnd.ms-excel',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'ppt': 'application/vnd.ms-powerpoint',
            'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'drawio': 'application/xml',
            'dio': 'application/xml',
        }

        return mime_types.get(ext)
