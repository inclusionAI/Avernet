"""The platform's own copy of a teclaw bot's manifest-delivered files (W8)."""
from agentclaw.community.core.bot_config_manifest.managed_files.reader import (
    ManagedFilesComposeReader,
)
from agentclaw.community.core.bot_config_manifest.managed_files.store import (
    BOT_DATA_STORE,
    CATEGORY_IDENTITY,
    CATEGORY_RESOURCES,
    CATEGORY_SKILLS,
    IDENTITY_NS,
    SKILLS_LOCAL_DIR,
    WORKSPACE_NS,
    ManagedFile,
    ManagedFileScope,
    ManagedFilesStore,
    ManagedFilesStoreError,
    OWNER_ENTITY_TYPE,
    digest_of,
)

__all__ = [
    "BOT_DATA_STORE",
    "CATEGORY_IDENTITY",
    "CATEGORY_RESOURCES",
    "CATEGORY_SKILLS",
    "IDENTITY_NS",
    "ManagedFile",
    "ManagedFileScope",
    "ManagedFilesComposeReader",
    "ManagedFilesStore",
    "ManagedFilesStoreError",
    "OWNER_ENTITY_TYPE",
    "SKILLS_LOCAL_DIR",
    "WORKSPACE_NS",
    "digest_of",
]
