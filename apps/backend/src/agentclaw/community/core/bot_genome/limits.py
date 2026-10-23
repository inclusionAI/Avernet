"""Limits the contract mirrors from the Manifest (README §6.4, §4.2).

Every value is imported from the module that owns it on the Manifest side,
so the two surfaces cannot drift: a patch may not carry what a Manifest
could not.
"""

from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    ARCHIVE_MEMBER_LIMIT,
    FETCH_ENTRY_LIMITS,
)
from agentclaw.community.core.bot_config_manifest.schema.limits import (
    MAX_ENTRIES_PER_CATEGORY,
    MAX_INLINE_CONTENT_BYTES,
    MAX_SCRIPT_BYTES,
)
from agentclaw.community.core.content_store.limits import MAX_AUDIT_ACTOR_CHARS

__all__ = [
    "MAX_AUDIT_ACTOR_CHARS",
    "MAX_DIFF_BYTES",
    "MAX_ENTRIES_PER_CATEGORY",
    "MAX_FILE_DIFFS",
    "MAX_INLINE_CONTENT_BYTES",
    "MAX_PERSONA_BYTES",
    "MAX_RESOURCE_FILE_BYTES",
    "MAX_SCRIPT_BYTES",
    "MAX_SKILL_BYTES",
    "MAX_TREE_BYTES",
    "MAX_TREE_MEMBERS",
    "MAX_UPLOAD_BYTES",
    "MAX_WRITTEN_PATH_BYTES",
    "MAX_SEGMENT_BYTES",
]

#: One unified diff (a ``file.edit`` of kind ``diff`` or a ``skill.update`` file diff): the inline limit.
MAX_DIFF_BYTES = MAX_INLINE_CONTENT_BYTES
#: ``file_diffs`` entries in one ``skill.update``.
MAX_FILE_DIFFS = 500
#: A persona file: the fetch-time cap of an identity entry.
MAX_PERSONA_BYTES = FETCH_ENTRY_LIMITS["identity"]
#: A local skill's tree and a file resource: the fetch-time caps.
MAX_SKILL_BYTES = FETCH_ENTRY_LIMITS["skills"]
MAX_RESOURCE_FILE_BYTES = FETCH_ENTRY_LIMITS["resources_file"]
#: A directory resource's tree: the archive cap and its member cap.
MAX_TREE_BYTES = FETCH_ENTRY_LIMITS["resources_archive"]
MAX_TREE_MEMBERS = ARCHIVE_MEMBER_LIMIT
#: ``PUT …/genome/content``: the largest single blob any entry may be.
MAX_UPLOAD_BYTES = max(MAX_SKILL_BYTES, MAX_RESOURCE_FILE_BYTES, MAX_TREE_BYTES)
#: README §3.4 / §4.2: one path segment, and one written workspace path.
MAX_SEGMENT_BYTES = 255
#: An audit identity (``created_actor``, ``moved_actor``, a promotion's ``actor_id``, and the content
#: store's ``modifier``) is ``MAX_AUDIT_ACTOR_CHARS`` wide on every column: the constant is the content
#: store's (imported above), and the genome's actor columns are sized by it so the two cannot drift.
MAX_WRITTEN_PATH_BYTES = 1024
